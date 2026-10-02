"""Customer overlay permission, atomicity and source models; no DB or GUI."""

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


class NativeDuplicateEntryError(NameError):
    """The installed Frappe duplicate-name category, distinct from validation."""


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


class Meta:
    def __init__(self, fields):
        self.fields = [Record(field) for field in fields]

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return []


class Document(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, name, value):
        self[name] = value

    def has_permission(self, permission):
        return (
            (self.doctype, permission) not in self.environment.denials
            and self.name not in self.environment.hidden_names
        )

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError(self.doctype + " " + permission)

    def get_doc_before_save(self):
        value = self.environment.records.get((self.doctype, self.name))
        return Document(deepcopy(value)) if value else None

    def save(self):
        env = self.environment
        self.check_permission("create" if self.is_new() else "write")
        if self.doctype == env.module.PROFILE:
            env.module.before_insert_profile(self) if self.is_new() else None
            env.module.validate_profile(self)
        else:
            if not self.customer_name or not self.customer_type:
                raise ValueError("native customer mandatory")
        if env.fail_save and self.doctype == env.module.PROFILE:
            env.frappe.db.after_commit.add(env.new_callback)
            env.frappe.local._realtime_log = ["native-event"]
            env.frappe.flags.currently_saving = ["native-save"]
            raise ValueError("native save failure")
        if self.is_new():
            if self.doctype == env.module.PROFILE and any(
                doctype == env.module.PROFILE
                and row.get("request_id") == self.request_id
                for (doctype, _), row in env.records.items()
            ):
                raise ValueError("unique request constraint")
            self.name = (
                "PROFILE-" + str(env.tick)
                if self.doctype == env.module.PROFILE
                else self.customer_name.strip()
                if env.customer_naming == "Customer Name"
                else "CUST-" + str(env.tick)
            )
            if self.doctype == "Customer" and env.customer_naming == "Customer Name":
                base, suffix = self.name, 1
                while (self.doctype, self.name) in env.records:
                    self.name = base + "-" + str(suffix)
                    suffix += 1
            if self.doctype == env.module.PROFILE and any(
                kind == env.module.PROFILE
                and row.get("binding_key") == self.binding_key
                for (kind, _), row in env.records.items()
            ):
                raise ValueError("unique binding constraint")
            if (self.doctype, self.name) in env.records:
                raise env.frappe.DuplicateEntryError(
                    self.doctype, self.name, ValueError("native duplicate name")
                )
            self.creation = "2026-10-02T00:00:00.000001"
            self.owner = env.frappe.session.user
        env.tick += 1
        self.modified = "2026-10-02T00:00:" + str(env.tick).zfill(2) + ".123456"
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.records[(self.doctype, self.name)] = deepcopy(dict(self))
        env.saves.append(self.doctype)
        return self


