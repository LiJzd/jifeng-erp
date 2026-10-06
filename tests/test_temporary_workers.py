"""Native-shaped read/create permissions and changing current HR qualification."""
import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Row(dict):
    __getattr__ = dict.__getitem__


class Doc(Row):
    def check_permission(self, permission):
        self["checks"].append(permission)
        if permission in self.get("denied", ()):
            raise PermissionError("native permission denied")
        callback = self.get("on_check")
        if callback:
            callback(permission)


class TestTemporaryWorkers(unittest.TestCase):
    def setUp(self):
        self.fields = {"employee_number", "employee_name", "company", "department", "status"}
        self.meta = types.SimpleNamespace(get_permitted_fieldnames=lambda **kwargs: self.fields)
        self.person = Doc(name="E-1", modified=datetime(2026, 10, 1, 9), employee_number="001",
            employee_name="合成员工", company="C-1", department="D-1", status="Active", meta=self.meta, checks=[])
        self.company = Doc(name="C-1", checks=[], denied={"read"})  # Native select-only HR User.
        self.probe = Doc(checks=[])
        self.source = Row(name="SUP-PRIVATE", employee="E-1", company="C-1", term_type="临时",
            modified=datetime(2026, 10, 1, 10), identity_number="PRIVATE-ID", bank_account_name="PRIVATE-BANK")
        self.lookup_count = 0
        self.list_count = 0
        self.after_lookup = None
        self.after_list = None
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = types.SimpleNamespace(user="HR-USER")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.methods = []
        def whitelist(**kwargs):
            self.methods.append(kwargs)
            return lambda function: function
        self.frappe.whitelist = whitelist
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: dt != "Jifeng Employee Supplement")
        self.frappe.get_meta = Mock(return_value=self.meta)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped list forbidden"))
        self.frappe.db = types.SimpleNamespace(get_value=Mock(side_effect=self.lookup))
        context = patch.dict(sys.modules, {"frappe": self.frappe})
        context.start()
        self.addCleanup(context.stop)
        path = Path(__file__).parents[1] / "jifeng_erp" / "temporary_workers.py"
        spec = importlib.util.spec_from_file_location("temporary_workers_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            self.assertEqual(doctype, {"doctype": "Jifeng Wage Entry", "company": "C-1", "employee": "E-1"})
            return self.probe
        if doctype == "Company":
            self.assertEqual(name, "C-1")
            return self.company
        if doctype == "Employee":
            self.assertEqual(name, "E-1")
            # Return a new document, as native get_doc does (not a live reference).
            doc = Doc(copy.copy(self.person))
            doc["checks"] = self.person["checks"]
            return doc
        raise AssertionError("private/other source document load forbidden: " + doctype)

    def get_list(self, doctype, **kwargs):
        self.assertEqual(doctype, "Employee")
        self.assertEqual(kwargs["filters"]["company"], "C-1")
        self.assertEqual(kwargs["filters"]["status"], "Active")
        self.list_count += 1
        row = Row({field: self.person[field] for field in kwargs["fields"] if field in self.person})
        result = [row] if self.person["company"] == "C-1" and self.person["status"] == "Active" else []
        if self.after_list:
            self.after_list(self.list_count)
        return result

    def lookup(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, "Jifeng Employee Supplement")
        self.assertEqual(filters, {"company": "C-1", "employee": "E-1"})
        self.assertEqual(fields, ["name", "employee", "company", "term_type", "modified"])
        self.assertEqual(kwargs, {"as_dict": True, "for_update": False})
        self.lookup_count += 1
        result = Row({field: self.source[field] for field in fields}) if self.source else None
        if self.after_lookup:
            self.after_lookup(self.lookup_count)
        return result

    def listed(self):
        return self.module.list_temporary_workers("C-1")

    def selection(self, row):
        return self.module.select_temporary_worker("C-1", row["name"], row["employee_version"], row["supplement_version"])

    def test_select_only_hr_user_can_use_narrow_service_without_supplement_grant(self):
        result = self.listed()
        self.assertEqual(self.list_count, 1)
        self.assertEqual(self.lookup_count, 1)
        row = result["rows"][0]
        selected = self.selection(row)
        self.assertTrue(selected["eligible"])
        self.assertTrue(result["readonly"])
        self.assertEqual(result["classification_basis"], "current_legacy_term_type")
        self.assertFalse(selected["historical_qualification_known"])
        self.assertTrue(all(value == "select" for value in self.company["checks"]))
        self.assertTrue(all(value == "create" for value in self.probe["checks"]))
        self.assertFalse(self.frappe.has_permission("Jifeng Employee Supplement", "read"))
        self.frappe.get_all.assert_not_called()

    def test_projection_never_exposes_supplement_identifier_or_private_data(self):
        row = self.listed()["rows"][0]
        self.assertEqual(set(row), {"name", "employee_number", "employee_name", "company", "department", "status",
                                   "employee_version", "supplement_version"})
        self.assertNotIn("SUP-PRIVATE", str(row))
        self.assertNotIn("PRIVATE-ID", str(row))
        self.assertNotIn("PRIVATE-BANK", str(row))
        self.assertEqual(len(row["supplement_version"]), 64)
        self.assertEqual(self.methods, [{"methods": ["GET"]}, {"methods": ["GET"]}])

    def test_guest_and_role_denials_precede_private_lookup(self):
        for target in ("Guest", "Jifeng Wage Entry", "Employee"):
            with self.subTest(target=target):
                self.frappe.session.user = "Guest" if target == "Guest" else "HR-USER"
                self.frappe.has_permission.side_effect = lambda dt, permission: dt != target
                with self.assertRaises(PermissionError):
                    self.listed()
                self.frappe.db.get_value.assert_not_called()

    def test_company_user_permission_denial_is_not_bypassed(self):
        self.company["denied"] = {"select"}
        with self.assertRaises(PermissionError):
            self.listed()
        self.frappe.db.get_value.assert_not_called()

    def test_employee_read_or_exact_new_wage_create_denial_omits_candidate(self):
        for document, denied in ((self.person, "read"), (self.probe, "create")):
            with self.subTest(permission=denied):
                document["denied"] = {denied}
                self.assertEqual(self.listed()["rows"], [])
                self.frappe.db.get_value.assert_not_called()
                document["denied"] = set()

    def test_cross_company_or_inactive_employee_is_not_selected(self):
        row = self.listed()["rows"][0]
        for field, value in (("company", "C-2"), ("status", "Left")):
            with self.subTest(field=field):
                original = self.person[field]
                self.person[field] = value
                self.assertEqual(self.listed()["rows"], [])
                with self.assertRaisesRegex(ValueError, "重新查询"):
                    self.selection(row)
                self.person[field] = original

    def test_missing_or_non_temporary_source_is_never_in_the_list(self):
        for value in (None, "长期", ""):
            with self.subTest(value=value):
                self.source = None if value is None else Row(name="S", employee="E-1", company="C-1", term_type=value, modified=datetime(2026, 10, 1))
                self.assertEqual(self.listed()["rows"], [])

    def test_wrong_source_employee_or_company_is_not_eligible(self):
        for field, value in (("employee", "E-2"), ("company", "C-2")):
            original = self.source[field]
            self.source[field] = value
            self.assertEqual(self.listed()["rows"], [])
            self.source[field] = original

    def test_required_mask_denies_but_optional_masks_do_not_get_refilled(self):
        self.fields.difference_update({"employee_number", "department"})
        row = self.module.list_temporary_workers("C-1", "合成")["rows"][0]
        self.assertNotIn("employee_number", row)
        self.assertNotIn("department", row)
        self.assertNotIn("employee_number", self.frappe.get_list.call_args.kwargs["or_filters"])
        self.fields.remove("employee_name")
        self.frappe.db.get_value.reset_mock()
        with self.assertRaises(PermissionError):
            self.listed()
        self.frappe.db.get_value.assert_not_called()

    def test_late_native_wage_permission_callback_cannot_keep_stale_employee_scope(self):
        self.probe["on_check"] = lambda permission: self.person.update(status="Left")
        self.assertEqual(self.listed()["rows"], [])
        self.frappe.db.get_value.assert_not_called()

    def test_late_native_permission_callback_changes_classification_before_lookup(self):
        self.probe["on_check"] = lambda permission: self.source.update(term_type="长期")
        self.assertEqual(self.listed()["rows"], [])

    def test_classification_change_between_selection_observations_is_rejected_even_without_modified_change(self):
        row = self.listed()["rows"][0]
        self.lookup_count = 0
        self.after_lookup = lambda count: self.source.update(term_type="长期") if count == 1 else None
        with self.assertRaisesRegex(ValueError, "重新查询"):
            self.selection(row)

    def test_source_modified_or_identity_change_between_selection_observations_is_rejected(self):
        row = self.listed()["rows"][0]
        for field, value in (("modified", datetime(2026, 10, 2)), ("name", "S-REPLACED")):
            with self.subTest(field=field):
                original = self.source[field]
                self.lookup_count = 0
                self.after_lookup = lambda count: self.source.update({field: value}) if count == 1 else None
                with self.assertRaisesRegex(ValueError, "重新查询"):
                    self.selection(row)
                self.source[field] = original

    def test_native_list_to_document_change_is_rejected(self):
        self.after_list = lambda count: self.person.update(employee_name="改名") if count == 1 else None
        with self.assertRaisesRegex(ValueError, "重新查询"):
            self.listed()

    def test_cached_list_tokens_do_not_authorize_selection_after_changes(self):
        row = self.listed()["rows"][0]
        for document, field, value in ((self.person, "modified", datetime(2026, 10, 2)),
                                      (self.source, "modified", datetime(2026, 10, 2)),
                                      (self.source, "term_type", "长期")):
            with self.subTest(field=field):
                original = document[field]
                document[field] = value
                with self.assertRaisesRegex(ValueError, "重新查询"):
                    self.selection(row)
                document[field] = original

    def test_permission_revocation_during_selection_is_not_cached(self):
        row = self.listed()["rows"][0]
        self.lookup_count = 0
        self.after_lookup = lambda count: self.probe.update(denied={"create"}) if count == 1 else None
        with self.assertRaisesRegex(ValueError, "重新查询"):
            self.selection(row)

    def test_malformed_versions_and_inputs_do_not_issue_lookup(self):
        for value in (None, "", "short", "G" * 64):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.module.select_temporary_worker("C-1", "E-1", value, "a" * 64)
        with self.assertRaises(ValueError):
            self.module.list_temporary_workers("C-1", "bad\nkeyword")
        self.frappe.db.get_value.assert_not_called()

    def test_large_native_candidate_set_is_rejected_before_private_lookup(self):
        self.frappe.get_list.side_effect = None
        self.frappe.get_list.return_value = [Row(name="E-1")] * 1001
        with self.assertRaisesRegex(ValueError, "人员过多"):
            self.listed()
        self.frappe.db.get_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
