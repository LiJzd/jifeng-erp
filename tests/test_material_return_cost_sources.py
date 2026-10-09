"""Selected physical return values through the actual read-only flow cost API."""
from decimal import Decimal
import importlib.util
import sys
import unittest
from unittest.mock import patch

import test_flow_cost_sources as base


class TestTrackedMaterialReturnCostSources(unittest.TestCase):
    def setUp(self):
        self.env = env = base.TestFlowCostSources()
        env.setUp()
        self.addCleanup(env.doCleanups)
        env.frappe.ValidationError = ValueError
        spec = importlib.util.spec_from_file_location("jifeng_erp.material_returns", base.ROOT / "jifeng_erp/material_returns.py")
        self.returns = module = importlib.util.module_from_spec(spec)
        module_patch = patch.dict(sys.modules, {spec.name: module})
        module_patch.start(); self.addCleanup(module_patch.stop)
        spec.loader.exec_module(module)
        import jifeng_erp
        package_patch = patch.object(jifeng_erp, "material_returns", module, create=True)
        package_patch.start(); self.addCleanup(package_patch.stop)
        for doctype, fields in module.MATERIAL_RETURN_READ_PARENTS.items():
            if doctype not in env.permitted:
                env.schema(doctype, fields | env.module.IDENTITY, set())
            else:
                env.permitted[doctype].update(fields)
                env.metas[doctype].update({field: base.Record(fieldname=field, fieldtype="Data", permlevel=0)
                    for field in fields if field not in env.metas[doctype]})
        for doctype, tables in module.MATERIAL_RETURN_READ_TABLES.items():
            for field, childtype, fields in tables:
                env.permitted[childtype].update(fields)
                env.metas[childtype].update({name: base.Record(fieldname=name, fieldtype="Data", permlevel=0)
                    for name in fields if name not in env.metas[childtype]})
        env.documents["Item", "RM"].has_batch_no = 1
        self.source = env.issue(jf_material_issue_flow="FLOW")
        self.original_row = env.documents["Stock Entry Detail", "ISSUE-RM"]
        self.original_row.serial_and_batch_bundle = "ISSUED-BUNDLE"
        env.documents[env.module.SLE, "ISSUE-SLE"].stock_value_difference = -120
        env.documents[env.module.SLE, "ISSUE-SLE"].serial_and_batch_bundle = "ISSUED-BUNDLE"
        self.bundle("ISSUED-BUNDLE", "ISSUE", self.original_row.name, (("CHEAP", 10, 4), ("COSTLY", 10, 8)), outward=True)

    def bundle(self, name, voucher, detail, lots, *, outward=False):
        env = self.env
        sign = -1 if outward else 1
        env.add(env.module.material_returns.BUNDLE, name, company="JF", item_code="RM", warehouse="RM-WH",
            voucher_type=env.module.SE, voucher_no=voucher, voucher_detail_no=detail,
            type_of_transaction="Outward" if outward else "Inward", is_cancelled=0,
            has_batch_no=1, has_serial_no=0, total_qty=sign*sum(qty for _,qty,_ in lots),
            total_amount=sign*sum(qty*rate for _,qty,rate in lots))
        for idx, (batch, qty, rate) in enumerate(lots, 1):
            env.add("Batch", batch, item="RM")
            env.child(env.module.material_returns.BUNDLE, name, "entries", name+"-"+str(idx),
                item_code="RM", warehouse="RM-WH", batch_no=batch, serial_no=None,
                qty=sign*qty, incoming_rate=rate, stock_value_difference=sign*qty*rate, is_cancelled=0)

    def returned(self, name="SELECTED", qty=5, batch="COSTLY", rate=8):
        env = self.env
        doc, row = env.issue_return(name=name, qty=qty, value=qty*rate)
        row.serial_and_batch_bundle = name+"-BUNDLE"
        row.set_basic_rate_manually, row.basic_rate = 1, rate
        env.documents[env.module.SLE, name+"-SLE"].serial_and_batch_bundle = row.serial_and_batch_bundle
        self.bundle(row.serial_and_batch_bundle, name, row.name, ((batch, qty, rate),))
        return doc, row

    def test_selected_expensive_lot_reduces_exact_actual_value_and_cancellation_restores_it(self):
        doc, _ = self.returned()
        result = self.env.module.material_issue_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 80)
        selected = next(row for row in result["rows"] if row["issue"] == doc.name)
        self.assertEqual((selected["qty"], selected["material"]), (-5, -40))
        doc.docstatus = 2
        self.env.documents[self.env.module.SLE, doc.name+"-SLE"].is_cancelled = 1
        self.assertEqual(self.env.module.material_issue_costs("FLOW")["totals"]["material"], 120)

    def test_same_batch_multiple_issued_rates_uses_exact_original_batch_pool(self):
        env = self.env
        bundle = env.documents[self.returns.BUNDLE, "ISSUED-BUNDLE"]
        bundle.total_amount = -240
        cheap = env.documents[self.returns.BUNDLE_ENTRY, "ISSUED-BUNDLE-1"]
        costly = env.documents[self.returns.BUNDLE_ENTRY, "ISSUED-BUNDLE-2"]
        costly.batch_no, costly.incoming_rate, costly.stock_value_difference = "CHEAP", 20, -200
        env.documents[env.module.SLE, "ISSUE-SLE"].stock_value_difference = -240
        self.returned(qty=5, batch="CHEAP", rate=12)
        self.assertEqual(env.module.material_issue_costs("FLOW")["totals"]["material"], 180)
        self.returned(name="REMAINDER", qty=15, batch="CHEAP", rate=12)
        self.assertEqual(env.module.material_issue_costs("FLOW")["totals"]["material"], 0)

    def test_batch_quantity_cap_does_not_use_another_batch_allowance(self):
        self.returned(qty=6)
        self.returned(name="OVER", qty=5)
        with self.assertRaises(ValueError):self.env.module.material_issue_costs("FLOW")

    def test_wrong_actual_value_and_bundle_value_do_not_hide_behind_whole_row_average(self):
        self.returned(rate=6)
        with self.assertRaises(ValueError):self.env.module.material_issue_costs("FLOW")

    def test_hidden_selected_bundle_or_batch_fields_refuses_partial_cost(self):
        self.returned()
        env = self.env
        env.hidden[self.returns.BUNDLE] = {"total_amount"}
        with self.assertRaises(PermissionError):env.module.material_issue_costs("FLOW")
        env.hidden.clear();env.list_denied.add((self.returns.BUNDLE_ENTRY,"SELECTED-BUNDLE-1"))
        with self.assertRaises((PermissionError, ValueError)):env.module.material_issue_costs("FLOW")

    def test_actual_batch_source_change_without_modified_invalidates_result(self):
        self.returned()
        env = self.env
        count = [0]
        def change(filters):
            if filters.get("name") == "COSTLY":
                count[0] += 1
                if count[0] > 1:env.documents["Batch", "COSTLY"].item = "FG"
        env.list_hooks["Batch"] = change
        with self.assertRaises((PermissionError, ValueError)):env.module.material_issue_costs("FLOW")


if __name__ == "__main__":unittest.main()