class TestCustomerArchive(unittest.TestCase):
    def setUp(self):
        self.records, self.metas, self.snapshots = {}, {}, {}
        self.denials, self.hidden_names = set(), set()
        self.hidden_fields = {}
        self.saves, self.tick, self.fail_save = [], 1, False
        self.customer_naming, self.datetime_fresh = "Customer Name", False
        self.hook = None
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = (
            PermissionError,
            RuntimeError,
        )
        self.frappe.DuplicateEntryError = NativeDuplicateEntryError
        self.frappe.session = Record(user="sales-manager@example.invalid")
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.throw = lambda text, error=ValueError: (_ for _ in ()).throw(
            error(text)
        )
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = self.new_doc
        self.frappe.get_meta = lambda doctype: self.metas[doctype]
        self.capability_calls = []

        def capability(doctype, permission, doc=None, throw=None):
            self.capability_calls.append((doctype, permission, throw))
            return (doctype, permission) not in self.denials and (
                not doc or doc.name not in self.hidden_names
            )

        self.frappe.has_permission = capability
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.db = types.SimpleNamespace(
            get_value=Mock(side_effect=self.get_value),
            savepoint=self.savepoint,
            rollback=self.rollback,
            **{
                name: Queue()
                for name in (
                    "before_commit",
                    "after_commit",
                    "before_rollback",
                    "after_rollback",
                )
            },
        )
        self.new_callback = Mock()
        naming = types.ModuleType("frappe.model.naming")
        naming.getseries = self.getseries
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse(
            (ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8")
        )
        definition = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_atomic"
        )
        namespace = {
            "frappe": self.frappe,
            "wraps": wraps,
            "deepcopy": deepcopy,
            "uuid4": uuid4,
        }
        exec(
            compile(
                ast.Module(body=[definition], type_ignores=[]), "native_atomic", "exec"
            ),
            namespace,
        )
        separation._atomic = namespace["_atomic"]
        self.modules = patch.dict(
            sys.modules,
            {
                "frappe": self.frappe,
                "frappe.model": model,
                "frappe.model.naming": naming,
                "jifeng_erp.personnel_separation": separation,
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(
            "customer_archive_model", ROOT / "jifeng_erp/customer_archive.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self
        definition = json.loads(
            (
                ROOT
                / "jifeng_erp/jifeng_erp/doctype/jifeng_customer_profile/jifeng_customer_profile.json"
            ).read_text()
        )
        self.metas[self.module.PROFILE] = Meta(
            definition["fields"]
            + [
                {"fieldname": name}
                for name in ("name", "modified", "creation", "modified_by", "owner")
            ]
        )
        self.metas["Customer"] = Meta(
            [
                {"fieldname": "name"},
                {"fieldname": "modified"},
                {"fieldname": "disabled"},
                {"fieldname": "customer_name", "fieldtype": "Data"},
                {
                    "fieldname": "customer_type",
                    "fieldtype": "Select",
                    "options": "Company\nIndividual\nPartnership",
                    "default": "Company",
                    "reqd": 1,
                },
                {
                    "fieldname": "customer_group",
                    "fieldtype": "Link",
                    "options": "Customer Group",
                },
                {
                    "fieldname": "naming_series",
                    "fieldtype": "Select",
                    "options": "CUST-.YYYY.-",
                },
            ]
        )
        self.metas["Customer"].fields.append(
            Record(fieldname="territory", fieldtype="Link", options="Territory")
        )
        self.metas[self.module.DICTIONARY] = Meta(
            [
                {"fieldname": name}
                for name in (
                    "name",
                    "modified",
                    "company",
                    "dictionary_type",
                    "value",
                    "inactive",
                )
            ]
        )
        self.metas["Company"] = Meta([])
        self.records[("Company", "Company A")] = {
            "doctype": "Company",
            "name": "Company A",
        }
        self.records[("Company", "Company B")] = {
            "doctype": "Company",
            "name": "Company B",
        }
        self.records[("Customer", "CUST-A")] = {
            "doctype": "Customer",
            "name": "CUST-A",
            "disabled": 0,
            "modified": "2026-10-02T00:00:00.123456",
            "customer_name": "Shared customer must not change",
        }

    def columns(self, doctype, permission_type="read", **kwargs):
        return {
            field.fieldname for field in self.metas[doctype].fields
        } - self.hidden_fields.get((doctype, permission_type), set())

    def get_doc(self, doctype, name, **kwargs):
        result = Document(deepcopy(self.records[(doctype, name)]))
        if self.datetime_fresh:
            for field in ("creation", "modified"):
                if isinstance(result.get(field), str):
                    result[field] = datetime.fromisoformat(result[field])
        if self.hook:
            self.hook(doctype, name)
        return result

    def new_doc(self, doctype):
        document = Document(doctype=doctype, __islocal=1)
        if doctype == "Customer":
            document.customer_type, document.naming_series, document.disabled = (
                "Company",
                "CUST-.YYYY.-",
                0,
            )
        return document

    def get_value(self, doctype, filters, field, **kwargs):
        if isinstance(filters, str):
            return self.records.get((doctype, filters), {}).get(field)
        return next(
            (
                row.get(field)
                for (kind, _), row in self.records.items()
                if kind == doctype
                and all(row.get(name) == value for name, value in filters.items())
            ),
            None,
        )

    def get_list(self, doctype, filters, **kwargs):
        result = []
        for (kind, name), row in self.records.items():
            if (
                kind != doctype
                or name in self.hidden_names
                or not all(row.get(field) == value for field, value in filters.items())
            ):
                continue
            if kwargs.get("or_filters") and not any(
                test[1].strip("%") in (row.get(field) or "")
                for field, test in kwargs["or_filters"].items()
            ):
                continue
            result.append(name)
        result.sort(
            key=lambda name: (self.records[(doctype, name)].get("modified"), name),
            reverse=True,
        )
        start = kwargs.get("limit_start", 0)
        result = result[start : start + kwargs.get("limit_page_length", 200)]
        if kwargs.get("fields"):
            return [
                {
                    field: self.records[(doctype, name)].get(field)
                    for field in kwargs["fields"]
                }
                for name in result
            ]
        return result

    def savepoint(self, name):
        self.snapshots[name] = deepcopy((self.records, self.saves))

    def rollback(self, save_point=None):
        self.records, self.saves = deepcopy(self.snapshots[save_point])

    def getseries(self, key, digits):
        row = self.records.setdefault(("Series", key), {"current": 0})
        row["current"] += 1
        return str(row["current"]).zfill(digits)

    def dictionary(
        self, field="category", kind="销售客户", company="Company A", inactive=0
    ):
        name = "DICT-" + company + "-" + kind + "-" + field
        self.records[(self.module.DICTIONARY, name)] = dict(
            doctype=self.module.DICTIONARY,
            name=name,
            modified="2026-10-02T00:00:00.123456",
            company=company,
            dictionary_type=self.module.DICT_TYPES[kind][field],
            value="  <b>分类原文</b>  ",
            inactive=inactive,
        )
        return name

    def payload(self, **changes):
        result = {
            "kind": "销售客户",
            "company": "Company A",
            "request_id": str(uuid4()),
            "customer_version": self.records[("Customer", "CUST-A")]["modified"],
            "profile": {
                "customer": "CUST-A",
                "legacy_code": "0007",
                "display_name": "  合成客户  ",
                "mobile": "0000123",
                "contact_name": "联系原文",
                "tax_number": "000098",
                "company_text": "旧公司原文",
                "notes": "  原文备注  ",
                "discount": "97% 原文",
                "inactive": 0,
            },
            "dictionary_versions": {},
        }
        result.update(changes)
        return result

    def native_payload(self, **changes):
        payload = self.payload()
        payload.pop("customer_version")
        payload["profile"].pop("customer")
        payload.update(changes)
        return payload

    def edit(self, row, **values):
        return {
            "kind": row["kind"],
            "company": row["company"],
            "name": row["name"],
            "profile_version": row["profile_version"],
            "customer_version": row["customer_version"],
            "profile": values,
            "dictionary_versions": row["dictionary_versions"],
        }

    def create(self):
        return self.module.save_archive(self.payload())

    def query(self, **kwargs):
        return self.module.query_archives("销售客户", "Company A", **kwargs)

    def test_link_overlay_preserves_native_customer_and_raw_text(self):
        original = deepcopy(self.records[("Customer", "CUST-A")])
        row = self.create()
        self.assertEqual(self.records[("Customer", "CUST-A")], original)
        self.assertEqual(self.saves, [self.module.PROFILE])
        self.assertEqual(row["profile"]["legacy_code"], "0007")
        self.assertEqual(row["profile"]["discount"], "97% 原文")
        self.assertEqual(row["profile"]["notes"], "  原文备注  ")
        self.assertNotIn("request_id", row["profile"])
        self.assertNotIn("binding_key", row["profile"])

    def test_edit_and_stop_never_save_shared_party(self):
        row = self.create()
        before = deepcopy(self.records[("Customer", "CUST-A")])
        saved = self.module.save_archive(
            self.edit(row, display_name="改档案名称", mobile="新文本", inactive=1)
        )
        self.assertEqual(saved["profile"]["inactive"], 1)
        self.assertEqual(self.records[("Customer", "CUST-A")], before)
        self.assertEqual(self.saves, [self.module.PROFILE] * 2)

    def test_preview_readonly_no_series_reservation(self):
        before = deepcopy(self.records)
        first = self.module.init_archive("销售客户", "Company A")
        second = self.module.init_archive("销售客户", "Company A")
        self.assertEqual(first["profile"]["legacy_number"], "001")
        self.assertEqual(first, second)
        self.assertTrue(first["field_meta"]["legacy_number"]["read_only"])
        self.assertTrue(first["number_reference_only"])
        self.assertEqual(self.records, before)
        self.assertEqual(self.create()["profile"]["legacy_number"], "001")

    def test_number_counters_and_bindings_independent_company_module(self):
        rows = []
        for company, kind in (
            ("Company A", "销售客户"),
            ("Company A", "订单客户"),
            ("Company B", "销售客户"),
            ("Company B", "订单客户"),
        ):
            payload = self.payload(kind=kind, company=company)
            if kind == "订单客户":
                payload["profile"].pop("discount")
            rows.append(self.module.save_archive(payload))
        self.assertEqual([r["profile"]["legacy_number"] for r in rows], ["001"] * 4)
        self.assertEqual(
            len({self.module._series_key(r["company"], r["kind"]) for r in rows}), 4
        )
        self.assertTrue(
            all(
                len(self.module._series_key(r["company"], r["kind"])) <= 100
                for r in rows
            )
        )

    def test_same_native_customer_duplicate_binding_rejected_without_save(self):
        self.create()
        before = deepcopy((self.records, self.saves))
        with self.assertRaisesRegex(ValueError, "已有.*档案"):
            self.create()
        self.assertEqual((self.records, self.saves), before)

    def test_new_native_customer_uses_normal_name_rules_and_no_contact_injection(self):
        payload = self.native_payload()
        payload["profile"]["display_name"] = "Shared customer must not change"
        row = self.module.save_archive(payload)
        party = self.records[("Customer", row["customer"])]
        self.assertEqual(row["customer"], "Shared customer must not change")
        self.assertNotEqual(row["customer"], "CUST-A")
        self.assertNotIn("mobile", party)
        self.assertNotIn("tax_number", party)
        self.assertNotIn("contact_name", party)
        self.assertNotIn("company_text", party)
        self.assertEqual(self.saves, ["Customer", self.module.PROFILE])
        second = self.module.save_archive(self.native_payload())
        self.assertNotEqual(second["customer"], row["customer"])

    def test_native_create_denied_but_explicit_existing_link_allowed(self):
        self.denials.add(("Customer", "create"))
        init = self.module.init_archive("销售客户", "Company A")
        self.assertFalse(init["can_create_customer"])
        self.assertTrue(init["can_link_customer"])
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.native_payload())
        self.assertEqual(self.saves, [])
        self.assertEqual(self.create()["customer"], "CUST-A")

    def test_native_timestamp_strings_and_fresh_datetimes_guard_same_persisted_party(
        self,
    ):
        self.datetime_fresh = True
        row = self.module.save_archive(self.native_payload())
        self.assertEqual(
            self.module.read_archive(row["name"], row["kind"], row["company"]), row
        )
        saved = self.module.save_archive(self.edit(row, notes="仅修改档案"))
        self.assertEqual(saved["customer_version"], row["customer_version"])

    def test_native_defaults_options_leaf_group_and_field_permissions(self):
        self.records[("Customer Group", "LEAF")] = dict(
            doctype="Customer Group", name="LEAF", is_group=0
        )
        self.records[("Customer Group", "TREE")] = dict(
            doctype="Customer Group", name="TREE", is_group=1
        )
        with self.assertRaisesRegex(ValueError, "具体原生客户分组"):
            self.module.save_archive(
                self.native_payload(customer_create={"customer_group": "TREE"})
            )
        self.hidden_names.add("LEAF")
        with self.assertRaises(PermissionError):
            self.module.save_archive(
                self.native_payload(customer_create={"customer_group": "LEAF"})
            )
        self.hidden_names.clear()
        with self.assertRaisesRegex(ValueError, "有效原生客户选项"):
            self.module.save_archive(
                self.native_payload(customer_create={"customer_type": "伪造"})
            )
        self.hidden_fields[("Customer", "write")] = {"territory"}
        with self.assertRaises(PermissionError):
            self.module.save_archive(
                self.native_payload(customer_create={"territory": None})
            )
        self.assertEqual(self.saves, [])

    def test_readonly_init_and_detail_quiet_but_actual_write_denied(self):
        row = self.create()
        self.denials |= {
            (self.module.PROFILE, "create"),
            (self.module.PROFILE, "write"),
        }
        init = self.module.init_archive("销售客户", "Company A")
        detail = self.module.read_archive(row["name"], "销售客户", "Company A")
        self.assertFalse(init["can_write"])
        self.assertFalse(detail["can_write"])
        self.assertTrue(all(call[2] is False for call in self.capability_calls))
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.edit(row, notes="deny"))

    def test_joint_native_customer_failure_rolls_back_all_callbacks_and_series(self):
        before = deepcopy((self.records, self.saves))
        self.fail_save = True
        with self.assertRaisesRegex(ValueError, "native save failure"):
            self.module.save_archive(self.native_payload())
        self.assertEqual((self.records, self.saves), before)
        self.assertEqual(self.frappe.db.after_commit._functions, [])
        self.assertIsNone(self.module._CREATING.get())
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))

    def test_native_customer_duplicate_is_rejected_without_adopting_existing_party(
        self,
    ):
        before = deepcopy((self.records, self.saves))
        original = Document.save

        def duplicate(document):
            if document.doctype == "Customer":
                document.name = "CUST-A"
                raise NativeDuplicateEntryError(
                    "Customer", "CUST-A", ValueError("native duplicate")
                )
            return original(document)

        with patch.object(Document, "save", duplicate):
            with self.assertRaisesRegex(ValueError, "未自动关联"):
                self.module.save_archive(self.native_payload())
        self.assertEqual((self.records, self.saves), before)

    def test_unrelated_native_duplicate_keeps_original_error_category(self):
        error = NativeDuplicateEntryError(
            "Contact", "unexpected-contact", ValueError("native duplicate")
        )
        with patch.object(Document, "save", side_effect=error):
            with self.assertRaises(NativeDuplicateEntryError) as failure:
                self.module.save_archive(self.native_payload())
        self.assertIs(failure.exception, error)

    def test_global_uuid_idempotency_scope_and_changed_content(self):
        payload = self.payload()
        row = self.module.save_archive(payload)
        self.assertEqual(self.module.save_archive(payload), row)
        self.assertEqual(self.saves, [self.module.PROFILE])
        self.assertEqual(
            self.module.resolve_creation(
                payload["request_id"], "销售客户", "Company A"
            )["archive"],
            row,
        )
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "销售客户", "Company B")
        payload["profile"]["notes"] = "different"
        with self.assertRaisesRegex(ValueError, "不同内容"):
            self.module.save_archive(payload)

    def test_uuid_unknown_resolution_is_read_only(self):
        before = deepcopy(self.records)
        result = self.module.resolve_creation(str(uuid4()), "销售客户", "Company A")
        self.assertEqual(result, {"status": "not_found"})
        self.assertEqual(self.records, before)

    def test_profile_and_customer_stale_versions_reject_before_save(self):
        row = self.create()
        before = deepcopy((self.records, self.saves))
        for field in ("profile_version", "customer_version"):
            payload = self.edit(row, notes="stale")
            payload[field] = "2020-01-01T00:00:00"
            with self.assertRaises(RuntimeError):
                self.module.save_archive(payload)
            self.assertEqual((self.records, self.saves), before)

    def test_company_customer_user_permission_and_mask_cannot_leak(self):
        row = self.create()
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], "销售客户", "Company B")
        self.hidden_names.add("CUST-A")
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], "销售客户", "Company A")
        self.assertEqual(self.query()["total"], 0)
        self.hidden_names.clear()
        self.hidden_fields[("Customer", "read")] = {"disabled"}
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], "销售客户", "Company A")

    def test_all_five_dictionary_types_exact_module_company_and_versions(self):
        for kind in self.module.KINDS:
            payload = self.payload(kind=kind)
            if kind == "订单客户":
                payload["profile"].pop("discount")
            for field in self.module.DICT_TYPES[kind]:
                name = self.dictionary(field, kind)
                payload["profile"][field] = name
                payload["dictionary_versions"][field] = self.records[
                    (self.module.DICTIONARY, name)
                ]["modified"]
            row = self.module.save_archive(payload)
            self.assertEqual(
                set(row["dictionary_versions"]), set(self.module.DICT_TYPES[kind])
            )
            self.assertEqual(
                set(row["dictionary_values"]), set(row["dictionary_versions"])
            )
            self.assertEqual(
                set(row["dictionary_values"].values()), {"  <b>分类原文</b>  "}
            )
            if kind == "订单客户":
                self.assertNotIn("discount", row["profile"])
                self.assertNotIn("customer_kind", row["profile"])

    def test_wrong_dictionary_type_company_and_stale_are_atomic(self):
        cases = [
            self.dictionary("grade"),
            self.dictionary("category", company="Company B"),
        ]
        for name in cases:
            payload = self.payload()
            payload["profile"]["category"] = name
            payload["dictionary_versions"] = {
                "category": self.records[(self.module.DICTIONARY, name)]["modified"]
            }
            before = deepcopy((self.records, self.saves))
            with self.assertRaises(PermissionError):
                self.module.save_archive(payload)
            self.assertEqual((self.records, self.saves), before)
        name = self.dictionary()
        payload = self.payload()
        payload["profile"]["category"] = name
        payload["dictionary_versions"] = {"category": "2020-01-01T00:00:00"}
        with self.assertRaises(RuntimeError):
            self.module.save_archive(payload)
        self.assertEqual(self.saves, [])

    def test_dictionary_versions_exact_nonempty_set(self):
        payload = self.payload()
        payload["dictionary_versions"] = {"category": None}
        with self.assertRaisesRegex(ValueError, "所有实际"):
            self.module.save_archive(payload)
        name = self.dictionary()
        payload = self.payload()
        payload["profile"]["category"] = name
        with self.assertRaisesRegex(ValueError, "所有实际"):
            self.module.save_archive(payload)

    def test_inactive_dictionary_new_selection_denied_history_retained(self):
        name = self.dictionary()
        payload = self.payload()
        payload["profile"]["category"] = name
        payload["dictionary_versions"] = {
            "category": self.records[(self.module.DICTIONARY, name)]["modified"]
        }
        row = self.module.save_archive(payload)
        self.records[(self.module.DICTIONARY, name)]["inactive"] = 1
        history = self.module.read_archive(row["name"], "销售客户", "Company A")
        self.module.save_archive(self.edit(history, notes="历史保留"))
        fresh = self.payload(company="Company B")
        name2 = self.dictionary(company="Company B", inactive=1)
        fresh["profile"]["category"] = name2
        fresh["dictionary_versions"] = {
            "category": self.records[(self.module.DICTIONARY, name2)]["modified"]
        }
        with self.assertRaisesRegex(ValueError, "停用客户词典"):
            self.module.save_archive(fresh)

    def test_dictionary_mask_or_hidden_source_never_returns_raw_value(self):
        name = self.dictionary()
        payload = self.payload()
        payload["profile"]["category"] = name
        payload["dictionary_versions"] = {
            "category": self.records[(self.module.DICTIONARY, name)]["modified"]
        }
        row = self.module.save_archive(payload)
        self.hidden_fields[(self.module.DICTIONARY, "read")] = {"value"}
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], "销售客户", "Company A")
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(name, "category", "销售客户", "Company A")
        self.hidden_fields.clear()
        self.hidden_names.add(name)
        self.assertEqual(self.query()["total"], 0)

    def test_profile_masked_dictionary_link_does_not_fill_linked_value_or_version(self):
        name = self.dictionary()
        payload = self.payload()
        payload["profile"]["category"] = name
        payload["dictionary_versions"] = {
            "category": self.records[(self.module.DICTIONARY, name)]["modified"]
        }
        row = self.module.save_archive(payload)
        self.hidden_fields[(self.module.PROFILE, "read")] = {"category"}
        masked = self.module.read_archive(row["name"], "销售客户", "Company A")
        self.assertNotIn("category", masked["profile"])
        self.assertNotIn("category", masked["dictionary_values"])
        self.assertNotIn("category", masked["dictionary_versions"])
        before = deepcopy((self.records, self.saves))
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.edit(masked, notes="blocked"))
        self.assertEqual((self.records, self.saves), before)

    def test_omitted_native_projection_field_is_not_backfilled_from_full_document(self):
        row = self.create()

        def omit(doctype, filters, **kwargs):
            rows = self.get_list(doctype, filters, **kwargs)
            if doctype == self.module.PROFILE and kwargs.get("fields"):
                for record in rows:
                    record.pop("notes", None)
            return rows

        self.frappe.get_list.side_effect = omit
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], row["kind"], row["company"])

    def test_late_permission_and_source_change_rejected(self):
        row = self.create()
        calls = 0

        def hook(kind, name):
            nonlocal calls
            if kind == "Customer":
                calls += 1
                if calls == 2:
                    self.hidden_fields[(self.module.PROFILE, "read")] = {"notes"}

        self.hook = hook
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], "销售客户", "Company A")
        self.hook = None
        self.hidden_fields.clear()
        calls = 0

        def mutate(kind, name):
            nonlocal calls
            if kind == "Customer":
                calls += 1
                if calls == 1:
                    self.records[(kind, name)]["disabled"] = 1

        self.hook = mutate
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.module.read_archive(row["name"], "销售客户", "Company A")

    def test_native_controller_blocks_identity_tamper_and_direct_insert(self):
        row = self.create()
        before = deepcopy(self.records)
        for field in self.module.IDENTITY | self.module.SERVER_FIELDS:
            doc = self.get_doc(self.module.PROFILE, row["name"])
            doc[field] = "tamper"
            with self.assertRaises((ValueError, PermissionError, KeyError)):
                doc.save()
        self.assertEqual(self.records, before)
        doc = self.new_doc(self.module.PROFILE)
        doc.company = "Company A"
        doc.archive_kind = "销售客户"
        doc.customer = "CUST-A"
        with self.assertRaisesRegex(ValueError, "入口新建"):
            doc.save()

    def test_complete_pagination_over_1000_and_offset_clamp(self):
        row = self.create()
        template = deepcopy(self.records[(self.module.PROFILE, row["name"])])
        for index in range(1004):
            name = "BULK-" + str(index).zfill(4)
            record = deepcopy(template)
            record["name"] = name
            self.records[(self.module.PROFILE, name)] = record
        result = self.query(page=999, page_size=100)
        self.assertEqual(result["total"], 1005)
        self.assertEqual(result["total_pages"], 11)
        self.assertEqual(result["page"], 11)
        self.assertEqual(len(result["rows"]), 5)
        self.assertTrue(result["readonly"])
        self.assertTrue(all(not value["can_write"] for value in result["rows"]))

    def test_order_forbidden_sales_fields_and_server_number_rejected(self):
        payload = self.payload(kind="订单客户")
        with self.assertRaisesRegex(ValueError, "不支持"):
            self.module.save_archive(payload)
        payload = self.payload()
        payload["profile"]["legacy_number"] = "999"
        with self.assertRaisesRegex(ValueError, "只读字段"):
            self.module.save_archive(payload)

    def test_new_disabled_customer_reject_but_existing_history_readable(self):
        row = self.create()
        self.records[("Customer", "CUST-A")]["disabled"] = 1
        history = self.module.read_archive(row["name"], "销售客户", "Company A")
        self.assertTrue(history["customer_disabled"])
        payload = self.payload(company="Company B")
        with self.assertRaisesRegex(ValueError, "停用原生客户"):
            self.module.save_archive(payload)


if __name__ == "__main__":
    unittest.main()
