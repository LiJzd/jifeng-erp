"""Behavioral models for ordinary modeling records; no database or browser."""

import ast
from copy import deepcopy
from datetime import datetime
from functools import wraps
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


ROOT = Path(__file__).parents[1]
DT = "Jifeng Plate Modeling"
RAW = ("legacy_number", "model_number", "product_code", "reference_unit_price", "modeler_text",
       "model_date", "remarks", "legacy_copy")
SOURCES = {"Item": {"name"}, "Jifeng Plate": {"name", "company"},
           "Jifeng Expense Voucher": {"name", "company", "docstatus"},
           "File": {"name", "file_name", "file_url", "creation", "is_private", "attached_to_doctype",
                    "attached_to_name", "attached_to_field"}}


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


class Document(Row):
    environment = None

    @property
    def meta(self):
        return self.environment.meta

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, action):
        env = self.environment
        return ((self.doctype, action) not in env.denied_roles and (self.name, action) not in env.denied_docs
                and self.company not in env.denied_companies)

    def check_permission(self, action):
        self.environment.calls.append((self.doctype, self.name, action))
        if not self.has_permission(action):
            raise PermissionError("native " + action)

    def get_doc_before_save(self):
        previous = self.environment.records.get(self.name)
        return Document(deepcopy(previous)) if previous else None

    def save(self):
        env = self.environment
        new = self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            env.api.before_insert_record(self)
        env.api.validate_record(self)
        if any(row.request_id == self.request_id and row.name != self.name for row in env.records.values()):
            env.frappe.local.message_log = ["private unique SQL content"]
            raise KeyError("private unique SQL content")
        env.tick += 1
        self.name = self.name or "MOD-" + str(env.tick)
        self.modified = datetime(2026, 10, 1, 12, 0, 0, env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.records[self.name] = Row(deepcopy(dict(self)))
        if not new:
            env.versions.append(self.name)
        if env.fail_after_save:
            for field in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
                getattr(env.frappe.db, field).add(lambda: env.cleanup_calls.append(field))
            env.frappe.local._realtime_log = ["failed write event"]
            env.frappe.flags.currently_saving = ["failed context"]
            raise ValueError("post save failure")
        return self


class TestPlateModeling(unittest.TestCase):
    def setUp(self):
        schema_path = ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_plate_modeling/jifeng_plate_modeling.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.fields = {row["fieldname"]: Row(row) for row in self.schema["fields"]}
        self.meta = types.SimpleNamespace(get_field=lambda field: self.fields.get(field))
        self.records, self.sources, self.versions, self.snapshots = {}, {}, [], {}
        self.denied_roles, self.denied_docs, self.denied_companies = set(), set(), set()
        self.hidden = {action: {doctype: set() for doctype in (DT, "Company", *SOURCES)} for action in ("read", "write")}
        self.calls, self.cleanup_calls, self.tick = [], [], 1
        self.fail_after_save, self.hide_receipts, self.forced_rows = False, False, None
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Row(user="stock@example.invalid")
        self.frappe.flags, self.frappe.local = Row(), types.SimpleNamespace()
        self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, LookupError
        self.frappe.TimestampMismatchError = RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.has_permission = lambda doctype, action: (doctype, action) not in self.denied_roles
        self.frappe.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            **{field: Queue() for field in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        native_model = types.ModuleType("frappe.model")
        native_model.get_permitted_fields = self.columns
        utils = types.ModuleType("frappe.utils")
        utils.today = lambda: "2026-10-01"
        atomic = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_atomic", "exec"), namespace)
        atomic._atomic = namespace["_atomic"]
        modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": native_model,
                                           "frappe.utils": utils, "jifeng_erp.personnel_separation": atomic})
        modules.start()
        self.addCleanup(modules.stop)
        self.api = self.load("modeling_under_test", ROOT / "jifeng_erp/plate_modeling.py")
        Document.environment = self
        self.source("Item", "ITEM-1")
        self.source("Jifeng Plate", "PLATE-1", company="C-1")
        self.source("Jifeng Expense Voucher", "EXP-1", company="C-1", docstatus=1)

    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def columns(self, doctype, permission_type, **kwargs):
        fields = set(self.fields) | {"name", "modified", "creation", "owner", "modified_by"} if doctype == DT else SOURCES[doctype]
        return fields - self.hidden[permission_type][doctype]

    def new_doc(self, doctype):
        self.assertEqual(doctype, DT)
        return Document(doctype=doctype, name=None, company="DENIED-DEFAULT", __islocal=1,
                        legacy_copy=0, model_date="2026-10-01")

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            def permission(action):
                self.calls.append((doctype, name, action))
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company permission")
            return Row(name=name, check_permission=permission)
        if doctype == DT:
            return Document(deepcopy(self.records[name]))
        if (doctype, name) not in self.sources:
            raise LookupError("missing source")
        return self.sources[doctype, name]

    def source(self, doctype, name, **values):
        def check(action):
            self.calls.append((doctype, name, action))
            if not permitted(action):
                raise PermissionError("source permission")
        def permitted(action):
            return (doctype, action) not in self.denied_roles and (name, action) not in self.denied_docs
        row = Row(doctype=doctype, name=name, check_permission=check, has_permission=permitted, **values)
        self.sources[doctype, name] = row
        return row

    def get_list(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, DT)
        if self.hide_receipts and "request_id" in filters:
            return []
        if self.forced_rows is not None:
            return deepcopy(self.forced_rows)
        result = []
        for row in self.records.values():
            if (row.name, "read") in self.denied_docs or row.company in self.denied_companies:
                continue
            if any(row.get(field) != value for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(value.strip("%") in str(row.get(field) or "")
                    for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Row({field: row.get(field) for field in fields if field not in self.hidden["read"][DT]}))
        return result

    def get_all(self, doctype, filters, **kwargs):
        self.assertEqual(doctype, "File")
        self.assertEqual(filters.get("attached_to_doctype"), DT)
        self.assertEqual(filters.get("attached_to_field"), "design_image")
        return [name for (kind, name), row in self.sources.items() if kind == "File"
                and all(row.get(field) == value for field, value in filters.items())]

    def savepoint(self, checkpoint):
        self.snapshots[checkpoint] = deepcopy((self.records, self.versions))

    def rollback(self, save_point):
        self.records, self.versions = deepcopy(self.snapshots[save_point])

    def payload(self, **changes):
        return {"company": "C-1", "request_id": str(uuid4()), "legacy_number": " 旧 001 ",
                "model_number": " 模001 ", "product_code": " 产品原文 ", "modeler_text": " 外部建模人 ",
                "reference_unit_price": "01.0000000000000000001", "model_date": "2026-10-01",
                "remarks": " 单行原文 ", "legacy_copy": 0, **changes}

    def saved(self, **changes):
        return self.api.save_record(self.payload(**changes))

    def update(self, row, **changes):
        return self.api.save_record({"name": row["name"], "version": row["version"], **changes})

    def attachment(self, row, name="FILE-1", **changes):
        return self.source("File", name, file_name="design.png", file_url="/private/files/design.png", is_private=1,
                           attached_to_doctype=DT, attached_to_name=row["name"], attached_to_field="design_image",
                           unique_url="/private/files/design.png?fid=" + name, creation=datetime(2026, 10, 1),
                           is_downloadable=lambda: True, **changes)

    def report(self, filters=None):
        with patch.dict(sys.modules, {"jifeng_erp.plate_modeling": self.api}):
            report = self.load("modeling_report", ROOT / "jifeng_erp/jifeng_erp/report/jifeng_plate_modeling_catalog/jifeng_plate_modeling_catalog.py")
            return report.execute(filters or {"company": "C-1"})

    def test_schema_is_ordinary_versioned_and_has_one_image_no_employee_or_posting(self):
        self.assertFalse(self.schema.get("is_submittable"))
        self.assertFalse(self.schema.get("allow_rename"))
        self.assertEqual(self.schema["track_changes"], 1)
        self.assertEqual([field for field in self.schema["field_order"] if field in RAW], list(RAW))
        self.assertEqual([name for name, field in self.fields.items() if field.fieldtype == "Attach Image"], ["design_image"])
        self.assertEqual(self.fields["modeler_text"].fieldtype, "Data")
        self.assertEqual(self.fields["reference_unit_price"].fieldtype, "Data")
        self.assertNotIn("default", self.fields["reference_unit_price"])
        self.assertFalse(any(field.get("options") == "Employee" for field in self.fields.values()))
        self.assertEqual({name for name, field in self.fields.items() if field.get("unique")}, {"request_id"})
        self.assertEqual(set(self.api.LINKS), {"item_code", "plate", "expense_voucher"})
        self.assertFalse(any(row.get("submit") or row.get("cancel") or row.get("delete") for row in self.schema["permissions"]))

    def test_init_clears_company_and_other_defaults_and_keeps_today_and_copy_zero(self):
        row = self.api.init_record()
        self.assertIsNone(row["company"])
        self.assertIsNone(row["name"])
        self.assertEqual(row["model_date"], "2026-10-01")
        self.assertEqual(row["legacy_copy"], 0)
        self.assertTrue(row["field_meta"]["design_image"]["read_only"])
        self.assertFalse(self.calls)

    def test_explicit_company_required_and_native_user_permission_enforced(self):
        for company in (None, "", " C-1 "):
            with self.subTest(company=company), self.assertRaises(ValueError):
                self.saved(company=company)
        self.denied_companies.add("C-1")
        with self.assertRaises(PermissionError):
            self.api.init_record("C-1")
        with self.assertRaises(PermissionError):
            self.saved()
        self.assertFalse(self.records)

    def test_raw_fields_remain_independent_no_source_calls_no_business_writes(self):
        payload = self.payload(legacy_copy=1)
        row = self.api.save_record(payload)
        for field in RAW:
            self.assertEqual(row[field], payload[field])
        self.assertEqual({kind for kind, name, action in self.calls}, {DT, "Company"})
        self.assertEqual(len(self.records), 1)
        self.assertEqual(len(self.sources), 3)

    def test_reference_price_preserves_empty_zero_negative_zero_and_long_decimal_text(self):
        for value in (None, "", "0", "-0", ".5", "01.00", "1e3", "0.12345678901234567890123456789"):
            with self.subTest(value=value):
                self.assertEqual(self.saved(reference_unit_price=value)["reference_unit_price"], None if value == "" else value)
        for value in ("-0.1", "NaN", "Infinity", "1x", 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.saved(reference_unit_price=value)

    def test_date_requires_exact_calendar_date_and_explicit_empty_is_preserved(self):
        self.assertIsNone(self.saved(model_date="")["model_date"])
        for value in ("2026-02-30", "20261001", "2026-1-1", "2026-10-01T00:00:00", 20261001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.saved(model_date=value)

    def test_copy_only_accepts_checkbox_states_and_does_not_clone(self):
        for value in (False, True, 0, 1, "0", "1"):
            self.assertEqual(self.saved(legacy_copy=value)["legacy_copy"], int(value))
        for value in ("yes", 2, 0.0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.saved(legacy_copy=value)
        self.assertEqual(len(self.records), 6)

    def test_existing_save_changes_only_submitted_fields_and_records_native_version(self):
        row = self.saved(remarks="第一行\n第二行")
        self.fields["remarks"]["read_only"] = 1
        saved = self.update(row, model_number=" 更改 ")
        self.assertEqual(saved["remarks"], "第一行\n第二行")
        self.assertEqual(saved["model_number"], " 更改 ")
        self.assertEqual(self.versions, [row["name"]])
        self.assertTrue(saved["field_meta"]["company"]["read_only"])

    def test_existing_timestamp_conflict_and_missing_version_never_save(self):
        row = self.saved()
        for version in (None, "", "2026-10-01T12:00:00.999999", "not a date"):
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                self.api.save_record({"name": row["name"], "version": version, "remarks": "change"})
        self.assertFalse(self.versions)

    def test_uuid_receipt_same_request_returns_original_current_record_without_second_write(self):
        payload = self.payload()
        row = self.api.save_record(payload)
        changed = self.update(row, remarks="later")
        receipt = self.api.save_record(payload)
        self.assertEqual(receipt, changed)
        self.assertEqual(self.api.resolve_creation(payload["request_id"], "C-1"), {"status": "found", "archive": changed})
        self.assertEqual(len(self.records), 1)

    def test_receipt_digest_conflict_and_wrong_company_cannot_create_duplicate(self):
        payload = self.payload()
        self.api.save_record(payload)
        with self.assertRaisesRegex(ValueError, "不自动重复保存"):
            self.api.save_record(payload | {"remarks": "other"})
        with self.assertRaisesRegex(ValueError, "不自动重复保存"):
            self.api.save_record(payload | {"company": "C-2"})
        self.assertEqual(len(self.records), 1)

    def test_receipt_unknown_absence_is_read_only_and_visibility_gated(self):
        identifier = str(uuid4())
        self.assertEqual(self.api.resolve_creation(identifier, "C-1"), {"status": "not_found"})
        self.assertFalse(self.records)
        self.frappe.new_doc.assert_not_called()
        self.hidden["read"][DT].add("request_id")
        with self.assertRaises(PermissionError):
            self.api.resolve_creation(identifier, "C-1")

    def test_uuid_validation_rejects_noncanonical_and_reserved_audit_payload(self):
        for identifier in (None, "", str(uuid4()).upper(), "00000000-0000-1000-8000-000000000000"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.saved(request_id=identifier)
        for field in ("request_digest", "docstatus", "force", "total_cost", "employee", "copy_from"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.saved(**{field: "invented"})

    def test_unique_collision_sanitizes_native_private_message_and_rolls_back(self):
        payload = self.payload()
        self.api.save_record(payload)
        self.hide_receipts = True
        self.frappe.local.message_log = ["before"]
        with self.assertRaisesRegex(ValueError, "不自动重复保存") as caught:
            self.api.save_record(payload | {"remarks": "other"})
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, ["before"])
        self.assertEqual(len(self.records), 1)

    def test_native_insert_receipt_is_server_only_and_exact_context_required(self):
        document = self.new_doc(DT)
        document.company = "C-1"
        document.save()
        self.assertTrue(document.request_id)
        self.assertTrue(document.request_digest)
        forged = self.new_doc(DT)
        forged.company, forged.request_id = "C-1", str(uuid4())
        with self.assertRaisesRegex(ValueError, "只能由服务器"):
            forged.save()
        missing = self.new_doc(DT)
        missing.company = "C-1"
        with self.assertRaisesRegex(ValueError, "只能由服务器"):
            self.api.validate_record(missing)

    def test_native_identity_company_uuid_digest_and_force_rename_protected(self):
        row = self.saved()
        for field, value in (("company", "C-2"), ("request_id", str(uuid4())), ("request_digest", "forged"), ("name", "other")):
            document = self.get_doc(DT, row["name"])
            document[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.api.validate_record(document)
        with self.assertRaises(ValueError):
            self.api.prevent_rename(self.get_doc(DT, row["name"]), row["name"], "other", True)
        with self.assertRaises(ValueError):
            self.update(row, company="C-2")
        with self.assertRaises(ValueError):
            self.update(row, request_id=str(uuid4()))

    def test_native_field_read_write_and_readonly_are_enforced_without_dropping_existing(self):
        row = self.saved()
        for action in ("read", "write"):
            self.hidden[action][DT].add("remarks")
            with self.subTest(action=action), self.assertRaises(PermissionError):
                self.update(row, remarks="denied")
            self.hidden[action][DT].clear()
        self.fields["remarks"]["read_only"] = 1
        with self.assertRaises(PermissionError):
            self.update(row, remarks="denied")
        self.assertFalse(self.versions)

    def test_read_hides_native_forbidden_fields_and_does_not_fallback_to_loaded_document(self):
        row = self.saved()
        self.hidden["read"][DT].add("remarks")
        self.assertNotIn("remarks", self.api.read_record(row["name"]))
        self.assertNotIn("remarks", self.api.list_records("C-1")[0])
        self.hidden["read"][DT].add("company")
        with self.assertRaises(PermissionError):
            self.api.read_record(row["name"])

    def test_explicit_links_require_read_select_same_company_and_source_fields(self):
        row = self.saved(item_code="ITEM-1", plate="PLATE-1", expense_voucher="EXP-1")
        self.assertEqual(row["expense_voucher_state"], "已提交")
        for kind, name in (("Item", "ITEM-1"), ("Jifeng Plate", "PLATE-1"), ("Jifeng Expense Voucher", "EXP-1")):
            self.assertIn((kind, name, "read"), self.calls)
            self.assertIn((kind, name, "select"), self.calls)
        self.assertTrue(any(call.kwargs.get("for_update") for call in self.frappe.get_doc.call_args_list
                            if call.args[0] in ("Item", "Jifeng Plate", "Jifeng Expense Voucher")))
        self.sources["Jifeng Plate", "PLATE-1"].company = "C-2"
        with self.assertRaises(PermissionError):
            self.saved(plate="PLATE-1")
        self.hidden["read"]["Jifeng Expense Voucher"].add("docstatus")
        with self.assertRaises(PermissionError):
            self.saved(expense_voucher="EXP-1")

    def test_expense_permission_optional_and_unsubmitted_or_cancelled_new_source_rejected(self):
        self.denied_roles.add(("Jifeng Expense Voucher", "read"))
        self.assertTrue(self.api.init_record("C-1")["field_meta"]["expense_voucher"]["read_only"])
        self.saved()
        with self.assertRaises(PermissionError):
            self.saved(expense_voucher="EXP-1")
        self.denied_roles.clear()
        for state in (0, 2):
            self.sources["Jifeng Expense Voucher", "EXP-1"].docstatus = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.saved(expense_voucher="EXP-1")

    def test_later_expense_cancellation_is_visible_trace_not_payment_or_approval(self):
        row = self.saved(expense_voucher="EXP-1")
        self.sources["Jifeng Expense Voucher", "EXP-1"].docstatus = 2
        reread = self.api.read_record(row["name"])
        self.assertEqual(reread["expense_voucher"], "EXP-1")
        self.assertEqual(reread["expense_voucher_state"], "已取消")
        changed = self.update(reread, remarks="其他原文仍可维护")
        self.assertEqual(changed["expense_voucher_state"], "已取消")
        self.assertNotIn("paid", str(changed))
        self.assertNotIn("approval_status", changed)

    def test_inaccessible_foreign_and_missing_sources_never_leak_link_names(self):
        row = self.saved(plate="PLATE-1", expense_voucher="EXP-1")
        self.denied_docs.add(("EXP-1", "read"))
        self.sources["Jifeng Plate", "PLATE-1"].company = "C-2"
        reread = self.api.read_record(row["name"])
        self.assertNotIn("expense_voucher", reread)
        self.assertNotIn("plate", reread)
        self.assertEqual(reread["expense_voucher_state"], "来源不可用")
        self.assertTrue(reread["field_meta"]["expense_voucher"]["read_only"])
        del self.sources["Jifeng Expense Voucher", "EXP-1"]
        self.assertNotIn("expense_voucher", self.api.list_records("C-1")[0])

    def test_image_new_record_must_be_saved_first_and_unavailable_url_is_not_returned(self):
        with self.assertRaisesRegex(ValueError, "先保存"):
            self.saved(design_image="/private/files/design.png")
        row = self.saved()
        self.records[row["name"]].design_image = "/private/files/legacy.png"
        reread = self.api.read_record(row["name"])
        self.assertNotIn("design_image", reread)
        self.assertEqual(reread["image_state"], {"url": "", "state": "附件不可用"})
        self.assertFalse(reread["field_meta"]["design_image"]["read_only"])

    def test_image_exact_private_binding_and_preview_unique_url_not_storage_url(self):
        row = self.saved()
        file = self.attachment(row)
        saved = self.update(row, design_image=file.file_url)
        self.assertEqual(saved["design_image"], file.file_url)
        self.assertEqual(saved["image_state"]["url"], file.unique_url)
        with self.assertRaises(PermissionError):
            self.update(saved, design_image=file.unique_url)
        self.assertEqual(self.api.list_image_attachments(row["name"])[0]["file_url"], file.file_url)

    def test_image_projection_explicit_null_does_not_fallback_to_full_document(self):
        row = self.saved()
        file = self.attachment(row)
        document = self.get_doc(DT, row["name"])
        document.design_image = file.file_url
        projected = self.api._pack(document, values={"name": row["name"], "company": "C-1",
                                   "modified": row["version"], "design_image": None}, include_meta=False)
        self.assertEqual(projected["image_state"], {"url": "", "state": "未上传"})
        self.assertIsNone(projected["design_image"])

    def test_image_alias_cannot_authorize_foreign_parent_field_public_or_denied_file(self):
        for field, value in (("attached_to_name", "other"), ("attached_to_field", "other_image"),
                             ("attached_to_doctype", "Jifeng Plate"), ("is_private", 0)):
            row = self.saved()
            file = self.attachment(row, name="FILE-" + row["name"])
            file[field] = value
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.update(row, design_image=file.file_url)
        row = self.saved()
        file = self.attachment(row)
        self.denied_docs.add((file.name, "read"))
        with self.assertRaises(PermissionError):
            self.update(row, design_image=file.file_url)
        self.assertEqual(self.api.list_image_attachments(row["name"]), [])

    def test_image_downloadability_and_file_field_permission_are_both_required(self):
        row = self.saved()
        file = self.attachment(row)
        file.is_downloadable = lambda: False
        with self.assertRaises(PermissionError):
            self.update(row, design_image=file.file_url)
        file.is_downloadable = lambda: True
        self.hidden["read"]["File"].add("file_url")
        with self.assertRaises(PermissionError):
            self.update(row, design_image=file.file_url)
        with self.assertRaises(PermissionError):
            self.api.list_image_attachments(row["name"])

    def test_private_storage_urls_reject_query_traversal_external_and_encoded_aliases(self):
        for value in ("https://example.invalid/design.png", "/files/design.png", "/private/files/../x.png",
                      "/private/files/%2fsecret.png", "/private/files/%252e%252e", "/private/files/a.png?fid=x",
                      "/private/files/a.png#x", "/private/files/a\\x.png", "/private/files/a%00.png"):
            with self.subTest(value=value):
                self.assertFalse(self.api._private_url(value))

    def test_attachment_reconciliation_is_readonly_and_requires_record_and_field_write(self):
        row = self.saved()
        self.attachment(row)
        before = deepcopy(self.records)
        self.api.list_image_attachments(row["name"])
        self.assertEqual(self.records, before)
        self.hidden["write"][DT].add("design_image")
        with self.assertRaises(PermissionError):
            self.api.list_image_attachments(row["name"])

    def test_failed_post_save_rolls_back_rows_versions_queues_and_native_context(self):
        row = self.saved()
        before = deepcopy(self.records)
        self.frappe.local._realtime_log = ["before"]
        self.frappe.flags.currently_saving = ["before"]
        def callback():
            return None
        for field in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            getattr(self.frappe.db, field).add(callback)
        self.fail_after_save = True
        with self.assertRaises(ValueError):
            self.update(row, remarks="failure")
        self.assertEqual(self.records, before)
        self.assertFalse(self.versions)
        self.assertEqual(self.frappe.local._realtime_log, ["before"])
        self.assertEqual(self.frappe.flags.currently_saving, ["before"])
        for field in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            self.assertEqual(getattr(self.frappe.db, field)._functions, [callback])

    def test_list_company_raw_search_native_permissions_and_field_projection(self):
        first = self.saved(model_number="needle")
        self.saved(company="C-2", model_number="needle")
        self.saved(model_number="other")
        self.assertEqual([row["name"] for row in self.api.list_records("C-1", "needle")], [first["name"]])
        self.denied_docs.add((first["name"], "read"))
        self.assertEqual(self.api.list_records("C-1", "needle"), [])
        self.denied_docs.clear()
        self.hidden["read"][DT].add("model_number")
        self.assertEqual(self.api.list_records("C-1", "needle"), [])

    def test_list_rejects_missing_identity_foreign_rows_and_concurrent_version(self):
        row = self.saved()
        self.forced_rows = [Row(name=row["name"], modified=row["version"], company="C-2")]
        self.assertEqual(self.api.list_records("C-1"), [])
        self.forced_rows = [Row(name=row["name"], company="C-1")]
        with self.assertRaises(PermissionError):
            self.api.list_records("C-1")
        self.forced_rows = [Row(name=row["name"], company="C-1", modified="2026-10-01T12:00:00.999999")]
        with self.assertRaises(ValueError):
            self.api.list_records("C-1")

    def test_duplicate_list_sources_and_attachment_limits_are_explicit(self):
        row = self.saved()
        self.forced_rows = [Row(name=row["name"], company="C-1", modified=row["version"])] * 101
        with self.assertRaises(PermissionError):
            self.api.list_records("C-1")
        self.forced_rows = None
        self.frappe.get_all.side_effect = None
        self.frappe.get_all.return_value = ["FILE"] * 101
        with self.assertRaisesRegex(ValueError, "附件较多"):
            self.api.list_image_attachments(row["name"])

    def test_report_keeps_price_raw_and_expense_current_state_without_total_or_paid(self):
        self.saved(expense_voucher="EXP-1")
        self.sources["Jifeng Expense Voucher", "EXP-1"].docstatus = 2
        columns, rows = self.report()
        price = next(column for column in columns if column["fieldname"] == "reference_unit_price")
        self.assertEqual(price["fieldtype"], "Data")
        self.assertEqual(rows[0]["reference_unit_price"], "01.0000000000000000001")
        self.assertEqual(rows[0]["expense_voucher_state"], "已取消")
        self.assertNotIn("design_image", rows[0])
        self.assertFalse(any("total" in column["fieldname"] or "paid" in column["fieldname"] for column in columns))

    def test_report_omits_unreadable_columns_and_raw_unavailable_urls(self):
        row = self.saved(expense_voucher="EXP-1")
        self.records[row["name"]].design_image = "/private/files/hidden.png"
        self.hidden["read"][DT].add("remarks")
        self.denied_roles.add(("Jifeng Expense Voucher", "read"))
        columns, rows = self.report()
        self.assertNotIn("remarks", {column["fieldname"] for column in columns})
        self.assertNotIn("expense_voucher", rows[0])
        self.assertEqual(rows[0]["image_url"], "")
        self.assertEqual(rows[0]["image_status"], "附件不可用")

    def test_guest_and_native_role_denials_apply_to_every_entrypoint(self):
        self.frappe.session.user = "Guest"
        for call in (lambda: self.api.init_record(), lambda: self.saved(), lambda: self.api.list_records("C-1"),
                     lambda: self.api.resolve_creation(str(uuid4()), "C-1"), lambda: self.api.read_record("missing")):
            with self.assertRaises(PermissionError):
                call()
        self.frappe.session.user = "stock@example.invalid"
        self.denied_roles.add((DT, "create"))
        with self.assertRaises(PermissionError):
            self.saved()
        self.denied_roles.clear()
        row = self.saved()
        self.denied_roles.add((DT, "write"))
        with self.assertRaises(PermissionError):
            self.update(row, remarks="denied")


if __name__ == "__main__":
    unittest.main()
