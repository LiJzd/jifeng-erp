"""Loss-source classification and replacement costing; no business writes."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_receipt_batches as preview_fixture
import test_flow_cost_sources as source_fixture


class TestProductionLossSources(unittest.TestCase):
    """Actual loss helper and cost collector over native-shaped documents/SLE."""

    def setUp(self):
        self.base = source_fixture.TestFlowCostSources(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.install_loss_contract(self.base)

    def install_loss_contract(self, native):
        self.base = native
        self.module, self.frappe = self.base.module, self.base.frappe
        self.document_type = type(self.base.get_doc(self.module.FLOW, "FLOW"))
        spec = importlib.util.spec_from_file_location("production_loss_cost_source_under_test",
            Path(__file__).parents[1] / "jifeng_erp/production_loss.py")
        self.loss = importlib.util.module_from_spec(spec)
        with patch.object(self.frappe, "whitelist", lambda **kwargs: lambda function: function), \
                patch.object(sys.modules["frappe.utils"], "now_datetime", lambda: datetime(2026, 10, 3, 11), create=True):
            spec.loader.exec_module(self.loss)
        import jifeng_erp
        patches = patch.object(jifeng_erp, "production_loss", self.loss, create=True)
        patches.start(); self.addCleanup(patches.stop)
        self.tables = {}
        for kind, fields in self.loss.PARENTS.items():
            self.extend_schema(kind, fields)
        for kind, tables in self.loss.TABLES.items():
            self.tables[kind] = tables
            for field, childtype, fields in tables:
                self.extend_schema(childtype, fields)
                self.base.metas[kind][field] = source_fixture.Record(
                    fieldname=field, fieldtype="Table", permlevel=0, options=childtype)
        self.frappe.get_doc.side_effect = self.get_doc

    def extend_schema(self, kind, fields):
        if kind not in self.base.metas:
            self.base.schema(kind, fields, set())
        else:
            self.base.permitted[kind].update(fields)
            for field in fields:
                if field not in self.base.metas[kind]:
                    self.base.metas[kind][field] = source_fixture.Record(
                        fieldname=field, fieldtype="Float", permlevel=0)

    def get_doc(self, kind, name=None, **kwargs):
        name = kind if name is None else name
        doc = self.base.get_doc(kind, name, **kwargs)
        for field, childtype, _fields in self.tables.get(kind, ()):
            doc[field] = [self.document_type(deepcopy(row))
                          for (row_kind, _), row in self.base.documents.items()
                          if row_kind == childtype and row.parent == name
                          and row.parenttype == kind and row.parentfield == field]
        return doc

    def child(self, kind, name, field, childname, **values):
        _field, childtype, fields = next(table for table in self.tables[kind] if table[0] == field)
        return self.base.add(childtype, childname, **({column: None for column in fields - {"name"}} | {
            "parent": name, "parenttype": kind, "parentfield": field, "idx": 1} | values))

    def seed_loss(self, name="LOSS", card="CARD", work="WO", qty=5):
        self.base.documents[self.module.FLOW, "FLOW"].update(stock_uom="Nos", bom="FG-BOM")
        if (self.module.WO, work) not in self.base.documents:
            self.base.add(self.module.WO, work, company="JF", production_item="FG", jf_flow_order="FLOW",
                stock_uom="Nos", bom_no="FG-BOM", qty=qty, wip_warehouse="RM-WH", track_semi_finished_goods=0)
            self.child(self.module.WO, work, "operations", work + "-OP", operation="CAST")
            self.child(self.module.WO, work, "required_items", work + "-RAW", item_code="RM",
                stock_uom="Kg", required_qty=10)
        if ("Job Card", card) not in self.base.documents:
            self.base.add("Job Card", card, company="JF", work_order=work, production_item="FG", stock_uom="Nos",
                operation="CAST", operation_id=work + "-OP", for_quantity=qty, process_loss_qty=qty,
                total_completed_qty=0, pending_qty=0, is_subcontracted=0, track_semi_finished_goods=0,
                is_corrective_job_card=0, finished_good=None, wip_warehouse="RM-WH")
            self.child("Job Card", card, "time_logs", card + "-TIME", employee="EMPLOYEE",
                from_time="2026-10-03 08:00:00", to_time="2026-10-03 09:00:00", completed_qty=0)
        doc, row = self.base.issue(name, qty=10, factor=1, stock_entry_type=self.loss.LOSS_TYPE,
            from_warehouse="RM-WH", work_order=None, job_card=None,
            jf_production_loss_work_order=work, jf_production_loss_job_card=card,
            jf_production_loss_qty=qty, jf_production_loss_no_residual=1, jf_production_loss_reason="全部破损，无残值",
            jf_production_loss_request="738da24c-b07a-44bc-b008-6b4e811e3531",
            jf_production_loss_source_version="2026-10-03 10:00:00")
        doc.jf_production_loss_source_signature = self.loss.source_signature(
            self.get_doc("Job Card", card), self.get_doc(self.module.WO, work))
        return doc, row

    def assert_no_writes(self):
        for method in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql,
                       self.frappe.new_doc, self.frappe.delete_doc):
            method.assert_not_called()

    def seed_plan_source(self):
        loss, _row = self.seed_loss()
        flow = self.base.documents[self.module.FLOW, "FLOW"]
        work = self.base.documents[self.module.WO, "WO"]
        flow.update(sales_order="SO", sales_order_item="SO-ROW")
        work.update(production_plan="PLAN", production_plan_item="PLAN-ROW",
                    production_plan_sub_assembly_item=None, sales_order="SO", sales_order_item="SO-ROW")
        self.base.add("BOM", "FG-BOM", company="JF", item="FG", uom="Nos", is_active=1)
        plan = self.base.add("Production Plan", "PLAN", company="JF", status="In Process")
        row = self.child("Production Plan", "PLAN", "po_items", "PLAN-ROW", item_code="FG",
            jf_flow_order="FLOW", bom_no="FG-BOM", planned_qty=5, stock_uom="Nos",
            sales_order="SO", sales_order_item="SO-ROW", item_reference=None)
        self.base.add("Sales Order", "SO", company="JF")
        self.child("Sales Order", "SO", "items", "SO-ROW", item_code="FG", qty=5,
                   uom="Nos", stock_uom="Nos", conversion_factor=1, stock_qty=5)
        loss.jf_production_loss_source_signature = self.loss.source_signature(
            self.get_doc("Job Card", "CARD"), self.get_doc(self.module.WO, "WO"))
        return loss, work, plan, row

    def test_plan_loss_then_native_plan_replacement_uses_actual_cost_once_and_keeps_source(self):
        loss, _work, plan, _row = self.seed_plan_source()
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 40)
        # Closing planning does not invalidate already booked loss history.
        plan.status = "Closed"
        self.assertEqual(self.module.production_batch_candidates("FLOW")[0]["lost_qty"], 5)
        plan.status = "In Process"
        replacement = self.base.manufacture("PLAN-REPLACE", work="PLAN-REPLACE-WO", material=40, qty=5)
        self.base.documents[self.module.WO, "PLAN-REPLACE-WO"].update(
            production_plan="PLAN", production_plan_item="PLAN-ROW", sales_order="SO", sales_order_item="SO-ROW")
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 80)
        replacement.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 40)
        loss.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.assert_no_writes()

    def test_plan_loss_exact_row_and_sales_reference_are_part_of_cost_proof(self):
        _loss, work, plan, row = self.seed_plan_source()
        cases = [(plan, "company", "OTHER"), (plan, "docstatus", 0),
            (work, "production_plan_item", "MISSING"), (row, "jf_flow_order", "OTHER"),
            (row, "bom_no", "OTHER"), (row, "sales_order_item", "OTHER"),
            (row, "stock_uom", "Box"), (row, "parentfield", "sub_assembly_items")]
        for target, field, value in cases:
            old = target.get(field); target[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, PermissionError)):
                self.module.posted_flow_costs("FLOW")
            target[field] = old
        self.assert_no_writes()

    def test_plan_loss_permissions_and_late_plan_row_or_role_edits_refuse_partial_cost(self):
        self.seed_plan_source()
        self.base.metas["Production Plan Item"]["planned_qty"].mask = 1
        with self.assertRaises(PermissionError): self.module.posted_flow_costs("FLOW")
        self.base.metas["Production Plan Item"]["planned_qty"].mask = 0
        for kind in ("Production Plan", "Sales Order"):
            self.base.denied.add((kind, "PLAN" if kind == "Production Plan" else "SO", "read"))
            with self.subTest(kind=kind), self.assertRaises(PermissionError):
                self.module.posted_flow_costs("FLOW")
            self.base.denied.clear()
        session = self.module._Session("FLOW")
        session.load_issues()
        self.base.documents["Production Plan Item", "PLAN-ROW"].planned_qty = 4
        with self.assertRaises(PermissionError): session.finish()
        self.base.documents["Production Plan Item", "PLAN-ROW"].planned_qty = 5
        # Native permission caches may still allow this request; uncached
        # metadata closure must detect a change in newly read plan scopes.
        permission = self.base.add("Custom DocPerm", "PLAN-PERM", parent="Production Plan", role="QA", read=1)
        session = self.module._Session("FLOW")
        session.load_issues()
        permission.read = 0
        with self.assertRaises(PermissionError): session.finish()
        self.assert_no_writes()

    def test_loss_stock_cost_is_counted_once_then_replacement_cancel_changes_exact_source_set(self):
        loss, _row = self.seed_loss()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["issue_material"],
                          result["totals"]["manufacture_material"]), (40, 40, 0))
        self.assertEqual(result["loss_batches"][0]["qty"], 5)
        candidates = self.module.production_batch_candidates("FLOW")
        self.assertEqual([(row["source_kind"], row["source_name"], row["qty"],
                           row["inventory_qty"], row["lost_qty"]) for row in candidates],
                         [("Production Loss", "LOSS", 5, 0, 5)])
        replacement = self.base.manufacture("REPLACE", work="REPLACE-WO", material=40, qty=5)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 80)
        self.assertEqual({row["source_name"] for row in self.module.production_batch_candidates("FLOW")}, {"LOSS", "REPLACE"})
        replacement.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 40)
        loss.docstatus = 2
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["loss_batches"]), (0, []))
        self.assert_no_writes()

    def test_batch_list_scope_rechecks_late_permissions_even_for_empty_pool(self):
        self.extend_schema("Batch", {"name", "item"})
        permission = self.base.add("Custom DocPerm", "BATCH-PERM", parent="Batch", role="QA", read=1)
        for rows in (False, True):
            if rows:
                self.base.add("Batch", "BATCH-1", item="RM")
            permission.read = 1
            session = self.module._Session("FLOW")
            result = session._loss_records("Batch", {"item": "RM"}, {"name", "item"})
            self.assertEqual(len(result), int(rows))
            permission.read = 0
            with self.subTest(nonempty=rows), self.assertRaises(PermissionError):
                session.finish()
        self.assert_no_writes()

    def seed_batch_loss_cost_source(self):
        loss, row = self.seed_loss()
        self.base.documents["Item", "RM"].has_batch_no = 1
        self.base.add("Batch", "BATCH-1", item="RM", disabled=0, use_batchwise_valuation=0)
        self.base.add(self.module.SE, "TRANSFER", company="JF", purpose="Material Transfer for Manufacture",
            work_order="WO", posting_date="2026-10-03", posting_time="09:00:00")
        self.child(self.module.SE, "TRANSFER", "items", "TRANSFER-ROW", item_code="RM", jf_flow_order="FLOW",
            docstatus=1, qty=10, transfer_qty=10, uom="Kg", stock_uom="Kg", conversion_factor=1,
            s_warehouse="RAW-WH", t_warehouse="RM-WH", batch_no="BATCH-1")
        for name, voucher, detail, quantity, clock in (
                ("INWARD", "TRANSFER", "TRANSFER-ROW", 10, "09:00:00"),
                ("OUTWARD", "LOSS", row.name, -10, "10:00:00")):
            self.base.add("Serial and Batch Bundle", name, company="JF", item_code="RM", warehouse="RM-WH",
                voucher_type=self.module.SE, voucher_no=voucher, voucher_detail_no=detail,
                type_of_transaction="Inward" if quantity > 0 else "Outward", is_cancelled=0,
                has_batch_no=1, has_serial_no=0, total_qty=quantity, total_amount=quantity * 4)
            self.child("Serial and Batch Bundle", name, "entries", name + "-ROW", item_code="RM",
                docstatus=1, warehouse="RM-WH", batch_no="BATCH-1", qty=quantity, incoming_rate=4,
                stock_value_difference=quantity * 4, is_cancelled=0,
                voucher_type=self.module.SE, voucher_no=voucher, voucher_detail_no=detail,
                type_of_transaction="Inward" if quantity > 0 else "Outward",
                posting_datetime="2026-10-03 " + clock, creation="2026-10-03 " + clock)
            if quantity > 0:
                self.base.add(self.module.SLE, "TRANSFER-IN", company="JF", item_code="RM", warehouse="RM-WH",
                    stock_uom="Kg", voucher_type=self.module.SE, voucher_no=voucher, voucher_detail_no=detail,
                    posting_date="2026-10-03", posting_time=clock, creation="2026-10-03 " + clock,
                    actual_qty=quantity, stock_value_difference=quantity * 4,
                    serial_and_batch_bundle=name, is_cancelled=0, is_adjustment_entry=0)
        row.update(batch_no="BATCH-1", serial_and_batch_bundle="OUTWARD", use_serial_batch_fields=1)
        self.base.documents[self.module.SLE, "LOSS-SLE"].update(serial_and_batch_bundle="OUTWARD",
            creation="2026-10-03 10:00:00")
        native_match = self.base.match_value
        def native_order(actual, op, value):
            if op in {"<", "<="}:
                if actual is None or value is None:
                    return False
                if isinstance(value, str) and hasattr(actual, "isoformat"):
                    actual = actual.isoformat()
                return actual < value if op == "<" else actual <= value
            return native_match(actual, op, value)
        self.base.match_value = native_order
        return loss

    def test_batch_loss_value_is_counted_once_and_cancel_excludes_source(self):
        loss = self.seed_batch_loss_cost_source()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["issue_material"]), (40, 40))
        self.assertEqual([source["loss_issue"] for source in result["loss_batches"]], ["LOSS"])
        loss.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.assert_no_writes()

    def seed_split_transfer_loss_source(self):
        loss = self.seed_batch_loss_cost_source()
        transfer = self.base.documents[self.module.SE, "TRANSFER"]
        row = self.base.documents["Stock Entry Detail", "TRANSFER-ROW"]
        row.update(qty=5, transfer_qty=5)
        self.base.documents[self.module.SLE, "TRANSFER-IN"].update(actual_qty=5, stock_value_difference=20)
        self.base.documents["Serial and Batch Bundle", "INWARD"].update(total_qty=5, total_amount=20)
        self.base.documents["Serial and Batch Entry", "INWARD-ROW"].update(qty=5, stock_value_difference=20)
        self.base.add(self.module.SE, "TRANSFER-SECOND", company="JF", purpose=transfer.purpose,
            work_order="WO", posting_date="2026-10-03", posting_time="09:30:00")
        self.child(self.module.SE, "TRANSFER-SECOND", "items", "TRANSFER-SECOND-ROW", item_code="RM",
            jf_flow_order="FLOW", docstatus=1, qty=5, transfer_qty=5, uom="Kg", stock_uom="Kg",
            conversion_factor=1, s_warehouse="RAW-WH", t_warehouse="RM-WH", batch_no="BATCH-1")
        self.base.add("Serial and Batch Bundle", "INWARD-SECOND", company="JF", item_code="RM",
            warehouse="RM-WH", voucher_type=self.module.SE, voucher_no="TRANSFER-SECOND",
            voucher_detail_no="TRANSFER-SECOND-ROW", type_of_transaction="Inward", is_cancelled=0,
            has_batch_no=1, has_serial_no=0, total_qty=5, total_amount=20)
        self.child("Serial and Batch Bundle", "INWARD-SECOND", "entries", "INWARD-SECOND-ROW",
            item_code="RM", docstatus=1, warehouse="RM-WH", batch_no="BATCH-1", qty=5,
            incoming_rate=4, stock_value_difference=20, is_cancelled=0,
            voucher_type=self.module.SE, voucher_no="TRANSFER-SECOND", voucher_detail_no="TRANSFER-SECOND-ROW",
            type_of_transaction="Inward", posting_datetime="2026-10-03 09:30:00",
            creation="2026-10-03 09:30:00")
        self.base.add(self.module.SLE, "TRANSFER-SECOND-IN", company="JF", item_code="RM",
            warehouse="RM-WH", stock_uom="Kg", voucher_type=self.module.SE, voucher_no="TRANSFER-SECOND",
            voucher_detail_no="TRANSFER-SECOND-ROW", posting_date="2026-10-03", posting_time="09:30:00",
            creation="2026-10-03 09:30:00", actual_qty=5, stock_value_difference=20,
            serial_and_batch_bundle="INWARD-SECOND", is_cancelled=0, is_adjustment_entry=0)
        return loss

    def test_two_actual_transfers_to_one_batch_keep_one_loss_cost_and_source_coverage(self):
        loss = self.seed_split_transfer_loss_source()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["issue_material"]), (40, 40))
        self.assertEqual(len(result["loss_batches"]), 1)
        self.base.list_denied.add((self.module.SLE, "TRANSFER-SECOND-IN"))
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.list_denied.clear()
        loss.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.assert_no_writes()

    def seed_serial_loss_cost_source(self, *, with_batch=False, serial_wise=True):
        loss = self.seed_batch_loss_cost_source()
        self.base.documents["Item", "RM"].update(has_serial_no=1, has_batch_no=int(with_batch),
            use_serial_no_wise_valuation=int(serial_wise), stock_uom="Nos")
        names = ["SERIAL-" + str(index) for index in range(1, 11)]
        for (kind, _name), document in list(self.base.documents.items()):
            if kind in {self.module.SLE, "Stock Entry Detail", "Work Order Item"} and document.get("item_code") == "RM":
                document.stock_uom = "Nos"
                if kind == "Stock Entry Detail":
                    document.update(uom="Nos", batch_no="BATCH-1" if with_batch else None,
                        serial_no="\n".join(names))
        for bundle_name, quantity in (("INWARD", 1), ("OUTWARD", -1)):
            bundle = self.base.documents["Serial and Batch Bundle", bundle_name]
            bundle.update(
                has_serial_no=1, has_batch_no=int(with_batch))
            del self.base.documents["Serial and Batch Entry", bundle_name + "-ROW"]
            for index, serial in enumerate(names):
                self.child("Serial and Batch Bundle", bundle_name, "entries", bundle_name + "-" + serial,
                    item_code="RM", docstatus=1, warehouse="RM-WH", serial_no=serial,
                    batch_no="BATCH-1" if with_batch else None, qty=quantity, incoming_rate=4,
                    stock_value_difference=quantity * 4, is_cancelled=0, idx=index + 1,
                    voucher_type=bundle.voucher_type, voucher_no=bundle.voucher_no,
                    voucher_detail_no=bundle.voucher_detail_no,
                    type_of_transaction="Inward" if quantity > 0 else "Outward",
                    posting_datetime="2026-10-03 " + ("09:00:00" if quantity > 0 else "10:00:00"),
                    creation="2026-10-03 " + ("09:00:00" if quantity > 0 else "10:00:00"))
        for serial in names:
            self.base.add("Serial No", serial, serial_no=serial, item_code="RM", company="JF",
                docstatus=0, batch_no="BATCH-1" if with_batch else None,
                warehouse=None, status="Consumed", purchase_rate=4)
        loss.jf_production_loss_source_signature = self.loss.source_signature(
            self.get_doc("Job Card", "CARD"), self.get_doc(self.module.WO, "WO"))
        return loss

    def seed_single_valuation_policy(self):
        self.extend_schema("Stock Settings", {"name", "modified", "do_not_use_batchwise_valuation"})
        self.base.metas["Stock Settings"]["do_not_use_batchwise_valuation"].fieldtype = "Check"
        doc = self.base.add("Stock Settings", "Stock Settings", docstatus=0,
            do_not_use_batchwise_valuation=0)
        self.single_stored = {"name": "Stock Settings", "modified": doc.modified,
                              "do_not_use_batchwise_valuation": "0"}
        self.frappe.db.get_singles_dict = Mock(side_effect=lambda doctype: deepcopy(self.single_stored)
            if doctype == "Stock Settings" else self.fail("unexpected Single read"))
        native_meta = self.frappe.get_meta
        def get_meta(doctype):
            meta = native_meta(doctype)
            meta.issingle = doctype == "Stock Settings"
            return meta
        self.frappe.get_meta = get_meta
        return doc

    def test_single_valuation_source_uses_native_document_and_raw_singles_without_fake_table(self):
        self.seed_single_valuation_policy()
        session = self.module._Session("FLOW")
        self.assertEqual(session._loss_reader("Stock Settings", "Stock Settings").do_not_use_batchwise_valuation, 0)
        session.finish()
        self.assertFalse(any(call.args and call.args[0] == "Stock Settings"
            for call in self.frappe.get_all.call_args_list + self.frappe.get_list.call_args_list))
        self.frappe.db.get_singles_dict.assert_called_with("Stock Settings")
        self.assert_no_writes()

    def test_single_policy_raw_change_without_modified_or_document_change_invalidates_cost(self):
        self.seed_single_valuation_policy()
        session = self.module._Session("FLOW")
        session._loss_reader("Stock Settings", "Stock Settings")
        with self.assertRaises(PermissionError):
            session.finish(lambda: self.single_stored.update(do_not_use_batchwise_valuation="1"))
        self.assert_no_writes()

    def test_single_policy_field_mask_and_late_role_metadata_deny_partial_cost(self):
        self.seed_single_valuation_policy()
        self.base.metas["Stock Settings"]["do_not_use_batchwise_valuation"].mask = 1
        with self.assertRaises(PermissionError):
            self.module._Session("FLOW")._loss_reader("Stock Settings", "Stock Settings")
        self.base.metas["Stock Settings"]["do_not_use_batchwise_valuation"].mask = 0
        permission = self.base.add("Custom DocPerm", "SINGLE-PERM", parent="Stock Settings", role="QA", read=1)
        session = self.module._Session("FLOW")
        session._loss_reader("Stock Settings", "Stock Settings")
        permission.read = 0
        with self.assertRaises(PermissionError):
            session.finish()
        self.assert_no_writes()

    def seed_different_price_batch_loss(self):
        loss = self.seed_split_transfer_loss_source()
        self.seed_single_valuation_policy()
        self.base.documents["Item", "RM"].valuation_method = "Moving Average"
        self.base.documents["Company", "JF"].valuation_method = "Moving Average"
        self.base.documents["Batch", "BATCH-1"].use_batchwise_valuation = 1
        self.base.documents[self.module.SLE, "TRANSFER-SECOND-IN"].stock_value_difference = 40
        self.base.documents["Serial and Batch Bundle", "INWARD-SECOND"].total_amount = 40
        self.base.documents["Serial and Batch Entry", "INWARD-SECOND-ROW"].update(
            incoming_rate=8, stock_value_difference=40)
        self.base.documents[self.module.SLE, "LOSS-SLE"].stock_value_difference = -60
        self.base.documents["Serial and Batch Bundle", "OUTWARD"].total_amount = -60
        self.base.documents["Serial and Batch Entry", "OUTWARD-ROW"].update(
            incoming_rate=6, stock_value_difference=-60)
        self.base.documents["Stock Entry Detail", "LOSS-RM"].update(
            basic_rate=6, basic_amount=60, amount=60, valuation_rate=6)
        return loss

    def test_actual_different_price_batch_values_feed_one_booked_loss_cost_and_cancel(self):
        loss = self.seed_different_price_batch_loss()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["issue_material"]), (60, 60))
        self.assertEqual([row["loss_issue"] for row in result["loss_batches"]], ["LOSS"])
        loss.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.assert_no_writes()

    def test_different_price_cost_requires_policy_and_exact_native_bundle_source_fields(self):
        self.seed_different_price_batch_loss()
        self.base.metas["Stock Settings"]["do_not_use_batchwise_valuation"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.metas["Stock Settings"]["do_not_use_batchwise_valuation"].mask = 0
        self.base.metas["Serial and Batch Entry"]["posting_datetime"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.metas["Serial and Batch Entry"]["posting_datetime"].mask = 0
        session = self.module._Session("FLOW")
        session.load_issues()
        with self.assertRaises(PermissionError):
            session.finish(lambda: self.single_stored.update(do_not_use_batchwise_valuation="1"))
        self.assert_no_writes()

    def test_exact_serial_history_values_once_with_and_without_serial_wise_valuation(self):
        for enabled in (True, False):
            with self.subTest(serial_wise=enabled):
                loss = self.seed_serial_loss_cost_source(serial_wise=enabled)
                result = self.module.posted_flow_costs("FLOW")
                self.assertEqual(result["totals"]["material"], 40)
                self.assertEqual([row["loss_issue"] for row in result["loss_batches"]], ["LOSS"])
                loss.docstatus = 2
                self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.assert_no_writes()

    def test_serial_cost_requires_visible_identity_fields_and_rechecks_late_changes(self):
        self.seed_serial_loss_cost_source(with_batch=True)
        self.base.list_denied.add(("Serial No", "SERIAL-10"))
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.list_denied.clear()
        self.base.metas["Serial No"]["batch_no"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.metas["Serial No"]["batch_no"].mask = 0
        session = self.module._Session("FLOW")
        session.load_issues()
        self.base.documents["Serial No", "SERIAL-10"].item_code = "OTHER"
        with self.assertRaises(PermissionError):
            session.finish()
        self.assert_no_writes()

    def test_serial_cost_rechecks_new_type_authorization_and_batch_identity(self):
        self.seed_serial_loss_cost_source(with_batch=True)
        permission = self.base.add("Custom DocPerm", "SERIAL-PERM", parent="Serial No", role="QA", read=1)
        session = self.module._Session("FLOW")
        session.load_issues()
        permission.read = 0
        with self.assertRaises(PermissionError):
            session.finish()
        permission.read = 1
        self.base.documents["Serial No", "SERIAL-10"].batch_no = "ANOTHER-BATCH"
        with self.assertRaises(ValueError):
            self.module.posted_flow_costs("FLOW")
        self.assert_no_writes()

    def test_batch_loss_cost_refuses_hidden_transfer_masked_batch_and_late_lot_edit(self):
        self.seed_batch_loss_cost_source()
        self.base.list_denied.add((self.module.SLE, "TRANSFER-IN"))
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.list_denied.clear()
        self.base.metas["Batch"]["use_batchwise_valuation"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.base.metas["Batch"]["use_batchwise_valuation"].mask = 0
        session = self.module._Session("FLOW")
        session.load_issues()
        self.base.documents["Serial and Batch Entry", "INWARD-ROW"].qty = 9
        with self.assertRaises(PermissionError):
            session.finish()
        self.assert_no_writes()

    def test_duplicate_card_or_other_cards_cannot_lend_work_order_loss_coverage(self):
        self.seed_loss()
        self.seed_loss("SECOND")
        with self.assertRaisesRegex(ValueError, "同一报工"):
            self.module.posted_flow_costs("FLOW")
        self.base.documents[self.module.SE, "SECOND"].docstatus = 2
        self.seed_loss("THIRD", card="OTHER-CARD")
        with self.assertRaisesRegex(ValueError, "同一工单"):
            self.module.material_issue_costs("FLOW")
        self.assert_no_writes()

    def test_actual_source_identity_signature_and_sle_are_required(self):
        doc, row = self.seed_loss()
        cases = [(doc, "stock_entry_type", "Material Issue"), (doc, "jf_production_loss_work_order", "OTHER"),
            (doc, "jf_production_loss_no_residual", 0), (doc, "jf_production_loss_source_signature", "wrong"),
            (row, "jf_flow_order", "OTHER"), (row, "transfer_qty", 9),
            (self.base.documents[self.module.SLE, "LOSS-SLE"], "actual_qty", -9),
            (self.base.documents["Job Card", "CARD"], "company", "OTHER")]
        for target, field, value in cases:
            old = target.get(field); target[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, PermissionError)):
                self.module.posted_flow_costs("FLOW")
            target[field] = old
        doc.stock_entry_type, doc.jf_production_loss_work_order = "Material Issue", "OTHER"
        with self.assertRaises(ValueError): self.module.material_issue_costs("FLOW")
        doc.stock_entry_type, doc.jf_production_loss_work_order = self.loss.LOSS_TYPE, "WO"
        self.base.documents[self.module.SLE, "LOSS-SLE"].is_cancelled = 1
        with self.assertRaises(ValueError): self.module.production_batch_candidates("FLOW")
        self.assert_no_writes()

    def test_hidden_source_child_fields_and_late_change_refuse_partial_cost(self):
        self.seed_loss()
        for kind, name in ((self.module.SE, "LOSS"), ("Job Card", "CARD"), ("Work Order Item", "WO-RAW")):
            self.base.list_denied.add((kind, name))
            with self.subTest(kind=kind), self.assertRaises(PermissionError):
                self.module.posted_flow_costs("FLOW")
            self.base.list_denied.clear()
        self.base.hidden["Job Card"] = {"process_loss_qty"}
        with self.assertRaises(PermissionError): self.module.posted_flow_costs("FLOW")
        self.base.hidden.clear()
        count = 0
        def edit_after_source_read(filters):
            nonlocal count
            count += 1
            if count == 2:
                self.base.documents["Job Card Time Log", "CARD-TIME"].to_time = "2026-10-03 09:30:00"
        self.base.list_hooks["Company"] = edit_after_source_read
        with self.assertRaises(PermissionError): self.module.posted_flow_costs("FLOW")
        self.assert_no_writes()

    def test_combined_candidates_keep_existing_outsourced_semi_source_graph_closed(self):
        cast, _receipt, _row = self.base.stage_chain(qty=10)
        cast.docstatus = 2
        for suffix, outsourced in (("CAST", 1), ("PLATE", 0)):
            self.base.documents["Work Order Operation", suffix + "-OP"].is_subcontracted = outsourced
            self.base.documents["Job Card", suffix + "-JC"].is_subcontracted = outsourced
        self.base.documents["Purchase Order Item", "PO-STAGE"].update(fg_item="SEMI", job_card="CAST-JC", bom="CAST-BOM")
        self.base.documents["Subcontracting Order Item", "SCO-STAGE"].update(item_code="SEMI", job_card="CAST-JC", bom="CAST-BOM")
        self.base.documents["Subcontracting Receipt Item", "PLATE-FG"].update(item_code="SEMI", job_card="CAST-JC",
            bom="CAST-BOM", rm_cost_per_qty=4, rm_supp_cost=40, service_cost_per_qty=1, amount=50)
        self.base.documents["Subcontracting Receipt Supplied Item", "PLATE-RM"].update(main_item_code="SEMI", rm_item_code="RM",
            consumed_qty=4, stock_uom="Kg", rate=10, amount=40)
        self.base.documents[self.module.SLE, "PLATE-RM-SLE"].update(item_code="RM", stock_uom="Kg", actual_qty=-4,
            stock_value_difference=-40)
        self.base.documents[self.module.SLE, "PLATE-FG-SLE"].update(item_code="SEMI", stock_value_difference=50)
        final = self.base.manufacture("FINAL", work="STAGE-WO", material=50, other=1, qty=10, raw_qty=10)
        final.job_card, final.bom_no = "PLATE-JC", "PLATE-BOM"
        self.base.documents["Stock Entry Detail", "FINAL-RM"].update(item_code="SEMI", uom="Nos", stock_uom="Nos")
        self.base.documents[self.module.SLE, "FINAL-RM-SLE"].update(item_code="SEMI", stock_uom="Nos")
        self.assertEqual([(row["source_kind"], row["source_name"], row["qty"])
                          for row in self.module.production_batch_candidates("FLOW")], [("Manufacture", "FINAL", 10)])
        self.assert_no_writes()


class TestProductionLossPreview(unittest.TestCase):
    def setUp(self):
        self.base = preview_fixture.TestReceiptBatches(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.preview = self.base.saving_preview()
        self.base.workflow.stock_plan_quantity.return_value = Decimal(5)
        self.loss = {"loss_issue": "LOSS", "work_order": "WO", "job_card": "JC",
            "company": "JF", "item_code": "FG", "stock_uom": "Nos",
            "qty": Decimal(5), "inventory_qty": Decimal(0), "lost_qty": Decimal(5)}
        self.actual = {"totals": {"material": Decimal(40), "processing": Decimal(0), "other": Decimal(0)},
            "issues": [{"issue": "LOSS", "material": Decimal(40)}], "receipts": [], "manufactures": [], "wages": [],
            "manufacture_batches": [], "loss_batches": [self.loss], "currency": "CNY"}
        # Isolate the preview's typed source/quantity contract. The complete
        # inventory/source service is exercised separately below.
        self.preview._read_actual_costs = Mock(side_effect=lambda: deepcopy(self.actual))
        self.batch = type(self.preview.batches[0])(receipt_type="首次回货", reference_no="LOSS",
            source_loss_issue="LOSS", source_manufacture=None, source_receipt=None, source_receipt_item=None,
            received_qty=5, good_qty=0, rework_qty=0, pending_qty=0, lost_qty=5, extra_processing_amount=0)
        self.preview.batches = [self.batch]

    def test_all_loss_keeps_real_cost_unallocated_without_fake_finished_stock(self):
        self.preview.validate()
        self.assertEqual((self.preview.total_cost, self.preview.estimated_unit_cost, self.preview.unallocated_cost,
                          self.preview.good_qty, self.preview.lost_qty), ("40", "0", "40", "0", "5"))
        self.assertEqual(self.batch.estimated_cost, "0")
        self.base.assert_no_posting()

    def test_actual_source_collector_drives_preview_and_replacement_reverse_without_double_cost(self):
        source = TestProductionLossSources(methodName="runTest")
        source.install_loss_contract(self.base.native)
        self.addCleanup(source.doCleanups)
        self.base.receipt.docstatus = 2
        source.seed_loss()
        del self.preview._read_actual_costs
        self.preview.validate()
        self.assertEqual((self.preview.material_amount, self.preview.total_cost, self.preview.unallocated_cost),
                         (Decimal(40), "40", "40"))
        replacement_batch = self.replacement()
        replacement = self.base.native.manufacture("REPLACEMENT", work="REPLACE-WO", material=40, qty=5)
        self.preview.validate()
        self.assertEqual((self.preview.total_cost, self.preview.estimated_unit_cost, self.preview.unallocated_cost),
                         ("80", "16", "0"))
        replacement.docstatus = 2
        self.preview.batches.remove(replacement_batch)
        self.preview.validate()
        self.assertEqual((self.preview.total_cost, self.preview.unallocated_cost), ("40", "40"))
        self.base.assert_no_posting()

    def replacement(self):
        source = {"manufacture": "REPLACEMENT", "work_order": "REPLACE-WO", "company": "JF", "item_code": "FG",
            "stock_uom": "Nos", "qty": Decimal(5), "inventory_qty": Decimal(5), "lost_qty": Decimal(0)}
        self.actual["manufacture_batches"] = [source]
        self.actual["manufactures"] = [{"manufacture": "REPLACEMENT", "material": Decimal(40)}]
        self.actual["totals"]["material"] = Decimal(80)
        batch = type(self.batch)(receipt_type="首次回货", reference_no="REPLACEMENT", source_manufacture="REPLACEMENT",
            source_loss_issue=None, source_receipt=None, source_receipt_item=None, received_qty=5,
            good_qty=5, rework_qty=0, pending_qty=0, lost_qty=0, extra_processing_amount=0)
        self.preview.batches.append(batch)
        return batch

    def test_replacement_adds_actual_input_then_cancel_restores_unallocated_loss(self):
        replacement = self.replacement()
        self.preview.validate()
        self.assertEqual((self.preview.planned_qty, self.preview.total_cost, self.preview.estimated_unit_cost,
                          self.preview.unallocated_cost, replacement.estimated_cost), (5, "80", "16", "0", "80"))
        self.actual["manufacture_batches"], self.actual["manufactures"] = [], []
        self.actual["totals"]["material"] = Decimal(40)
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.preview.validate()
        self.preview.batches.remove(replacement)
        self.preview.validate()
        self.assertEqual((self.preview.total_cost, self.preview.unallocated_cost, self.preview.good_qty), ("40", "40", "0"))
        self.base.assert_no_posting()

    def test_loss_cannot_borrow_classification_or_mix_duplicate_source_kinds(self):
        cases = (("lost_qty", 4), ("good_qty", 1), ("pending_qty", 1), ("rework_qty", 1),
                 ("extra_processing_amount", 1), ("source_manufacture", "LOSS"))
        for field, value in cases:
            previous = self.batch.get(field)
            self.batch[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.preview.validate()
            self.batch[field] = previous
        self.preview.batches.append(type(self.batch)(self.batch))
        with self.assertRaisesRegex(ValueError, "重复"): self.preview.validate()
        self.base.assert_no_posting()

    def test_removing_loss_link_or_manufacture_link_cannot_lend_extra_coverage(self):
        self.batch.source_loss_issue = None
        with self.assertRaisesRegex(ValueError, "确认损耗"): self.preview.validate()
        self.batch.source_loss_issue = "LOSS"
        replacement = self.replacement(); replacement.source_manufacture = None
        with self.assertRaisesRegex(ValueError, "确认损耗"): self.preview.validate()
        self.base.assert_no_posting()


if __name__ == "__main__": unittest.main()
