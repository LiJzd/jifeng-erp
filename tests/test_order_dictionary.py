"""Order-dictionary permissions, source races, complete paging and safe receipts."""

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
DOCTYPE = "Jifeng Order Dictionary"
EXPECTED_TYPES = {"订单类型": "type", "订单要求": "requirement"}
SCHEMA = json.loads(
    (
        ROOT
        / "jifeng_erp/jifeng_erp/doctype/jifeng_order_dictionary/jifeng_order_dictionary.json"
    ).read_text(encoding="utf-8")
)


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
        return (
            action not in env.role_denials
            and self.name not in env.document_denials
            and self.company not in env.denied_companies
        )

    def check_permission(self, action):
        if not self.has_permission(action):
            raise PermissionError("native " + action)

    def get_doc_before_save(self):
        saved = self.environment.documents.get(self.name)
        return Document(deepcopy(saved)) if saved else None

    def save(self):
        env, new = self.environment, self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            env.module.before_insert_dictionary(self)
        env.module.validate_dictionary(self)
        if any(
            row["request_id"] == self.request_id and row["name"] != self.name
            for row in env.documents.values()
        ):
            env.frappe.local.message_log = list(
                getattr(env.frappe.local, "message_log", None) or []
            ) + [{"message": "private unique index"}]
            raise KeyError("private unique SQL")
        env.tick += 1
        self.name = self.name or "ORD-D-" + str(env.tick)
        self.modified = datetime(2026, 10, 2, 0, 0, 0) + timedelta(
            microseconds=env.tick
        )
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append(deepcopy(dict(self)))
        if env.fail_after_save:
            env.db.after_commit.add(lambda: None)
            env.frappe.flags.currently_saving = ["unexpected save context"]
            raise ValueError("native post-save failure")
        return self


