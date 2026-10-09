"""Full-loss source, own-WIP stock, quota and native progress risks; no DB writes."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).parents[1]
REQUEST = "e1d534d5-6bea-4b66-9836-981eec36c27e"


class Doc(dict):
    def __deepcopy__(self, memo):
        result = Doc()
        memo[id(self)] = result
        for key, value in dict.items(self):
            result[key] = deepcopy(value, memo)
        return result

    def __getattribute__(self, key):
        if key not in ("__class__", "__dict__") and dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return super().__getattribute__(key)

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def precision(self, field):
        return self.get("precisions", {}).get(field, 6)

    def check_permission(self, action):
        if action in self.get("denied", ()):
            raise PermissionError("denied")

    def get_doc_before_save(self):
        return self.get("previous")

    def is_new(self):
        return not self.get("name") or self.get("new", False)

    def db_set(self, field, value, **kwargs):
        self[field] = value

    def insert(self):
        self.name = "LOSS"
        self.docstatus = 0
        self.new = False
        self.store["Stock Entry", self.name] = self
        self.insert_count.append(self.name)
        return self


class ProductionLossTests(unittest.TestCase):
    def setUp(self):
        self.flow = Doc(doctype="Jifeng Flow Order", name="FLOW", company="JF", item_code="FG", stock_uom="Nos",
                        bom="BOM", modified="flow-v1", materials=[Doc(item_code="RM")])
        self.work = Doc(doctype="Work Order", name="WO", company="JF", production_item="FG", stock_uom="Nos",
                        bom_no="BOM", jf_flow_order="FLOW", docstatus=1, qty=5, produced_qty=0, status="In Process",
                        wip_warehouse="WIP", modified="wo-v1", required_items=[Doc(name="RM-ROW", item_code="RM",
                        required_qty=10, stock_uom="Kg", consumed_qty=0, returned_qty=0, source_warehouse="RAW")],
                        operations=[Doc(name="OP", operation="OPERATION")])
        self.card = Doc(doctype="Job Card", name="JC", company="JF", work_order="WO", production_item="FG",
                        stock_uom="Nos", operation="OPERATION", operation_id="OP", docstatus=1, for_quantity=5,
                        total_completed_qty=0, process_loss_qty=5, pending_qty=0, modified="2026-10-09 10:00:00.000001",
                        wip_warehouse="WIP", time_logs=[Doc(name="LOG", employee="EMP", from_time="2026-10-09 09:00",
                        to_time="2026-10-09 10:00", completed_qty=0)])
        self.warehouse = Doc(name="WIP", company="JF", is_group=0, disabled=0)
        self.store = {("Jifeng Flow Order", "FLOW"): self.flow, ("Work Order", "WO"): self.work,
                      ("Job Card", "JC"): self.card, ("Warehouse", "WIP"): self.warehouse,
                      ("Item", "FG"): Doc(name="FG", stock_uom="Nos", is_stock_item=1),
                      ("Item", "RM"): Doc(name="RM", stock_uom="Kg", is_stock_item=1)}
        self.ledger = {}
        self.queries, self.insert_count = [], []
        self.lock_error = None
        self.other_allocated = 0
        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda **kwargs: lambda fn: fn
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.TimestampMismatchError = RuntimeError
        frappe.QueryTimeoutError = TimeoutError
        frappe.QueryDeadlockError = ConnectionAbortedError
        frappe.has_permission = Mock()

        def get_doc(kind, name=None, **kwargs):
            if isinstance(kind, dict):
                doc = Doc(kind)
                doc.items = [Doc(row) for row in doc.get("items") or []]
                doc.new = True
                doc.store, doc.insert_count = self.store, self.insert_count
                return doc
            return self.store[kind, name]

        frappe.get_doc = get_doc

        def get_value(kind, name, field, **kwargs):
            return self.store[kind, name].get(field) if (kind, name) in self.store else None

        def sql(query, args, **kwargs):
            self.queries.append(query)
            if self.lock_error:
                raise self.lock_error("lock occupied")
            if "tabStock Ledger Entry" in query:
                return deepcopy(self.ledger.get(args[0], []))
            if "SELECT qty, produced_qty, process_loss_qty" in query:
                return [Doc(qty=self.other_allocated, produced_qty=0, process_loss_qty=0, docstatus=0)] if self.other_allocated else []
            if "WHERE production_plan=%s" in query:
                return []
            entries = [doc for (kind, _), doc in self.store.items() if kind == "Stock Entry"]
            if "process_loss_qty FROM" in query:
                return [Doc(name=doc.name, process_loss_qty=doc.get("process_loss_qty") or 0) for doc in entries
                        if doc.get("work_order") == args[0] and doc.purpose == "Manufacture" and doc.docstatus == 1]
            if "purpose IN" in query:
                return [Doc(name=doc.name) for doc in entries if doc.docstatus == 1 and (
                    doc.get("work_order") == args[0] and doc.purpose in (
                        "Material Transfer for Manufacture", "Manufacture", "Material Consumption for Manufacture")
                    or doc.get("jf_production_loss_work_order") == args[1])]
            if "jf_production_loss_" in query and "tabStock Entry` WHERE" in query:
                normalized = query.replace(" ", "")
                field = next(field for field in ("request", "job_card", "work_order")
                             if f"jf_production_loss_{field}=%s" in query)
                return [Doc(name=doc.name, docstatus=doc.docstatus) for doc in entries if doc.get("jf_production_loss_" + field) == args[0]
                        and ("docstatus=1" not in normalized or doc.docstatus == 1)
                        and ("docstatus<2" not in normalized or doc.docstatus < 2)]
            if "WHERE name=%s" in query:
                kind = query.split("`tab")[1].split("`")[0]
                return [Doc(name=args[0])] if (kind, args[0]) in self.store else []
            return []

        frappe.db = Doc(sql=sql, get_value=get_value)
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda value, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
        utils.get_datetime = lambda value: datetime.fromisoformat(str(value))
        utils.now_datetime = lambda: datetime(2026, 10, 9, 10, 0, 0)
        quantity = types.ModuleType("jifeng_erp.flow_quantity")
        quantity.check_quantity_fields = Mock()
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.stock_plan_quantity = lambda flow: 5
        workflow.validate_flow_source = Mock()
        instructions = types.ModuleType("jifeng_erp.production_instruction")
        instructions._allocated = lambda *args, **kwargs: self.other_allocated
        self.modules = {"frappe": frappe, "frappe.utils": utils, "jifeng_erp.flow_quantity": quantity,
                        "jifeng_erp.workflow": workflow, "jifeng_erp.production_instruction": instructions}
        self.patcher = patch.dict(sys.modules, self.modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        spec = importlib.util.spec_from_file_location("jifeng_erp.production_loss", ROOT / "jifeng_erp/production_loss.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        self.addCleanup(lambda: sys.modules.pop(spec.name, None))
        spec.loader.exec_module(self.module)
        self.transfer = Doc(doctype="Stock Entry", name="TRANSFER", company="JF", purpose="Material Transfer for Manufacture",
                            stock_entry_type="Material Transfer for Manufacture", work_order="WO", docstatus=1,
                            items=[Doc(name="TRANSFER-ROW", item_code="RM", stock_uom="Kg", transfer_qty=10,
                                       s_warehouse="RAW", t_warehouse="WIP")])
        self.store["Stock Entry", "TRANSFER"] = self.transfer
        self.ledger["TRANSFER"] = [Doc(name="SLE-IN", voucher_detail_no="TRANSFER-ROW", company="JF", item_code="RM",
                        warehouse="WIP", stock_uom="Kg", actual_qty=10, stock_value_difference=40)]

    def entry(self, name="LOSS", status=0):
        return Doc(doctype="Stock Entry", name=name, company="JF", purpose="Material Issue", stock_entry_type=self.module.LOSS_TYPE,
                   from_warehouse="WIP", docstatus=status, jf_production_loss_job_card="JC", jf_production_loss_work_order="WO",
                   jf_production_loss_qty=5, jf_production_loss_reason="实际全部报废", jf_production_loss_no_residual=1,
                   jf_production_loss_request=REQUEST, jf_production_loss_source_version=self.card.modified,
                   jf_production_loss_source_signature=self.module.source_signature(self.card, self.work),
                   items=[Doc(name="LOSS-ROW", item_code="RM", jf_flow_order="FLOW", qty=10, uom="Kg", stock_uom="Kg",
                              conversion_factor=1, transfer_qty=10, s_warehouse="WIP")])

    def source(self, doc=None):
        return self.module.read_loss_source(doc or self.entry(), read=lambda kind, name: self.store[kind, name])

    def post(self):
        doc = self.entry(status=1)
        self.store["Stock Entry", doc.name] = doc
        self.ledger[doc.name] = [Doc(name="SLE-OUT", voucher_detail_no="LOSS-ROW", company="JF", item_code="RM",
                        warehouse="WIP", stock_uom="Kg", actual_qty=-10, stock_value_difference=-40)]
        return doc

    def test_source_is_real_zero_finished_full_loss_and_progress_version_not_identity(self):
        result = self.source()
        self.assertEqual((result["qty"], result["inventory_qty"], result["lost_qty"]), (Decimal(5), Decimal(0), Decimal(5)))
        self.card.modified = "2026-10-09 11:00:00"
        self.work.modified, self.work.process_loss_qty = "new", 5
        self.assertEqual(self.source()["qty"], 5)
        self.assertEqual(self.card.total_completed_qty, 0)

    def test_partial_or_pending_or_semi_or_unconfirmed_residual_rejected(self):
        for field, value in (("total_completed_qty", 1), ("pending_qty", 1), ("track_semi_finished_goods", 1),
                             ("is_subcontracted", 1), ("is_corrective_job_card", 1)):
            original = self.card.get(field)
            self.card[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.source()
            self.card[field] = original
        doc = self.entry(); doc.jf_production_loss_no_residual = 0
        with self.assertRaisesRegex(ValueError, "残值"): self.source(doc)
        doc = self.entry(); doc.jf_production_loss_reason = " "
        with self.assertRaises(ValueError): self.source(doc)
        self.store["Item", "RM"].has_batch_no = 1
        with self.assertRaisesRegex(ValueError, "逐批核对"): self.source()

    def test_orphan_plan_refs_and_subassembly_full_loss_remain_rejected(self):
        for field in ("production_plan", "production_plan_item", "production_plan_sub_assembly_item"):
            self.work[field] = "PLAN-SOURCE"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "生产计划"):
                self.source()
            self.work[field] = None
        self.work.track_semi_finished_goods = 1
        manufacture = Doc(company="JF", work_order="WO", purpose="Manufacture", process_loss_qty=1)
        with patch.object(self.module, "_cancel_flow_capacity", side_effect=AssertionError("semi passed ordinary loss gate")):
            self.module.before_cancel_loss_entry(manufacture)

    def test_saved_header_loss_cannot_round_up_beyond_reported_loss(self):
        doc = self.entry(); doc.jf_production_loss_qty = 5.0000004
        self.card.precisions = {"process_loss_qty": 3}
        with self.assertRaisesRegex(ValueError, "必须等于"):
            self.source(doc)

    def test_source_or_unit_changed_and_native_fields_cannot_be_mixed(self):
        doc = self.entry(); self.card.time_logs[0].completed_qty = 1
        with self.assertRaises(ValueError): self.source(doc)
        self.card.time_logs[0].completed_qty = 0
        self.work.required_items[0].required_qty = 11
        with self.assertRaisesRegex(ValueError, "来源已变化"): self.source(doc)
        self.work.required_items[0].required_qty = 10
        for field, value in (("work_order", "WO"), ("job_card", "JC"), ("jf_material_issue_flow", "FLOW"),
                             ("process_loss_qty", 5), ("fg_completed_qty", 5)):
            doc = self.entry(); doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.source(doc)
        doc = self.entry(); doc.items[0].stock_uom = "Nos"
        with self.assertRaises(ValueError): self.source(doc)

    def test_own_wip_requires_exact_sle_not_a_shared_bin_or_other_wo(self):
        self.assertEqual(self.module._own_wip_pool(self.work)["RM"], 10)
        self.ledger["TRANSFER"] = []
        with self.assertRaisesRegex(ValueError, "库存账不完整"): self.module._own_wip_pool(self.work)
        self.transfer.work_order = "OTHER-WO"
        with self.assertRaisesRegex(ValueError, "剩余在制库存"):
            self.module.validate_loss_entry(self.entry())

    def test_wrong_sle_warehouse_company_or_quantity_rejected(self):
        for field, value in (("warehouse", "OTHER-WIP"), ("company", "OTHER"), ("actual_qty", 9), ("stock_uom", "Nos")):
            original = self.ledger["TRANSFER"][0][field]
            self.ledger["TRANSFER"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.module._own_wip_pool(self.work)
            self.ledger["TRANSFER"][0][field] = original

    def test_one_card_cannot_be_double_posted_and_cancelled_source_releases(self):
        doc = self.post()
        with self.assertRaisesRegex(ValueError, "重复办理"):
            self.module.validate_loss_entry(self.entry(name="LOSS-2"))
        self.assertEqual(self.module.get_booked_loss_for_job_card("JC"), 5)
        self.assertEqual(self.module.get_booked_loss_for_work_order("WO"), 5)
        doc.docstatus = 2
        self.ledger[doc.name] = []
        self.assertEqual(self.module.get_booked_loss_for_work_order("WO"), 0)

    def test_original_native_manufacture_loss_cannot_be_booked_again(self):
        original = Doc(name="MANUFACTURE", work_order="WO", purpose="Manufacture", docstatus=1, process_loss_qty=1)
        self.store["Stock Entry", original.name] = original
        with self.assertRaisesRegex(ValueError, "不能混用"):
            self.module._validate_budget(self.entry(), self.source(), self.work, self.card)

    def test_native_partial_manufacture_cancel_reclaims_replacement_draft_capacity(self):
        self.work.produced_qty = 4
        manufacture = Doc(name="MAKE", company="JF", work_order="WO", purpose="Manufacture", docstatus=1,
                          process_loss_qty=1, items=[Doc(is_finished_item=1, transfer_qty=4)])
        self.store["Stock Entry", "MAKE"] = manufacture
        self.other_allocated = 1
        with self.assertRaisesRegex(ValueError, "补产预留"):
            self.module.before_cancel_loss_entry(manufacture)
        self.other_allocated = 0
        self.module.before_cancel_loss_entry(manufacture)

    def test_native_plan_loss_cancel_prelocks_actual_parent_child_and_peer_scope_nowait(self):
        self.work.production_plan, self.work.production_plan_item = "PLAN", "PLAN-ROW"
        self.store["Production Plan", "PLAN"] = Doc(name="PLAN")
        self.store["Production Plan Item", "PLAN-ROW"] = Doc(name="PLAN-ROW", parent="PLAN")
        self.module._lock_native_plan_quantities(self.work)
        plan_queries = [query for query in self.queries if "Production Plan" in query or "production_plan=%s" in query]
        self.assertEqual(len(plan_queries), 3)
        self.assertTrue(all("NOWAIT" in query for query in plan_queries))
        self.assertIn("production_plan_item=%s", plan_queries[-1])
        self.assertIn("docstatus<2", plan_queries[-1])
        self.lock_error = TimeoutError
        with self.assertRaisesRegex(TimeoutError, "正在更新"):
            self.module._lock_native_plan_quantities(self.work)

    def test_cancel_checks_replacement_drafts_and_source_cancel_is_inverse(self):
        doc = self.post()
        self.other_allocated = 5
        with self.assertRaisesRegex(ValueError, "补产预留"): self.module.before_cancel_loss_entry(doc)
        self.other_allocated = 0
        self.module.before_cancel_loss_entry(doc)
        with self.assertRaisesRegex(ValueError, "逆序"): self.module.before_cancel_loss_entry(self.transfer)
        with self.assertRaisesRegex(ValueError, "先撤销"): self.module.before_cancel_source(self.card)
        doc.docstatus = 2
        self.module.before_cancel_source(self.card)
        self.module.before_cancel_loss_entry(self.transfer)

    def test_source_metadata_cannot_be_removed_to_bypass_validation(self):
        old = self.entry()
        doc = deepcopy(old); doc.previous = old; doc.stock_entry_type = "Material Issue"
        with self.assertRaisesRegex(ValueError, "不能更改"): self.module.validate_loss_entry(doc)

    def test_approved_loss_freezes_business_source_but_preserves_native_progress(self):
        self.post()
        current = Doc(self.card)
        current.modified, current.status = "new-version", "Completed"
        self.module.validate_loss_source(current)
        current.process_loss_qty = 4
        with self.assertRaisesRegex(ValueError, "不能修改"): self.module.validate_loss_source(current)
        current = Doc(self.work); current.produced_qty, current.process_loss_qty = 0, 5
        self.module.validate_loss_source(current)
        current.qty = 6
        with self.assertRaises(ValueError): self.module.validate_loss_source(current)

    def test_only_initial_version_is_exact_and_another_draft_is_not_silently_created(self):
        new = self.entry(); new.new = True; new.jf_production_loss_source_version = "2026-10-09 09:00:00"
        with self.assertRaises(RuntimeError): self.module.validate_loss_entry(new)
        draft = self.entry(name="FIRST")
        self.store["Stock Entry", "FIRST"] = draft
        with self.assertRaisesRegex(ValueError, "草稿"):
            self.module.validate_loss_entry(self.entry(name="SECOND"))

    def test_create_draft_reuses_exact_request_and_unknown_result_does_not_insert_again(self):
        context = self.module.inspect_loss_issue("JC", REQUEST)
        self.assertEqual(context["items"][0]["qty"], 10)
        result = self.module.create_loss_issue("JC", self.card.modified, 1, "全部报废", "WIP", REQUEST)
        self.assertEqual(result["docstatus"], 0)
        second = self.module.create_loss_issue("JC", "outdated", 1, "重复未知结果", "WIP", REQUEST)
        self.assertEqual(result["stock_entry"], second["stock_entry"])
        self.assertEqual(len(self.insert_count), 1)
        self.store["Stock Entry", "LOSS"].docstatus = 2
        with self.assertRaisesRegex(ValueError, "不能自动重新办理"):
            self.module.create_loss_issue("JC", self.card.modified, 1, "重新报废", "WIP", REQUEST)
        self.assertEqual(len(self.insert_count), 1)

    def test_cancelled_exact_request_reads_after_card_source_changes_without_replaying(self):
        doc = self.entry(status=2)
        self.store["Stock Entry", "LOSS"] = doc
        self.card.update(docstatus=2, total_completed_qty=3, process_loss_qty=0, pending_qty=2,
                         work_order="CHANGED-WORK", modified="new-source")
        self.work.update(docstatus=2, bom_no="CHANGED-BOM")
        self.flow.bom = "CHANGED-BOM"
        with patch.object(self.module, "_creation_context", side_effect=AssertionError("history used new source")), \
             patch.object(self.module, "read_loss_source", side_effect=AssertionError("history used current signature")):
            result = self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
            self.assertEqual(result, {"stock_entry": "LOSS", "docstatus": 2, "existing": True})
            with self.assertRaisesRegex(ValueError, "不能自动重新办理"):
                self.module.create_loss_issue("JC", "new-source", 1, "重新确认", "WIP", REQUEST)
        self.assertFalse(self.insert_count)

    def test_cancelled_history_is_card_bound_and_requires_both_document_and_used_field_permissions(self):
        doc = self.entry(status=2)
        self.store["Stock Entry", "LOSS"] = doc
        other = Doc(self.card); other.name = "OTHER-CARD"
        self.store["Job Card", other.name] = other
        with self.assertRaisesRegex(ValueError, "指向不一致"):
            self.module.inspect_loss_issue(other.name, REQUEST, only_previous=1)
        for target in (self.card, doc):
            target.denied = ("read",)
            with self.assertRaises(PermissionError): self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
            target.denied = ()
        for doctype, denied_field in (("Stock Entry", "jf_production_loss_request"), ("Job Card", "name")):
            def deny(kind, fields, **kwargs):
                if kind == doctype and denied_field in fields: raise PermissionError("field/mask denied")
            with patch.object(self.modules["jifeng_erp.flow_quantity"], "check_quantity_fields", deny), self.assertRaises(PermissionError):
                self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)

    def test_history_advisory_status_is_rechecked_and_active_request_keeps_flow_first_lock_order(self):
        doc = self.entry()
        self.store["Stock Entry", "LOSS"] = doc
        result = self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
        self.assertTrue(result["existing"])
        first_active_lock = next(i for i, query in enumerate(self.queries)
                                 if "tabStock Entry` WHERE jf_production_loss_request" in query and "FOR UPDATE" in query)
        flow_lock = next(i for i, query in enumerate(self.queries) if "tabJifeng Flow Order" in query)
        self.assertLess(flow_lock, first_active_lock)
        with patch.object(self.module, "_request_records", side_effect=[
                [Doc(name="LOSS", docstatus=2)], [Doc(name="LOSS", docstatus=0)]]):
            with self.assertRaises(RuntimeError): self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)

    def test_previous_absence_is_a_current_locked_read_without_loss_eligibility_and_pending_write_is_unknown(self):
        self.card.total_completed_qty, self.card.process_loss_qty = 5, 0
        with patch.object(self.module, "_creation_context", side_effect=AssertionError("absence used create context")):
            result = self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
            self.assertFalse(result["existing"])
            current = [query for query in self.queries if "jf_production_loss_request=%s" in query and "NOWAIT" in query]
            self.assertEqual(len(current), 1)
            with patch.object(self.module, "_request_records", side_effect=[[], TimeoutError("pending insert")]):
                with self.assertRaises(TimeoutError): self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
            with patch.object(self.module, "_request_records", side_effect=[[], [Doc(name="JUST-CREATED", docstatus=0)]]):
                with self.assertRaises(RuntimeError): self.module.inspect_loss_issue("JC", REQUEST, only_previous=1)
        self.assertFalse(self.insert_count)
        with self.assertRaisesRegex(ValueError, "全部损耗"):
            self.module.inspect_loss_issue("JC", REQUEST)

    def test_stale_source_permission_and_wrong_warehouse_do_not_create(self):
        with self.assertRaises(RuntimeError):
            self.module.create_loss_issue("JC", "stale", 1, "全部报废", "WIP", REQUEST)
        with self.assertRaisesRegex(ValueError, "在制仓库"):
            self.module.create_loss_issue("JC", self.card.modified, 1, "全部报废", "OTHER", REQUEST)
        self.card.denied = ("read",)
        with self.assertRaises(PermissionError): self.module.inspect_loss_issue("JC", REQUEST)
        self.assertEqual(len(self.insert_count), 0)

    def test_nowait_timeout_and_deadlock_abort_before_progress_or_draft(self):
        for error in (TimeoutError, ConnectionAbortedError):
            self.lock_error = error
            with self.subTest(error=error), self.assertRaisesRegex(error, "正在更新"):
                self.module.inspect_loss_issue("JC", REQUEST)
        self.assertFalse(self.insert_count)
        self.assertTrue(all("NOWAIT" in query for query in self.queries if "FOR UPDATE" in query))

    def progress_module(self):
        spec = importlib.util.spec_from_file_location("loss_progress_under_test", ROOT / "jifeng_erp/production_loss_progress.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_native_wo_and_card_progress_keeps_finished_wage_completed_zero_and_reverses(self):
        progress = self.progress_module()
        class NativeWork(Doc):
            def _process_loss_qty(self): return 0
            def update_consumed_qty_for_required_items(self):
                for item in self.required_items: item.db_set("consumed_qty", 0)
            def update_consumed_qty_in_stock_reservation(self, item, qty, warehouse):
                self.reserved = qty, warehouse
        class Work(progress.ProductionLossWorkOrderMixin, NativeWork): pass
        class NativeCard(Doc):
            def get_consumed_process_loss(self): return 0
        class Card(progress.ProductionLossJobCardMixin, NativeCard): pass
        work, card = Work(self.work), Card(self.card)
        work.reserve_stock = 1
        doc = self.post()
        self.assertEqual(work._process_loss_qty(), 5)
        self.assertEqual(card.get_consumed_process_loss(), 5)
        work.update_consumed_qty_for_required_items()
        self.assertEqual(work.required_items[0].consumed_qty, 10)
        self.assertEqual(work.reserved, (10, "WIP"))
        self.assertEqual((card.total_completed_qty, card.get("manufactured_qty") or 0), (0, 0))
        doc.docstatus = 2
        self.assertEqual(work._process_loss_qty(), 0)
        self.assertEqual(card.get_consumed_process_loss(), 0)
        work.update_consumed_qty_for_required_items()
        self.assertEqual(work.required_items[0].consumed_qty, 0)
        work.track_semi_finished_goods = 1
        self.assertEqual(work._process_loss_qty(), 0)
        work.track_semi_finished_goods = 0
        work.new, card.new = True, True
        with patch.object(progress, "get_booked_loss_for_work_order", side_effect=AssertionError("new WO queried")), \
             patch.object(progress, "get_booked_loss_for_job_card", side_effect=AssertionError("new Card queried")):
            self.assertEqual(work._process_loss_qty(), 0)
            self.assertEqual(card.get_consumed_process_loss(), 0)

    def test_submit_and_cancel_use_native_tail_without_jobcard_finished_updates(self):
        doc = self.post()
        calls = []
        for method in ("update_work_order_qty", "update_consumed_qty_for_required_items", "update_status", "update_planned_qty"):
            self.work[method] = lambda name=method: calls.append(name)
        self.card.set_status = lambda **kwargs: calls.append("card_status")
        self.module.update_loss_progress(doc)
        self.assertEqual(calls, ["update_work_order_qty", "update_consumed_qty_for_required_items",
                                 "update_status", "update_planned_qty", "card_status"])
        self.assertEqual(self.card.total_completed_qty, 0)

    def test_reserved_wip_is_released_before_native_sle_and_cancel_restores_without_missing_current_sle(self):
        progress = self.progress_module()
        doc = self.entry(status=1)
        self.store["Stock Entry", "LOSS"] = doc
        self.work.reserve_stock = 1
        events = []
        self.work.update_consumed_qty_in_stock_reservation = lambda item, qty, warehouse: events.append(("reserve", qty, warehouse))
        native = types.ModuleType("erpnext.manufacturing.doctype.work_order.work_order")
        native.get_consumed_qty = lambda *args: 0
        class NativeEntry(Doc):
            def update_stock_ledger(self, *args, **kwargs):
                events.append(("native_sle", self.docstatus))
                return "native-result"
        class Entry(progress.ProductionLossStockEntryMixin, NativeEntry): pass
        with patch.dict(sys.modules, {native.__name__: native}):
            # Native submit has saved docstatus=1 but not created its SLE yet.
            self.assertNotIn("LOSS", self.ledger)
            self.assertEqual(Entry(doc).update_stock_ledger(), "native-result")
            self.assertEqual(events, [("reserve", 10, "WIP"), ("native_sle", 1)])
            events.clear(); doc.docstatus = 2
            Entry(doc).update_stock_ledger()
            self.assertEqual(events, [("reserve", 0, "WIP"), ("native_sle", 2)])
            events.clear()
            Entry(self.transfer).update_stock_ledger()
            self.assertEqual(events, [("native_sle", 1)])


if __name__ == "__main__":
    unittest.main()
