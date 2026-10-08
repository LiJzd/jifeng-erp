"""Check production stock rows keep their source flow when ERPNext creates entries."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Record(dict):
    def __init__(self, **values):
        super().__init__(values)
        self.setdefault("docstatus", 0)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def is_new(self):
        return bool(self.get("__islocal", True))

    def check_permission(self, permission):
        if permission in self.get("denied", set()):
            raise PermissionError("native read denied")


class TestFlowStockEntry(unittest.TestCase):
    def setUp(self):
        self.flow = Record(name="FLOW-1", company="JF", item_code="RING",
                           materials=[Record(item_code="SILVER")])
        self.work_order = Record(company="JF", production_item="RING", jf_flow_order="FLOW-1")
        self.entry = Record(doctype="Stock Entry", docstatus=0, company="JF", work_order="WO-1", items=[
            Record(item_code="SILVER", jf_flow_order=None),
            Record(item_code="RING", jf_flow_order=None),
            Record(item_code="SCRAP", jf_flow_order=None),
        ])
        frappe = types.ModuleType("frappe")
        self.frappe = frappe
        frappe.PermissionError = PermissionError
        frappe.session = Record(user="stock-user")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.documents = {
            ("Work Order", "WO-1"): self.work_order,
            ("Jifeng Flow Order", "FLOW-1"): self.flow,
        }
        frappe.get_doc = lambda doctype, name, **kwargs: self.documents[(doctype, name)]
        sources = {
            ("Material Request Item", "MR-ROW"): Record(
                parent="MR-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Order Item", "PO-ROW"): Record(
                parent="PO-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Receipt Item", "PR-ROW"): Record(
                parent="PR-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Invoice Item", "PI-ROW"): Record(
                parent="PI-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Subcontracting Order Item", "SCO-ROW"): Record(
                parent="SCO-1", item_code="RING", jf_flow_order="FLOW-1"),
            ("Subcontracting Receipt Item", "SCR-ROW"): Record(
                parent="SCR-1", item_code="RING", jf_flow_order="FLOW-1"),
            ("Subcontracting Order Supplied Item", "SCO-RM-ROW"): Record(
                parent="SCO-1", reference_name="SCO-ROW", main_item_code="RING",
                rm_item_code="SILVER"),
        }
        frappe.db = types.SimpleNamespace(get_value=lambda doctype, name, fields, as_dict=False:
                                          sources.get((doctype, name)))
        self.sources = sources
        self.material_issue_calls = []
        self.manufacturing_guard_calls = []
        material_issue = types.ModuleType("jifeng_erp.material_issue")
        def assert_no_active_work_order(flow):
            self.material_issue_calls.append(flow.name)
            if flow.name in getattr(self, "blocked_work_order_flows", set()):
                raise ValueError("本流程已有有效生产工单")
        material_issue.assert_no_active_work_order = assert_no_active_work_order
        def assert_no_net_issue_for_manufacturing(flow):
            self.manufacturing_guard_calls.append(flow.name)
            if flow.name in getattr(self, "blocked_manufacturing_flows", set()):
                raise ValueError("本流程仍有净领料")
        material_issue.assert_no_net_issue_for_manufacturing = assert_no_net_issue_for_manufacturing
        production = types.ModuleType("jifeng_erp.production_instruction")
        production.validate_work_order = lambda doc: None
        custom = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
        custom.create_custom_fields = lambda *args, **kwargs: None
        modules = {
            "frappe": frappe,
            "frappe.custom": types.ModuleType("frappe.custom"),
            "frappe.custom.doctype": types.ModuleType("frappe.custom.doctype"),
            "frappe.custom.doctype.custom_field": types.ModuleType("frappe.custom.doctype.custom_field"),
            "frappe.custom.doctype.custom_field.custom_field": custom,
            "jifeng_erp.material_issue": material_issue,
            "jifeng_erp.production_instruction": production,
        }
        modules_patch = patch.dict(sys.modules, modules)
        modules_patch.start()
        self.addCleanup(modules_patch.stop)
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "setup.py"
        spec = importlib.util.spec_from_file_location("flow_setup_under_test", path)
        self.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.setup)
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.stock_plan_quantity = lambda flow: 5
        workflow_patch = patch.dict(sys.modules, {"jifeng_erp.workflow": workflow})
        workflow_patch.start()
        self.addCleanup(workflow_patch.stop)

    def test_work_order_flow_is_copied_to_material_and_finished_item(self):
        self.setup.validate_flow_links(self.entry)
        self.assertEqual([row.jf_flow_order for row in self.entry["items"]],
                         ["FLOW-1", "FLOW-1", None])

    def test_other_flow_cannot_be_linked_to_work_order_entry(self):
        self.entry["items"][0].jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "其他流程单"):
            self.setup.validate_flow_links(self.entry)

    def test_company_mismatch_blocks_stock_entry(self):
        self.entry.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "公司不一致"):
            self.setup.validate_flow_links(self.entry)

    def test_unrelated_work_order_item_keeps_manual_flow_empty(self):
        self.entry["items"] = [Record(item_code="SCRAP", jf_flow_order=None)]
        self.setup.validate_flow_links(self.entry)
        self.assertIsNone(self.entry["items"][0].jf_flow_order)

    def test_row_linked_manual_issue_checks_active_work_order_after_flow_lock(self):
        entry = Record(doctype="Stock Entry", docstatus=0, company="JF", purpose="Material Issue",
                       stock_entry_type="Material Issue", items=[
                           Record(item_code="SILVER", jf_flow_order="FLOW-1")])
        self.setup.validate_flow_links(entry)
        self.assertEqual(self.material_issue_calls, ["FLOW-1"])

    def test_row_linked_manual_issue_is_rejected_when_work_order_is_active(self):
        self.blocked_work_order_flows = {"FLOW-1"}
        entry = Record(doctype="Stock Entry", docstatus=0, company="JF", purpose="Material Issue",
                       stock_entry_type="Material Issue", items=[
                           Record(item_code="SILVER", jf_flow_order="FLOW-1")])
        with self.assertRaisesRegex(ValueError, "已有有效生产工单"):
            self.setup.validate_flow_links(entry)

    def test_native_work_order_save_checks_net_issues(self):
        self.blocked_manufacturing_flows = {"FLOW-1"}
        work = Record(doctype="Work Order", docstatus=0, jf_flow_order="FLOW-1", company="JF")
        with self.assertRaisesRegex(ValueError, "仍有净领料"):
            self.setup.validate_flow_links(work)
        self.assertEqual(self.manufacturing_guard_calls, ["FLOW-1"])

    def test_work_order_transfer_or_manufacture_checks_net_issues(self):
        self.blocked_manufacturing_flows = {"FLOW-1"}
        entry = Record(doctype="Stock Entry", docstatus=0, company="JF", work_order="WO-1",
                       purpose="Material Transfer for Manufacture",
                       stock_entry_type="Material Transfer for Manufacture", items=[
                           Record(item_code="SILVER", jf_flow_order=None),
                           Record(item_code="RING", jf_flow_order=None)])
        with self.assertRaisesRegex(ValueError, "仍有净领料"):
            self.setup.validate_flow_links(entry)
        self.assertEqual(self.manufacturing_guard_calls, ["FLOW-1"])

    def test_procurement_documents_accept_only_materials_from_matching_flow(self):
        for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
            with self.subTest(doctype=doctype):
                doc = Record(doctype=doctype, company="JF", items=[
                    Record(item_code="SILVER", jf_flow_order="FLOW-1"),
                ])
                self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SCRAP"
                with self.assertRaisesRegex(ValueError, "不在流程单物料分析"):
                    self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SILVER"
                doc.company = "OTHER"
                with self.assertRaisesRegex(ValueError, "公司不一致"):
                    self.setup.validate_flow_links(doc)

    def test_procurement_source_links_fill_flow_and_reject_conflicts(self):
        cases = (
            ("Purchase Order", {"material_request_item": "MR-ROW", "material_request": "MR-1"}),
            ("Purchase Receipt", {"purchase_order_item": "PO-ROW", "purchase_order": "PO-1"}),
            ("Purchase Invoice", {"pr_detail": "PR-ROW", "purchase_receipt": "PR-1",
                                  "po_detail": "PO-ROW", "purchase_order": "PO-1"}),
            ("Subcontracting Receipt", {"subcontracting_order_item": "SCO-ROW",
                                          "subcontracting_order": "SCO-1"}),
        )
        for doctype, references in cases:
            with self.subTest(doctype=doctype):
                item_code = "RING" if doctype == "Subcontracting Receipt" else "SILVER"
                row = Record(item_code=item_code, jf_flow_order=None, **references)
                doc = Record(doctype=doctype, company="JF", items=[row])
                self.setup.validate_flow_links(doc)
                self.assertEqual(row.jf_flow_order, "FLOW-1")
                row.jf_flow_order = "FLOW-OTHER"
                with self.assertRaisesRegex(ValueError, "与来源单据不一致"):
                    self.setup.validate_flow_links(doc)

    def test_subcontracting_order_and_receipt_match_finished_item(self):
        for doctype in ("Subcontracting Order", "Subcontracting Receipt"):
            with self.subTest(doctype=doctype):
                doc = Record(doctype=doctype, company="JF", items=[
                    Record(item_code="RING", jf_flow_order="FLOW-1"),
                ])
                self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SILVER"
                with self.assertRaisesRegex(ValueError, "商品与流程单商品不一致"):
                    self.setup.validate_flow_links(doc)

    def test_return_inherits_flow_from_original_item_and_checks_source(self):
        cases = (
            ("Purchase Receipt", "purchase_receipt_item", "PR-ROW", "PR-1", "SILVER"),
            ("Purchase Invoice", "purchase_invoice_item", "PI-ROW", "PI-1", "SILVER"),
            ("Subcontracting Receipt", "subcontracting_receipt_item", "SCR-ROW", "SCR-1", "RING"),
        )
        for doctype, source_field, source_row, parent, item_code in cases:
            with self.subTest(doctype=doctype):
                row = Record(item_code=item_code, jf_flow_order=None, **{source_field: source_row})
                doc = Record(doctype=doctype, company="JF", is_return=1,
                             return_against=parent, items=[row])
                self.setup.validate_flow_links(doc)
                self.assertEqual(row.jf_flow_order, "FLOW-1")
                doc.return_against = "OTHER"
                with self.assertRaisesRegex(ValueError, "来源单据与来源明细不一致"):
                    self.setup.validate_flow_links(doc)
                doc.return_against = parent
                row.item_code = "SCRAP"
                with self.assertRaisesRegex(ValueError, "商品与来源明细不一致"):
                    self.setup.validate_flow_links(doc)

    def test_subcontracting_transfer_inherits_source_flow(self):
        row = Record(item_code="SILVER", subcontracted_item="RING",
                     sco_rm_detail="SCO-RM-ROW", jf_flow_order=None)
        entry = Record(doctype="Stock Entry", docstatus=0, company="JF", subcontracting_order="SCO-1", items=[row])
        self.setup.validate_flow_links(entry)
        self.assertEqual(row.jf_flow_order, "FLOW-1")

        row.jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "与来源单据不一致"):
            self.setup.validate_flow_links(entry)

        row.jf_flow_order = None
        entry.subcontracting_order = "SCO-OTHER"
        with self.assertRaisesRegex(ValueError, "来源明细与委外订单不一致"):
            self.setup.validate_flow_links(entry)

    def test_subcontract_return_cannot_copy_original_positive_loss(self):
        row = Record(item_code="RING", jf_flow_order=None, subcontracting_receipt_item="SCR-ROW",
                     process_loss_qty=1)
        doc = Record(doctype="Subcontracting Receipt", company="JF", is_return=1,
                     return_against="SCR-1", items=[row])
        with self.assertRaisesRegex(ValueError, "工艺损耗须为 0"):
            self.setup.validate_flow_links(doc)
        row.process_loss_qty = 0
        self.setup.validate_flow_links(doc)
        self.assertEqual(row.jf_flow_order, "FLOW-1")

    def test_job_card_inherits_and_protects_work_order_flow(self):
        doc = Record(doctype="Job Card", work_order="WO-1", company="JF", production_item="RING", jf_flow_order=None)
        self.setup.validate_flow_links(doc)
        self.assertEqual(doc.jf_flow_order, "FLOW-1")
        doc.jf_flow_order = "OTHER"
        with self.assertRaisesRegex(ValueError, "来源不一致"):
            self.setup.validate_flow_links(doc)
        doc.jf_flow_order = "FLOW-1"
        doc.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "公司、商品不一致"):
            self.setup.validate_flow_links(doc)

    def stage(self):
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "subcontract_stage.py"
        spec = importlib.util.spec_from_file_location("jifeng_erp.subcontract_stage", path)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        import jifeng_erp

        module_patch = patch.dict(sys.modules, {"jifeng_erp.subcontract_stage": stage})
        package_patch = patch.object(jifeng_erp, "subcontract_stage", stage, create=True)
        module_patch.start()
        package_patch.start()
        self.addCleanup(module_patch.stop)
        self.addCleanup(package_patch.stop)
        self.stage_module = stage
        self.hidden = {}
        schemas = dict(stage.TABLES)
        schemas["Subcontracting Order supplied"] = ("supplied_items", "Subcontracting Order Supplied Item",
            stage.CHILD | {"reference_name", "main_item_code", "rm_item_code"})
        def meta(doctype):
            def field(name):
                for parent, (table, child, _fields) in schemas.items():
                    if (parent == doctype or parent == doctype + " supplied") and name == table:
                        return Record(fieldname=name, fieldtype="Table", options=child, permlevel=0)
                return Record(fieldname=name, fieldtype="Data", permlevel=0)
            return types.SimpleNamespace(get_field=field, get_permlevel_access=lambda **kwargs: {0})
        self.frappe.get_meta = meta
        model = types.ModuleType("frappe.model")
        child_fields = {child: fields for _field, child, fields in schemas.values()}
        model.get_permitted_fields = lambda doctype, **kwargs: (
            stage.PARENTS.get(doctype, child_fields.get(doctype, {"name"})) - self.hidden.get(doctype, set()))
        model_patch = patch.dict(sys.modules, {"frappe.model": model})
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.flow.update(doctype="Jifeng Flow Order", modified="v1")
        self.work_order.update(doctype="Work Order", name="WO-1", docstatus=1, track_semi_finished_goods=1,
            operations=[Record(name="OP-ROW", parent="WO-1", parenttype="Work Order", parentfield="operations",
                               operation="PLATING", finished_good="SEMI", is_subcontracted=1)])
        self.card = Record(doctype="Job Card", name="JC", company="JF", docstatus=0, work_order="WO-1",
            production_item="RING", operation_id="OP-ROW", operation="PLATING", finished_good="SEMI",
            semi_fg_bom="STAGE-BOM", track_semi_finished_goods=1, is_subcontracted=1)
        self.bom = Record(doctype="BOM", name="STAGE-BOM", docstatus=1, company="JF", item="SEMI",
            items=[Record(item_code="PREVIOUS-SEMI")])
        self.po_row = Record(name="PO-STAGE", parent="PO-STAGE", parenttype="Purchase Order", parentfield="items",
            item_code="SERVICE", fg_item="SEMI", job_card="JC", bom="STAGE-BOM", jf_flow_order=None, qty=2)
        self.po_stage = Record(doctype="Purchase Order", name="PO-STAGE", company="JF", supplier="SUP", docstatus=1,
            is_subcontracted=1, is_old_subcontracting_flow=0, items=[self.po_row])
        self.sco_row = Record(name="SCO-STAGE", parent="SCO-STAGE", parenttype="Subcontracting Order", parentfield="items",
            item_code="SEMI", job_card="JC", bom="STAGE-BOM", purchase_order_item="PO-STAGE", jf_flow_order=None, qty=2)
        self.sco_stage = Record(doctype="Subcontracting Order", name="SCO-STAGE", company="JF", supplier="SUP", docstatus=1,
            purchase_order="PO-STAGE", items=[self.sco_row], supplied_items=[Record(name="RM-STAGE", parent="SCO-STAGE",
            parenttype="Subcontracting Order", parentfield="supplied_items", reference_name="SCO-STAGE",
            main_item_code="SEMI", rm_item_code="PREVIOUS-SEMI")])
        self.scr_row = Record(name="SCR-STAGE", parent="SCR-STAGE", parenttype="Subcontracting Receipt", parentfield="items",
            item_code="SEMI", job_card="JC", bom="STAGE-BOM", purchase_order="PO-STAGE", purchase_order_item="PO-STAGE",
            subcontracting_order="SCO-STAGE", subcontracting_order_item="SCO-STAGE", jf_flow_order=None, qty=1)
        self.scr_stage = Record(doctype="Subcontracting Receipt", name="SCR-STAGE", company="JF", supplier="SUP", items=[self.scr_row])
        self.documents.update({("Job Card", "JC"): self.card, ("BOM", "STAGE-BOM"): self.bom,
            ("Purchase Order", "PO-STAGE"): self.po_stage, ("Subcontracting Order", "SCO-STAGE"): self.sco_stage})
        for item in ("SEMI", "PREVIOUS-SEMI", "SERVICE"):
            self.documents["Item", item] = Record(name=item)
        self.sources["Subcontracting Order Item", "SCO-STAGE"] = self.sco_row
        self.sources["Subcontracting Order Supplied Item", "RM-STAGE"] = self.sco_stage.supplied_items[0]
        return stage

    def test_native_stage_po_sco_two_partial_receipts_keep_final_flow_and_quantities(self):
        self.stage()
        self.setup.validate_flow_links(self.po_stage)
        self.setup.validate_flow_links(self.sco_stage)
        for quantity in (1, 0.5):
            self.scr_row.qty = quantity
            self.setup.validate_flow_links(self.scr_stage)
            self.assertEqual(self.scr_row.qty, quantity)
            self.assertEqual(self.scr_row.jf_flow_order, "FLOW-1")
        self.assertEqual((self.po_row.jf_flow_order, self.sco_row.jf_flow_order), ("FLOW-1", "FLOW-1"))
        self.assertEqual(self.flow.item_code, "RING")

    def native_clearing_po(self, after_clear=None):
        stage = self.stage()
        calls = []

        class NativePurchaseOrder(Record):
            def validate_for_subcontracting(doc):
                calls.append(True)
                for row in doc.get("items") or []:
                    row.bom = None
                if after_clear:
                    after_clear(doc)
                return "native validation retained"

        class StagePurchaseOrder(stage.PurchaseOrderStageMixin, NativePurchaseOrder):
            pass

        return StagePurchaseOrder(**self.po_stage), calls

    def test_native_new_flow_bom_clearing_preserves_only_exact_job_card_stage(self):
        doc, calls = self.native_clearing_po()
        ordinary = Record(item_code="ORDINARY", bom="DEFAULT-BOM", job_card=None)
        doc["items"].append(ordinary)
        self.assertEqual(doc.validate_for_subcontracting(), "native validation retained")
        self.assertEqual(calls, [True])
        self.assertEqual(self.po_row.bom, "STAGE-BOM")
        self.assertIsNone(ordinary.bom)
        self.setup.validate_flow_links(doc)
        self.assertEqual(self.po_row.jf_flow_order, "FLOW-1")

    def test_native_bom_restore_cannot_repair_wrong_incoming_source(self):
        for field, value in (("bom", None), ("bom", "OTHER"), ("fg_item", "OTHER"),
                             ("job_card", "OTHER"), ("jf_flow_order", "OTHER")):
            doc, calls = self.native_clearing_po()
            self.po_row[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                doc.validate_for_subcontracting()
            self.assertEqual(calls, [], "wrong caller source must be rejected before native BOM clearing")

    def test_native_bom_restore_rejects_replaced_deleted_or_changed_stage_row(self):
        changes = [lambda doc: doc["items"].clear(),
                   lambda doc: doc["items"].__setitem__(0, Record(**doc["items"][0])),
                   lambda doc: doc["items"][0].update(fg_item="OTHER"),
                   lambda doc: doc["items"][0].update(qty=999),
                   lambda doc: doc.update(company="OTHER"),
                   lambda doc: doc["items"][0].update(bom="OTHER")]
        for change in changes:
            doc, calls = self.native_clearing_po(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                doc.validate_for_subcontracting()
            self.assertEqual(calls, [True])

    def test_native_bom_restore_propagates_native_failures_and_rechecks_source_access(self):
        def fail(doc):
            raise ValueError("native supplier validation")
        doc, _ = self.native_clearing_po(fail)
        with self.assertRaisesRegex(ValueError, "native supplier validation"):
            doc.validate_for_subcontracting()

        def deny(doc):
            self.card.denied = {"read"}
        doc, _ = self.native_clearing_po(deny)
        with self.assertRaises(PermissionError):
            doc.validate_for_subcontracting()

    def test_native_bom_restore_retains_existing_cancelled_stage_history_rule(self):
        doc, calls = self.native_clearing_po()
        doc.docstatus = 2
        self.card.docstatus = self.work_order.docstatus = 2
        doc.validate_for_subcontracting()
        self.assertEqual((self.po_row.bom, calls), ("STAGE-BOM", [True]))
        doc.docstatus = 0
        with self.assertRaises(ValueError):
            doc.validate_for_subcontracting()

    def test_native_bom_restore_leaves_non_job_card_and_old_flow_methods_unchanged(self):
        doc, calls = self.native_clearing_po()
        self.po_row.job_card = None
        doc.validate_for_subcontracting()
        self.assertEqual(calls, [True])
        self.assertIsNone(self.po_row.bom)

        doc, calls = self.native_clearing_po()
        doc.is_old_subcontracting_flow = 1
        doc.validate_for_subcontracting()
        self.assertEqual(calls, [True])
        self.assertIsNone(self.po_row.bom, "old-flow behavior is delegated to native code without repair")

    def test_stage_must_match_actual_operation_bom_company_and_job_card(self):
        self.stage()
        for record, field, value in ((self.card, "operation_id", "OTHER"), (self.card, "finished_good", "SILVER"),
                (self.work_order, "track_semi_finished_goods", 0), (self.card, "track_semi_finished_goods", 0),
                (self.bom, "item", "SILVER"), (self.bom, "company", "OTHER"), (self.sco_row, "bom", "OTHER"),
                (self.work_order.operations[0], "is_subcontracted", 0), (self.card, "docstatus", 2)):
            old = record[field]
            record[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                self.setup.validate_flow_links(self.sco_stage)
            record[field] = old

    def test_stage_cannot_hide_fields_or_sources_or_use_mapper_wrong_po_row(self):
        self.stage()
        for record in (self.card, self.work_order, self.bom, self.po_stage, self.documents["Item", "SEMI"]):
            record.denied = {"read"}
            with self.assertRaises(PermissionError):
                self.setup.validate_flow_links(self.sco_stage)
            record.denied = set()
        self.hidden["Work Order Operation"] = {"finished_good"}
        with self.assertRaises(PermissionError):
            self.setup.validate_flow_links(self.sco_stage)
        self.hidden.clear()
        other = Record(**self.po_row)
        other.name, other.job_card = "OTHER-ROW", "OTHER-JC"
        other_card = Record(**self.card)
        other_card.name = "OTHER-JC"
        self.documents["Job Card", "OTHER-JC"] = other_card
        self.po_stage["items"].append(other)
        self.sco_row.purchase_order_item = "OTHER-ROW"
        with self.assertRaises(ValueError):
            self.setup.validate_flow_links(self.sco_stage)

    def test_stage_stock_flow_comes_from_exact_native_bom_and_rejects_outsourced_duplicate_manufacture(self):
        self.stage()
        self.card.is_subcontracted = self.work_order.operations[0].is_subcontracted = 0
        stock = Record(doctype="Stock Entry", company="JF", work_order="WO-1", job_card="JC", bom_no="STAGE-BOM",
            purpose="Manufacture", items=[Record(item_code="PREVIOUS-SEMI", jf_flow_order=None),
                                           Record(item_code="SEMI", jf_flow_order=None)])
        self.setup.validate_flow_links(stock)
        self.assertEqual([row.jf_flow_order for row in stock["items"]], ["FLOW-1", "FLOW-1"])
        stock["items"][1].item_code = "RING"
        with self.assertRaisesRegex(ValueError, "不属于当前报工阶段"):
            self.setup.validate_flow_links(stock)
        stock["items"][1].item_code = "SEMI"
        stock["items"][0].item_code = "ARBITRARY"
        with self.assertRaises(ValueError):
            self.setup.validate_flow_links(stock)
        stock["items"][0].item_code = "PREVIOUS-SEMI"
        self.card.is_subcontracted = self.work_order.operations[0].is_subcontracted = 1
        with self.assertRaisesRegex(ValueError, "重复产出"):
            self.setup.validate_flow_links(stock)

    def test_subcontract_stage_transfers_only_exact_supplied_bom_item(self):
        self.stage()
        self.setup.validate_flow_links(self.po_stage)
        self.setup.validate_flow_links(self.sco_stage)
        row = Record(item_code="PREVIOUS-SEMI", sco_rm_detail="RM-STAGE", subcontracted_item="SEMI", jf_flow_order=None)
        stock = Record(doctype="Stock Entry", company="JF", subcontracting_order="SCO-STAGE", items=[row])
        self.setup.validate_flow_links(stock)
        self.assertEqual(row.jf_flow_order, "FLOW-1")
        self.bom["items"][0].item_code = "OTHER"
        with self.assertRaises(ValueError):
            self.setup.validate_flow_links(stock)

    def test_stage_service_invoice_keeps_po_source_without_relabelling_service(self):
        self.stage()
        self.setup.validate_flow_links(self.po_stage)
        self.sources["Purchase Order Item", "PO-STAGE"] = self.po_row
        row = Record(item_code="SERVICE", purchase_order="PO-STAGE", po_detail="PO-STAGE", jf_flow_order=None)
        invoice = Record(doctype="Purchase Invoice", company="JF", supplier="SUP", items=[row])
        self.setup.validate_flow_links(invoice)
        self.assertEqual((row.item_code, row.jf_flow_order), ("SERVICE", "FLOW-1"))

    def test_semi_finished_work_order_cannot_manufacture_without_exact_stage_job_card(self):
        self.stage()
        self.entry.purpose = "Manufacture"
        with self.assertRaisesRegex(ValueError, "重复产出"):
            self.setup.validate_flow_links(self.entry)

    def test_stage_return_keeps_original_job_card_and_native_negative_quantity(self):
        self.stage()
        self.scr_stage.docstatus = 1
        self.documents["Subcontracting Receipt", "SCR-STAGE"] = self.scr_stage
        self.setup.validate_flow_links(self.scr_stage)
        original = self.scr_row
        self.sources["Subcontracting Receipt Item", "SCR-STAGE"] = original
        returned = Record(**original)
        returned.name, returned.parent, returned.qty = "RET-ROW", "RETURN", -0.5
        returned.subcontracting_receipt_item = "SCR-STAGE"
        document = Record(doctype="Subcontracting Receipt", name="RETURN", company="JF", supplier="SUP",
            is_return=1, return_against="SCR-STAGE", items=[returned])
        self.setup.validate_flow_links(document)
        self.assertEqual((returned.qty, returned.jf_flow_order), (-0.5, "FLOW-1"))
        original.job_card = "DIFFERENT"
        with self.assertRaises(ValueError):
            self.setup.validate_flow_links(document)


if __name__ == "__main__":
    unittest.main()
