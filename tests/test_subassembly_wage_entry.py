"""Actual wage-entry validation against a native-shaped child production graph.

Framework reads are controlled; no database, payroll or stock write is run.
Unchanged workbench/classification/quote guards are isolated here. The child
source proof, supervisor shares, wage amounts and entry callbacks are real.
"""

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_production_subassembly_loss as source_test


ROOT = Path(__file__).parents[1] / "jifeng_erp"
Doc = source_test.Doc


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SubassemblyWageEntryTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing complete source graph without inheriting its
        # stock/production tests or inventing a second source implementation.
        self.graph = source_test.ProductionSubassemblyLossTests()
        self.graph.setUp()
        self.addCleanup(self.graph.doCleanups)
        # Native Document.as_dict exposes fields without confusing the BOM's
        # "items" table with Python's dict.items method in source snapshots.
        native_dict = patch.object(Doc, "as_dict", lambda doc: dict(doc), create=True)
        native_dict.start()
        self.addCleanup(native_dict.stop)
        self.h = h = self.graph.h
        self.frappe = frappe = h.modules["frappe"]
        frappe.session = Doc(user="Administrator")
        frappe.conf = Doc()
        frappe.PermissionError = PermissionError
        frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        frappe.throw = lambda message, error=ValueError, *args: (_ for _ in ()).throw(error(message))
        frappe.only_for = Mock()
        frappe.has_permission = Mock(return_value=True)
        h.modules["frappe.utils"].getdate = lambda value: date.fromisoformat(str(value).split(" ")[0])

        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda kind, **kwargs: set(self.metas[kind])
        document = types.ModuleType("frappe.model.document")
        document.Document = Doc
        guards = {}
        for kind, names in {
            "wage_workbench": ("authorize_native_restore", "guard_request_identity", "prepare_wage_amendment"),
            "wage_personnel_classification": ("authorize_classification_restore", "bind_classification", "guard_approved_classification"),
            "wage_rate_references": ("guard_cancelled_wage_snapshot", "validate_wage_reference",
                                     "guard_approved_allocation_quotes", "validate_allocation_reference"),
        }.items():
            module = types.ModuleType("jifeng_erp." + kind)
            for name in names: setattr(module, name, Mock())
            guards[module.__name__] = module
        scope = patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document, **guards})
        scope.start()
        self.addCleanup(scope.stop)
        self.payroll = load("child_entry_payroll", "payroll.py")
        scope = patch.dict(sys.modules, {"jifeng_erp.payroll": self.payroll})
        scope.start()
        self.addCleanup(scope.stop)
        pieces = load("child_entry_pieces", "piece_workers.py")
        scope = patch.dict(sys.modules, {"jifeng_erp.piece_workers": pieces})
        scope.start()
        self.addCleanup(scope.stop)
        self.allocation = load("child_entry_allocation", "wage_allocation.py")
        reader = load("child_entry_reader", "flow_wage_cost_sources.py")
        scope = patch.dict(sys.modules, {"jifeng_erp.wage_allocation": self.allocation,
                                        "jifeng_erp.flow_wage_cost_sources": reader})
        scope.start()
        self.addCleanup(scope.stop)

        self.fields, self.tables = self.allocation.child_wage_source_contract()
        self.metas = {}
        for kind, fields in self.fields.items(): self.schema(kind, fields)
        for kind, tables in self.tables.items():
            for field, child, fields in tables:
                self.schema(child, fields)
                self.metas[kind][field] = Doc(fieldname=field, fieldtype="Table", options=child, permlevel=0)
        # These are the actual legacy load_source checks, in addition to the
        # child's closed source contract above.
        self.schema("Employee", {"name", "employee_name", "status", "company"})
        model.get_permitted_fields = lambda kind, **kwargs: set(self.metas[kind])
        frappe.get_meta = self.meta
        frappe.get_all = Mock(side_effect=self.records)
        frappe.get_list = Mock(side_effect=self.records)
        old_doc = frappe.get_doc
        frappe.get_doc = Mock(side_effect=lambda kind, name=None, **kwargs:
            Doc(kind) if isinstance(kind, dict) else old_doc(kind, name, **kwargs))
        old_value = frappe.db.get_value
        frappe.db.get_value = Mock(side_effect=lambda kind, *args, **kwargs:
            Doc(name="EMP-SUP", wage_method="计件", modified="2026-10-01 08:00:00")
            if kind == "Jifeng Employee Supplement" else "CNY" if kind == "Company"
            else old_value(kind, *args, **kwargs))
        old_sql = frappe.db.sql
        frappe.db.sql = Mock(side_effect=lambda query, values, **kwargs:
            [] if "tabJifeng Wage" in query else old_sql(query, values, **kwargs))

        h.card.update(posting_date="2026-10-09", total_completed_qty=10, process_loss_qty=0, pending_qty=0,
                      sub_operations=[], time_logs=[
            Doc(name="L-1", employee="E-1", from_time="2026-10-09 09:00:00",
                to_time="2026-10-09 10:00:00", time_in_mins=60, completed_qty=0),
            Doc(name="L-2", employee="E-2", from_time="2026-10-09 09:00:00",
                to_time="2026-10-09 10:00:00", time_in_mins=60, completed_qty=10)])
        h.store["Company", "JF"] = Doc(doctype="Company", name="JF", modified="company-v1", default_currency="CNY")
        h.store["Salary Component", "Piece"] = Doc(doctype="Salary Component", name="Piece", type="Earning", disabled=0)
        h.store["Jifeng Flow Order", "OTHER"] = Doc(doctype="Jifeng Flow Order", name="OTHER", company="JF",
                                                   item_code="OTHER-FG", stock_uom="Nos", bom="OTHER-BOM")
        for index in (1, 2):
            h.store["Employee", f"E-{index}"] = Doc(doctype="Employee", name=f"E-{index}", company="JF",
                                                   status="Active", employee_name=f"员工{index}")
        self.approved = Doc(doctype="Jifeng Wage Allocation", name="A-1", docstatus=1, job_card="JC",
            payroll_date="2026-10-09", salary_component="Piece", employees=[
                Doc(name="R-1", employee="E-1", quantity=6, piece_rate=2, include_time_pay=0, hourly_rate=0, extra_amount=0),
                Doc(name="R-2", employee="E-2", quantity=4, piece_rate=3, include_time_pay=0, hourly_rate=0, extra_amount=0)])
        h.store["Jifeng Wage Allocation", "A-1"] = self.approved
        self.normalize_children()
        self.entry = load("child_wage_entry", "jifeng_erp/doctype/jifeng_wage_entry/jifeng_wage_entry.py")

    def schema(self, kind, fields):
        self.metas.setdefault(kind, {}).update({name: Doc(fieldname=name, fieldtype="Data", permlevel=0)
                                              for name in fields})

    def meta(self, kind):
        fields = self.metas[kind]
        return Doc(get_field=lambda name: fields.get(name),
                   get_permlevel_access=lambda **kwargs: [0])

    def records(self, kind, filters, fields, **kwargs):
        # Enumerate full native children from the same exact parent document,
        # so the actual reader checks membership and all projected values.
        rows = {doc.name: doc for (doctype, _), doc in self.h.store.items() if doctype == kind}
        for parent, tables in self.tables.items():
            for field, child, names in tables:
                if child != kind: continue
                for (doctype, _), doc in self.h.store.items():
                    if doctype != parent: continue
                    for row in doc.get(field) or []:
                        rows[row.name] = row
        selected = [row for row in rows.values() if all(row.get(key) in value[1]
            if isinstance(value, list) and value[0] == "in" else row.get(key) == value
            for key, value in filters.items())]
        return [Doc(row) if fields == ["*"] else Doc({field: row.get(field) for field in fields})
                for row in sorted(selected, key=lambda row: row.name)]

    def normalize_children(self):
        # The existing loss graph does not require all wage-reader identity
        # columns. Populate native child identities before the first read,
        # never as a side effect of a query or source-proof closure.
        for parent, tables in self.tables.items():
            for field, child, names in tables:
                for (doctype, _), doc in self.h.store.items():
                    if doctype == parent:
                        for index, row in enumerate(doc.get(field) or [], 1):
                            if not row.get("name"): row.name = f"{doc.name}-{field}-{index}"
                            row.update(parent=doc.name, parenttype=parent, parentfield=field, idx=index)

    def approve(self):
        self.allocation.validate_allocation(self.approved)

    def wage(self, index=0):
        self.approve()
        values = self.allocation.wage_values(self.approved, self.approved.employees[index])
        return self.entry.JifengWageEntry(doctype="Jifeng Wage Entry", name=f"W-{index + 1}", docstatus=0, **values)

    def independent(self, **changes):
        return self.entry.JifengWageEntry({"doctype": "Jifeng Wage Entry", "name": "MANUAL", "docstatus": 0,
            "employee": "E-1", "company": "JF", "job_card": "JC", "operation": None, "work_order": None,
            "flow_order": None, "stock_uom": None, "salary_component": "Piece", "quantity": 10, "piece_rate": 2,
            "hours": 0, "hourly_rate": 0, "extra_amount": 0, "work_date": "2026-10-09",
            "payroll_date": "2026-10-09", **changes})

    def test_approved_six_and_four_kg_use_child_operation_and_leave_child_flow_empty(self):
        wages = [self.wage(index) for index in (0, 1)]
        for wage in wages:
            wage.validate()
            wage.before_submit()
            self.assertEqual((wage.flow_order, wage.work_order, wage.operation, wage.stock_uom),
                             ("FLOW", "WO", "OPERATION", "Kg"))
        self.assertEqual([wage.quantity for wage in wages], [Decimal(6), Decimal(4)])
        self.assertEqual([wage.total_amount for wage in wages], [Decimal("12.00"), Decimal("12.00")])
        self.assertEqual(sum(wage.quantity for wage in wages), self.h.card.total_completed_qty)
        self.assertFalse(self.h.work.jf_flow_order)
        self.assertEqual((self.h.flow.item_code, self.h.flow.stock_uom, self.h.card.total_completed_qty), ("FG", "Nos", 10))

    def test_entry_cannot_change_approved_share_employee_or_own_amount(self):
        for field, value in (("quantity", 7), ("employee", "E-2"), ("piece_rate", 3)):
            wage = self.wage()
            wage[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "必须与已审核工资分配"):
                wage.validate()

    def test_supervisor_shares_must_equal_child_completed_not_parent_five(self):
        for first, second in ((3, 2), (6, 5)):
            self.approved.employees[0].quantity, self.approved.employees[1].quantity = first, second
            with self.subTest(shares=(first, second)), self.assertRaisesRegex(ValueError, "合计必须等于"):
                self.approve()

    def test_inexact_child_plan_parent_flow_and_inventory_unit_rejected(self):
        for doc, field, wrong in ((self.graph.sub, "production_plan_item", "MISSING"),
                (self.graph.plan_h.row, "jf_flow_order", "OTHER"), (self.graph.sub, "stock_uom", "Nos")):
            previous = doc.get(field)
            doc[field] = wrong
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.independent().validate()
            doc[field] = previous

    def test_entry_wrong_root_flow_unit_or_operation_rejected(self):
        for field, wrong in (("flow_order", "OTHER"), ("stock_uom", "Nos"), ("operation", "父成品工序")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.independent(**{field: wrong}).validate()

    def test_single_worker_child_quantity_cap_and_native_submission_membership(self):
        self.h.card.time_logs = self.h.card.time_logs[:1]
        wage = self.independent()
        wage.validate()
        wage.before_submit()
        self.assertEqual((wage.flow_order, wage.stock_uom, wage.total_amount), ("FLOW", "Kg", Decimal("20.00")))
        with self.assertRaisesRegex(ValueError, "不能超过已完成数量"):
            self.independent(quantity=11).validate()
        self.h.card.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交的报工单"):
            wage.before_submit()

    def test_manual_without_job_card_and_old_direct_or_parent_plan_paths_remain(self):
        manual = self.independent(job_card=None, stock_uom="Kg", quantity=2)
        manual.validate()
        manual.before_submit()
        self.assertEqual(manual.total_amount, Decimal("4.00"))
        self.assertFalse(manual.work_order)
        self.assertFalse(manual.flow_order)
        self.h.card.time_logs = self.h.card.time_logs[:1]
        self.h.card.update(production_item="FG", stock_uom="Nos", for_quantity=5, total_completed_qty=5)
        self.h.work.update(production_item="FG", stock_uom="Nos", bom_no="BOM", qty=5)
        for plan in (None, "PLAN"):
            self.h.work.update(production_plan=plan, production_plan_sub_assembly_item=None,
                               production_plan_item="PLAN-ROW" if plan else None, jf_flow_order="FLOW")
            wage = self.independent(quantity=5)
            wage.validate()
            wage.before_submit()
            self.assertEqual((wage.flow_order, wage.work_order, wage.stock_uom, wage.total_amount),
                             ("FLOW", "WO", "Nos", Decimal("10.00")))
            self.assertIsNone(self.allocation.resolve_wage_work_source(self.h.work, card=self.h.card))


if __name__ == "__main__":
    unittest.main()
