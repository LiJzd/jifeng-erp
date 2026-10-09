import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)

    def check_permission(self, action):
        if action in self.get("denied", ()):
            raise PermissionError("denied")

    def get_doc_before_save(self):
        return self.get("previous")

    def is_new(self):
        return bool(self.get("new"))

    def get_items_and_operations_from_bom(self):
        self["bom_loaded"] = True

    def insert(self):
        self["name"] = "REPLACEMENT"
        self["docstatus"] = 0
        self["modified"] = "new-version"
        self["produced_qty"] = self["process_loss_qty"] = 0
        self["status"] = "Draft"
        self.store[self.name] = self


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.flow = Row(name="FLOW", company="JF", item_code="FG", bom="BOM", stock_uom="Nos",
                        sales_order="SO", sales_order_item="SO-ROW", modified="v1", planned_qty=5)
        self.work = Row(name="WO", company="JF", production_item="FG", bom_no="BOM", qty=5,
                        produced_qty=0, process_loss_qty=5, docstatus=1, jf_flow_order="FLOW",
                        sales_order="SO", sales_order_item="SO-ROW", modified="wo-v1", status="Completed")
        self.works = {self.work.name: self.work}
        self.sources = [{"source_kind": "Production Loss", "source_name": "LOSS", "work_order": "WO",
                         "company": "JF", "item_code": "FG", "stock_uom": "Nos", "qty": 5,
                         "inventory_qty": 0, "lost_qty": 5}]
        self.bom = Row(docstatus=1, is_active=1, company="JF", item="FG")
        self.locks = []
        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda **kw: lambda fn: fn
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.TimestampMismatchError = RuntimeError
        frappe.QueryTimeoutError = TimeoutError
        frappe.QueryDeadlockError = TimeoutError
        frappe.session = Row(user="ordinary@test")

        def sql(query, params, **kwargs):
            self.locks.append(query)
            if "jf_replacement_of=%s" in query:
                return [Row(qty=work.qty) for work in self.works.values()
                        if work.get("jf_replacement_of") == params[0] and work.docstatus < 2 and work.name != params[1]]
            return [Row(name=work.name) for work in self.works.values()
                    if work.jf_flow_order == params[0] and work.docstatus < 2]

        frappe.db = Row(sql=sql)

        def get_doc(doctype, name=None, **kwargs):
            if isinstance(doctype, dict):
                doc = Row(doctype)
                doc["store"] = self.works
                return doc
            return self.flow if doctype == "Jifeng Flow Order" else self.bom if doctype == "BOM" else self.works[name]

        frappe.get_doc = get_doc
        quantity = types.ModuleType("jifeng_erp.flow_quantity")
        quantity.check_quantity_fields = lambda *args, **kwargs: None
        instructions = types.ModuleType("jifeng_erp.production_instruction")

        def match(flow, item, bom, sales_order, row):
            if (flow.item_code, flow.bom, flow.sales_order, flow.sales_order_item) != (item, bom, sales_order, row):
                raise ValueError("source mismatch")

        instructions._match = match
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.stock_plan_quantity = lambda flow: flow.planned_qty
        workflow.validate_flow_source = lambda flow: None
        sources = types.ModuleType("jifeng_erp.flow_cost_sources")
        sources.production_batch_candidates = lambda name: list(self.sources)
        issues = types.ModuleType("jifeng_erp.material_issue")
        issues.assert_no_net_issue_for_manufacturing = lambda flow: None
        self.patch = patch.dict(sys.modules, {"frappe": frappe, "jifeng_erp.flow_quantity": quantity,
                  "jifeng_erp.production_instruction": instructions, "jifeng_erp.workflow": workflow,
                  "jifeng_erp.flow_cost_sources": sources, "jifeng_erp.material_issue": issues})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("replacement_under_test",
                     Path(__file__).parents[1] / "jifeng_erp/production_replacement.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def context(self):
        return self.module.inspect_replacement("FLOW", "v1")

    def make(self, context=None):
        context = context or self.context()
        return self.module.make_replacement("FLOW", "v1", "WO", context["snapshot"])

    def test_full_loss_creates_five_and_reuses_exact_draft(self):
        self.assertEqual(self.context()["available_qty"], 5)
        first = self.make()
        self.assertEqual(first["qty"], 5)
        self.assertEqual(self.works[first["work_order"]].jf_replacement_of, "WO")
        second = self.make()
        self.assertEqual(second["work_order"], first["work_order"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.works), 2)
        self.assertTrue(all("FOR UPDATE NOWAIT" in query for query in self.locks))

    def test_partial_loss_produces_exact_missing_one(self):
        self.work["produced_qty"], self.work["process_loss_qty"] = 4, 1
        self.sources[0].update(qty=5, inventory_qty=4, lost_qty=1)
        self.assertEqual(self.make()["qty"], 1)

    def test_changed_reservation_rejects_old_confirmation(self):
        context = self.context()
        self.make()
        with self.assertRaisesRegex(ValueError, "变化"):
            self.make(context)
        self.assertEqual(len(self.works), 2)

    def test_unposted_or_changed_loss_rejected(self):
        self.sources[0]["lost_qty"] = 4
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.context()

    def test_cancelled_replacement_releases_source_quantity(self):
        result = self.make()
        self.works[result["work_order"]]["docstatus"] = 2
        self.assertEqual(self.context()["sources"][0]["available_qty"], 5)

    def test_no_manufactured_loss_does_not_create(self):
        self.work.update(process_loss_qty=0, produced_qty=0)
        self.sources.clear()
        with self.assertRaisesRegex(ValueError, "损耗工单"):
            self.make()

    def test_hidden_other_order_blocks_and_does_not_leak(self):
        self.work["denied"] = ("read",)
        with self.assertRaises(PermissionError):
            self.context()

    def test_stale_flow_version(self):
        self.flow["modified"] = "v2"
        with self.assertRaises(RuntimeError):
            self.context()

    def test_exact_sales_row_and_inventory_unit(self):
        self.work["sales_order_item"] = "OTHER"
        with self.assertRaises(ValueError):
            self.context()
        self.work["sales_order_item"] = "SO-ROW"
        self.sources[0]["stock_uom"] = "Box"
        with self.assertRaisesRegex(ValueError, "库存单位"):
            self.context()

    def test_same_source_cannot_borrow_another_orders_loss(self):
        self.work.update(qty=3, process_loss_qty=1, produced_qty=2)
        other = Row(self.work)
        other.update(name="WO-2", qty=2, process_loss_qty=2, produced_qty=0)
        self.works[other.name] = other
        self.sources[0].update(qty=3, lost_qty=1, inventory_qty=2)
        self.sources.append(dict(self.sources[0], source_name="LOSS2", work_order="WO-2", qty=2, lost_qty=2, inventory_qty=0))
        self.assertEqual(self.context()["available_qty"], 3)
        self.assertEqual(self.make()["qty"], 1)

    def test_native_edit_cannot_overbook_original_loss(self):
        doc = Row(self.work)
        doc.update(name="NEW", docstatus=0, new=True, jf_replacement_of="WO", qty=6)
        with self.assertRaisesRegex(ValueError, "超过"):
            self.module.validate_replacement(doc)

    def test_source_cannot_be_removed_after_saving(self):
        doc = Row(self.work)
        doc.update(name="NEW", docstatus=0, jf_replacement_of=None, previous=Row(jf_replacement_of="WO"))
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.validate_replacement(doc)

    def test_production_plan_source_keeps_native_exact_plan_row_factory(self):
        self.work["production_plan"] = "PLAN"
        self.work["production_plan_item"] = "PLAN-ROW"
        state = self.context()
        self.assertTrue(state["plan_source"])
        self.assertEqual(state["sources"], [])
        with self.assertRaisesRegex(ValueError, "生产计划"):
            self.make(state)
        doc = Row(self.work)
        doc.update(name="NEW", docstatus=0, new=True, jf_replacement_of="WO", qty=5)
        with self.assertRaisesRegex(ValueError, "生产计划"):
            self.module.validate_replacement(doc)


if __name__ == "__main__":
    unittest.main()
