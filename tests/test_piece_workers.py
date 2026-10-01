"""Purpose-bound qualification projection, current guards and historical evidence."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1] / "jifeng_erp"


class Doc(types.SimpleNamespace):
    def check_permission(self, permission):
        if permission in getattr(self, "denied", ()):
            raise PermissionError("native document denied")


class Row(dict):
    __getattr__ = dict.__getitem__


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPieceWorkers(unittest.TestCase):
    def setUp(self):
        self.fields = {"name", "employee_number", "employee_name", "company", "department", "status"}
        self.meta = types.SimpleNamespace(get_permitted_fieldnames=lambda **kwargs: self.fields)
        self.person = Doc(name="E-1", company="C-1", status="Active", employee_name="合成人员", meta=self.meta)
        self.company = Doc()
        self.probe = Doc()
        self.source = Doc(name="SUP-PRIVATE", wage_method="计件", modified=datetime.datetime(2026, 10, 1, 9, 0, 0, 123456))
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = types.SimpleNamespace(user="HR-USER")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda: (lambda fn: fn)
        self.frappe.has_permission = Mock(side_effect=lambda doctype, permission: doctype != "Jifeng Employee Supplement")
        self.frappe.db = types.SimpleNamespace(get_value=Mock(side_effect=lambda *args, **kwargs: self.source))
        self.frappe.get_meta = Mock(return_value=self.meta)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(return_value=[Row(name="E-1", employee_name="合成人员", company="C-1", status="Active")])
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped list forbidden"))
        scope = patch.dict(sys.modules, {"frappe": self.frappe})
        scope.start()
        self.addCleanup(scope.stop)
        payroll = load("piece_payroll", "payroll.py")
        scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        scope.start()
        self.addCleanup(scope.stop)
        self.module = load("piece_workers_under_test", "piece_workers.py")

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            self.assertEqual(doctype, {"doctype": "Jifeng Wage Entry", "company": "C-1", "employee": "E-1"})
            return self.probe
        if doctype == "Company":
            return self.company
        if doctype == "Employee":
            return self.person
        raise AssertionError("Qualification must not load private HR archive: " + doctype)

    def test_hr_user_without_supplement_read_gets_only_boolean_projection(self):
        self.assertFalse(self.frappe.has_permission("Jifeng Employee Supplement", "read"))
        self.assertEqual(self.module.select_piece_worker("C-1", "E-1"),
                         {"company": "C-1", "employee": "E-1", "eligible": True})
        call = self.frappe.db.get_value.call_args
        self.assertEqual(call.args[2], ["name", "wage_method", "modified"])
        self.assertFalse(call.kwargs["for_update"])
        self.frappe.get_all.assert_not_called()

    def test_no_wage_create_is_denied_before_lookup(self):
        self.frappe.has_permission.side_effect = lambda dt, permission: dt != "Jifeng Wage Entry"
        with self.assertRaises(PermissionError):
            self.module.select_piece_worker("C-1", "E-1")
        self.frappe.db.get_value.assert_not_called()

    def test_exact_wage_create_user_permission_is_checked(self):
        self.probe.denied = ("create",)
        with self.assertRaises(PermissionError):
            self.module.require_piece_worker("C-1", "E-1")
        self.frappe.db.get_value.assert_not_called()

    def test_company_select_employee_read_and_field_scope_precede_lookup(self):
        for document, permission in ((self.company, "select"), (self.person, "read")):
            with self.subTest(permission=permission):
                document.denied = (permission,)
                with self.assertRaises(PermissionError):
                    self.module.require_piece_worker("C-1", "E-1")
                document.denied = ()
        self.fields.remove("company")
        with self.assertRaises(PermissionError):
            self.module.require_piece_worker("C-1", "E-1")
        self.frappe.db.get_value.assert_not_called()

    def test_guest_cannot_query_or_select(self):
        self.frappe.session.user = "Guest"
        for action in (lambda: self.module.list_piece_workers("C-1"),
                       lambda: self.module.select_piece_worker("C-1", "E-1")):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.get_list.assert_not_called()
        self.frappe.db.get_value.assert_not_called()

    def test_foreign_or_inactive_employee_is_not_a_piece_worker(self):
        for field, value in (("company", "C-2"), ("status", "Left")):
            original = getattr(self.person, field)
            setattr(self.person, field, value)
            with self.assertRaisesRegex(ValueError, "在职员工"):
                self.module.require_piece_worker("C-1", "E-1")
            setattr(self.person, field, original)
        self.frappe.db.get_value.assert_not_called()

    def test_missing_or_time_archive_cannot_be_piece_worker(self):
        for source in (None, Doc(wage_method="计时"), Doc(wage_method="")):
            self.source = source
            with self.assertRaisesRegex(ValueError, "工资方式必须为计件"):
                self.module.select_piece_worker("C-1", "E-1")

    def test_query_uses_native_list_then_document_scope_and_never_refills_hidden_fields(self):
        self.assertEqual(self.module.list_piece_workers("C-1", "合成"),
                         [{"name": "E-1", "employee_name": "合成人员", "company": "C-1", "status": "Active"}])
        self.assertEqual(self.frappe.get_list.call_args.kwargs["filters"], {"company": "C-1", "status": "Active"})
        self.assertIn("or_filters", self.frappe.get_list.call_args.kwargs)
        self.person.denied = ("read",)
        self.frappe.db.get_value.reset_mock()
        self.assertEqual(self.module.list_piece_workers("C-1"), [])
        self.frappe.db.get_value.assert_not_called()

    def test_hidden_employee_number_is_neither_selected_nor_filtered(self):
        self.fields.remove("employee_number")
        self.module.list_piece_workers("C-1", "abc")
        query = self.frappe.get_list.call_args.kwargs
        self.assertNotIn("employee_number", query["fields"])
        self.assertNotIn("employee_number", query["or_filters"])

    def test_stale_native_list_candidate_changed_company_or_status_is_omitted(self):
        for field, value in (("company", "C-2"), ("status", "Left")):
            original = getattr(self.person, field)
            setattr(self.person, field, value)
            self.assertEqual(self.module.list_piece_workers("C-1"), [])
            setattr(self.person, field, original)
        self.frappe.db.get_value.assert_not_called()

    def test_query_does_not_return_other_pay_types_or_archive_metadata(self):
        self.source.wage_method = "计时"
        self.assertEqual(self.module.list_piece_workers("C-1"), [])
        self.source.wage_method = "计件"
        row = self.module.list_piece_workers("C-1")[0]
        self.assertNotIn("wage_method", row)
        self.assertNotIn("modified", row)
        self.assertNotIn("qualification_source", row)

    def test_current_piece_snapshot_is_locked_and_opaque(self):
        snapshot = self.module.require_piece_worker("C-1", "E-1")
        self.assertEqual(snapshot["piece_worker_qualified"], 1)
        self.assertEqual(snapshot["piece_worker_qualification_status"], "已核实")
        for field in self.module.SNAPSHOT_FIELDS[1:3]:
            self.assertEqual(len(snapshot[field]), 64)
            self.assertNotIn("SUP-PRIVATE", snapshot[field])
            self.assertNotIn("2026", snapshot[field])
        self.assertTrue(self.frappe.db.get_value.call_args.kwargs["for_update"])

    def test_pure_time_and_extra_do_not_lookup_piece_qualification(self):
        draft = Doc(quantity=0, piece_rate=0, hours=2, hourly_rate=3, extra_amount=1)
        self.module.bind_piece_worker(draft)
        self.assertEqual(draft.piece_worker_qualification_status, "不适用")
        self.frappe.db.get_value.assert_not_called()

    def test_new_or_changed_piece_cannot_forge_snapshot(self):
        self.source.wage_method = "计时"
        draft = Doc(company="C-1", employee="E-1", quantity=2, piece_rate=3,
                    piece_worker_qualified=1, piece_worker_qualification_source="f" * 64,
                    piece_worker_qualification_version="e" * 64)
        with self.assertRaisesRegex(ValueError, "工资方式必须为计件"):
            self.module.bind_piece_worker(draft)

    def test_approved_snapshot_survives_later_classification_change(self):
        old = Doc(**self.module.require_piece_worker("C-1", "E-1"))
        self.source.wage_method = "计时"
        draft = Doc(quantity=2, piece_rate=3)
        self.frappe.db.get_value.reset_mock()
        self.module.bind_piece_worker(draft, historical=old)
        self.assertEqual(draft.piece_worker_qualification_source, old.piece_worker_qualification_source)
        self.assertEqual(draft.piece_worker_qualification_status, "已核实")
        self.frappe.db.get_value.assert_not_called()

    def test_approved_legacy_snapshot_keeps_unknown_historical_classification(self):
        self.source.wage_method = "计时"
        draft = Doc(quantity=2, piece_rate=3)
        self.module.bind_piece_worker(draft, historical=Doc())
        self.assertEqual(draft.piece_worker_qualification_status, "历史未记录")
        self.assertEqual(draft.piece_worker_qualified, 0)
        self.assertIsNone(draft.piece_worker_qualification_version)
        self.frappe.db.get_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
