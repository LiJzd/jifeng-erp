"""Accepted completion through installed 16.36 native receipt/card callbacks."""

import ast
import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from copy import deepcopy
from unittest.mock import patch


ROOT = pathlib.Path(__file__).parents[1] / "jifeng_erp"

# Captured installed JobCard.set_manufactured_qty, 2026-10-09. Both branches
# execute below; only the in-memory database and native tail writes are faked.
_NATIVE_CARD = '''
def set_manufactured_qty(self):
    table_name = "Stock Entry"
    child_name = "Stock Entry Detail"
    if self.is_subcontracted:
        table_name = "Subcontracting Receipt Item"
    table = frappe.qb.DocType(table_name)
    query = frappe.qb.from_(table).where((table.job_card == self.name) & (table.docstatus == 1))
    if self.is_subcontracted:
        query = query.select(Sum(table.qty))
    else:
        child = frappe.qb.DocType(child_name)
        query = (query.join(child).on(table.name == child.parent)
            .select(Sum(child.transfer_qty))
            .where((table.purpose == "Manufacture") & (child.is_finished_item == 1)))
    qty = query.run()[0][0] or 0.0
    self.manufactured_qty = flt(qty)
    self.db_set("manufactured_qty", self.manufactured_qty)
    self.update_semi_finished_good_details()
    self.set_status(update_status=True)
'''

# Exact native submit/cancel lifecycle: quantity refresh remains after stock,
# accounting and status callbacks. No submit/permission/wage gate is replaced.
_NATIVE_RECEIPT = '''
def on_submit(self):
    self.validate_closed_subcontracting_order()
    self.validate_bom_required_qty()
    self.update_status_updater_args()
    self.update_prevdoc_status()
    self.set_subcontracting_order_status(update_bin=False)
    self.set_consumed_qty_in_subcontract_order()
    for table_name in ["items", "supplied_items"]:
        self.make_bundle_using_old_serial_batch_fields(table_name)
    self.update_stock_reservation_entries()
    self.update_stock_ledger()
    self.make_gl_entries()
    self.repost_future_sle_and_gle()
    self.update_status()
    self.auto_create_purchase_receipt()
    self.update_job_card()

def on_cancel(self):
    self.ignore_linked_doctypes = (
        "GL Entry", "Stock Ledger Entry", "Repost Item Valuation", "Serial and Batch Bundle")
    self.validate_closed_subcontracting_order()
    self.update_status_updater_args()
    self.update_prevdoc_status()
    self.set_consumed_qty_in_subcontract_order()
    self.set_subcontracting_order_status(update_bin=False)
    self.update_stock_ledger()
    self.update_stock_reservation_entries()
    self.make_gl_entries_on_cancel()
    self.repost_future_sle_and_gle()
    self.update_status()
    self.delete_auto_created_batches()
    self.update_job_card()
'''


class Row(dict):
    __getattr__ = dict.get
    def __setattr__(self, field, value):
        self[field] = value


class Doc(types.SimpleNamespace):
    def get(self, field, default=None):
        return getattr(self, field, default)


class Expression:
    def __getattr__(self, name):
        return self
    def __eq__(self, other):
        return self
    def __and__(self, other):
        return self


