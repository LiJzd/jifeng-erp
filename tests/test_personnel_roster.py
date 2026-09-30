"""Native employee scope must not depend on optional/private HR supplements."""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def has_permission(self, permission):
        return self.get(permission + "_allowed", True)

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("没有档案权限")


class TestPersonnelRoster(unittest.TestCase):
    def setUp(self):
        self.documents, self.listed, self.permissions = {}, {}, {"Employee": True,
            "Jifeng Employee Supplement": False}
        frappe = types.ModuleType("frappe")
        frappe._dict, frappe.PermissionError = Record, PermissionError
        frappe.session = Record(user="hr-user@example.invalid")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.has_permission = Mock(side_effect=lambda doctype, permission: self.permissions.get(doctype, False))
        frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[(doctype, name)])
        frappe.get_list = Mock(side_effect=self.native_list)
        self.frappe = frappe
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, kind="Data", options=None, width=130: {
            "fieldname": field, "label": label, "fieldtype": kind, "options": options, "width": width}
        with patch.dict(sys.modules, {"frappe": frappe, "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location("personnel_roster_under_test",
                                                          ROOT / "jifeng_erp/personnel_roster.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.add("Company", "JF", select_allowed=True, read_allowed=False)
        self.add("Company", "OTHER", select_allowed=False)
        self.add("Department", "工厂", company="JF")
        self.add("Department", "外部部门", company="OTHER")
        self.add("Designation", "组长")
        self.employee = self.add("Employee", "EMP-1", employee_number="001", employee_name="甲",
            company="JF", department="工厂", designation="组长", status="Active",
            date_of_joining="2026-01-01", relieving_date=None,
            identity_number="PRIVATE-ID", cell_number="PRIVATE-PHONE", bank_ac_no="PRIVATE-BANK")
        self.listed["Employee"] = ["EMP-1"]

    def add(self, doctype, name, **fields):
        record = Record(doctype=doctype, name=name, **fields)
        self.documents[(doctype, name)] = record
        return record

    def native_list(self, doctype, filters, fields, **kwargs):
        rows = []
        for name in self.listed.get(doctype, []):
            document = self.documents[(doctype, name)]
            if all(document.get(field) in value[1] if isinstance(value, list) and value[0] == "in"
                   else document.get(field) == value for field, value in filters.items()):
                rows.append(Record({field: document.get(field) for field in fields}))
        return rows

    def report(self, **filters):
        return self.module.personnel_roster({"company": "JF", **filters})

    def supplement(self, **fields):
        self.permissions[self.module.SUPPLEMENT] = True
        document = self.add(self.module.SUPPLEMENT, "SUPP-1", employee="EMP-1", company="JF",
                            term_type="长期", wage_method="计件", identity_number="PRIVATE-SUPP-ID",
                            bank_account_name="PRIVATE-SUPP-BANK", **fields)
        self.listed[self.module.SUPPLEMENT] = [document.name]
        return document

    def test_guest_cannot_query_even_with_an_incorrect_type_permission(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.get_list.assert_not_called()

    def test_employee_select_without_read_is_insufficient(self):
        self.permissions["Employee"] = False
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.get_doc.assert_not_called()
        self.frappe.get_list.assert_not_called()

    def test_company_selection_does_not_require_company_full_read(self):
        _, rows = self.report()
        self.assertEqual(rows[0]["employee"], "EMP-1")
        self.assertFalse(self.documents[("Company", "JF")].has_permission("read"))
        self.frappe.get_doc.assert_any_call("Company", "JF")

    def test_denied_company_cannot_reach_employee_queries(self):
        with self.assertRaises(PermissionError):
            self.report(company="OTHER")
        self.frappe.get_list.assert_not_called()

    def test_company_is_required_and_structured_values_are_rejected(self):
        for value in (None, "", " ", ["JF"], {"name": "JF"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(company=value)
        self.frappe.get_list.assert_not_called()

    def test_native_list_scope_and_post_load_employee_read_both_apply(self):
        self.add("Employee", "EMP-HIDDEN", company="JF", employee_name="隐私", status="Active")
        _, rows = self.report()  # Hidden native User Permission row is absent from get_list.
        self.assertEqual([row["employee"] for row in rows], ["EMP-1"])
        self.assertNotIn(("Employee", "EMP-HIDDEN"), [call.args for call in self.frappe.get_doc.call_args_list])
        self.employee.read_allowed = False
        _, rows = self.report()
        self.assertEqual(rows, [])

    def test_source_company_change_between_query_and_load_is_omitted(self):
        original = self.native_list
        def query_then_change(doctype, **kwargs):
            rows = original(doctype, **kwargs)
            if doctype == "Employee":
                self.employee.company = "OTHER"
            return rows
        self.frappe.get_list.side_effect = query_then_change
        _, rows = self.report()
        self.assertEqual(rows, [])

    def test_department_designation_status_are_exact_native_filters(self):
        columns, rows = self.report(department="工厂", designation="组长", status="Active")
        self.assertEqual(len(rows), 1)
        self.frappe.get_list.assert_called_once_with("Employee", filters={
            "company": "JF", "department": "工厂", "designation": "组长", "status": "Active"},
            fields=list(self.module.EMPLOYEE_FIELDS), order_by="employee_name asc, name asc", limit_page_length=0)
        self.assertEqual(next(column["options"] for column in columns if column["fieldname"] == "status"),
                         "Active\nInactive\nSuspended\nLeft")
        self.assertEqual(self.report(status="Left")[1], [])

    def test_foreign_or_unselectable_department_and_designation_are_denied(self):
        with self.assertRaises(ValueError):
            self.report(department="外部部门")
        self.documents[("Department", "工厂")].select_allowed = False
        with self.assertRaises(PermissionError):
            self.report(department="工厂")
        self.documents[("Designation", "组长")].select_allowed = False
        with self.assertRaises(PermissionError):
            self.report(designation="组长")
        self.frappe.get_list.assert_not_called()

    def test_invalid_native_status_and_structured_filters_are_rejected(self):
        for filters in ({"status": "在职"}, {"status": "Unknown"}, {"status": ["Active"]},
                        {"department": ["工厂"]}, {"designation": {"name": "组长"}}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.report(**filters)
        self.frappe.get_list.assert_not_called()

    def test_all_four_current_statuses_have_no_active_only_default(self):
        for index, status in enumerate(self.module.STATUSES):
            name = "STATUS-" + str(index)
            self.add("Employee", name, company="JF", employee_name=name, status=status)
            self.listed["Employee"].append(name)
        _, rows = self.report()
        self.assertEqual({row["status"] for row in rows}, set(self.module.STATUSES))
        self.assertNotIn("status", self.frappe.get_list.call_args.kwargs["filters"])

    def test_optional_native_fields_stay_empty_and_dates_are_not_inferred(self):
        self.employee.update(employee_number=None, department=None, designation=None,
                             date_of_joining="2030-01-01", relieving_date=None)
        _, rows = self.report()
        self.assertIsNone(rows[0]["employee_number"])
        self.assertEqual(rows[0]["employee"], "EMP-1")
        self.assertIsNone(rows[0]["department"])
        self.assertIsNone(rows[0]["designation"])
        self.assertIsNone(rows[0]["relieving_date"])
        self.assertEqual(rows[0]["date_of_joining"], "2030-01-01")
        self.assertEqual(rows[0]["status"], "Active")

    def test_hr_user_without_supplement_access_gets_only_native_roster(self):
        self.add(self.module.SUPPLEMENT, "HIDDEN-SUPP", employee="EMP-1", company="JF", term_type="长期")
        columns, rows = self.report()
        self.assertNotIn("term_type", {column["fieldname"] for column in columns})
        self.assertNotIn("wage_method", rows[0])
        self.frappe.get_list.assert_called_once()
        self.assertNotIn(self.module.SUPPLEMENT, [call.args[0] for call in self.frappe.get_doc.call_args_list])

    def test_readable_supplement_exposes_only_classifications_not_private_fields(self):
        self.supplement()
        columns, rows = self.report()
        self.assertEqual((rows[0]["term_type"], rows[0]["wage_method"]), ("长期", "计件"))
        self.assertEqual(next(column["label"] for column in columns if column["fieldname"] == "term_type"),
                         "人员类型（旧档案）")
        self.assertNotIn("PRIVATE", json.dumps(rows))
        self.assertEqual(set(rows[0]), {"employee", *self.module.EMPLOYEE_FIELDS[1:], "term_type", "wage_method"})
        for call in self.frappe.get_list.call_args_list:
            allowed = self.module.EMPLOYEE_FIELDS if call.args[0] == "Employee" else self.module.SUPPLEMENT_FIELDS
            self.assertEqual(call.kwargs["fields"], list(allowed))

    def test_hidden_supplement_is_indistinguishable_from_absent_supplement(self):
        document = self.supplement(read_allowed=False)
        columns, hidden = self.report()
        self.listed[self.module.SUPPLEMENT] = []
        absent_columns, absent = self.report()
        self.assertEqual(hidden, absent)
        self.assertEqual(columns, absent_columns)
        self.assertIsNone(hidden[0]["term_type"])
        self.assertNotIn(document.name, json.dumps(hidden))

    def test_supplement_company_change_and_employee_scope_cannot_leak(self):
        document = self.supplement()
        original = self.native_list
        def query_then_change(doctype, **kwargs):
            rows = original(doctype, **kwargs)
            if doctype == self.module.SUPPLEMENT:
                document.company = "OTHER"
            return rows
        self.frappe.get_list.side_effect = query_then_change
        _, rows = self.report()
        self.assertIsNone(rows[0]["term_type"])
        self.assertEqual(rows[0]["employee"], "EMP-1")

    def test_supplement_permission_loss_does_not_block_native_employee_rows(self):
        self.supplement()
        original = self.native_list
        def lose_access(doctype, **kwargs):
            if doctype == self.module.SUPPLEMENT:
                raise PermissionError("补充档案已撤权")
            return original(doctype, **kwargs)
        self.frappe.get_list.side_effect = lose_access
        columns, rows = self.report()
        self.assertEqual(rows[0]["employee"], "EMP-1")
        self.assertNotIn("term_type", rows[0])
        self.assertNotIn("term_type", {column["fieldname"] for column in columns})

    def test_optional_empty_supplement_values_are_not_guessed(self):
        document = self.supplement()
        document.term_type, document.wage_method = None, ""
        _, rows = self.report()
        self.assertIsNone(rows[0]["term_type"])
        self.assertEqual(rows[0]["wage_method"], "")

    def test_report_definition_uses_native_employee_and_hr_roles(self):
        directory = ROOT / "jifeng_erp/jifeng_erp/report/jifeng_personnel_roster"
        definition = json.loads((directory / "jifeng_personnel_roster.json").read_text(encoding="utf-8"))
        self.assertEqual(definition["ref_doctype"], "Employee")
        self.assertEqual({row["role"] for row in definition["roles"]}, {"HR User", "HR Manager"})
        self.assertEqual(definition["prepared_report"], 0)
        self.assertEqual(definition["add_total_row"], 0)
        self.assertNotIn("history", (directory / "jifeng_personnel_roster.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
