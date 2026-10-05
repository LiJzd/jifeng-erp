"""Expense-account service contracts; mocked native calls, not DB acceptance."""

from copy import deepcopy
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
STAMP = datetime(2026, 10, 5, 20, 1, 2, 123456)


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class TestFinanceAccounts(unittest.TestCase):
    def setUp(self):
        env = self

        class Document(Row):
            def has_permission(self, action):
                return ((self.doctype, action) not in env.denied
                        and (self.doctype, self.name, action) not in env.document_denied)

            def check_permission(self, action):
                if not self.has_permission(action):
                    raise PermissionError("native permission")

            def insert(self):
                self.check_permission("create")
                env.native_calls.append(("insert", deepcopy(dict(self))))
                if env.native_error:
                    raise env.native_error
                self.name = " - ".join(p for p in (self.account_number, self.account_name, "JF") if p)
                self.modified = STAMP
                return self

            def save(self):
                self.check_permission("write")
                env.native_calls.append(("save", deepcopy(dict(self))))
                if env.native_error:
                    raise env.native_error
                self.modified = datetime(2026, 10, 5, 20, 1, 3, 123456)
                return self

        self.Document = Document
        self.denied, self.document_denied, self.hidden, self.masked = set(), set(), {}, set()
        self.native_calls, self.native_error = [], None
        self.disabled_readonly = False
        self.documents = {}
        self.add("Company", "JF", default_currency="CNY", lft=1, rgt=2)
        self.add("Company", "OTHER", default_currency="USD", lft=3, rgt=4)
        self.parent = self.account("费用组 - JF", is_group=1, parent_account=None)
        self.expense = self.account("6001 - 房租 - JF", account_name="房租", account_number="6001")
        self.account("6002 - 拒读 - JF", account_name="拒读", account_number="6002")
        self.document_denied.add(("Account", "6002 - 拒读 - JF", "read"))
        self.account("他公司", company="OTHER", account_currency="USD")
        self.account("外币", account_currency="USD")
        self.account("收入", root_type="Income")
        frappe = types.ModuleType("frappe")
        frappe.session = Row(user="ordinary-account-user@example.invalid")
        frappe.PermissionError = PermissionError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda **kwargs: lambda method: method
        frappe.has_permission = Mock(side_effect=lambda doctype, action, **kwargs: (doctype, action) not in self.denied)
        frappe.get_doc = Mock(side_effect=self.get_doc)
        frappe.get_list = Mock(side_effect=self.get_list)
        frappe.get_meta = Mock(side_effect=self.get_meta)
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, permission_type, **kwargs: (
            {"name", "default_currency", "lft", "rgt"} if doctype == "Company" else set(self.fields)
        ) - self.hidden.get((doctype, permission_type), set())
        self.frappe = frappe
        self.fields = {"name", "modified", "company", "account_name", "account_number", "parent_account",
                       "root_type", "account_type", "is_group", "account_currency", "disabled"}
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model}):
            spec = importlib.util.spec_from_file_location("finance_accounts_under_test", ROOT / "jifeng_erp/finance_accounts.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def add(self, doctype, name, **values):
        document = self.Document(doctype=doctype, name=name, **values)
        self.documents[doctype, name] = document
        return document

    def account(self, name, **values):
        fields = dict(modified=STAMP, company="JF", root_type="Expense", account_type="",
                      account_name=name, account_number="", is_group=0, account_currency="CNY", disabled=0,
                      parent_account="费用组 - JF", report_type="Profit and Loss")
        fields.update(values)
        return self.add("Account", name, **fields)

    def get_doc(self, doctype, name=None, **kwargs):
        return self.Document(doctype) if isinstance(doctype, dict) else self.documents[doctype, name]

    def get_meta(self, doctype):
        fields = [Row(fieldname=f, permlevel=0, mask=f in self.masked, read_only=False) for f in self.fields]
        disabled = Row(fieldname="disabled", read_only=self.disabled_readonly, set_only_once=False)
        return Row(fields=fields, get_permlevel_access=lambda *args, **kwargs: [],
                   get_field=lambda name: disabled if name == "disabled" else None)

    def get_list(self, doctype, fields, filters, limit_start, limit_page_length, **kwargs):
        # Native query scope is simulated independently of the per-document
        # read rejection, so a denied row cannot contribute to the total.
        self.assertLessEqual(set(kwargs), {"order_by", "or_filters"})
        rows = [d for (kind, _), d in self.documents.items() if kind == doctype
                and all(d.get(key) == value for key, value in filters.items())]
        rows.sort(key=lambda d: d.name)
        return [{f: d.get(f) for f in fields} for d in rows[limit_start:limit_start + limit_page_length]]

    def create(self, **changes):
        payload = dict(company="JF", parent_account=self.parent.name, account_name="办公费", account_number="6601")
        payload.update(changes)
        return self.module.save_expense_account(payload)

    def update(self, **changes):
        payload = dict(name=self.expense.name, company="JF", modified=STAMP.isoformat(), disabled=1)
        payload.update(changes)
        return self.module.save_expense_account(payload)

    def test_native_read_scope_paging_keyword_and_denied_rows(self):
        result = self.module.query_expense_accounts("JF", search="%_", page_size=1)
        self.assertEqual((result["total"], len(result["rows"])), (1, 1))
        self.assertEqual(result["rows"][0]["name"], self.expense.name)
        arguments = self.frappe.get_list.call_args.kwargs
        self.assertNotIn("ignore_permissions", arguments)
        self.assertNotIn("ignore_user_permissions", arguments)
        self.assertEqual(arguments["filters"], {"company": "JF", "root_type": "Expense", "is_group": 0, "account_currency": "CNY"})
        self.assertEqual(arguments["or_filters"][0], ["account_name", "like", "%\\%\\_%"])
        self.assertEqual(set(arguments["fields"]), self.fields)
        self.assertNotIn("拒读", str(result))

    def test_read_permission_field_and_mask_denials_precede_queries(self):
        for mode in ("guest", "type", "field", "mask"):
            with self.subTest(mode=mode):
                self.frappe.get_list.reset_mock()
                self.frappe.session.user = "Guest" if mode == "guest" else "ordinary"
                self.denied = {("Account", "read")} if mode == "type" else set()
                self.hidden = {("Account", "read"): {"account_name"}} if mode == "field" else {}
                self.masked = {"account_name"} if mode == "mask" else set()
                with self.assertRaises(PermissionError):
                    self.module.query_expense_accounts("JF")
                self.frappe.get_list.assert_not_called()

    def test_creation_uses_native_insert_and_native_name_without_permission_bypass(self):
        result = self.create()
        action, values = self.native_calls[0]
        self.assertEqual(action, "insert")
        self.assertNotIn("name", values)
        self.assertNotIn("flags", values)
        self.assertEqual((values["company"], values["parent_account"], values["root_type"], values["account_currency"], values["is_group"]),
                         ("JF", self.parent.name, "Expense", "CNY", 0))
        self.assertEqual(result["name"], "6601 - 办公费 - JF")

    def test_company_parent_group_and_currency_must_match_before_insert(self):
        cases = [{"company": "OTHER"}, {"root_type": "Income"}, {"is_group": 0},
                 {"account_currency": "USD"}, {"disabled": 1}]
        original = deepcopy(dict(self.parent))
        for changes in cases:
            with self.subTest(changes=changes):
                self.parent.clear()
                self.parent.update(original)
                self.parent.update(changes)
                with self.assertRaises(ValueError):
                    self.create()
                self.assertEqual(self.native_calls, [])
        self.parent.clear()
        self.parent.update(original)
        self.document_denied.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.create()

    def test_create_and_write_permissions_are_native_and_field_scoped(self):
        self.denied.add(("Account", "create"))
        with self.assertRaises(PermissionError):
            self.create()
        self.denied.clear()
        self.document_denied.add(("Account", self.expense.name, "write"))
        with self.assertRaises(PermissionError):
            self.update()
        self.document_denied.clear()
        self.hidden["Account", "write"] = {"disabled"}
        with self.assertRaises(PermissionError):
            self.update()
        self.assertEqual(self.native_calls, [])

    def test_stale_version_rejects_before_changing_existing_account(self):
        before = deepcopy(dict(self.expense))
        with self.assertRaisesRegex(ValueError, "已被修改"):
            self.update(modified="2026-10-05 20:01:02.123455")
        self.assertEqual(dict(self.expense), before)
        self.assertEqual(self.native_calls, [])

    def test_only_native_disabled_save_preserves_existing_account_identity(self):
        identity = {f: self.expense[f] for f in ("name", "company", "parent_account", "root_type", "account_type", "account_currency", "account_name", "account_number")}
        result = self.update()
        self.assertEqual([action for action, _ in self.native_calls], ["save"])
        self.assertEqual({f: result[f] for f in identity}, identity)
        self.assertEqual(result["disabled"], 1)
        self.frappe.get_doc.assert_any_call("Account", self.expense.name, for_update=True)
        self.assertEqual(result["modified"], "2026-10-05T20:01:03.123456")

    def test_name_number_and_identity_updates_are_explicitly_unavailable(self):
        for key, value in (("account_name", "新名称"), ("account_number", "6602"), ("parent_account", "新父组"),
                           ("root_type", "Income"), ("account_type", "Bank"), ("account_currency", "USD")):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "尚未开放"):
                self.update(**{key: value})
        with self.assertRaises(ValueError):
            self.update(company="OTHER")
        self.assertEqual(self.native_calls, [])

    def test_native_insert_and_save_failures_propagate_without_retry(self):
        self.native_error = ValueError("native Account validation failed")
        for action in (self.create, self.update):
            self.native_calls.clear()
            with self.assertRaisesRegex(ValueError, "native Account validation"):
                action()
            self.assertEqual(len(self.native_calls), 1)

    def test_native_descendant_company_sync_cannot_escape_current_company(self):
        self.documents["Company", "JF"].rgt = 6
        for action in (self.create, self.update):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ValueError, "子公司"):
                action()
        self.assertEqual(self.native_calls, [])
        self.frappe.get_doc.assert_any_call("Company", "JF", for_update=True)
        # Queries remain ordinary read operations, including company trees.
        result = self.module.query_expense_accounts("JF")
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["rows"][0]["can_write"])
        self.assertEqual(result["rows"][0]["editable_fields"], [])


if __name__ == "__main__":
    unittest.main()
