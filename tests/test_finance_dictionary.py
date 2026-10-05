"""Targeted contracts with mocked native Documents; no database acceptance."""

from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
DOCTYPE = "Jifeng Finance Dictionary"
REQUEST = "c9a22b22-4e5d-46f2-a3c3-1cb6fb7f2c1e"
STAMP = datetime(2026, 10, 5, 22, 10, 1, 123456)


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class TestFinanceDictionary(unittest.TestCase):
    def setUp(self):
        env = self

        class Document(Row):
            def set(self, field, value):
                self[field] = value

            def is_new(self):
                return self.__dict__.get("_new_document", False)

            def get_doc_before_save(self):
                if self.is_new():
                    return None
                previous = env.documents.get((self.doctype, self.name))
                return Document(deepcopy(previous)) if previous else None

            def has_permission(self, action):
                return ((self.doctype, action) not in env.denied
                        and (self.doctype, self.name, action) not in env.row_denied)

            def check_permission(self, action):
                if not self.has_permission(action):
                    raise PermissionError("native permission")

            def insert(self):
                self.check_permission("create")
                env.native_calls.append(("insert", deepcopy(dict(self))))
                env.module.before_insert_dictionary(self)
                env.module.name_dictionary(self)
                env.module.validate_dictionary(self)
                if (self.doctype, self.name) in env.documents:
                    raise ValueError("native duplicate name")
                return self.persist()

            def save(self):
                self.check_permission("write")
                env.native_calls.append(("save", deepcopy(dict(self))))
                env.module.validate_dictionary(self)
                return self.persist()

            def persist(self):
                if env.native_error:
                    raise env.native_error
                self.modified = STAMP + timedelta(microseconds=len(env.native_calls))
                env.documents[self.doctype, self.name] = deepcopy(dict(self))
                self.__dict__["_new_document"] = False
                return self

        self.Document = Document
        self.denied, self.row_denied, self.hidden, self.masked, self.readonly = set(), set(), {}, set(), set()
        self.native_calls, self.native_error, self.series = [], None, {}
        self.documents, self.links = {}, set()
        self.dictionary_fields = {"name", "modified", "company", "dictionary_type", "legacy_number", "value", "remarks", "inactive", "account", "request_id"}
        self.company_fields = {"name", "default_currency"}
        self.account_fields = {"name", "company", "root_type", "account_type", "is_group", "account_currency", "disabled"}
        self.add("Company", "JF", default_currency="CNY")
        self.add("Company", "OTHER", default_currency="USD")
        self.add("Account", "EXP", company="JF", root_type="Expense", account_type="", is_group=0, account_currency="CNY", disabled=0)
        self.add("Account", "BANK", company="JF", root_type="Asset", account_type="Bank", is_group=0, account_currency="CNY", disabled=0)
        frappe = types.ModuleType("frappe")
        frappe.session = Row(user="finance-user@example.invalid")
        frappe.PermissionError = PermissionError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda **kwargs: lambda method: method
        frappe.has_permission = Mock(side_effect=lambda doctype, action, **kwargs: (doctype, action) not in self.denied)
        frappe.get_doc = Mock(side_effect=self.get_doc)
        frappe.get_list = Mock(side_effect=self.get_list)
        frappe.get_meta = Mock(side_effect=self.get_meta)
        frappe.delete_doc = Mock(side_effect=self.delete_doc)
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, permission_type, **kwargs: self.fields(doctype) - self.hidden.get((doctype, permission_type), set())
        naming = types.ModuleType("frappe.model.naming")
        naming.getseries = Mock(side_effect=self.getseries)
        self.frappe, self.naming = frappe, naming
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model, "frappe.model.naming": naming}):
            spec = importlib.util.spec_from_file_location("finance_dictionary_under_test", ROOT / "jifeng_erp/finance_dictionary.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.modules = {"frappe.model.naming": naming}

    def fields(self, doctype):
        return {DOCTYPE: self.dictionary_fields, "Company": self.company_fields, "Account": self.account_fields}[doctype]

    def add(self, doctype, name, **values):
        self.documents[doctype, name] = dict(doctype=doctype, name=name, **values)

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            document = self.Document(deepcopy(doctype))
            document.__dict__["_new_document"] = True
            return document
        return self.Document(deepcopy(self.documents[doctype, name]))

    def get_meta(self, doctype):
        fields = [Row(fieldname=f, permlevel=0, mask=(doctype, f) in self.masked,
                      read_only=f in ("legacy_number", "account", "request_id") if doctype == DOCTYPE else False,
                      set_only_once=f in ("company", "dictionary_type", "request_id") if doctype == DOCTYPE else False)
                  for f in self.fields(doctype)]
        for field in fields:
            if (doctype, field.fieldname) in self.readonly:
                field.read_only = True
        return Row(fields=fields, get_permlevel_access=lambda *args, **kwargs: [],
                   get_field=lambda name: next((field for field in fields if field.fieldname == name), None))

    def get_list(self, doctype, fields, filters, limit_start=0, limit_page_length=25, **kwargs):
        self.assertLessEqual(set(kwargs), {"order_by", "or_filters"})
        rows = [row for (kind, _), row in self.documents.items() if kind == doctype
                and all(row.get(field) == value for field, value in filters.items())]
        rows.sort(key=lambda row: row["name"])
        return [{field: row.get(field) for field in fields} for row in rows[limit_start:limit_start + limit_page_length]]

    def getseries(self, key, digits):
        self.series[key] = self.series.get(key, 0) + 1
        return str(self.series[key]).zfill(digits)

    def delete_doc(self, doctype, name):
        self.get_doc(doctype, name).check_permission("delete")
        if name in self.links:
            raise ValueError("native LinkExistsError")
        del self.documents[doctype, name]

    def create(self, **changes):
        payload = dict(company="JF", dictionary_type="费用科目", request_id=REQUEST,
                       legacy_number="", value="  办公费用  ", remarks=" 原始\n备注 ", inactive=0)
        payload.update(changes)
        with patch.dict(sys.modules, self.modules):
            return self.module.save_dictionary(payload)

    def update(self, dto, **changes):
        payload = {field: dto[field] for field in ("name", "modified", "company", "dictionary_type", "legacy_number", "value", "remarks", "inactive")}
        payload.update(changes)
        return self.module.save_dictionary(payload)

    def test_init_and_missing_creation_are_readonly_and_dto_is_complete(self):
        dto = self.module.init_dictionary("费用科目", "JF")
        self.assertEqual(set(dto), set(self.module.FIELDS) | {"can_write", "can_create"})
        self.assertEqual((dto["legacy_number"], dto["inactive"], dto["account"], dto["can_create"]), ("", 0, None, True))
        self.assertIsNone(self.module.read_creation(REQUEST, "费用科目", "JF"))
        self.naming.getseries.assert_not_called()
        self.assertEqual(self.native_calls, [])

    def test_native_insert_stable_uuid_name_raw_text_and_independent_series(self):
        dto = self.create()
        self.assertEqual(dto["name"], "JF-FIN-" + REQUEST)
        self.assertEqual((dto["legacy_number"], dto["value"], dto["remarks"]), ("001", "  办公费用  ", " 原始\n备注 "))
        self.assertNotIn("name", self.native_calls[0][1])
        self.assertNotIn("flags", self.native_calls[0][1])
        self.assertEqual(self.module.read_creation(REQUEST, "费用科目", "JF"), dto)
        bank = self.create(dictionary_type="收款银行", request_id="17764cc5-7aac-48a0-8f0c-a599f180fc4c")
        other = self.create(company="OTHER", request_id="9f2a03b8-2790-4948-86c3-0b8bff92d545")
        self.assertEqual((bank["legacy_number"], other["legacy_number"]), ("01", "001"))
        self.assertEqual(len(self.series), 3)
        self.assertTrue(all(len(key) < 140 for key in self.series))

    def test_native_save_version_guard_and_readonly_number(self):
        dto = self.create()
        result = self.update(dto, value=" 新名称 ", remarks="", inactive=1)
        self.assertEqual((result["value"], result["inactive"], result["legacy_number"]), (" 新名称 ", 1, "001"))
        self.assertNotEqual(result["modified"], dto["modified"])
        self.frappe.get_doc.assert_any_call(DOCTYPE, dto["name"], for_update=True)
        with self.assertRaisesRegex(ValueError, "已被修改"):
            self.update(dto, value="过期保存")
        with self.assertRaisesRegex(ValueError, "编号不能修改"):
            self.update(result, legacy_number="999")
        self.assertEqual(self.module.read_dictionary(result["name"], "费用科目", "JF"), result)

    def test_create_and_controller_reject_number_tampering_blank_value_and_bad_uuid(self):
        for changes in ({"legacy_number": "001"}, {"value": " \t "}, {"request_id": "not-a-uuid"},
                        {"request_id": "c9a22b22-4e5d-16f2-a3c3-1cb6fb7f2c1e"}, {"account": "EXP"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)
        dto = self.create()
        document = self.get_doc(DOCTYPE, dto["name"])
        document.legacy_number = "wrong"
        with self.assertRaisesRegex(ValueError, "编号不能修改"):
            self.module.validate_dictionary(document)

    def test_current_company_type_row_field_mask_and_native_permissions(self):
        dto = self.create()
        for company, kind in (("OTHER", "费用科目"), ("JF", "收款银行")):
            with self.subTest(company=company, kind=kind), self.assertRaises(ValueError):
                self.module.read_dictionary(dto["name"], kind, company)
        self.row_denied.add((DOCTYPE, dto["name"], "read"))
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(dto["name"], "费用科目", "JF")
        self.assertIsNone(self.module.read_creation(REQUEST, "费用科目", "JF"))
        self.row_denied.clear()
        for mode in ("guest", "type", "field", "mask"):
            with self.subTest(mode=mode):
                self.frappe.session.user = "Guest" if mode == "guest" else "ordinary"
                self.denied = {(DOCTYPE, "read")} if mode == "type" else set()
                self.hidden = {(DOCTYPE, "read"): {"value"}} if mode == "field" else {}
                self.masked = {(DOCTYPE, "value")} if mode == "mask" else set()
                self.frappe.get_list.reset_mock()
                with self.assertRaises(PermissionError):
                    self.module.list_dictionary("费用科目", "JF")
                self.frappe.get_list.assert_not_called()

    def test_denied_rows_do_not_affect_list_count_scope_paging_and_keyword(self):
        dto = self.create()
        denied = self.create(request_id="17764cc5-7aac-48a0-8f0c-a599f180fc4c", value="拒读")
        self.create(dictionary_type="收款银行", request_id="9f2a03b8-2790-4948-86c3-0b8bff92d545")
        self.row_denied.add((DOCTYPE, denied["name"], "read"))
        result = self.module.list_dictionary("费用科目", "JF", search="%_", start=0, page_length=1)
        self.assertEqual((result["total"], result["rows"][0]["name"]), (1, dto["name"]))
        arguments = self.frappe.get_list.call_args.kwargs
        self.assertEqual(arguments["filters"], {"company": "JF", "dictionary_type": "费用科目", "inactive": 0})
        self.assertEqual(arguments["or_filters"][0], ["legacy_number", "like", "%\\%\\_%"])
        self.assertNotIn("ignore_permissions", arguments)
        self.assertNotIn("拒读", str(result))

    def test_write_create_field_and_readonly_permissions(self):
        self.denied.add((DOCTYPE, "create"))
        self.assertFalse(self.module.init_dictionary("费用科目", "JF")["can_create"])
        with self.assertRaises(PermissionError):
            self.create()
        self.assertEqual(self.native_calls, [])
        self.denied.clear()
        dto = self.create()
        self.row_denied.add((DOCTYPE, dto["name"], "write"))
        self.assertFalse(self.module.read_dictionary(dto["name"], "费用科目", "JF")["can_write"])
        with self.assertRaises(PermissionError):
            self.update(dto)
        self.row_denied.clear()
        for mode in ("field", "readonly"):
            with self.subTest(mode=mode):
                self.hidden = {(DOCTYPE, "write"): {"remarks"}} if mode == "field" else {}
                self.readonly = {(DOCTYPE, "remarks")} if mode == "readonly" else set()
                with self.assertRaises(PermissionError):
                    self.update(dto, remarks="改备注")

    def test_paging_clamps_after_rows_disappear_and_rejects_partial_boundaries(self):
        dto = self.create()
        result = self.module.list_dictionary("费用科目", "JF", start=25, page_length=25)
        self.assertEqual((result["start"], result["total"], result["rows"][0]["name"]), (0, 1, dto["name"]))
        with self.assertRaisesRegex(ValueError, "完整分页"):
            self.module.list_dictionary("费用科目", "JF", start=1, page_length=25)
        self.update(dto, inactive=1)
        empty = self.module.list_dictionary("费用科目", "JF", start=25, page_length=25)
        self.assertEqual((empty["start"], empty["total"], empty["rows"]), (0, 0, []))

    def test_identity_and_ordinary_mapping_changes_rejected_in_controller(self):
        dto = self.create()
        for field, value in (("company", "OTHER"), ("dictionary_type", "收款银行"),
                             ("request_id", "17764cc5-7aac-48a0-8f0c-a599f180fc4c"), ("account", "EXP")):
            with self.subTest(field=field):
                document = self.get_doc(DOCTYPE, dto["name"])
                document[field] = value
                with self.assertRaises(ValueError):
                    self.module.validate_dictionary(document)
        with self.assertRaises(ValueError):
            self.update(dto, account="EXP")

    def test_advanced_mapping_is_explicit_scoped_and_historical_inactive_editable(self):
        dto = self.create()
        mapped = self.module.set_dictionary_account(dto["name"], "费用科目", "JF", dto["modified"], "EXP")
        self.assertEqual(mapped["account"], "EXP")
        self.assertEqual(self.module.require_dictionary_account(mapped["name"], "费用科目", "JF"), "EXP")
        self.documents["Account", "EXP"]["disabled"] = 1
        with self.assertRaises(ValueError):
            self.module.require_dictionary_account(mapped["name"], "费用科目", "JF")
        historical = self.update(mapped, inactive=1, remarks="历史备注")
        self.assertEqual((historical["account"], historical["inactive"]), ("EXP", 1))
        cleared = self.module.set_dictionary_account(historical["name"], "费用科目", "JF", historical["modified"], None)
        self.assertIsNone(cleared["account"])
        with self.assertRaises(ValueError):
            self.module.require_dictionary_account(cleared["name"], "费用科目", "JF")

    def test_mapping_wrong_type_company_currency_group_disabled_and_permissions(self):
        dto = self.create()
        original = deepcopy(self.documents["Account", "EXP"])
        for changes in ({"company": "OTHER"}, {"account_currency": "USD"}, {"is_group": 1},
                        {"root_type": "Income"}, {"disabled": 1}):
            with self.subTest(changes=changes):
                self.documents["Account", "EXP"] = dict(original, **changes)
                with self.assertRaises(ValueError):
                    self.module.set_dictionary_account(dto["name"], "费用科目", "JF", dto["modified"], "EXP")
        self.documents["Account", "EXP"] = original
        self.row_denied.add(("Account", "EXP", "read"))
        with self.assertRaises(PermissionError):
            self.module.set_dictionary_account(dto["name"], "费用科目", "JF", dto["modified"], "EXP")
        self.row_denied.clear()
        bank = self.create(dictionary_type="付款银行", request_id="17764cc5-7aac-48a0-8f0c-a599f180fc4c")
        result = self.module.set_dictionary_account(bank["name"], "付款银行", "JF", bank["modified"], "BANK")
        self.assertEqual(result["account"], "BANK")
        with self.assertRaises(ValueError):
            self.module.set_dictionary_account(result["name"], "付款银行", "JF", result["modified"], "EXP")

    def test_unknown_create_is_read_checked_without_retry_and_native_failures_propagate(self):
        dto = self.create()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.create()
        self.assertEqual(self.module.read_creation(REQUEST, "费用科目", "JF"), dto)
        self.native_error = ValueError("native save failure")
        count = len(self.native_calls)
        with self.assertRaisesRegex(ValueError, "native save failure"):
            self.update(dto, remarks="失败保存")
        self.assertEqual(len(self.native_calls), count + 1)
        self.assertEqual(self.module.read_dictionary(dto["name"], "费用科目", "JF"), dto)

    def test_delete_checks_version_native_permission_and_native_links(self):
        dto = self.create()
        self.denied.add((DOCTYPE, "delete"))
        with self.assertRaises(PermissionError):
            self.module.delete_dictionary(dto["name"], "费用科目", "JF", dto["modified"])
        self.denied.clear()
        with self.assertRaisesRegex(ValueError, "已被修改"):
            self.module.delete_dictionary(dto["name"], "费用科目", "JF", "2026-10-05 22:10:00")
        self.frappe.delete_doc.assert_not_called()
        self.links.add(dto["name"])
        with self.assertRaisesRegex(ValueError, "LinkExistsError"):
            self.module.delete_dictionary(dto["name"], "费用科目", "JF", dto["modified"])
        self.frappe.delete_doc.assert_called_once_with(DOCTYPE, dto["name"])
        self.links.clear()
        self.assertEqual(self.module.delete_dictionary(dto["name"], "费用科目", "JF", dto["modified"]), {"deleted": True})

    def test_doctype_is_independent_tracked_readonly_number_and_native_controller(self):
        directory = ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_finance_dictionary"
        metadata = json.loads((directory / "jifeng_finance_dictionary.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["name"], metadata["track_changes"]), (DOCTYPE, 1))
        fields = {field["fieldname"]: field for field in metadata["fields"]}
        self.assertEqual((fields["legacy_number"]["read_only"], fields["legacy_number"]["no_copy"]), (1, 1))
        self.assertTrue(all(fields[field]["set_only_once"] for field in ("company", "dictionary_type", "request_id")))
        self.assertEqual(fields["account"]["options"], "Account")
        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = self.Document
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model.document": document_module,
                                      "frappe.model.naming": self.naming, "jifeng_erp.finance_dictionary": self.module}):
            spec = importlib.util.spec_from_file_location("finance_dictionary_controller", directory / "jifeng_finance_dictionary.py")
            controller = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(controller)
            document = controller.JifengFinanceDictionary(doctype=DOCTYPE, company="JF", dictionary_type="费用科目",
                                                         value="原生表单新增", remarks="", inactive=0)
            document.__dict__["_new_document"] = True
            document.before_insert()
            document.autoname()
            document.validate()
        self.assertEqual(document.legacy_number, "001")
        self.assertEqual(document.name, "JF-FIN-" + document.request_id)
        with self.assertRaisesRegex(ValueError, "不能重命名"):
            document.before_rename(document.name, "新身份")


if __name__ == "__main__":
    unittest.main()
