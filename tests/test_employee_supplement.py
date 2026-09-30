"""Verify the employee extension's private attachment boundary."""

import importlib.util
from datetime import date
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


class TestEmployeeSupplement(unittest.TestCase):
    def setUp(self):
        self.employee = types.SimpleNamespace(
            employee_name="测试员工", company="测试公司", department="生产部",
            check_permission=Mock(),
        )
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_doc = Mock(return_value=self.employee)
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.frappe.db = types.SimpleNamespace(exists=Mock(return_value=True))
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {})
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: date.fromisoformat(str(value))
        archive = types.ModuleType("jifeng_erp.personnel_archive")
        archive.is_archive_save = Mock(return_value=False)
        self.archive = archive
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                      "frappe.model.document": document, "frappe.utils": utils,
                                      "jifeng_erp.personnel_archive": archive}):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "jifeng_erp" / "doctype" / "jifeng_employee_supplement" / "jifeng_employee_supplement.py"
            spec = importlib.util.spec_from_file_location("employee_supplement_under_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.module = module
        self.supplement = module.JifengEmployeeSupplement()
        self.supplement.doctype = "Jifeng Employee Supplement"
        self.supplement.name = "HR-0001"
        self.supplement.employee = "HR-0001"
        self.supplement.identity_number = "  TEST-ID  "
        self.supplement.bank_account_name = "  测试户名  "
        self.supplement.identity_image = "/private/files/test-id.png"

    def test_private_file_attached_to_same_record_is_accepted(self):
        self.supplement.validate()
        self.assertEqual(self.supplement.identity_number, "TEST-ID")
        self.assertEqual(self.supplement.employee_name, "测试员工")
        self.frappe.db.exists.assert_called_once_with("File", {
            "file_url": "/private/files/test-id.png", "is_private": 1,
            "attached_to_doctype": "Jifeng Employee Supplement", "attached_to_name": "HR-0001",
        })

    def test_public_file_is_rejected(self):
        self.supplement.identity_image = "/files/public-id.png"
        with self.assertRaisesRegex(ValueError, "私有附件"):
            self.supplement.validate()
        self.frappe.db.exists.assert_not_called()

    def test_private_file_attached_elsewhere_is_rejected(self):
        self.frappe.db.exists.return_value = False
        with self.assertRaisesRegex(ValueError, "当前档案"):
            self.supplement.validate()

    def test_employee_update_finds_supplement_by_link(self):
        supplement = Mock()
        self.frappe.db.get_value = Mock(return_value="OLD-EMPLOYEE-ID")
        self.frappe.get_doc = Mock(return_value=supplement)
        self.module.refresh_employee_supplement(types.SimpleNamespace(
            name="NEW-EMPLOYEE-ID", employee_name="新姓名", company="新公司", department="新部门",
        ))
        self.frappe.db.get_value.assert_called_once_with(
            "Jifeng Employee Supplement", {"employee": "NEW-EMPLOYEE-ID"}, "name",
        )
        self.frappe.get_doc.assert_called_once_with("Jifeng Employee Supplement", "OLD-EMPLOYEE-ID")
        self.assertEqual(supplement.company, "新公司")
        supplement.save.assert_called_once_with(ignore_permissions=True)

    def test_classic_api_does_not_use_the_privileged_refresh(self):
        self.archive.is_archive_save.return_value = True
        self.frappe.db.get_value = Mock()
        self.module.refresh_employee_supplement(self.employee)
        self.frappe.db.get_value.assert_not_called()
        self.frappe.get_doc.assert_not_called()

    def test_archived_dates_are_checked_without_formal_personnel_actions(self):
        row = types.SimpleNamespace(from_date="2026-03-02", to_date="2026-03-01")
        row.get = lambda name: getattr(row, name, None)
        self.supplement.salary_history = [row]
        with self.assertRaisesRegex(ValueError, "开始日期"):
            self.supplement.validate()


if __name__ == "__main__":
    unittest.main()
