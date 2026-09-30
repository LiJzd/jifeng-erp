import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class TestPersonnelUiPermissions(unittest.TestCase):
    def setUp(self):
        self.fields = [types.SimpleNamespace(fieldname=name, label=name, fieldtype="Link",
                       options="Department", hidden=0, read_only=0)
                       for name in ("department", "designation", "branch", "grade", "reports_to",
                                    "employment_type", "bank_ac_no", "date_of_joining")]
        self.readable = {field.fieldname for field in self.fields}
        self.writable = set(self.readable)
        self.denials = set()
        self.values = {"department": "DEPT-A", "bank_ac_no": "PRIVATE-BANK", "date_of_joining": "2024-01-01"}
        meta = types.SimpleNamespace(fields=self.fields, get_field=lambda name: next(
            (field for field in self.fields if field.fieldname == name), None))
        document = types.SimpleNamespace(meta=meta, get=self.values.get,
            check_permission=Mock(side_effect=lambda permission: self._check(permission)))
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_doc = Mock(return_value=document)
        self.frappe.get_meta = Mock(return_value=meta)
        self.frappe.has_permission = Mock(return_value=True)
        self.frappe.whitelist = lambda: lambda method: method
        self.frappe._ = lambda value: value
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=lambda doctype, permission_type, **kwargs:
            self.readable if permission_type == "read" else self.writable)
        utils = types.ModuleType("frappe.utils")
        utils.formatdate = lambda value: "DATE:" + str(value)
        utils.format_datetime = lambda value: "DATETIME:" + str(value)
        changes = types.ModuleType("jifeng_erp.personnel_changes")
        changes.JOB_FIELDS = {"department", "designation", "branch", "grade", "reports_to", "employment_type"}
        path = Path(__file__).parents[1] / "jifeng_erp/personnel_ui.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                     "frappe.utils": utils, "jifeng_erp.personnel_changes": changes}):
            spec = importlib.util.spec_from_file_location("personnel_ui_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def _check(self, permission):
        if permission in self.denials:
            raise PermissionError("native Employee " + permission + " denied")

    def test_rpc_denies_hidden_field_before_reading_employee_value(self):
        self.readable.remove("bank_ac_no")
        with self.assertRaises(PermissionError):
            self.module.get_employee_field_property("EMP-A", "bank_ac_no")
        self.frappe.get_doc.assert_not_called()

    def test_rpc_requires_complete_native_employee_read(self):
        self.denials.add("read")
        with self.assertRaises(PermissionError):
            self.module.get_employee_field_property("HIDDEN-EMPLOYEE", "department")

    def test_readable_rpc_retains_native_response_shape_and_date_format(self):
        self.assertEqual(self.module.get_employee_field_property("EMP-A", "department"), {
            "value": "DEPT-A", "datatype": "Link", "label": "department", "options": "Department"})
        self.fields[-1].fieldtype = "Date"
        self.assertEqual(self.module.get_employee_field_property("EMP-A", "date_of_joining")["value"],
                         "DATE:2024-01-01")

    def test_job_options_exclude_private_readonly_and_unwritable_fields(self):
        self.writable.remove("designation")
        self.fields[2].hidden = 1
        self.fields[3].read_only = 1
        self.assertEqual([field["value"] for field in self.module.get_job_properties("EMP-A", "Employee Transfer")],
                         ["department", "reports_to", "employment_type"])

    def test_job_options_require_source_create_and_employee_read_write(self):
        for permission in ("read", "write"):
            self.denials = {permission}
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.module.get_job_properties("EMP-A", "Employee Promotion")
        self.denials.clear()
        self.frappe.has_permission.return_value = False
        with self.assertRaises(PermissionError):
            self.module.get_job_properties("EMP-A", "Employee Promotion")

    def test_unrelated_doctype_and_missing_native_rpc_arguments_do_not_return_values(self):
        with self.assertRaises(ValueError):
            self.module.get_job_properties("EMP-A", "Employee")
        self.assertIsNone(self.module.get_employee_field_property("", "bank_ac_no"))
        self.assertIsNone(self.module.get_employee_field_property("EMP-A", "unknown_field"))


if __name__ == "__main__":
    unittest.main()