class TestSubcontractingJobCardQty(unittest.TestCase):
    def setUp(self):
        self.rows, self.events, self.queries = [], [], []
        self.quantity_precision = 6
        self.normal_stock_qty = 7
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda **kwargs: lambda method: method
        frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.flt = lambda value, precision=None: (round(float(value or 0), precision)
                                                   if precision is not None else float(value or 0))
        utils.get_datetime = datetime.datetime.fromisoformat
        self.modules = {"frappe": frappe, "frappe.utils": utils}
        module_patch = patch.dict(sys.modules, self.modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        spec = importlib.util.spec_from_file_location("receipt_qty_subject", ROOT / "subcontracting_returns.py")
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        self.frappe = frappe
        outer = self
        class Query:
            def __init__(self, table):
                self.table = table
            def where(self, condition): return self
            def select(self, value): return self
            def join(self, child): return self
            def on(self, condition): return self
            def run(self):
                if self.table.table_name == "Stock Entry":
                    outer.events.append(("native_stock_query",))
                    return [[outer.normal_stock_qty]]
                return [[sum(row.qty for row in outer.rows if row.job_card == "JC" and row.docstatus == 1)]]
        def table(name):
            value = Expression()
            value.table_name = name
            return value
        frappe.qb = types.SimpleNamespace(DocType=table, from_=Query)
        native = {"frappe": frappe, "Sum": lambda field: field, "flt": utils.flt}
        exec(_NATIVE_CARD, native)
        class NativeCard(Doc):
            set_manufactured_qty = native["set_manufactured_qty"]
            def db_set(self, field, value): outer.events.append(("db_set", field, value))
            def update_semi_finished_good_details(self): outer.events.append(("semi_finished",))
            def set_status(self, **kwargs): outer.events.append(("status", kwargs))
            def add_time_log(self, args): outer.events.append(("native_time_log", args)); return "saved"
        self.NativeCard = NativeCard
        # Execute the real current wage mixin class, preserving its hooks in
        # either Frappe extension order. Its source guard is observed, not bypassed.
        tree = ast.parse((ROOT / "wage_allocation.py").read_text(encoding="utf-8"))
        wage_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                          and node.name == "WageSourceJobCardMixin")
        wage_globals = {
            "_approved_job_card_source": lambda doc: self.events.append(("wage_before",)) or "snapshot",
            "_assert_job_card_source": lambda doc, before: self.events.append(("wage_after", before)),
        }
        exec(compile(ast.Module(body=[wage_class], type_ignores=[]), "wage_allocation.py", "exec"), wage_globals)
        self.WageMixin = wage_globals["WageSourceJobCardMixin"]
        Card = type("Card", (self.WageMixin, self.subject.SubcontractingJobCardQtyMixin, NativeCard), {})
        self.card = Card(name="JC", company="JF", stock_uom="Nos", is_subcontracted=1,
                         total_completed_qty=12, process_loss_qty=3, time_logs=["untouched"])
        frappe.db = types.SimpleNamespace(sql=self.sql)
        frappe.get_list = lambda *args, **kwargs: self.fail("internal total must not omit hidden sources")
        frappe.get_doc = self.get_doc
        lifecycle = {}
        exec(_NATIVE_RECEIPT, lifecycle)
        self.Receipt = type("Receipt", (self.subject.SubcontractingReceiptReturnMixin, Doc), lifecycle)

    def sql(self, query, values, **kwargs):
        self.queries.append((query, values, kwargs))
        self.assertIn("for update nowait", query.lower())
        self.assertTrue(kwargs["as_dict"])
        if "from `tabJob Card`" in query:
            return [Row(name=self.card.name, company=self.card.company,
                        stock_uom=self.card.stock_uom, is_subcontracted=self.card.is_subcontracted)]
        self.assertIn("parent.docstatus = 1 and child.docstatus = 1", query)
        self.assertIn("child.parenttype = 'Subcontracting Receipt'", query)
        self.assertIn("child.parentfield = 'items'", query)
        originals = {row.name: row for row in self.rows}
        return [row for row in self.rows if row.docstatus == row.parent_docstatus == 1
                and (row.job_card == values[0]
                     or originals.get(row.subcontracting_receipt_item, {}).get("job_card") == values[1])]

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            self.assertEqual(doctype["doctype"], "Subcontracting Receipt Item")
            self.assertIsNone(name)
            self.assertEqual(kwargs, {})
            class NativeQuantityRow(Row):
                def precision(row, field):
                    return self.quantity_precision
            return NativeQuantityRow(doctype)
        self.assertEqual((doctype, name, kwargs), ("Job Card", "JC", {}))
        self.events.append(("get_card", name))
        return self.card

    def source(self, name="SRC", *, qty=5, rejected=2, loss=1, **changes):
        row = Row(name=name + "-ROW", parent=name, docstatus=1, parent_docstatus=1,
            job_card="JC", item_code="FG", stock_uom="Nos", conversion_factor=1,
            qty=qty, rejected_qty=rejected, process_loss_qty=loss, received_qty=qty + rejected + loss,
            warehouse="Good", rejected_warehouse="Rejected", receipt_is_return=0,
            receipt_company="JF", receipt_supplier="Supplier", receipt_supplier_warehouse="SupplierStock")
        row.update(changes)
        self.rows.append(row)
        return row

    def returned(self, source, *, name="RETURN", accepted=0, rejected=0, mapped=False, **changes):
        row = deepcopy(source)
        row.update(name=name + "-ROW", parent=name, receipt_is_return=1,
            receipt_return_against=source.parent, subcontracting_receipt_item=source.name,
            qty=-rejected if mapped else -accepted, rejected_qty=0 if mapped else -rejected,
            warehouse="Rejected" if mapped else "Good", process_loss_qty=0,
            received_qty=-(accepted + rejected))
        row.update(changes)
        self.rows.append(row)
        return row

    def receipt(self, rows):
        receipt = self.Receipt(name="CURRENT", company="JF", items=rows)
        methods = ("validate_closed_subcontracting_order", "validate_bom_required_qty",
            "update_status_updater_args", "update_prevdoc_status", "set_subcontracting_order_status",
            "set_consumed_qty_in_subcontract_order", "make_bundle_using_old_serial_batch_fields",
            "update_stock_reservation_entries", "update_stock_ledger", "make_gl_entries",
            "repost_future_sle_and_gle", "update_status", "auto_create_purchase_receipt",
            "make_gl_entries_on_cancel", "delete_auto_created_batches")
        for name in methods:
            setattr(receipt, name, lambda *args, _name=name, **kwargs: self.events.append((_name,)))
        return receipt

    def test_native_bug_and_rejected_mapper_fixed(self):
        source = self.source()
        self.returned(source, rejected=1, mapped=True)
        self.NativeCard.set_manufactured_qty(self.card)
        self.assertEqual(self.card.manufactured_qty, 4)
        self.events.clear()
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 5)
        self.assertEqual(self.events, [("db_set", "manufactured_qty", 5), ("semi_finished",),
                                      ("status", {"update_status": True})])

    def test_rejected_only_source_stays_zero(self):
        source = self.source(qty=0, rejected=2, loss=0)
        self.returned(source, rejected=1, mapped=True)
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 0)

    def test_accepted_rejected_loss_and_two_sources_sum_once(self):
        source = self.source()
        second = self.source("SECOND", qty=3, rejected=1, loss=2)
        self.returned(source, name="R1", accepted=2, rejected=1)
        self.returned(second, name="R2", rejected=1, mapped=True)
        receipt = self.receipt([source, second])
        self.assertEqual(receipt.get_manufactured_qty("JC"), 6)
        receipt.update_job_card()
        self.assertEqual(self.card.manufactured_qty, 6)
        self.assertEqual(sum(event[0] == "get_card" for event in self.events), 1)
        self.assertEqual(sum(event[0] == "db_set" for event in self.events), 1)
        self.assertEqual((self.card.total_completed_qty, self.card.process_loss_qty, self.card.time_logs),
                         (12, 3, ["untouched"]))

    def test_native_submit_cancel_recomputes_effective_rows(self):
        source = self.source()
        returned = self.returned(source, accepted=2)
        receipt = self.receipt([returned])
        receipt.on_submit()
        self.assertEqual(self.card.manufactured_qty, 3)
        self.assertLess(self.events.index(("make_gl_entries",)), self.events.index(("get_card", "JC")))
        returned.docstatus = returned.parent_docstatus = 2
        self.events.clear()
        receipt.on_cancel()
        self.assertEqual(self.card.manufactured_qty, 5)
        self.assertLess(self.events.index(("make_gl_entries_on_cancel",)), self.events.index(("get_card", "JC")))
        source.parent_docstatus = 2
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 0)

    def test_hidden_effective_sources_count_parent_and_child_status(self):
        self.source(qty=4)
        self.source("CANCEL", qty=99, parent_docstatus=2)
        self.source("CHILD", qty=99, docstatus=2)
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 4)

    def test_source_identity_capacity_company_and_cf_fail_before_write(self):
        source = self.source()
        returned = self.returned(source, accepted=1)
        cases = ((returned, "receipt_return_against", "OTHER"), (returned, "job_card", "OTHER"),
            (returned, "receipt_company", "OTHER"), (returned, "stock_uom", "Box"),
            (returned, "warehouse", "OTHER"), (returned, "qty", -6),
            (returned, "conversion_factor", 2), (returned, "process_loss_qty", -1),
            (source, "parent_docstatus", 2))
        for row, field, value in cases:
            with self.subTest(field=field):
                before = row.get(field)
                row[field] = value
                self.events.clear()
                with self.assertRaises(ValueError): self.card.set_manufactured_qty()
                self.assertEqual(self.events, [])
                row[field] = before

    def test_rejected_capacity_is_checked_separately(self):
        source = self.source()
        self.returned(source, name="R1", rejected=2, mapped=True)
        self.returned(source, name="R2", rejected=1, mapped=True)
        with self.assertRaisesRegex(ValueError, "累计退回"): self.card.set_manufactured_qty()

    def test_native_quantity_precision_nine_rejects_sub_micro_over_return(self):
        self.quantity_precision = 9
        source = self.source(qty=1, rejected=0, loss=0)
        self.returned(source, accepted=1.0000004)
        with self.assertRaisesRegex(ValueError, "累计退回"): self.card.set_manufactured_qty()
        self.assertEqual(self.events, [])

    def test_native_quantity_precision_three_accepts_exact_split_and_received_rounding(self):
        self.quantity_precision = 3
        source = self.source(qty=1, rejected=0, loss=0, received_qty=1.0000004)
        for index, qty in enumerate((0.333, 0.333, 0.334)):
            self.returned(source, name="R" + str(index), accepted=qty)
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 0)

    def test_card_or_receipt_lock_conflict_preserves_db_error_and_does_not_retry(self):
        self.source()
        for phase in ("card", "receipt"):
            for error in (self.frappe.QueryTimeoutError, self.frappe.QueryDeadlockError):
                with self.subTest(phase=phase, error=error.__name__):
                    calls = []
                    def locked(query, values, **kwargs):
                        calls.append(query)
                        if ("from `tabJob Card`" in query) == (phase == "card"):
                            raise error("original database lock conflict")
                        return self.sql(query, values, **kwargs)
                    self.events.clear()
                    with patch.object(self.frappe.db, "sql", side_effect=locked):
                        with self.assertRaisesRegex(error, "同工序委外收回正在更新"):
                            self.card.set_manufactured_qty()
                    self.assertEqual(len(calls), 1 if phase == "card" else 2)
                    self.assertEqual(self.events, [])

    def test_ordinary_manufacturing_uses_native_query_and_tail(self):
        self.card.is_subcontracted = 0
        self.card.set_manufactured_qty()
        self.assertEqual(self.card.manufactured_qty, 7)
        self.assertEqual(self.queries, [])
        self.assertEqual(self.events, [("native_stock_query",), ("db_set", "manufactured_qty", 7),
            ("semi_finished",), ("status", {"update_status": True})])

    def test_native_tail_failure_propagates_and_time_log_wage_guard_remains(self):
        self.source()
        self.card.update_semi_finished_good_details = lambda: (_ for _ in ()).throw(ValueError("native tail"))
        with self.assertRaisesRegex(ValueError, "native tail"): self.card.set_manufactured_qty()
        self.assertFalse(any(event[0] == "status" for event in self.events))
        self.events.clear()
        self.assertEqual(self.card.add_time_log({"completed_qty": 1}), "saved")
        self.assertEqual([event[0] for event in self.events], ["wage_before", "native_time_log", "wage_after"])

    def test_hooks_keep_wage_mixin_and_existing_cancel_gates(self):
        tree = ast.parse((ROOT / "hooks.py").read_text(encoding="utf-8"))
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in ("extend_doctype_class", "doc_events")}
        self.assertEqual(values["extend_doctype_class"]["Job Card"], [
            "jifeng_erp.wage_allocation.WageSourceJobCardMixin",
            "jifeng_erp.subcontracting_returns.SubcontractingJobCardQtyMixin"])
        self.assertEqual(values["doc_events"]["Job Card"]["before_cancel"],
                         "jifeng_erp.payroll.validate_job_card_cancel")


if __name__ == "__main__":
    unittest.main()
