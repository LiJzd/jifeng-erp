"""One approved allocation, exact wages, native salary creation and atomic failure."""

import ast
from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_wage_allocation as allocation_fixture

Doc = allocation_fixture.Doc


ROOT = Path(__file__).parents[1] / "jifeng_erp"


class TestAllocationProcessing(unittest.TestCase):
    def setUp(self):
        self.base = allocation_fixture.TestWageAllocation(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.frappe, self.docs = self.base.frappe, self.base.docs
        self.events, self.denied, self.hidden = [], set(), set()
        self.clock, self.fail_wage = 0, None
        self.frappe.ValidationError = ValueError
        self.frappe._dict = lambda value: Doc(**value)
        self.frappe.whitelist = lambda **kwargs: lambda method: method
        outer = self

        def as_dict(doc):
            def plain(value):
                if isinstance(value, Doc): return as_dict(value)
                if isinstance(value, list): return [plain(item) for item in value]
                return value
            return {key: plain(value) for key, value in vars(doc).items()
                    if not callable(value) and key not in ("hidden", "meta")}
        def has_permission(doc, permission):
            return ((doc.get("doctype"), doc.get("name"), permission) not in outer.denied
                    and (doc.get("doctype"), doc.get("employee"), permission) not in outer.denied
                    and (doc.get("doctype"), doc.get("name")) not in outer.hidden)
        def check_permission(doc, permission):
            if not has_permission(doc, permission): raise PermissionError("source denied")
        for name, method in (("as_dict", as_dict), ("has_permission", has_permission), ("check_permission", check_permission)):
            scope = patch.object(Doc, name, method, create=True)
            scope.start(); self.addCleanup(scope.stop)

        self.base.card.for_quantity, self.base.card.total_completed_qty = 10, 7
        self.base.card.process_loss_qty, self.base.card.pending_qty = 1, 2
        self.base.card.time_logs[-1].completed_qty = 7
        self.allocation = self.base.allocation
        self.allocation.employees[0].quantity, self.allocation.employees[1].quantity = 3, 4
        for row in self.allocation.employees: row.piece_rate = 2
        self.base.approve()
        self.frappe.session.user = "qa@example.test"
        self.allocation.doctype, self.allocation.modified = "Jifeng Wage Allocation", "2026-10-09 08:00:00.123456"
        for (doctype, name), doc in self.docs.items():
            doc.doctype, doc.name = doctype, name
            if not doc.get("modified"): doc.modified = "2026-10-09 07:00:00"
        self.docs[("Company", "C-1")].default_currency = "CNY"
        self.wages = []
        for index, row in enumerate(self.allocation.employees):
            wage = Doc(doctype="Jifeng Wage Entry", name="W" + str(index), docstatus=0,
                       modified="2026-10-09 08:00:01", **self.base.module.wage_values(self.allocation, row))
            row.wage_entry = wage.name
            self.docs[(wage.doctype, wage.name)] = wage
            self.wages.append(wage)
            assignment = Doc(doctype="Salary Structure Assignment", name="SSA" + str(index),
                employee=wage.employee, company=wage.company, docstatus=1, from_date="2026-01-01",
                currency="CNY", modified="2026-10-09 07:00:00")
            self.docs[(assignment.doctype, assignment.name)] = assignment

        # Execute the existing single-wage Additional Salary method itself.
        tree = ast.parse((ROOT / "jifeng_erp/doctype/jifeng_wage_entry/jifeng_wage_entry.py").read_text(encoding="utf-8"))
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        nodes = []
        for name in ("make_additional_salary", "before_submit"):
            node = deepcopy(next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == name))
            node.decorator_list = []
            nodes.append(node)
        native = {"frappe": self.frappe, "active_additional_salary": sys.modules["jifeng_erp.payroll"].active_additional_salary}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "existing_wage_entry.py", "exec"), native)

        def submit(wage):
            self.events.append(("submit", wage.name))
            wage.check_permission("write"); wage.check_permission("submit")
            if wage.name == self.fail_wage: raise RuntimeError("second native submit failed")
            native["before_submit"](wage)
            self.base.module.validate_allocated_wage(wage)
            wage.docstatus = 1
            self.clock += 1; wage.modified = "2026-10-09 09:00:%02d" % self.clock
            return wage

        def reload(doc): return doc
        for wage in self.wages:
            wage.submit = types.MethodType(submit, wage)
            wage.reload = types.MethodType(reload, wage)
            wage.make_additional_salary = types.MethodType(native["make_additional_salary"], wage)

        def get_doc(doctype, name=None, **kwargs):
            if isinstance(doctype, dict):
                values = deepcopy(doctype)
                if values.get("doctype") == "Jifeng Wage Allocation":
                    values["employees"] = [Doc(**row) for row in values["employees"]]
                doc = Doc(**values)
                if doc.get("doctype") == "Additional Salary":
                    doc.insert = types.MethodType(self.insert_salary, doc)
                return doc
            return self.docs[(doctype, name)]
        self.frappe.get_doc = get_doc
        self.frappe.db.sql = self.sql
        old_get_value = self.frappe.db.get_value
        def get_value(doctype, filters, fields=None, **kwargs):
            if doctype == "Salary Structure Assignment":
                found = [doc for (kind, _), doc in self.docs.items() if kind == doctype
                    and doc.employee == filters["employee"] and doc.company == filters["company"]
                    and doc.docstatus == 1 and str(doc.from_date) <= str(filters["from_date"][1])]
                return sorted(found, key=lambda row: row.from_date, reverse=True)[0].currency if found else None
            if doctype == "Company": return self.docs[(doctype, filters)].default_currency
            if doctype == "Jifeng Wage Entry": return filters
            return old_get_value(doctype, filters, fields, **kwargs)
        self.frappe.db.get_value = get_value

        # Real field permission helper, with native field/table fixtures.
        from jifeng_erp.wage_allocation import SOURCE_FIELDS, WAGE_FIELDS
        metas = self.base.source_metas
        def add_meta(doctype, names, tables=None):
            fields = [Doc(fieldname=name, fieldtype="Data", permlevel=0, mask=0) for name in set(names)]
            for field in fields:
                if tables and field.fieldname in tables: field.fieldtype, field.options = "Table", tables[field.fieldname]
            meta = Doc(fields=fields, read_levels=[0], mask_levels=[0])
            meta.get_field = lambda name: next((field for field in fields if field.fieldname == name), None)
            meta.get_permlevel_access = lambda permission_type="read", **kwargs: meta.mask_levels if permission_type == "mask" else meta.read_levels
            metas[doctype] = meta
        add_meta("Jifeng Wage Allocation", (*SOURCE_FIELDS, "modified", "docstatus", "payroll_date", "salary_component", "allocated_qty", "total_amount", "employees"),
                 {"employees": "Jifeng Wage Allocation Employee"})
        add_meta("Jifeng Wage Entry", ("name", "modified", "docstatus", *WAGE_FIELDS))
        add_meta("Salary Structure Assignment", ("name", "modified", "employee", "company", "docstatus", "from_date", "currency"))
        spec = importlib.util.spec_from_file_location("allocation_processing_test", ROOT / "wage_allocation_processing.py")
        self.subject = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.subject)
        add_meta("Jifeng Wage Allocation Employee", self.subject.ROW_FIELDS)
        add_meta("Additional Salary", self.subject.SALARY_FIELDS)

    def insert_salary(self, salary):
        salary.check_permission("create")
        salary.docstatus, salary.disabled = 0, 0
        salary.name = "AS" + str(sum(kind == "Additional Salary" for kind, _ in self.docs))
        salary.modified = "2026-10-09 09:01:00"
        sys.modules["jifeng_erp.payroll"].validate_wage_additional_salary(salary)
        self.docs[(salary.doctype, salary.name)] = salary
        self.events.append(("insert_salary", salary.name, salary.employee))
        return salary

    def sql(self, query, values, as_dict=False):
        if "NOWAIT" in query: self.assertIn("FOR UPDATE", query)
        result = []
        if "WHERE name=%s" in query:
            kind = query.split("`tab", 1)[1].split("`", 1)[0]
            doc = self.docs.get((kind, values[0])); result = [doc] if doc else []
        elif "FROM `tabJifeng Wage Entry`" in query:
            if "WHERE wage_allocation=%s" in query:
                result = [doc for (kind, _), doc in self.docs.items() if kind == "Jifeng Wage Entry" and doc.wage_allocation == values[0]]
            elif "wage_allocation_employee = %s" in query:
                result = [doc for (kind, _), doc in self.docs.items() if kind == "Jifeng Wage Entry"
                    and doc.wage_allocation == values[0] and doc.wage_allocation_employee == values[1]
                    and doc.docstatus < 2 and (len(values) < 3 or doc.name != values[2])]
            else:
                result = [doc for (kind, _), doc in self.docs.items() if kind == "Jifeng Wage Entry"
                    and doc.job_card == values[0] and doc.docstatus < 2 and (len(values) < 2 or doc.name != values[1])]
        elif "FROM `tabAdditional Salary`" in query:
            result = [doc for (kind, _), doc in self.docs.items() if kind == "Additional Salary"
                and doc.ref_doctype == "Jifeng Wage Entry" and doc.ref_docname == values[0]
                and ("docstatus < 2" not in query or doc.docstatus < 2)
                and (len(values) < 2 or doc.name != values[1])]
        elif "FROM `tabSalary Structure Assignment`" in query and "WHERE employee=" in query:
            result = sorted([doc for (kind, _), doc in self.docs.items() if kind == "Salary Structure Assignment"
                and (doc.employee, doc.company) == tuple(values[:2]) and doc.docstatus == 1 and str(doc.from_date) <= str(values[2])],
                key=lambda doc: doc.from_date, reverse=True)
        elif "tabJifeng Wage Allocation`" in query:
            result = []  # _active_allocations excludes this allocation.
        if as_dict: return result
        return [(row.name,) for row in result]

    def inspect(self):
        return self.subject.inspect_allocation_processing(self.allocation.name, self.allocation.modified)

    def process(self, view=None):
        view = view or self.inspect()
        return self.subject.process_allocation_wages(self.allocation.name, self.allocation.modified, view["snapshot"])

    def add_salary(self, index, status):
        wage = self.wages[index]; wage.docstatus = 1
        salary = self.frappe.get_doc({"doctype": "Additional Salary", "employee": wage.employee,
            "company": wage.company, "salary_component": wage.salary_component, "currency": "CNY",
            "amount": wage.total_amount, "payroll_date": wage.payroll_date, "is_recurring": 0,
            "overwrite_salary_structure_amount": 0, "ref_doctype": wage.doctype, "ref_docname": wage.name})
        salary.insert(); salary.docstatus = status
        self.events.clear(); return salary

    def test_loss_and_pending_not_paid_two_wages_created_as_drafts_then_repeat(self):
        view = self.inspect()
        self.assertEqual(tuple(Decimal(view[field]) for field in ("source_completed_qty", "allocated_qty", "total_amount")), (7, 7, 14))
        self.assertEqual(Decimal(view["production_quantities"]["pending_qty"]), 2)
        self.assertEqual(self.events, [])
        result = self.process(view)
        self.assertEqual(result["submitted_wages"], ["W0", "W1"])
        self.assertEqual(len(result["created_salaries"]), 2)
        self.assertEqual([doc.docstatus for (kind, _), doc in self.docs.items() if kind == "Additional Salary"], [0, 0])
        before = deepcopy(self.events)
        self.assertTrue(self.process()["already_completed"])
        self.assertEqual(self.events, before)

    def test_partial_existing_draft_and_submitted_salary_are_reused(self):
        first, second = self.add_salary(0, 0), self.add_salary(1, 1)
        result = self.process()
        self.assertTrue(result["already_completed"])
        self.assertEqual([row["additional_salary"] for row in result["current"]["rows"]], [first.name, second.name])
        self.assertEqual(self.events, [])

    def test_only_cancelled_salary_is_visible_and_blocks_reissue(self):
        salary = self.add_salary(0, 2)
        view = self.inspect()
        self.assertFalse(view["can_process"])
        self.assertEqual(view["rows"][0]["cancelled_salaries"], [salary.name])
        with self.assertRaisesRegex(ValueError, "不自动重发"): self.process(view)
        self.assertEqual(self.events, [])

    def test_zero_amount_row_is_displayed_but_not_submitted_or_recreated(self):
        row = self.allocation.employees[1]
        row.piece_rate = row.piece_amount = row.total_amount = 0
        self.allocation.total_amount = 6
        del self.docs[("Jifeng Wage Entry", "W1")]
        row.wage_entry = None
        view = self.inspect()
        self.assertEqual(view["rows"][1]["wage_status"], "零金额无需计薪")
        result = self.process(view)
        self.assertEqual(result["submitted_wages"], ["W0"])
        self.assertEqual(len(result["created_salaries"]), 1)
        self.assertEqual(Decimal(result["current"]["total_amount"]), 6)

    def test_cancelled_wage_with_active_salary_not_hidden_behind_replacement(self):
        salary = self.add_salary(0, 0)
        old = self.wages[0]
        old.docstatus = 2
        replacement = Doc(**old.as_dict()); replacement.name, replacement.docstatus = "NEW", 0
        self.docs[(replacement.doctype, replacement.name)] = replacement
        self.allocation.employees[0].wage_entry = replacement.name
        with self.assertRaisesRegex(ValueError, "有效工资附加项"): self.inspect()
        self.assertEqual(salary.docstatus, 0)
        self.assertEqual(self.events, [])

    def test_permissions_of_second_wage_and_new_salary_checked_before_any_write(self):
        for denial in (("Jifeng Wage Entry", "W1", "submit"), ("Additional Salary", "E-2", "create")):
            self.denied.add(denial)
            view = self.inspect(); self.assertFalse(view["can_process"])
            with self.assertRaises(PermissionError): self.process(view)
            self.assertEqual(self.events, [])
            self.denied.clear()

    def test_hidden_wage_or_salary_cannot_be_skipped(self):
        salary = self.add_salary(0, 0)
        for target in (("Jifeng Wage Entry", "W1"), ("Additional Salary", salary.name)):
            self.hidden.add(target)
            with self.assertRaises(PermissionError): self.inspect()
            self.hidden.clear()

    def test_confirmed_downstream_change_rejected_before_write(self):
        view = self.inspect(); self.wages[1].docstatus = 1
        with self.assertRaisesRegex(ValueError, "状态已变化"): self.process(view)
        self.assertEqual(self.events, [])

    def test_current_company_currency_change_not_hidden_by_saved_allocation(self):
        self.docs[("Company", "C-1")].default_currency = "USD"
        with self.assertRaisesRegex(ValueError, "本位币"): self.inspect()

    def test_wrong_link_or_duplicate_active_source_rejected(self):
        self.allocation.employees[1].wage_entry = "W0"
        with self.assertRaisesRegex(ValueError, "准确生成"): self.inspect()
        self.allocation.employees[1].wage_entry = "W1"
        duplicate = Doc(**self.wages[1].as_dict()); duplicate.name = "DUP"
        self.docs[(duplicate.doctype, duplicate.name)] = duplicate
        with self.assertRaisesRegex(ValueError, "重复有效计薪"): self.inspect()

    def test_second_native_failure_propagates_for_request_atomic_rollback(self):
        view = self.inspect(); self.fail_wage = "W1"
        # Frappe owns the transaction rollback. Emulate that request boundary,
        # not a catch-and-continue inside the batch implementation.
        originals = {key: deepcopy(doc.as_dict()) for key, doc in self.docs.items()}
        with self.assertRaisesRegex(RuntimeError, "second native"):
            try: self.process(view)
            except Exception:
                for key in list(self.docs):
                    if key not in originals: del self.docs[key]
                    else:
                        for field, value in originals[key].items(): setattr(self.docs[key], field, value)
                raise
        self.assertEqual([wage.docstatus for wage in self.wages], [0, 0])
        self.assertFalse(any(kind == "Additional Salary" for kind, _ in self.docs))


if __name__ == "__main__": unittest.main()