class OrderDictionaryTest(unittest.TestCase):
    def setUp(self):
        self.documents, self.versions, self.series, self.snapshots = {}, [], {}, {}
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
        self.frappe.get_meta = Mock(return_value=self.meta)
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
                side_effect=lambda dt, name, field, **kwargs: self.series.get(name)
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
        self.frappe.db = self.db
        model, naming = (
            types.ModuleType("frappe.model"),
            types.ModuleType("frappe.model.naming"),
        )
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            (
                {field.fieldname for field in self.meta.fields}
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
            "order_dictionary_under_test", ROOT / "jifeng_erp/order_dictionary.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self

    def new_doc(self, doctype):
        self.assertEqual(doctype, DOCTYPE)
        return Document(
            doctype=DOCTYPE,
            name=None,
            company="DEFAULT-DENIED",
            dictionary_type="订单类型",
            numbering_mode=None,
            inactive=0,
            __islocal=1,
        )

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":

            def check(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company permission")

            return Row(name=name, check_permission=check)
        self.assertEqual(
            doctype, DOCTYPE, "must never change native Customer or Sales Order"
        )
        if self.source_hook:
            self.source_hook(name)
        return Document(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **options):
        self.assertEqual(doctype, DOCTYPE)
        result = []
        for name, row in self.documents.items():
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
        self.snapshots[point] = deepcopy((self.documents, self.versions, self.series))

    def rollback(self, save_point):
        self.documents, self.versions, self.series = deepcopy(
            self.snapshots[save_point]
        )

    def payload(self, **changes):
        return {
            "request_id": str(uuid4()),
            "company": "C-1",
            "dictionary_type": "订单类型",
            "value": " 订单词典名称 ",
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

    def test_schema_sales_roles_only_and_no_native_delete_export(self):
        self.assertEqual(
            {row["role"] for row in SCHEMA["permissions"]},
            {"System Manager", "Sales Manager", "Sales User"},
        )
        for row in SCHEMA["permissions"]:
            self.assertFalse(row.get("delete") or row.get("export"))
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
        first = self.module.init_dictionary("订单类型", "C-1")
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
        self.assertIsNone(
            self.module.init_dictionary("订单类型", "C-1")["number_preview"]
        )
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
            self.module.resolve_creation(payload["request_id"], "订单类型", "C-1")[
                "archive"
            ],
            changed,
        )
        self.assertEqual(
            self.module.resolve_creation(str(uuid4()), "订单类型", "C-1"),
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
        self.assertEqual(
            self.module.resolve_creation(payload["request_id"], "订单类型", "C-1"),
            {"status": "not_found"},
        )
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
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"]))
        self.assertNotIn(
            "remarks", self.module.list_dictionary("订单类型", "C-1")["rows"][0]
        )
        self.omitted.clear()
        self.meta.get_field("remarks").mask = 1
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"]))
        with self.assertRaises(PermissionError):
            self.update(archive, remarks="masked write")
        self.mask_levels.add(0)
        self.assertEqual(
            self.module.read_dictionary(archive["name"])["remarks"], archive["remarks"]
        )

    def test_required_mask_or_projection_omission_fails_entire_query(self):
        archive = self.saved()
        self.meta.get_field("value").mask = 1
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("订单类型", "C-1")
        self.meta.get_field("value").mask = 0
        self.omitted.add("value")
        for action in (
            lambda: self.module.read_dictionary(archive["name"]),
            lambda: self.module.list_dictionary("订单类型", "C-1"),
        ):
            with self.assertRaises(PermissionError):
                action()

    def test_receipt_projection_and_field_permissions_are_not_backfilled(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.omitted.add("request_digest")
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "订单类型", "C-1")
        self.omitted.clear()
        self.hidden["read"].add("request_digest")
        self.assertEqual(
            self.module.read_dictionary(archive["name"])["name"], archive["name"]
        )
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "订单类型", "C-1")

    def test_complete_paging_over_a_thousand_and_out_of_range_clamping(self):
        for index in range(1103):
            name = "ROW-" + str(index).zfill(4)
            self.documents[name] = {
                "doctype": DOCTYPE,
                "name": name,
                "company": "C-1",
                "dictionary_type": "订单类型",
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
            "订单类型", "C-1", start="1100", page_length="100"
        )
        self.assertEqual(
            (response["total"], response["start"], len(response["rows"])),
            (1103, 1100, 3),
        )
        self.assertEqual(response["rows"][0]["value"], "ROW-1100")
        self.assertEqual(
            self.module.list_dictionary(
                "订单类型", "C-1", start=999999, page_length=100
            )["start"],
            1100,
        )
        self.assertEqual(
            self.module.list_dictionary("订单类型", "C-1", search="absent", start=100)[
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
            "订单类型", "C-1", search="001", include_inactive="1"
        )
        self.assertEqual(
            (
                result["total"],
                result["company"],
                result["dictionary_type"],
                result["search"],
                result["include_inactive"],
            ),
            (1, "C-1", "订单类型", "001", 1),
        )
        self.hidden["read"].add("legacy_number")
        self.assertEqual(
            self.module.list_dictionary("订单类型", "C-1", search="001")["total"], 0
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
            self.module.list_dictionary("订单类型", "C-1")
        self.source_hook = None
        original = self.frappe.get_list.side_effect

        def changed_projection(*args, **kwargs):
            change(archive["name"])
            return original(*args, **kwargs)

        self.frappe.get_list.side_effect = changed_projection
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.read_dictionary(archive["name"])
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
            self.module.list_dictionary("订单类型", "C-1")

    def test_company_select_and_document_permissions_guard_read_create_and_receipts(
        self,
    ):
        archive, payload = self.saved(), self.payload(company="C-2")
        self.denied_companies.add("C-2")
        for action in (
            lambda: self.module.init_dictionary("订单类型", "C-2"),
            lambda: self.module.save_dictionary(payload),
            lambda: self.module.list_dictionary("订单类型", "C-2"),
            lambda: self.module.resolve_creation(
                payload["request_id"], "订单类型", "C-2"
            ),
        ):
            with self.assertRaises(PermissionError):
                action()
        self.document_denials.add(archive["name"])
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"])

    def test_sales_user_can_read_but_cannot_create_edit_or_gain_export(self):
        archive = self.saved()
        self.role_denials.update(("create", "write", "export", "delete"))
        self.assertFalse(self.module.init_dictionary("订单类型", "C-1")["can_write"])
        self.assertFalse(self.module.read_dictionary(archive["name"])["can_write"])
        self.assertEqual(self.module.list_dictionary("订单类型", "C-1")["total"], 1)
        for action in (
            lambda: self.saved(),
            lambda: self.update(archive, value="edit"),
        ):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("订单类型", "C-1")

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
                self.module.init_dictionary("订单类型"),
                self.module.init_dictionary("订单类型", "C-1"),
                self.module.read_dictionary(archive["name"]),
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
                "dictionary_type": "订单类型",
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
                self.module.list_dictionary("订单类型", "C-1", start=start)
        for length in (0, 1, True, 26, 101):
            with self.subTest(length=length), self.assertRaises(ValueError):
                self.module.list_dictionary("订单类型", "C-1", page_length=length)

    def test_deletion_is_rejected_even_for_administrator(self):
        archive = self.saved()
        self.frappe.session.user = "Administrator"
        with self.assertRaisesRegex(ValueError, "删除尚未实现"):
            self.module.prevent_delete(self.get_doc(DOCTYPE, archive["name"]))
        self.assertIn(archive["name"], self.documents)

    def test_two_dictionary_types_keep_schema_columns_default_and_permissions(self):
        self.assertEqual(self.module.TYPES, EXPECTED_TYPES)
        field = self.meta.get_field("dictionary_type")
        self.assertEqual(field.options.splitlines(), list(EXPECTED_TYPES))
        self.assertEqual(
            (field.default, field.fieldtype, field.set_only_once),
            ("订单类型", "Select", 1),
        )
        columns = [
            "company",
            "dictionary_type",
            "legacy_number",
            "value",
            "remarks",
            "abbreviation",
            "inactive",
            "numbering_mode",
            "request_id",
            "request_digest",
        ]
        self.assertEqual(SCHEMA["field_order"], columns)
        self.assertEqual([row["fieldname"] for row in SCHEMA["fields"]], columns)
        self.assertEqual(
            SCHEMA["permissions"],
            [
                {
                    "role": "System Manager",
                    "read": 1,
                    "select": 1,
                    "write": 1,
                    "create": 1,
                },
                {
                    "role": "Sales Manager",
                    "read": 1,
                    "select": 1,
                    "write": 1,
                    "create": 1,
                },
                {"role": "Sales User", "read": 1, "select": 1},
            ],
        )

    def test_independent_series_keys_fit_native_varchar100_for_all_companies(self):
        # The real native experiment found tabSeries.name is varchar(100).
        # Company identifiers are hashed; even a full-length Unicode company
        # name must fit without shortening or colliding with another scope.
        companies = ("C-1", "集" * 140)
        expected_lengths = {"订单类型": 81, "订单要求": 88}
        keys = [
            self.module._series_key(company, dictionary_type)
            for company in companies
            for dictionary_type in EXPECTED_TYPES
        ]
        self.assertEqual(len(set(keys)), 4)
        for company in companies:
            for dictionary_type, expected_length in expected_lengths.items():
                with self.subTest(company=company, dictionary_type=dictionary_type):
                    key = self.module._series_key(company, dictionary_type)
                    self.assertTrue(key.isascii())
                    self.assertEqual(len(key), expected_length)
                    self.assertLessEqual(len(key), 100)
                    for original_type in EXPECTED_TYPES:
                        original_key = (
                            "JF-ORD-D-N-"
                            + hashlib.sha256(company.encode()).hexdigest()
                            + "-"
                            + EXPECTED_TYPES[original_type]
                            + "-"
                        )
                        self.assertEqual(
                            self.module._series_key(company, original_type),
                            original_key,
                        )

    def test_company_and_type_counters_and_same_name_lists_are_independent(self):
        original_key = "JF-ORD-D-N-" + hashlib.sha256(b"C-1").hexdigest() + "-type-"
        self.assertEqual(self.module._series_key("C-1", "订单类型"), original_key)
        self.series[original_key] = 7
        archives = {}
        for company in ("C-1", "C-2"):
            for dictionary_type, code in EXPECTED_TYPES.items():
                with self.subTest(company=company, dictionary_type=dictionary_type):
                    key = (
                        "JF-ORD-D-N-"
                        + hashlib.sha256(company.encode()).hexdigest()
                        + "-"
                        + code
                        + "-"
                    )
                    self.assertEqual(
                        self.module._series_key(company, dictionary_type), key
                    )
                    start = 8 if key == original_key else 1
                    self.assertEqual(
                        self.module.init_dictionary(dictionary_type, company)[
                            "number_preview"
                        ],
                        str(start).zfill(3),
                    )
                    rows = [
                        self.saved(
                            company=company,
                            dictionary_type=dictionary_type,
                            value="共同名称",
                        )
                        for _ in range(2)
                    ]
                    archives[(company, dictionary_type)] = rows
                    self.assertEqual(
                        [row["legacy_number"] for row in rows],
                        [str(start).zfill(3), str(start + 1).zfill(3)],
                    )
                    self.assertEqual(
                        self.module.init_dictionary(dictionary_type, company)[
                            "number_preview"
                        ],
                        str(start + 2).zfill(3),
                    )
        self.assertEqual(len(self.series), 4)
        self.assertEqual(self.series[original_key], 9)
        for (company, dictionary_type), rows in archives.items():
            with self.subTest(company=company, dictionary_type=dictionary_type):
                result = self.module.list_dictionary(
                    dictionary_type, company, search="共同名称"
                )
                self.assertEqual(result["total"], 2)
                self.assertEqual(
                    {row["name"] for row in result["rows"]},
                    {row["name"] for row in rows},
                )
                self.assertTrue(
                    all(
                        (row["company"], row["dictionary_type"])
                        == (company, dictionary_type)
                        for row in result["rows"]
                    )
                )

    def test_other_dictionary_and_native_series_are_never_consumed(self):
        previous = {
            "JF-CUST-T-N-" + hashlib.sha256(b"C-1").hexdigest() + "-customer_type-": 8,
            "JF-PROD-N-" + hashlib.sha256(b"C-1").hexdigest() + "-style-": 12,
            "SAL-ORD-2026-": 31,
        }
        self.series.update(previous)
        for dictionary_type in EXPECTED_TYPES:
            archive = self.saved(dictionary_type=dictionary_type)
            self.assertEqual(archive["legacy_number"], "001")
        self.assertEqual({key: self.series[key] for key in previous}, previous)

    def test_historical_abbreviation_is_preserved_on_rename_and_still_readonly(self):
        for dictionary_type in EXPECTED_TYPES:
            archive = self.saved(dictionary_type=dictionary_type)
            # Existing imported spelling is not a new server-generated rule.
            self.documents[archive["name"]]["abbreviation"] = " 历史 JP "
            archive = self.module.read_dictionary(archive["name"], dictionary_type)
            renamed = self.update(archive, value=" 改名仍保留原简拼 ")
            self.assertEqual(renamed["abbreviation"], " 历史 JP ")
            self.assertTrue(renamed["field_meta"]["abbreviation"]["read_only"])
            with self.assertRaisesRegex(ValueError, "不支持的参数"):
                self.update(renamed, abbreviation="NEW")

    def test_cross_type_uuid_is_not_found_and_collision_rolls_back_without_receipt_reuse(
        self,
    ):
        for dictionary_type in EXPECTED_TYPES:
            with self.subTest(dictionary_type=dictionary_type):
                payload = self.payload(dictionary_type=dictionary_type)
                archive = self.module.save_dictionary(payload)
                current = self.update(archive, remarks="后续备注")
                before = deepcopy((self.documents, self.versions, self.series))
                allocations = self.allocate.call_count
                self.assertEqual(self.module.save_dictionary(payload), current)
                self.assertEqual(self.allocate.call_count, allocations)
                self.assertEqual(
                    self.module.resolve_creation(
                        payload["request_id"], dictionary_type, "C-1"
                    ),
                    {"status": "found", "archive": current},
                )
                for target_type in set(EXPECTED_TYPES) - {dictionary_type}:
                    with self.subTest(target_type=target_type):
                        self.assertEqual(
                            self.module.resolve_creation(
                                payload["request_id"], target_type, "C-1"
                            ),
                            {"status": "not_found"},
                        )
                        self.frappe.local.message_log = [{"message": "既有消息"}]
                        with self.assertRaises(ValueError) as caught:
                            self.module.save_dictionary(
                                payload | {"dictionary_type": target_type}
                            )
                        self.assertEqual(str(caught.exception), self.module.COLLISION)
                        self.assertNotIn(archive["name"], str(caught.exception))
                        self.assertEqual(
                            self.frappe.local.message_log, [{"message": "既有消息"}]
                        )
                        self.assertEqual(
                            (self.documents, self.versions, self.series), before
                        )
                self.assertEqual(
                    self.module.resolve_creation(
                        payload["request_id"], dictionary_type, "C-2"
                    ),
                    {"status": "not_found"},
                )
                with self.assertRaisesRegex(ValueError, "身份冲突"):
                    self.module.save_dictionary(payload | {"company": "C-2"})
                self.assertEqual((self.documents, self.versions, self.series), before)

    def test_explicit_type_read_and_native_or_api_identity_change_reject_wrong_dictionary(
        self,
    ):
        for dictionary_type in EXPECTED_TYPES:
            archive = self.saved(dictionary_type=dictionary_type)
            self.assertEqual(
                self.module.read_dictionary(archive["name"], dictionary_type)[
                    "dictionary_type"
                ],
                dictionary_type,
            )
            before = deepcopy((self.documents, self.versions, self.series))
            for target_type in set(EXPECTED_TYPES) - {dictionary_type}:
                with self.subTest(
                    dictionary_type=dictionary_type, target_type=target_type
                ):
                    with self.assertRaisesRegex(ValueError, "词条类型.*不一致"):
                        self.module.read_dictionary(archive["name"], target_type)
                    with self.assertRaisesRegex(ValueError, "不能修改"):
                        self.update(archive, dictionary_type=target_type)
                    document = self.get_doc(DOCTYPE, archive["name"])
                    document.dictionary_type = target_type
                    with self.assertRaisesRegex(ValueError, "不能修改"):
                        document.save()
                    self.assertEqual(
                        (self.documents, self.versions, self.series), before
                    )

    def test_new_types_stale_version_and_masks_preserve_hidden_original_fields(self):
        for dictionary_type in EXPECTED_TYPES:
            with self.subTest(dictionary_type=dictionary_type):
                archive = self.saved(
                    dictionary_type=dictionary_type,
                    remarks=dictionary_type + " 私有备注",
                )
                current = self.update(archive, value="后续名称")
                before = deepcopy((self.documents, self.versions, self.series))
                with self.assertRaises(RuntimeError):
                    self.update(archive, value="过期覆盖")
                self.assertEqual((self.documents, self.versions, self.series), before)
                self.assertTrue(
                    self.frappe.get_doc.call_args_list[-1].kwargs.get("for_update")
                )
                self.meta.get_field("remarks").mask = 1
                self.assertNotIn(
                    "remarks",
                    self.module.read_dictionary(current["name"], dictionary_type),
                )
                self.assertTrue(
                    all(
                        "remarks" not in row
                        for row in self.module.list_dictionary(dictionary_type, "C-1")[
                            "rows"
                        ]
                    )
                )
                visible = self.module.save_dictionary(
                    {
                        "name": current["name"],
                        "version": current["version"],
                        "company": "C-1",
                        "dictionary_type": dictionary_type,
                        "value": "可见字段修改",
                    }
                )
                self.assertNotIn("remarks", visible)
                self.assertEqual(
                    self.documents[current["name"]]["remarks"], archive["remarks"]
                )
                before = deepcopy((self.documents, self.versions, self.series))
                with self.assertRaises(PermissionError):
                    self.update(visible, remarks="掩码字段写入")
                self.assertEqual((self.documents, self.versions, self.series), before)
                self.meta.get_field("remarks").mask = 0
                for required in ("value", "dictionary_type"):
                    self.meta.get_field(required).mask = 1
                    try:
                        for action in (
                            lambda: self.module.read_dictionary(
                                visible["name"], dictionary_type
                            ),
                            lambda: self.module.list_dictionary(dictionary_type, "C-1"),
                            lambda: self.update(visible, value="无权覆盖"),
                        ):
                            with (
                                self.subTest(required=required),
                                self.assertRaises(PermissionError),
                            ):
                                action()
                        self.assertEqual(
                            (self.documents, self.versions, self.series), before
                        )
                    finally:
                        self.meta.get_field(required).mask = 0

    def test_second_scan_type_drift_rejects_entire_new_type_query(self):
        original = self.frappe.get_list.side_effect
        counterparts = {"订单类型": "订单要求", "订单要求": "订单类型"}
        for dictionary_type, counterpart in counterparts.items():
            archive = self.saved(dictionary_type=dictionary_type)
            calls = 0

            def drift(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self.documents[archive["name"]]["dictionary_type"] = counterpart
                return original(*args, **kwargs)

            before = deepcopy((self.versions, self.series))
            self.frappe.get_list.side_effect = drift
            try:
                with (
                    self.subTest(dictionary_type=dictionary_type),
                    self.assertRaisesRegex(ValueError, "来源已变化"),
                ):
                    self.module.list_dictionary(dictionary_type, "C-1")
                self.assertEqual(calls, 2)
                self.assertEqual((self.versions, self.series), before)
                self.assertEqual(
                    self.documents[archive["name"]]["dictionary_type"], counterpart
                )
            finally:
                self.frappe.get_list.side_effect = original

    def test_new_types_sales_user_capabilities_are_quiet_and_native_company_guards_remain(
        self,
    ):
        archives = {
            dictionary_type: self.saved(dictionary_type=dictionary_type)
            for dictionary_type in EXPECTED_TYPES
        }
        original = Document.has_permission

        def noisy_permission(document, action):
            allowed = original(document, action)
            if not allowed:
                self.frappe.local.message_log.append(
                    {"message": "native denial " + action}
                )
            return allowed

        self.role_denials.update(("create", "write", "export", "delete"))
        self.denied_companies.add("C-2")
        with patch.object(Document, "has_permission", noisy_permission):
            for dictionary_type, archive in archives.items():
                with self.subTest(dictionary_type=dictionary_type):
                    self.frappe.local.message_log = []
                    before = deepcopy((self.documents, self.versions, self.series))
                    responses = (
                        self.module.init_dictionary(dictionary_type),
                        self.module.init_dictionary(dictionary_type, "C-1"),
                        self.module.read_dictionary(archive["name"], dictionary_type),
                    )
                    self.assertTrue(all(not row["can_write"] for row in responses))
                    self.assertTrue(
                        all(
                            row["field_meta"]["value"]["read_only"] for row in responses
                        )
                    )
                    self.assertEqual(
                        self.module.list_dictionary(dictionary_type, "C-1")["total"], 1
                    )
                    self.assertEqual(self.frappe.local.message_log, [])
                    for action in (
                        lambda: self.saved(dictionary_type=dictionary_type),
                        lambda: self.update(archive, value="无权修改"),
                    ):
                        with self.assertRaises(PermissionError):
                            action()
                    self.assertEqual(
                        (self.documents, self.versions, self.series), before
                    )
                    for action in (
                        lambda: self.module.init_dictionary(dictionary_type, "C-2"),
                        lambda: self.module.list_dictionary(dictionary_type, "C-2"),
                        lambda: self.module.resolve_creation(
                            str(uuid4()), dictionary_type, "C-2"
                        ),
                        lambda: self.saved(
                            dictionary_type=dictionary_type, company="C-2"
                        ),
                    ):
                        with self.assertRaisesRegex(
                            PermissionError, "company permission"
                        ):
                            action()
                    self.assertEqual(
                        (self.documents, self.versions, self.series), before
                    )


if __name__ == "__main__":
    unittest.main()
