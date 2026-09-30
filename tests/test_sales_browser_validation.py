"""Persistent sales fixtures reject foreign references and unsafe identities."""

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestSalesBrowserValidation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_site_path = Mock(return_value=self.directory.name)
        self.frappe.get_all = Mock()
        self.frappe.get_meta = Mock()
        self.frappe.db = types.SimpleNamespace(get_value=Mock(), get_single_value=Mock())
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.now = lambda: "2026-09-30 12:00:00"
        utils.today = lambda: "2026-09-30"
        utils.add_days = Mock()
        diagnostics = types.ModuleType("jifeng_erp.diagnostics")
        diagnostics._begin_rollback_only = Mock()
        diagnostics._end_rollback_only = Mock()
        inventory = types.ModuleType("jifeng_erp.inventory_validation")
        inventory._ledger_snapshot = Mock()
        inventory._read = Mock()
        sales = types.ModuleType("jifeng_erp.sales_validation")
        for name in ("_cancel_closing", "_closing", "_invoice", "_mode", "_money", "_opening", "_return"):
            setattr(sales, name, Mock())
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "sales_browser_validation.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                "jifeng_erp.diagnostics": diagnostics, "jifeng_erp.inventory_validation": inventory,
                "jifeng_erp.sales_validation": sales}):
            spec = importlib.util.spec_from_file_location("sales_browser_validation_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.data = {"fixture_type": "sales_browser", "fixture_version": 1,
            "prefix": "JF-SALES-BROWSER-012345abcdef", "company": "QA", "records": [],
            **{kind: [] for kind in self.module.KINDS}}

    def test_journal_rejects_changed_sources_and_path_before_cleanup(self):
        self.data["items"] = ["QA-ITEM"]
        self.module._journal(self.data)
        self.assertEqual(self.module._load(self.data)["items"], ["QA-ITEM"])
        tampered = {**self.data, "items": ["REAL-ITEM"]}
        with self.assertRaisesRegex(ValueError, "来源集合"):
            self.module._load(tampered)
        tampered = {**self.data, "artifact_path": str(pathlib.Path(self.directory.name) / "other.json")}
        with self.assertRaisesRegex(ValueError, "路径"):
            self.module._load(tampered)

    def test_verify_path_does_not_create_directory(self):
        missing = pathlib.Path(self.directory.name) / "missing"
        self.frappe.get_site_path.return_value = str(missing)
        self.module._path(self.data)
        self.assertFalse(missing.exists())

    def test_snapshot_uses_persistent_schema_and_keeps_real_parent_and_child_fields(self):
        payment = Record(name="PAY-ROW", parent="QA-PROFILE", parenttype="POS Profile",
                         mode_of_payment="QA-CASH", default=1)
        payment.meta = types.SimpleNamespace(has_field=lambda field: field == "mode_of_payment")
        profile = Record(doctype="POS Profile", company="QA", income_account="QA-INCOME",
                         customer_group="Government", territory="Rest Of The World",
                         payments=[payment], applicable_for_users=[])
        profile.meta = types.SimpleNamespace(has_field=lambda field: field in {"company", "income_account", "payments"})
        initial = self.module._snapshot(profile)
        profile["customer_group"], profile["territory"], payment["default"] = None, None, None
        self.assertEqual(self.module._snapshot(profile), initial)
        self.assertNotIn("customer_group", initial)
        self.assertNotIn("territory", initial)
        self.assertNotIn("applicable_for_users", initial)
        self.assertNotIn("default", initial["payments"][0])
        profile["income_account"] = "OTHER-INCOME"
        self.assertNotEqual(self.module._snapshot(profile), initial)
        profile["income_account"] = "QA-INCOME"
        payment["mode_of_payment"] = "OTHER-CASH"
        self.assertNotEqual(self.module._snapshot(profile), initial)

    def test_foreign_customer_reference_without_fixture_item_is_rejected(self):
        self.data["customers"] = ["QA-CUSTOMER"]
        self.data["records"] = [{"doctype": "Customer", "name": "QA-CUSTOMER"}]
        field = Record(doctype="Payment Entry", fieldname="party", fieldtype="Dynamic Link", options="party_type")
        meta = Record(name="Payment Entry", istable=False, issingle=False, is_virtual=False)
        meta.get_field = lambda name: field
        self.frappe.get_meta.return_value = meta
        self.frappe.get_all.side_effect = [[field], [], [Record(name="FOREIGN-PAYMENT")]]
        with self.assertRaisesRegex(AssertionError, "日志外单据"):
            self.module._references(self.data)
        query = self.frappe.get_all.call_args.kwargs["filters"]
        self.assertEqual(query, {"party": ["in", ["QA-CUSTOMER"]], "party_type": "Customer"})

    def test_native_user_permission_audit_rejects_foreign_reference_or_actor(self):
        self.data["users"], self.data["created_by"] = ["QA-USER"], "Administrator"
        self.data["records"] = [{"doctype": "User", "name": "QA-USER"}]
        field = Record(doctype="Permission Log", fieldname="for_document", fieldtype="Dynamic Link", options="for_doctype")
        meta = Record(name="Permission Log", istable=False, issingle=False, is_virtual=False)
        meta.get_field = lambda name: field
        self.frappe.get_meta.return_value = meta
        native = Record(name="PERM-1", for_doctype="User", for_document="QA-USER", owner="Administrator",
                        changed_by="Administrator", reference_type=None, reference=None)
        self.frappe.get_all.side_effect = [[field], [], [native]]
        self.module._references(self.data)
        self.assertNotIn("changes", self.frappe.get_all.call_args.kwargs["fields"])
        for change in ({"reference_type": "User", "reference": "FOREIGN-USER"},
                       {"changed_by": "FOREIGN-ACTOR"}, {"owner": "FOREIGN-ACTOR"},
                       {"for_document": "FOREIGN-USER"}):
            with self.subTest(change=change):
                self.frappe.get_all.side_effect = [[field], [], [Record({**native, **change})]]
                with self.assertRaisesRegex(AssertionError, "权限审计"):
                    self.module._references(self.data)

    def test_immutable_native_ledger_requires_owned_voucher(self):
        self.data["customers"] = ["QA-CUSTOMER"]
        self.data["records"] = [{"doctype": "Customer", "name": "QA-CUSTOMER"},
                                {"doctype": "Sales Invoice", "name": "QA-SI"}]
        field = Record(doctype="GL Entry", fieldname="party", fieldtype="Dynamic Link", options="party_type")
        meta = Record(name="GL Entry", istable=False, issingle=False, is_virtual=False)
        meta.get_field = lambda name: field
        self.frappe.get_meta.return_value = meta
        owned = Record(name="GL-1", voucher_type="Sales Invoice", voucher_no="QA-SI", company="QA")
        self.frappe.get_all.side_effect = [[field], [], [owned]]
        self.module._references(self.data)
        # get_all normally returns Frappe dicts.
        self.frappe.get_all.side_effect = [[field], [], [Record({**owned, "voucher_no": "FOREIGN-SI"})]]
        with self.assertRaisesRegex(AssertionError, "日志外原生账簿"):
            self.module._references(self.data)

    def test_effective_gl_does_not_hide_wrong_account_or_voucher_offsets(self):
        rows = [dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="A", debit=10, credit=0, is_cancelled=0),
                dict(voucher_type="Sales Invoice", voucher_no="SI-2", account="A", debit=0, credit=10, is_cancelled=0),
                dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="B", debit=0, credit=10, is_cancelled=0),
                dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="A", debit=999, credit=0, is_cancelled=1)]
        checks = self.module._net_gl(rows)
        self.assertEqual({(row["voucher_no"], row["account"]): Decimal(row["net_value"]) for row in checks},
                         {("SI-1", "A"): Decimal(10), ("SI-1", "B"): Decimal(-10), ("SI-2", "A"): Decimal(-10)})
        retained = self.module._net_gl(rows, include_cancelled=True)
        self.assertEqual({(row["voucher_no"], row["account"]): Decimal(row["net_value"]) for row in retained},
                         {("SI-1", "A"): Decimal(1009), ("SI-1", "B"): Decimal(-10), ("SI-2", "A"): Decimal(-10)})

    def test_cancelled_flags_alone_cannot_pass_restored_history_check(self):
        rows = [dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="A", debit=10, credit=0, is_cancelled=1),
                dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="B", debit=0, credit=10, is_cancelled=1)]
        docs = {("Sales Invoice", "SI-1"): Record(docstatus=2)}
        with patch.object(self.module, "_ledger", return_value={"gl": rows, "stock": []}):
            with self.assertRaisesRegex(AssertionError, "保留历史净额"):
                self.module._restored(self.data, docs)

    def test_native_cancelled_reversals_return_both_net_check_groups(self):
        rows = [dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="A", debit=10, credit=0, is_cancelled=1),
                dict(voucher_type="Sales Invoice", voucher_no="SI-1", account="A", debit=0, credit=10, is_cancelled=1)]
        docs = {("Sales Invoice", "SI-1"): Record(docstatus=2)}
        with patch.object(self.module, "_ledger", return_value={"gl": rows, "stock": []}), \
                patch.object(self.module, "_reports", return_value={"transaction_rows": [], "posted_rows": []}):
            restored = self.module._restored(self.data, docs)
        self.assertEqual(restored["effective_gl_by_voucher_account"], [])
        self.assertEqual(restored["retained_gl_by_voucher_account"], [
            {"voucher_type": "Sales Invoice", "voucher_no": "SI-1", "account": "A", "net_value": "0"}])

    def test_ledger_checks_original_history_and_no_extra_submitted_postings(self):
        self.data["items"], self.data["customers"] = ["QA-ITEM"], ["QA-CUSTOMER"]
        self.data["records"] = [{"doctype": "Sales Invoice", "name": "QA-SI"}]
        original = Record(name="GL-1", company="QA", account="A", voucher_type="Sales Invoice",
                          voucher_no="QA-SI", debit=Decimal("10.00"), credit=0, is_cancelled=0,
                          party_type="Customer", party="QA-CUSTOMER")
        self.data["ledger_records"] = {"gl": json.loads(json.dumps([original], default=str))}
        self.frappe.get_all.side_effect = [[], [], [original], ["GL-1"]]
        self.assertEqual(len(self.module._ledger(self.data)["gl"]), 1)
        changed = Record({**original, "debit": Decimal("20.00")})
        self.frappe.get_all.side_effect = [[], [], [changed], ["GL-1"]]
        with self.assertRaisesRegex(AssertionError, "历史内容"):
            self.module._ledger(self.data)
        extra = Record({**original, "name": "GL-2"})
        self.frappe.get_all.side_effect = [[], [], [original, extra], ["GL-1", "GL-2"]]
        self.frappe.db.get_value.return_value = 1
        with self.assertRaisesRegex(AssertionError, "额外总账"):
            self.module._ledger(self.data)


if __name__ == "__main__":
    unittest.main()
