"""Actual archive/auth helpers with an AP stub; no native DB, HTTP or GUI."""

from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


TREE = Path(__file__).resolve().parents[1]
REPO = TREE if (TREE / "tests/test_partner_archive.py").is_file() else TREE.parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPlatingPayables(unittest.TestCase):
    def setUp(self):
        search_path = patch.object(sys, "path", [str(REPO), *sys.path])
        search_path.start()
        self.addCleanup(search_path.stop)
        fixtures = load_module("plating_archive_fixtures", REPO / "tests/test_partner_archive.py")
        self.Record = fixtures.Record
        self.env = fixtures.TestPartnerArchive("test_joint_save_preserves_raw_and_shared_supplier")
        self.addCleanup(self.env.doCleanups)
        self.env.setUp()
        self.P = self.env.module
        self.profile = {"doctype": self.P.PROFILE, "name": "PLATING-A", "company": "Company A",
            "archive_kind": "电镀厂", "supplier": "SUP-A", "modified": "2026-10-02T00:00:00.123456",
            "creation": "2026-10-01T00:00:00.123456", "owner": "synthetic-owner",
            "modified_by": "synthetic-accounts-user", "request_digest": "f" * 64,
            **self.env.payload()["profile"]}
        self.env.records[self.P.PROFILE, "PLATING-A"] = deepcopy(self.profile)
        self.env.records["Company", "Company A"]["private_company_profile"] = "must-not-return-company-profile"
        self.env.records["Supplier", "SUP-A"].update(bank_account="supplier-bank-must-not-fallback",
            mobile="supplier-mobile-must-not-fallback", private_child=[{"private": "not-a-dto"}])
        self.auth = {
            "User": [{"name": self.env.frappe.session.user, "enabled": 1, "user_type": "System User"}],
            "Has Role": [{"name": "ROLE", "parenttype": "User", "parent": self.env.frappe.session.user,
                          "role": "Accounts User"}],
            "User Permission": [{"name": "COMPANY-UP", "user": self.env.frappe.session.user,
                                  "allow": "Company", "for_value": "Company A"}],
            "DocPerm": [{"name": "PROFILE-READ", "parent": self.P.PROFILE, "role": "Accounts User", "read": 1},
                        {"name": "COMPANY-SELECT", "parent": "Company", "role": "Accounts User", "select": 1}],
            "Custom DocPerm": [{"name": "PROFILE-CUSTOM", "parent": self.P.PROFILE, "role": "Accounts User", "read": 1}],
            "DocField": [{"name": "PROFILE-BANK", "parent": self.P.PROFILE, "fieldname": "bank_account", "mask": 0}],
            "Property Setter": [{"name": "PROFILE-BANK-MASK", "doc_type": self.P.PROFILE,
                                 "field_name": "bank_account", "property": "mask", "value": "0"}],
            "DocShare": [{"name": "PROFILE-SHARE", "share_doctype": self.P.PROFILE, "share_name": "PLATING-A",
                          "user": self.env.frappe.session.user, "read": 1}],
            "Report": [{"name": "Accounts Payable", "disabled": 0}],
        }
        self.env.frappe.get_all = Mock(side_effect=self.get_all)
        self.methods = []
        self.env.frappe.whitelist = lambda **kwargs: self.decorator(kwargs)
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: date.fromisoformat(value)
        self.utilities = patch.dict(sys.modules, {"frappe.utils": utils})
        self.utilities.start()
        self.addCleanup(self.utilities.stop)
        self.A = load_module("plating_native_ap_helpers", REPO / "jifeng_erp/supplier_reconciliation.py")
        self.native = {"company": "Company A", "supplier": "SUP-A", "report_date": "2026-10-02",
            "currency": "CNY", "amount_basis": "company_currency", "payable_account": None,
            "accounts": ["PAYABLE"], "source_report": "Accounts Payable",
            "rows": [{"voucher_type": "Purchase Invoice", "voucher_no": "synthetic-PI",
                      "invoiced": "120.00", "paid": "70.00", "credit_note": "20.00", "outstanding": "30.00"}]}
        self.A.query_payables = Mock(return_value=self.native)
        package = types.ModuleType("jifeng_erp")
        package.partner_archive, package.supplier_reconciliation = self.P, self.A
        self.dependencies = patch.dict(sys.modules, {"jifeng_erp": package,
            "jifeng_erp.partner_archive": self.P, "jifeng_erp.supplier_reconciliation": self.A})
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)
        self.module = load_module("plating_payables_candidate", TREE / "jifeng_erp/plating_payables.py")
        self.original_records = deepcopy(self.env.records)

    def decorator(self, kwargs):
        self.methods.append(kwargs)
        return lambda function: function

    @staticmethod
    def matches(row, filters):
        for field, expected in (filters or {}).items():
            if isinstance(expected, list):
                if expected[0] == "in" and row.get(field) not in expected[1]:
                    return False
            elif row.get(field) != expected:
                return False
        return True

    def get_all(self, doctype, filters, **kwargs):
        return [self.Record(deepcopy(row)) for row in self.auth.get(doctype, []) if self.matches(row, filters)]

    def read(self, company="Company A", name="PLATING-A", report_date="2026-10-02"):
        return self.module.read_payables(company, name, report_date)

    def during_ap(self, change):
        self.A.query_payables.side_effect = lambda *args: (change(), self.native)[1]

    def test_get_exact_arguments_and_unchanged_native_response_without_writes(self):
        result = self.read()
        self.assertEqual(self.methods[-1], {"methods": ["GET"]})
        self.A.query_payables.assert_called_once_with("Company A", "SUP-A", "2026-10-02")
        self.assertIs(result["payables"], self.native)
        self.assertEqual(result["amount_scope"], "关联供应商在本公司截至所选日期的已过账应付，包含该供应商的全部业务")
        self.assertEqual(set(result), {"read_only", "kind", "archive", "payables", "amount_scope"})
        self.assertTrue(result["read_only"])
        self.assertFalse(result["archive"]["can_write"])
        self.assertTrue(all(field["read_only"] for field in result["archive"]["field_meta"].values()))
        self.assertEqual(self.env.records, self.original_records)
        self.assertEqual(self.env.saves, [])
        self.assertEqual(self.env.snapshots, {})

    def test_contact_bank_original_text_and_missing_fields_never_fallback(self):
        for field, value in (("bank_account", " 0000123 "), ("mobile", " 000000 "),
                             ("display_name", "<b>合成电镀厂</b>"), ("address", "原文\n地址")):
            self.env.records[self.P.PROFILE, "PLATING-A"][field] = value
        result = self.read()
        self.assertEqual(result["archive"]["profile"]["bank_account"], " 0000123 ")
        self.assertEqual(result["archive"]["profile"]["address"], "原文\n地址")
        self.env.hidden_fields[self.P.PROFILE, "read"] = {"bank_account", "mobile"}
        result = self.read()
        self.assertNotIn("bank_account", result["archive"]["profile"])
        self.assertNotIn("mobile", result["archive"]["field_meta"])
        self.assertNotIn("must-not-fallback", repr(result))
        self.assertNotIn("must-not-return-company-profile", repr(result))
        self.assertNotIn("not-a-dto", repr(result))

    def test_company_select_without_read_and_inactive_history_are_allowed(self):
        self.env.denials.add(("Company", "read"))
        self.env.records[self.P.PROFILE, "PLATING-A"]["inactive"] = 1
        self.env.records["Supplier", "SUP-A"]["disabled"] = 1
        result = self.read()
        self.assertTrue(result["archive"]["profile"]["inactive"])
        self.assertTrue(result["archive"]["supplier_disabled"])

    def test_shared_supplier_never_becomes_plating_only_amount(self):
        other = dict(self.profile, name="OUTSOURCE-A", archive_kind="外发工厂")
        self.env.records[self.P.PROFILE, "OUTSOURCE-A"] = other
        result = self.read()
        self.assertEqual(result["payables"]["supplier"], "SUP-A")
        self.assertEqual(result["payables"]["rows"], self.native["rows"])
        self.assertIn("包含该供应商的全部业务", result["amount_scope"])
        self.assertNotIn("total", result)
        self.assertNotIn("summary", result)

    def test_wrong_kind_company_guest_and_missing_archive_never_call_ap(self):
        for change in (lambda: self.env.records[self.P.PROFILE, "PLATING-A"].update(archive_kind="外发工厂"),
                       lambda: self.env.records[self.P.PROFILE, "PLATING-A"].update(company="Company B"),
                       lambda: setattr(self.env.frappe.session, "user", "Guest")):
            with self.subTest(change=change):
                self.env.records[self.P.PROFILE, "PLATING-A"] = deepcopy(self.profile)
                self.env.frappe.session.user = "synthetic-user"
                change()
                with self.assertRaises(PermissionError):
                    self.read()
        self.env.frappe.session.user = "synthetic-user"
        with self.assertRaises(KeyError):
            self.read(name="MISSING")
        self.A.query_payables.assert_not_called()

    def test_required_archive_supplier_and_company_permissions_refuse_before_ap(self):
        for permission in ((self.P.PROFILE, "read"), ("Supplier", "read"),
                           ("Supplier", "select"), ("Company", "select")):
            with self.subTest(permission=permission):
                self.env.denials = {permission}
                with self.assertRaises(PermissionError):
                    self.read()
        self.env.denials = set()
        self.env.hidden_fields[self.P.PROFILE, "read"] = {"supplier"}
        with self.assertRaises(PermissionError):
            self.read()
        self.A.query_payables.assert_not_called()

    def test_profile_supplier_and_binding_changes_during_ap_refuse_whole_response(self):
        cases = ((self.P.PROFILE, "PLATING-A", "notes", "changed"),
                 (self.P.PROFILE, "PLATING-A", "supplier", "SUP-B"),
                 ("Supplier", "SUP-A", "supplier_name", "changed"),
                 ("Supplier", "SUP-A", "disabled", 1))
        self.env.records["Supplier", "SUP-B"] = dict(self.env.records["Supplier", "SUP-A"], name="SUP-B")
        for doctype, name, field, value in cases:
            with self.subTest(field=field):
                before = deepcopy(self.env.records)
                self.during_ap(lambda: self.env.records[doctype, name].update({field: value}))
                with self.assertRaises(ValueError):
                    self.read()
                self.env.records = before

    def test_detached_source_handles_original_loaded_document_in_place_change(self):
        load = self.P._load
        held = []
        def tracked_load(*args):
            documents = load(*args)
            held.append(documents)
            return documents
        def changed():
            # This hidden identity never enters _pack; a fresh guard alone sees
            # the same mutated in-memory/current rows and the unchanged DTO.
            held[0][0]["request_digest"] = "0" * 64
            self.env.records[self.P.PROFILE, "PLATING-A"]["request_digest"] = "0" * 64
        with patch.object(self.P, "_load", side_effect=tracked_load):
            self.during_ap(changed)
            with self.assertRaises(ValueError):
                self.read()

    def test_supplier_identity_is_frozen_before_pack_and_permission_reads(self):
        pack = self.P._pack
        def changed_pack(profile, supplier, **kwargs):
            supplier.name = "changed-cached-name"
            return pack(profile, supplier, **kwargs)
        with patch.object(self.P, "_pack", side_effect=changed_pack):
            with self.assertRaises(ValueError):
                self.read()
        self.A.query_payables.assert_called_once_with("Company A", "SUP-A", "2026-10-02")

    def test_raw_auth_changes_reject_when_native_permissions_remain_cached(self):
        cases = (("Has Role", "role", "changed-role"), ("User Permission", "for_value", "Company B"),
                 ("DocPerm", "read", 0), ("Custom DocPerm", "read", 0), ("DocField", "mask", 1),
                 ("Property Setter", "value", "1"), ("DocShare", "read", 0),
                 ("User", "enabled", 0), ("Report", "disabled", 1))
        for doctype, field, value in cases:
            with self.subTest(doctype=doctype):
                before = deepcopy(self.auth)
                self.during_ap(lambda: self.auth[doctype][0].update({field: value}))
                with self.assertRaises(ValueError):
                    self.read()
                self.auth = before

    def test_raw_company_select_revocation_and_pack_time_mask_change_are_detected(self):
        self.during_ap(lambda: self.auth["DocPerm"][1].update(select=0))
        with self.assertRaises(ValueError):
            self.read()
        self.auth["DocPerm"][1]["select"] = 1
        self.A.query_payables.side_effect = None
        pack = self.P._pack
        def changed_pack(*args, **kwargs):
            self.auth["DocField"][0]["mask"] = 1
            return pack(*args, **kwargs)
        with patch.object(self.P, "_pack", side_effect=changed_pack):
            with self.assertRaises(ValueError):
                self.read()

    def test_late_guard_cached_permission_change_is_checked_after_guard(self):
        guard = self.P._guard
        def changed_guard(*args):
            guard(*args)
            self.auth["DocField"][0]["mask"] = 1
        with patch.object(self.P, "_guard", side_effect=changed_guard):
            with self.assertRaises(ValueError):
                self.read()

    def test_fresh_permission_and_bank_projection_changes_during_ap_refuse(self):
        self.during_ap(lambda: self.env.denials.add((self.P.PROFILE, "read")))
        with self.assertRaises(PermissionError):
            self.read()
        self.env.denials = set()
        self.during_ap(lambda: self.env.hidden_fields.update({(self.P.PROFILE, "read"): {"bank_account"}}))
        with self.assertRaises(PermissionError):
            self.read()

    def test_native_exception_propagates_same_instance_without_partial_archive(self):
        for error in (PermissionError("native-permission"), ValueError("native-validation"), RuntimeError("native-failure")):
            with self.subTest(error=type(error)):
                self.A.query_payables.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    self.read()
                self.assertIs(caught.exception, error)
                self.assertEqual(self.env.saves, [])

    def test_empty_native_rows_stay_empty_without_zero_balance_or_fallback(self):
        self.native["rows"] = []
        result = self.read()
        self.assertIs(result["payables"], self.native)
        self.assertEqual(result["payables"]["rows"], [])
        self.assertNotIn("total", result["payables"])
        self.assertNotIn("outstanding", result["payables"])

    def test_no_extra_supplier_kind_account_or_write_permission_arguments(self):
        self.env.denials.update({(self.P.PROFILE, "write"), ("Supplier", "write"), ("Supplier", "create")})
        self.read()
        for field in ("supplier", "kind", "payable_account"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.module.read_payables("Company A", "PLATING-A", "2026-10-02", **{field: "forbidden"})
        self.assertEqual(self.env.saves, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
