"""Plating-color permissions, source races, complete paging and safe receipts."""

import ast
from copy import deepcopy
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


ROOT = Path(__file__).parents[1]
DOCTYPE = "Jifeng Plating Color"
RECEIPT = "Jifeng Plating Color Receipt"
EXPECTED_TYPES = {"镀色": "color"}
SCHEMA = json.loads(
    (
        ROOT
        / "jifeng_erp/jifeng_erp/doctype/jifeng_plating_color/jifeng_plating_color.json"
    ).read_text(encoding="utf-8")
)

RECEIPT_SCHEMA = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_plating_color_receipt/jifeng_plating_color_receipt.json").read_text(encoding="utf-8"))


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
        return self.environment.receipt_meta if self.doctype == RECEIPT else self.environment.meta

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, action):
        env = self.environment
        return (
            action not in env.role_denials
            and self.name not in env.document_denials
            and self.company not in env.denied_companies
        )

    def check_permission(self, action):
        if not self.has_permission(action):
            raise PermissionError("native " + action)

    def get_doc_before_save(self):
        store = self.environment.receipts if self.doctype == RECEIPT else self.environment.documents
        saved = store.get(self.name)
        return Document(deepcopy(saved)) if saved else None

    def save(self):
        env, new = self.environment, self.is_new()
        receipt = self.doctype == RECEIPT
        store = env.receipts if receipt else env.documents
        self.check_permission("create" if new else "write")
        if receipt:
            env.module.validate_receipt(self)
        else:
            if new:
                env.module.before_insert_dictionary(self)
            env.module.validate_dictionary(self)
        unique_fields = ("creation_request_id", "delete_request_id") if receipt else ("request_id",)
        if any(self.get(field) and row.get(field) == self.get(field) and row["name"] != self.name
               for row in store.values() for field in unique_fields):
            env.frappe.local.message_log = list(getattr(env.frappe.local, "message_log", None) or []) + [{"message": "private unique index"}]
            raise KeyError("private unique SQL")
        env.tick += 1
        self.name = self.name or ("RECEIPT-" if receipt else "PL-C-") + str(env.tick)
        self.modified = datetime(2026, 10, 2) + timedelta(microseconds=env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        store[self.name] = deepcopy(dict(self))
        if not receipt:
            if new:
                env.module.after_insert_dictionary(self)
            else:
                env.versions.append(deepcopy(dict(self)))
        if env.fail_after_save and not receipt:
            env.db.after_commit.add(lambda: None)
            env.frappe.flags.currently_saving = ["unexpected save context"]
            raise ValueError("native post-save failure")
        return self


class PlatingColorTest(unittest.TestCase):
    def setUp(self):
        self.documents, self.versions, self.series, self.snapshots = {}, [], {}, {}
        self.receipts, self.deleted, self.references = {}, [], set()
        self.role_denials, self.document_denials, self.denied_companies = (
            set(),
            set(),
            set(),
        )
        self.hidden = {"read": set(), "write": set()}
        self.mask_levels, self.omitted, self.tick, self.fail_after_save = (
            set(),
            set(),
            0,
            False,
        )
        self.source_hook = None
        self.meta = Row(fields=[Row(field) for field in SCHEMA["fields"]])
        self.meta.get_field = lambda name: next(
            (field for field in self.meta.fields if field.fieldname == name), None
        )
        self.receipt_meta = Row(fields=[Row(field) for field in RECEIPT_SCHEMA["fields"]])
        self.receipt_meta.get_field = lambda name: next((field for field in self.receipt_meta.fields if field.fieldname == name), None)
        self.receipt_meta.get_permlevel_access = lambda *args, **kwargs: self.mask_levels
        self.meta.get_permlevel_access = Mock(
            side_effect=lambda action, parenttype: self.mask_levels
        )
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Row(user="sales-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = (
            PermissionError,
            RuntimeError,
        )
        self.frappe.DoesNotExistError = LookupError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(
            error(message)
        )
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.flags, self.frappe.local = Row(), types.SimpleNamespace()
        self.frappe.has_permission = Mock(
            side_effect=lambda dt, action, doc=None, throw=False: (
                action not in self.role_denials
                and (
                    doc is None
                    or doc.name not in self.document_denials
                    and doc.company not in self.denied_companies
                )
            )
        )
        self.frappe.get_meta = Mock(side_effect=lambda dt: self.receipt_meta if dt == RECEIPT else self.meta)
        self.frappe.utils = Row(now_datetime=lambda: datetime(2026, 10, 7))
        self.frappe.LinkExistsError = RuntimeError
        self.frappe.delete_doc = Mock(side_effect=self.delete_native)
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(
            side_effect=AssertionError("unscoped listing forbidden")
        )
        self.db = types.SimpleNamespace(
            savepoint=self.savepoint,
            rollback=self.rollback,
            get_value=Mock(
                side_effect=self.db_value
            ),
            **{
                field: Queue()
                for field in (
                    "before_commit",
                    "after_commit",
                    "before_rollback",
                    "after_rollback",
                )
            },
        )
        self.db.exists = lambda dt, name: name in (self.receipts if dt == RECEIPT else self.documents)
        self.frappe.db = self.db
        model, naming = (
            types.ModuleType("frappe.model"),
            types.ModuleType("frappe.model.naming"),
        )
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            (
                {field.fieldname for field in (self.receipt_meta if dt == RECEIPT else self.meta).fields}
                | {"name", "modified", "creation", "owner", "modified_by"}
            )
            - self.hidden[permission_type]
        )
        naming.getseries = Mock(side_effect=self.next_number)
        self.allocate = naming.getseries
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse(
            (ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8")
        )
        function = next(
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
                ast.Module(body=[function], type_ignores=[]), "actual_atomic", "exec"
            ),
            namespace,
        )
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(
            sys.modules,
            {
                "frappe": self.frappe,
                "frappe.model": model,
                "frappe.model.naming": naming,
                "jifeng_erp.personnel_separation": separation,
            },
        )
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location(
            "plating_color_under_test", ROOT / "jifeng_erp/plating_color.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self

    def new_doc(self, doctype):
        if doctype == RECEIPT:
            return Document(doctype=RECEIPT, name=None, __islocal=1)
        self.assertEqual(doctype, DOCTYPE)
        return Document(
            doctype=DOCTYPE,
            name=None,
            company="DEFAULT-DENIED",
            dictionary_type="镀色",
            numbering_mode=None,
            inactive=0,
            __islocal=1,
        )

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            return Document(**doctype, __islocal=0)
        if doctype == RECEIPT:
            return Document(deepcopy(self.receipts[name]))
        if doctype == "Company":

            def check(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company permission")

            return Row(name=name, check_permission=check)
        self.assertEqual(
            doctype,
            DOCTYPE,
            "must never touch native Item, Sales Order or existing dictionaries",
        )
        if self.source_hook:
            self.source_hook(name)
        return Document(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **options):
        self.assertIn(doctype, (DOCTYPE, RECEIPT))
        result = []
        store = self.receipts if doctype == RECEIPT else self.documents
        for name, row in store.items():
            if name in self.document_denials or row["company"] in self.denied_companies:
                continue
            if not all(row.get(field) == value for field, value in filters.items()):
                continue
            if options.get("or_filters") and not any(
                value.strip("%") in (row.get(field) or "")
                for field, operator, value in options["or_filters"]
            ):
                continue
            result.append(
                Row(
                    {
                        field: row.get(field)
                        for field in fields
                        if field not in self.omitted
                    }
                )
            )
        result.sort(key=lambda row: (row.get("value") or "", row["name"]))
        start, length = (
            options.get("limit_start", 0),
            options.get("limit_page_length", 20),
        )
        return result[start : start + length]

    def next_number(self, key, digits):
        self.series[key] = self.series.get(key, 0) + 1
        return str(self.series[key]).zfill(digits)

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.documents, self.versions, self.series, self.receipts, self.deleted))

    def rollback(self, save_point):
        self.documents, self.versions, self.series, self.receipts, self.deleted = deepcopy(self.snapshots[save_point])

    def db_value(self, doctype, filters, field, **kwargs):
        if doctype == "Series":
            return self.series.get(filters)
        self.assertIn(doctype, (DOCTYPE, RECEIPT))
        store = self.receipts if doctype == RECEIPT else self.documents
        rows = [row for row in store.values() if all(row.get(key) == value for key, value in filters.items())]
        return rows[0].get(field) if rows else None

    def delete_native(self, doctype, name, **kwargs):
        self.assertEqual(doctype, DOCTYPE)
        self.assertEqual(kwargs, {"ignore_missing": False})
        document = self.get_doc(doctype, name)
        document.check_permission("delete")
        self.module.before_delete_dictionary(document)
        if name in self.references:
            raise self.frappe.LinkExistsError("actual native Link protection")
        self.deleted.append(deepcopy(self.documents.pop(name)))
        self.module.after_delete_dictionary(document)

    def delete_payload(self, archive, **changes):
        return {"name": archive["name"], "creation": str(archive["creation"]), "version": archive["version"],
                "company": archive["company"], "dictionary_type": "镀色", "request_id": str(uuid4()), **changes}

    def payload(self, **changes):
        return {
            "request_id": str(uuid4()),
            "company": "C-1",
            "dictionary_type": "镀色",
            "code": " 原代码-01 ",
            "value": " 镀色档案名称 ",
            "color_system": " 原色系 / A ",
            "remarks": " 原文\n备注 ",
            "inactive": 0,
            "numbering_mode": self.module.AUTO_MODE,
            **changes,
        }

    def saved(self, **changes):
        return self.module.save_dictionary(self.payload(**changes))

    def update(self, archive, **changes):
        return self.module.save_dictionary(
            {
                field: archive[field]
                for field in (
                    "name",
                    "version",
                    *self.module.IDENTITY,
                    *self.module.EDIT_FIELDS,
                    "numbering_mode",
                )
                if field in archive
            }
            | changes
        )

    def test_schema_sales_roles_only_and_native_manager_delete(self):
        self.assertEqual(
            {row["role"] for row in SCHEMA["permissions"]},
            {"System Manager", "Sales Manager", "Sales User"},
        )
        for row in SCHEMA["permissions"]:
            self.assertFalse(row.get("export"))
            self.assertEqual(bool(row.get("delete")), row["role"] != "Sales User")
            if row["role"] == "Sales User":
                self.assertFalse(row.get("create") or row.get("write"))
        self.assertTrue(self.meta.get_field("abbreviation").read_only)
        self.assertTrue(self.meta.get_field("legacy_number").read_only)

    def test_original_text_and_native_identity_are_retained(self):
        payload = self.payload()
        archive = self.module.save_dictionary(json.dumps(payload))
        self.assertEqual(
            (archive["value"], archive["remarks"]),
            (payload["value"], payload["remarks"]),
        )
        self.assertEqual(archive["legacy_number"], "001")
        self.assertIsNone(archive["abbreviation"])
        self.assertNotIn("request_digest", archive)
        self.assertTrue(archive["field_meta"]["abbreviation"]["read_only"])
        changed = self.update(archive, value=" 改名 ", remarks="", inactive=1)
        self.assertEqual(
            (changed["name"], changed["remarks"], changed["inactive"]),
            (archive["name"], "", 1),
        )
        self.assertEqual(len(self.versions), 1)

    def test_preview_does_not_allocate_and_company_counter_is_independent(self):
        first = self.module.init_dictionary("镀色", "C-1")
        self.assertEqual(first["number_preview"], "001")
        self.assertTrue(first["number_preview_is_provisional"])
        self.allocate.assert_not_called()
        a, b, c = self.saved(), self.saved(), self.saved(company="C-2")
        self.assertEqual(
            (a["legacy_number"], b["legacy_number"], c["legacy_number"]),
            ("001", "002", "001"),
        )
        self.hidden["read"].add("legacy_number")
        self.db.get_value.reset_mock()
        self.assertIsNone(self.module.init_dictionary("镀色", "C-1")["number_preview"])
        self.db.get_value.assert_not_called()

    def test_uuid_receipt_retry_reads_current_version_and_never_allocates_again(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        changed = self.update(archive, value="后续修改")
        before, allocations = (
            deepcopy((self.documents, self.versions, self.series)),
            self.allocate.call_count,
        )
        self.assertEqual(self.module.save_dictionary(payload), changed)
        self.assertEqual(
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1")[
                "archive"
            ],
            changed,
        )
        self.assertEqual(
            self.module.resolve_creation(str(uuid4()), "镀色", "C-1"),
            {"status": "not_found"},
        )
        self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertEqual(self.allocate.call_count, allocations)
        with self.assertRaisesRegex(ValueError, "身份冲突"):
            self.module.save_dictionary(payload | {"value": "另一个初始值"})

    def test_hidden_collision_is_generic_and_rolls_back_number_and_messages(self):
        payload, archive = self.payload(), None
        archive = self.module.save_dictionary(payload)
        self.document_denials.add(archive["name"])
        self.frappe.local.message_log = [{"message": "existing"}]
        before = deepcopy((self.documents, self.versions, self.series))
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1")
        with self.assertRaisesRegex(ValueError, "身份冲突") as caught:
            self.module.save_dictionary(payload)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(archive["name"], str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, [{"message": "existing"}])
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_atomic_post_save_failure_restores_native_records_series_and_existing_context(
        self,
    ):
        def marker():
            return None

        self.db.after_commit.add(marker)
        self.frappe.flags.currently_saving = ["prior context"]
        before = deepcopy((self.documents, self.versions, self.series))
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "post-save"):
            self.saved()
        self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertEqual(self.db.after_commit._functions, [marker])
        self.assertEqual(self.frappe.flags.currently_saving, ["prior context"])

    def test_stale_save_uses_native_row_lock_and_does_not_overwrite(self):
        archive = self.saved()
        changed = self.update(archive, value="新值")
        with self.assertRaises(RuntimeError):
            self.update(archive, value="旧值覆盖")
        self.assertEqual(self.documents[archive["name"]]["value"], changed["value"])
        self.assertTrue(
            any(
                call.kwargs.get("for_update")
                for call in self.frappe.get_doc.call_args_list
            )
        )

    def test_read_and_list_never_backfill_native_omissions_or_masks(self):
        archive = self.saved()
        self.omitted.add("remarks")
        self.assertNotIn(
            "remarks", self.module.read_dictionary(archive["name"], "镀色")
        )
        self.assertNotIn(
            "remarks", self.module.list_dictionary("镀色", "C-1")["rows"][0]
        )
        self.omitted.clear()
        self.meta.get_field("remarks").mask = 1
        self.assertNotIn(
            "remarks", self.module.read_dictionary(archive["name"], "镀色")
        )
        with self.assertRaises(PermissionError):
            self.update(archive, remarks="masked write")
        self.mask_levels.add(0)
        self.assertEqual(
            self.module.read_dictionary(archive["name"], "镀色")["remarks"],
            archive["remarks"],
        )

    def test_required_mask_or_projection_omission_fails_entire_query(self):
        archive = self.saved()
        self.meta.get_field("value").mask = 1
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("镀色", "C-1")
        self.meta.get_field("value").mask = 0
        self.omitted.add("value")
        for action in (
            lambda: self.module.read_dictionary(archive["name"], "镀色"),
            lambda: self.module.list_dictionary("镀色", "C-1"),
        ):
            with self.assertRaises(PermissionError):
                action()

    def test_receipt_projection_and_field_permissions_are_not_backfilled(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.omitted.add("request_digest")
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1")
        self.omitted.clear()
        self.hidden["read"].add("request_digest")
        self.assertEqual(
            self.module.read_dictionary(archive["name"], "镀色")["name"],
            archive["name"],
        )
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1")

    def test_complete_paging_over_a_thousand_and_out_of_range_clamping(self):
        for index in range(1103):
            name = "ROW-" + str(index).zfill(4)
            self.documents[name] = {
                "doctype": DOCTYPE,
                "name": name,
                "company": "C-1",
                "dictionary_type": "镀色",
                "value": name,
                "modified": datetime(2026, 10, 2),
                "inactive": 0,
                "numbering_mode": self.module.AUTO_MODE,
                "legacy_number": str(index).zfill(5),
                "remarks": "original",
                "abbreviation": None,
                "__islocal": 0,
            }
        response = self.module.list_dictionary(
            "镀色", "C-1", start="1100", page_length="100"
        )
        self.assertEqual(
            (response["total"], response["start"], len(response["rows"])),
            (1103, 1100, 3),
        )
        self.assertEqual(response["rows"][0]["value"], "ROW-1100")
        self.assertEqual(
            self.module.list_dictionary("镀色", "C-1", start=999999, page_length=100)[
                "start"
            ],
            1100,
        )
        self.assertEqual(
            self.module.list_dictionary("镀色", "C-1", search="absent", start=100)[
                "start"
            ],
            0,
        )
        self.frappe.get_all.assert_not_called()

    def test_paging_counts_only_readable_and_search_filters_cannot_use_hidden_field(
        self,
    ):
        self.saved()
        b = self.saved(value="another")
        self.document_denials.add(b["name"])
        result = self.module.list_dictionary(
            "镀色", "C-1", search="001", include_inactive="1"
        )
        self.assertEqual(
            (
                result["total"],
                result["company"],
                result["dictionary_type"],
                result["search"],
                result["include_inactive"],
            ),
            (1, "C-1", "镀色", "001", 1),
        )
        self.hidden["read"].add("legacy_number")
        self.assertEqual(
            self.module.list_dictionary("镀色", "C-1", search="001")["total"], 0
        )
        self.assertTrue(
            all(
                field != "legacy_number"
                for field, operator, value in self.frappe.get_list.call_args.kwargs[
                    "or_filters"
                ]
            )
        )

    def test_loaded_source_version_or_identity_race_fails_without_partial_rows(self):
        archive = self.saved()

        def change(name):
            self.documents[name]["modified"] += timedelta(microseconds=1)

        self.source_hook = change
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.list_dictionary("镀色", "C-1")
        self.source_hook = None
        original = self.frappe.get_list.side_effect

        def changed_projection(*args, **kwargs):
            change(archive["name"])
            return original(*args, **kwargs)

        self.frappe.get_list.side_effect = changed_projection
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.read_dictionary(archive["name"], "镀色")
        count = 0

        def drift(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.documents[archive["name"]]["value"] = "between scans"
                self.documents[archive["name"]]["modified"] += timedelta(microseconds=1)
            return original(*args, **kwargs)

        self.frappe.get_list.side_effect = drift
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.list_dictionary("镀色", "C-1")

    def test_company_select_and_document_permissions_guard_read_create_and_receipts(
        self,
    ):
        archive, payload = self.saved(), self.payload(company="C-2")
        self.denied_companies.add("C-2")
        for action in (
            lambda: self.module.init_dictionary("镀色", "C-2"),
            lambda: self.module.save_dictionary(payload),
            lambda: self.module.list_dictionary("镀色", "C-2"),
            lambda: self.module.resolve_creation(payload["request_id"], "镀色", "C-2"),
        ):
            with self.assertRaises(PermissionError):
                action()
        self.document_denials.add(archive["name"])
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"], "镀色")

    def test_sales_user_can_read_but_cannot_create_edit_or_gain_export(self):
        archive = self.saved()
        self.role_denials.update(("create", "write", "export", "delete"))
        self.assertFalse(self.module.init_dictionary("镀色", "C-1")["can_write"])
        self.assertFalse(
            self.module.read_dictionary(archive["name"], "镀色")["can_write"]
        )
        self.assertEqual(self.module.list_dictionary("镀色", "C-1")["total"], 1)
        for action in (
            lambda: self.saved(),
            lambda: self.update(archive, value="edit"),
        ):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("镀色", "C-1")

    def test_readonly_capability_probe_is_quiet_but_native_write_guard_still_denies(
        self,
    ):
        archive = self.saved()
        original = Document.has_permission

        def native_noisy_permission(document, action):
            allowed = original(document, action)
            if not allowed:
                self.frappe.local.message_log.append(
                    {"message": "native denial " + action}
                )
            return allowed

        self.role_denials.update(("create", "write"))
        self.frappe.local.message_log = []
        before = deepcopy((self.documents, self.versions, self.series))
        with patch.object(Document, "has_permission", native_noisy_permission):
            archives = (
                self.module.init_dictionary("镀色"),
                self.module.init_dictionary("镀色", "C-1"),
                self.module.read_dictionary(archive["name"], "镀色"),
            )
            self.assertTrue(all(not row["can_write"] for row in archives))
            self.assertTrue(
                all(row["field_meta"]["value"]["read_only"] for row in archives)
            )
            self.assertEqual(self.frappe.local.message_log, [])
            self.assertEqual((self.documents, self.versions, self.series), before)
            with self.assertRaises(PermissionError):
                self.update(archive, remarks="unauthorized")
            self.assertIn(
                {"message": "native denial write"}, self.frappe.local.message_log
            )
            self.assertEqual((self.documents, self.versions, self.series), before)

    def test_native_creation_cannot_forge_server_identity_number_or_abbreviation(self):
        for field, value in (
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
            ("numbering_mode", self.module.AUTO_MODE),
            ("legacy_number", "001"),
            ("abbreviation", "JP"),
        ):
            document = self.new_doc(DOCTYPE)
            document.company, document.value = "C-1", "native"
            document[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                document.save()
        direct = self.new_doc(DOCTYPE)
        direct.company, direct.value = "C-1", "native allowed"
        direct.save()
        self.assertEqual(direct.legacy_number, "001")

    def test_native_and_api_existing_readonly_and_identity_fields_cannot_change(self):
        archive = self.saved()
        for field, value in (
            ("company", "C-2"),
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
            ("legacy_number", "X"),
            ("abbreviation", "JP"),
        ):
            document = self.get_doc(DOCTYPE, archive["name"])
            document[field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "不能修改"),
            ):
                document.save()
        for change in (
            {"legacy_number": "001"},
            {"abbreviation": None},
            {"request_digest": "forged"},
            {"unknown": 1},
        ):
            with self.assertRaisesRegex(ValueError, "不支持的参数"):
                self.update(archive, **change)
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.update(archive, company="C-2")

    def test_partial_visible_edit_preserves_hidden_original_empty_remarks(self):
        archive = self.saved(remarks="")
        self.hidden["read"].add("remarks")
        self.hidden["write"].add("remarks")
        changed = self.module.save_dictionary(
            {
                "name": archive["name"],
                "version": archive["version"],
                "company": "C-1",
                "dictionary_type": "镀色",
                "value": "visible change",
            }
        )
        self.assertNotIn("remarks", changed)
        self.assertEqual(self.documents[archive["name"]]["remarks"], "")

    def test_invalid_types_flags_versions_uuid_and_paging_are_rejected(self):
        for change in (
            {"dictionary_type": "Company"},
            {"value": " "},
            {"value": "x" * 141},
            {"remarks": 2},
            {"inactive": 1.0},
            {"inactive": "true"},
            {"request_id": "bad"},
            {"version": "old"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.saved(**change)
        for start in (-1, True, 1.0, "1.0", "-1", 9007199254740992, "9" * 5000):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.module.list_dictionary("镀色", "C-1", start=start)
        for length in (0, 1, True, 26, 101):
            with self.subTest(length=length), self.assertRaises(ValueError):
                self.module.list_dictionary("镀色", "C-1", page_length=length)

    def test_creation_deleted_replay_and_same_name_replacement_keep_permanent_identity(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        delete_request = self.delete_payload(archive)
        reply = self.module.delete_dictionary(delete_request)
        self.assertEqual(reply["status"], "deleted")
        self.assertEqual(len(self.deleted), 1)
        self.assertEqual(self.module.resolve_delete(delete_request), reply)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "镀色", "C-1")["status"], "deleted")
        before = deepcopy((self.documents, self.receipts, self.series))
        with self.assertRaisesRegex(ValueError, "已删除"):
            self.module.save_dictionary(payload)
        self.assertEqual((self.documents, self.receipts, self.series), before)
        replacement = self.new_doc(DOCTYPE)
        replacement.name, replacement.company, replacement.value = archive["name"], "C-1", "同名新镀色"
        replacement.save()
        self.assertNotEqual(replacement.request_id, payload["request_id"])
        self.assertNotEqual(replacement.creation.isoformat(), archive["creation"])
        self.assertEqual(self.module.resolve_delete(delete_request), reply)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "镀色", "C-1")["status"], "deleted")
        with self.assertRaisesRegex(ValueError, "删除请求已使用"):
            self.module.delete_dictionary(delete_request)
        self.assertEqual(self.documents[replacement.name]["value"], "同名新镀色")

    def test_direct_native_form_create_and_delete_use_the_same_permanent_record(self):
        document = self.new_doc(DOCTYPE)
        document.company, document.value = "C-1", "原生表单镀色"
        document.save()
        request_id = document.request_id
        self.assertEqual(len(self.receipts), 1)
        self.frappe.delete_doc(DOCTYPE, document.name, ignore_missing=False)
        record = next(iter(self.receipts.values()))
        self.assertEqual(record["status"], "deleted")
        self.assertEqual(record["creation_request_id"], request_id)
        self.assertEqual(record["color_name"], self.deleted[0]["name"])
        self.assertEqual(self.module.resolve_creation(request_id, "镀色", "C-1")["status"], "deleted")
        with patch.object(self.module, "uuid4", return_value=request_id), self.assertRaisesRegex(ValueError, "身份冲突"):
            replacement = self.new_doc(DOCTYPE)
            replacement.company, replacement.value = "C-1", "另一原生新建"
            replacement.save()
        self.assertFalse(self.documents)

    def test_deletion_preserves_native_http_timestamp_strings_after_exact_comparison(self):
        archive = self.saved()
        self.assertIsInstance(archive["creation"], datetime)
        listed = self.module.list_dictionary("镀色", "C-1")["rows"][0]
        self.assertIsInstance(listed["creation"], datetime)
        request = self.delete_payload(archive, version=archive["version"].replace("T", " "))
        self.assertIn(" ", request["creation"])
        self.assertTrue(request["creation"].endswith("000001"))
        reply = self.module.delete_dictionary(request)
        self.assertEqual(reply, {"status": "deleted", **request})
        self.assertEqual(self.module.resolve_delete(request), reply)
        iso_request = {**request, "creation": request["creation"].replace(" ", "T"),
                       "version": request["version"].replace(" ", "T")}
        self.assertEqual(self.module.resolve_delete(iso_request), {"status": "deleted", **iso_request})
        for field in ("creation", "version"):
            changed = datetime.fromisoformat(request[field]) + timedelta(microseconds=1)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.resolve_delete({**request, field: str(changed)})

    def test_native_link_rejection_rolls_back_receipt_and_keeps_source_and_deleted_audit(self):
        archive = self.saved()
        self.references.add(archive["name"])
        before = deepcopy((self.documents, self.receipts, self.deleted, self.series))
        with self.assertRaisesRegex(ValueError, "业务引用") as caught:
            self.module.delete_dictionary(self.delete_payload(archive))
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual((self.documents, self.receipts, self.deleted, self.series), before)
        self.assertEqual(self.frappe.delete_doc.call_args.kwargs, {"ignore_missing": False})
        self.assertTrue(all(record["status"] == "active" for record in self.receipts.values()))

    def test_late_native_delete_failure_rolls_back_both_color_and_permanent_status(self):
        archive = self.saved()
        before = deepcopy((self.documents, self.receipts, self.deleted, self.series))
        original = self.module.after_delete_dictionary
        def late_failure(document):
            original(document)
            raise ValueError("native audit or attachment failure")
        with patch.object(self.module, "after_delete_dictionary", side_effect=late_failure), self.assertRaises(ValueError):
            self.module.delete_dictionary(self.delete_payload(archive))
        self.assertEqual((self.documents, self.receipts, self.deleted, self.series), before)

    def test_stale_creation_version_and_cross_company_never_delete_a_color(self):
        archive = self.saved()
        before = deepcopy((self.documents, self.receipts, self.deleted))
        for changes in ({"creation": "2020-01-01T00:00:00"}, {"version": "2020-01-01T00:00:00"}, {"company": "C-2"}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.module.delete_dictionary(self.delete_payload(archive, **changes))
            self.assertEqual((self.documents, self.receipts, self.deleted), before)
        self.frappe.delete_doc.assert_not_called()

    def test_delete_permission_mask_and_company_revocation_keep_business_unchanged(self):
        archive = self.saved()
        before = deepcopy((self.documents, self.receipts, self.deleted))
        self.role_denials.add("delete")
        with self.assertRaises(PermissionError):
            self.module.delete_dictionary(self.delete_payload(archive))
        self.role_denials.clear()
        self.hidden["read"].add("creation")
        with self.assertRaises(PermissionError):
            self.module.delete_dictionary(self.delete_payload(archive))
        self.hidden["read"].clear()
        self.denied_companies.add("C-1")
        with self.assertRaises(PermissionError):
            self.module.delete_dictionary(self.delete_payload(archive))
        self.assertEqual((self.documents, self.receipts, self.deleted), before)

    def test_receipts_cannot_be_created_edited_deleted_or_renamed_by_native_forms(self):
        archive = self.saved()
        record = self.get_doc(RECEIPT, next(iter(self.receipts)))
        record.status = "deleted"
        with self.assertRaisesRegex(ValueError, "只能由原生"):
            record.save()
        with self.assertRaisesRegex(ValueError, "只能由原生"):
            self.new_doc(RECEIPT).save()
        for action in (lambda: self.module.prevent_receipt_change(record), lambda: self.module.prevent_receipt_change(record, "new", False)):
            with self.assertRaisesRegex(ValueError, "永久保留"):
                action()
        self.assertEqual(self.receipts[record.name]["status"], "active")
        self.assertIn(archive["name"], self.documents)

    def test_unknown_or_current_source_is_not_deletion_evidence_and_no_read_calls_write(self):
        archive = self.saved()
        request = self.delete_payload(archive)
        before = deepcopy((self.documents, self.receipts, self.deleted, self.series))
        self.assertEqual(self.module.resolve_delete(request), {"status": "not_deleted", **request})
        self.assertEqual(self.module.resolve_delete(request | {"name": "ABSENT"}), {"status": "unknown"})
        self.assertEqual((self.documents, self.receipts, self.deleted, self.series), before)
        self.frappe.delete_doc.assert_not_called()

    def test_deleted_record_read_still_requires_source_scope_fields_and_receipt_fields(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        request = self.delete_payload(archive)
        self.module.delete_dictionary(request)
        for field in ("creation", "owner", "value", "deleted_version"):
            self.hidden["read"].add(field)
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.resolve_delete(request)
            self.hidden["read"].clear()
        self.document_denials.add(archive["name"])
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1")
        self.document_denials.clear()
        self.denied_companies.add("C-1")
        with self.assertRaises(PermissionError):
            self.module.resolve_delete(request)

    def test_receipt_creation_failure_rolls_back_insert_and_number_in_api_transaction(self):
        before = deepcopy((self.documents, self.receipts, self.series, self.deleted))
        with patch.object(self.module, "_save_record", side_effect=PermissionError("receipt create denied")), self.assertRaises(PermissionError):
            self.saved()
        self.assertEqual((self.documents, self.receipts, self.series, self.deleted), before)

    def test_on_trash_to_after_delete_version_drift_cannot_write_a_false_receipt(self):
        archive = self.saved()
        before = deepcopy((self.documents, self.receipts, self.deleted))
        original = self.module.after_delete_dictionary
        def changed_version(document):
            document.modified += timedelta(seconds=1)
            original(document)
        with patch.object(self.module, "after_delete_dictionary", side_effect=changed_version), self.assertRaisesRegex(ValueError, "准确原生来源"):
            self.module.delete_dictionary(self.delete_payload(archive))
        self.assertEqual((self.documents, self.receipts, self.deleted), before)

    def test_permanent_record_does_not_duplicate_original_text_number_or_digest(self):
        archive = self.saved()
        record = next(iter(self.receipts.values()))
        for field in ("code", "value", "color_system", "remarks", "legacy_number", "creation_digest", "request_digest"):
            self.assertNotIn(field, record)
        self.hidden["read"].update(("legacy_number", "code", "color_system", "remarks"))
        self.assertNotIn("legacy_number", self.module.read_dictionary(archive["name"], "镀色"))
        self.assertNotIn("legacy_number", record)

    def test_two_colors_can_keep_two_permanent_deletion_records(self):
        archives = [self.saved(value="第一镀色"), self.saved(value="第二镀色")]
        requests = [self.delete_payload(archive) for archive in archives]
        for request in requests:
            self.assertEqual(self.module.delete_dictionary(request)["status"], "deleted")
        self.assertFalse(self.documents)
        self.assertEqual(len(self.receipts), 2)
        self.assertEqual(len(self.deleted), 2)
        for request in requests:
            self.assertEqual(self.module.resolve_delete(request)["request_id"], request["request_id"])

    def test_fixed_readonly_type_and_independent_schema(self):
        self.assertEqual(self.module.TYPES, EXPECTED_TYPES)
        field = self.meta.get_field("dictionary_type")
        self.assertEqual(
            (
                field.options,
                field.default,
                field.fieldtype,
                field.set_only_once,
                field.read_only,
            ),
            ("镀色", "镀色", "Select", 1, 1),
        )
        columns = [
            "company",
            "dictionary_type",
            "legacy_number",
            "code",
            "value",
            "color_system",
            "remarks",
            "abbreviation",
            "inactive",
            "numbering_mode",
            "request_id",
            "request_digest",
        ]
        self.assertEqual(SCHEMA["field_order"], columns)
        self.assertEqual([row["fieldname"] for row in SCHEMA["fields"]], columns)
        self.assertEqual(SCHEMA["autoname"], "format:JF-PL-C-{YYYY}-{#####}")
        self.assertEqual(
            SCHEMA["permissions"],
            [
                {
                    "role": "System Manager",
                    "read": 1,
                    "select": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1,
                },
                {
                    "role": "Sales Manager",
                    "read": 1,
                    "select": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1,
                },
                {"role": "Sales User", "read": 1, "select": 1},
            ],
        )
        for name in ("code", "color_system"):
            self.assertEqual(self.meta.get_field(name).fieldtype, "Data")
            self.assertFalse(self.meta.get_field(name).reqd)
        self.assertTrue(self.meta.get_field("request_id").unique)
        archive = self.module.init_dictionary("镀色", "C-1")
        self.assertTrue(archive["field_meta"]["dictionary_type"]["read_only"])
        self.assertTrue(archive["can_write"])
        self.assertEqual(self.saved()["dictionary_type"], "镀色")

    def test_all_api_and_native_identities_require_exact_fixed_type(self):
        archive = self.saved()
        for invalid in (None, "", " 镀色", "镀色 ", "订单类型", "订单要求", 1, False):
            before = deepcopy((self.documents, self.versions, self.series))
            for action in (
                lambda: self.module.init_dictionary(invalid, "C-1"),
                lambda: self.module.read_dictionary(archive["name"], invalid),
                lambda: self.module.list_dictionary(invalid, "C-1"),
                lambda: self.module.resolve_creation(str(uuid4()), invalid, "C-1"),
                lambda: self.saved(dictionary_type=invalid),
                lambda: self.update(archive, dictionary_type=invalid),
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    action()
            native = self.new_doc(DOCTYPE)
            native.company, native.value, native.dictionary_type = (
                "C-1",
                "native",
                invalid,
            )
            with self.assertRaises(ValueError):
                native.save()
            self.assertEqual((self.documents, self.versions, self.series), before)
        payload = self.payload()
        del payload["dictionary_type"]
        with self.assertRaises(ValueError):
            self.module.save_dictionary(payload)
        with self.assertRaises(TypeError):
            self.module.read_dictionary(archive["name"])

    def test_code_color_system_and_name_preserve_original_text_without_mapping(self):
        payload = self.payload(code=" 0001 / 原文-Ａ ", color_system=" 原色系: 蓝/银 ")
        archive = self.module.save_dictionary(payload)
        for field in self.module.EDIT_FIELDS:
            self.assertEqual(archive[field], payload[field])
        for field in ("code", "color_system"):
            self.assertIn(field, archive["field_meta"])
            self.assertFalse(archive["field_meta"][field]["read_only"])
        changed = self.update(archive, code="", color_system=None, value=" 改名原文 ")
        self.assertEqual(
            (changed["code"], changed["color_system"], changed["value"]),
            ("", None, " 改名原文 "),
        )
        # Neither display names nor code have an evidenced uniqueness rule.
        second = self.saved(
            code=payload["code"],
            value=payload["value"],
            color_system=payload["color_system"],
        )
        self.assertNotEqual(second["name"], archive["name"])
        self.assertEqual(second["legacy_number"], "002")
        self.frappe.get_all.assert_not_called()

    def test_optional_original_text_validation_and_required_name_are_atomic(self):
        for field in ("code", "color_system"):
            for invalid in (1, False, [], {}, "x" * 141):
                before = deepcopy((self.documents, self.versions, self.series))
                with (
                    self.subTest(field=field, invalid=invalid),
                    self.assertRaises(ValueError),
                ):
                    self.saved(**{field: invalid})
                self.assertEqual((self.documents, self.versions, self.series), before)
        for invalid in (None, "", " \n\t", False):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                self.saved(value=invalid)
        archive = self.saved(code=None, color_system="")
        self.assertIsNone(archive["code"])
        self.assertEqual(archive["color_system"], "")

    def test_original_code_and_color_system_are_in_uuid_digest(self):
        payload = self.payload(code=" 001 ", color_system=" 原色系 ")
        archive = self.module.save_dictionary(payload)
        current = self.update(archive, code=" 后续代码 ", color_system=" 后续色系 ")
        before = deepcopy((self.documents, self.versions, self.series))
        allocations = self.allocate.call_count
        self.assertEqual(self.module.save_dictionary(payload), current)
        for field, value in (("code", "001"), ("color_system", "原色系")):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "身份冲突"),
            ):
                self.module.save_dictionary(payload | {field: value})
            self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertEqual(self.allocate.call_count, allocations)
        self.assertEqual(
            self.module.resolve_creation(payload["request_id"], "镀色", "C-1"),
            {"status": "found", "archive": current},
        )
        self.assertEqual(
            self.module.resolve_creation(payload["request_id"], "镀色", "C-2"),
            {"status": "not_found"},
        )
        with self.assertRaisesRegex(ValueError, "身份冲突"):
            self.module.save_dictionary(payload | {"company": "C-2"})
        self.assertEqual((self.documents, self.versions, self.series), before)
        for field in ("code", "color_system"):
            self.assertNotEqual(
                self.module._digest("C-1", "镀色", self.payload(**{field: None})),
                self.module._digest("C-1", "镀色", self.payload(**{field: ""})),
            )

    def test_search_uses_each_visible_original_field_and_never_masked_filters(self):
        archive = self.saved(
            code="CODE-TOKEN",
            value="NAME-TOKEN",
            color_system="COLOR-TOKEN",
            remarks="REMARK-TOKEN",
        )
        self.documents[archive["name"]]["abbreviation"] = "ABBR-TOKEN"
        for field, term in (
            ("legacy_number", "001"),
            ("code", "CODE-TOKEN"),
            ("value", "NAME-TOKEN"),
            ("color_system", "COLOR-TOKEN"),
            ("remarks", "REMARK-TOKEN"),
            ("abbreviation", "ABBR-TOKEN"),
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    self.module.list_dictionary("镀色", "C-1", search=term)["total"], 1
                )
                if field == "value":
                    continue  # Required visible identity; masking denies the query.
                self.meta.get_field(field).mask = 1
                try:
                    result = self.module.list_dictionary("镀色", "C-1", search=term)
                    self.assertEqual(result["total"], 0)
                    self.assertNotIn(
                        field,
                        {
                            row[0]
                            for row in self.frappe.get_list.call_args.kwargs[
                                "or_filters"
                            ]
                        },
                    )
                finally:
                    self.meta.get_field(field).mask = 0

    def test_optional_field_projection_and_masks_never_backfill_and_preserve_hidden_values(
        self,
    ):
        archive = self.saved(code=" 隐藏代码 ", color_system=" 隐藏色系 ")
        for field in ("code", "color_system"):
            self.omitted.add(field)
            result = self.module.read_dictionary(archive["name"], "镀色")
            self.assertNotIn(field, result)
            self.assertNotIn(field, result["field_meta"])
            self.omitted.remove(field)
            self.meta.get_field(field).mask = 1
        for result in (
            self.module.init_dictionary("镀色", "C-1"),
            self.module.read_dictionary(archive["name"], "镀色"),
        ):
            for field in ("code", "color_system"):
                self.assertNotIn(field, result)
                self.assertNotIn(field, result["field_meta"])
        listing = self.module.list_dictionary("镀色", "C-1")
        self.assertTrue(
            all(
                "code" not in row and "color_system" not in row
                for row in listing["rows"]
            )
        )
        changed = self.module.save_dictionary(
            {
                "name": archive["name"],
                "version": archive["version"],
                "company": "C-1",
                "dictionary_type": "镀色",
                "value": "可见修改",
            }
        )
        for field in ("code", "color_system"):
            self.assertEqual(self.documents[archive["name"]][field], archive[field])
            self.assertNotIn(field, changed)
            before = deepcopy((self.documents, self.versions, self.series))
            with self.assertRaises(PermissionError):
                self.update(changed, **{field: "隐藏覆盖"})
            self.assertEqual((self.documents, self.versions, self.series), before)
        self.mask_levels.add(0)
        visible = self.module.read_dictionary(archive["name"], "镀色")
        self.assertEqual(visible["code"], archive["code"])
        self.assertEqual(visible["color_system"], archive["color_system"])

    def test_native_and_api_readonly_original_fields_reject_edits(self):
        archive = self.saved()
        for field in ("code", "color_system", "value"):
            self.meta.get_field(field).read_only = 1
            try:
                before = deepcopy((self.documents, self.versions, self.series))
                current = self.module.read_dictionary(archive["name"], "镀色")
                self.assertTrue(current["field_meta"][field]["read_only"])
                with self.assertRaises(PermissionError):
                    self.update(current, **{field: "非法覆盖"})
                native = self.get_doc(DOCTYPE, archive["name"])
                native[field] = "非法原生表单覆盖"
                with self.assertRaises(PermissionError):
                    native.save()
                with self.assertRaises(PermissionError):
                    self.saved(**{field: "非法新建"})
                self.assertEqual((self.documents, self.versions, self.series), before)
            finally:
                self.meta.get_field(field).read_only = 0

    def test_stale_version_is_required_even_when_optional_fields_are_masked(self):
        archive = self.saved()
        current = self.update(archive, code="新代码")
        for field in ("code", "color_system"):
            self.meta.get_field(field).mask = 1
        before = deepcopy((self.documents, self.versions, self.series))
        for version in (archive["version"], None, "", "bad", current["version"] + "Z"):
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                self.module.save_dictionary(
                    {
                        "name": current["name"],
                        "version": version,
                        "company": "C-1",
                        "dictionary_type": "镀色",
                        "value": "旧版本覆盖",
                    }
                )
            self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertTrue(self.frappe.get_doc.call_args.kwargs["for_update"])

    def test_counter_keys_fit_native_series_and_never_consume_other_dictionary_numbers(
        self,
    ):
        companies = ("C-1", "集" * 140)
        keys = [self.module._series_key(company, "镀色") for company in companies]
        self.assertEqual(len(set(keys)), 2)
        for company, key in zip(companies, keys):
            self.assertEqual(
                key,
                "JF-PL-C-N-" + hashlib.sha256(company.encode()).hexdigest() + "-color-",
            )
            self.assertTrue(key.isascii())
            self.assertLess(len(key), 94)
        previous = {
            "JF-ORD-D-N-" + hashlib.sha256(b"C-1").hexdigest() + "-type-": 7,
            "JF-ORD-D-N-" + hashlib.sha256(b"C-1").hexdigest() + "-requirement-": 9,
            "JF-CUST-T-N-" + hashlib.sha256(b"C-1").hexdigest() + "-customer_type-": 8,
            "JF-PROD-N-" + hashlib.sha256(b"C-1").hexdigest() + "-style-": 12,
            "SAL-ORD-2026-": 31,
        }
        self.series.update(previous)
        self.assertEqual(self.saved()["legacy_number"], "001")
        self.assertEqual(self.saved(company=companies[1])["legacy_number"], "001")
        self.assertEqual({key: self.series[key] for key in previous}, previous)

    def test_historical_abbreviation_is_preserved_without_recalculation(self):
        archive = self.saved()
        self.documents[archive["name"]]["abbreviation"] = " 历史 JP 原文 "
        archive = self.module.read_dictionary(archive["name"], "镀色")
        changed = self.update(
            archive, value="改名", code="改代码", color_system="改色系"
        )
        self.assertEqual(changed["abbreviation"], " 历史 JP 原文 ")
        self.assertTrue(changed["field_meta"]["abbreviation"]["read_only"])


if __name__ == "__main__":
    unittest.main()
