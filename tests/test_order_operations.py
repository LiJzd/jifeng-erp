"""Models for shared native operations; these are not native DB acceptance."""

import ast
from copy import deepcopy
from datetime import datetime, timedelta
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
NATIVE = "Operation"
ORDER = "Jifeng Order Operation"
SCHEMA = json.loads(
    (
        ROOT
        / "jifeng_erp/jifeng_erp/doctype/jifeng_order_operation/jifeng_order_operation.json"
    ).read_text(encoding="utf-8")
)
DEFAULTS = {
    "name",
    "modified",
    "modified_by",
    "creation",
    "owner",
    "docstatus",
    "idx",
    "_user_tags",
}


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, callback):
        self._functions.append(callback)

    def reset(self):
        self._functions.clear()


class Document(Row):
    environment = None

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def has_permission(self, action):
        return self.environment.allowed(self.doctype, action, self)

    def check_permission(self, action):
        if not self.has_permission(action):
            raise PermissionError("native " + self.doctype + " " + action)

    def get_doc_before_save(self):
        saved = self.environment.tables[self.doctype].get(self.name)
        return Document(deepcopy(saved)) if saved else None

    def save(self):
        env, new = self.environment, self.is_new()
        self.check_permission("create" if new else "write")
        env.saves.append((self.doctype, self.name, new))
        if self.doctype == ORDER:
            if new:
                env.module.before_insert_ordering(self)
            env.module.validate_ordering(self)
            if any(
                (
                    row["operation"] == self.operation
                    or self.request_id
                    and row.get("request_id") == self.request_id
                )
                and row["name"] != self.name
                for row in env.tables[ORDER].values()
            ):
                env.frappe.local.message_log.append({"message": "private SQL unique"})
                raise KeyError("private constraint values")
            if env.order_mutation:
                env.order_mutation(self)
        elif self.doctype == NATIVE:
            if new and self.name in env.tables[NATIVE]:
                env.frappe.local.message_log.append(
                    {"message": "private SQL duplicate"}
                )
                raise KeyError("private duplicate values")
            if not self.description:
                self.description = self.name
            self.total_operation_time = 0.0
            for row in self.sub_operations or []:
                self.total_operation_time += row["time_in_mins"]
            if env.native_mutation:
                env.native_mutation(self)
        else:
            raise AssertionError("unexpected business write " + self.doctype)
        env.tick += 1
        self.name = self.name or "ORDER-" + str(env.tick)
        self.modified = datetime(2026, 10, 3) + timedelta(microseconds=env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.tables[self.doctype][self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append((self.doctype, deepcopy(dict(self))))
        if env.fail_type == self.doctype:
            env.db.after_commit.add(lambda: None)
            env.frappe.flags.currently_saving = ["unexpected native context"]
            raise ValueError("native post-save failure")
        return self


class OrderOperationsTest(unittest.TestCase):
    def setUp(self):
        self.tables = {NATIVE: {}, ORDER: {}}
        self.protected = {
            "BOM": {
                "operations": [
                    {
                        "operation": "旧工序",
                        "idx": 1,
                        "sequence_id": 1,
                        "description": "旧说明",
                    }
                ]
            },
            "Work Order": {
                "operations": [{"name": "WO-OP", "idx": 1, "sequence_id": 1}]
            },
            "Job Card": {"operation_id": "WO-OP", "sequence_id": 1},
            "Item": {"unchanged": True},
            "Customer": {"unchanged": True},
            "GL Entry": [{"unchanged": True}],
            "Stock Ledger Entry": [{"unchanged": True}],
        }
        self.tick, self.versions, self.snapshots, self.saves = 0, [], {}, []
        self.denials, self.document_denials = set(), set()
        self.hidden = {
            action: {NATIVE: set(), ORDER: set()} for action in ("read", "write")
        }
        self.omitted = {NATIVE: set(), ORDER: set()}
        self.mask_levels = {NATIVE: set(), ORDER: set()}
        self.native_mutation, self.order_mutation, self.fail_type, self.list_hook = (
            None,
            None,
            None,
            None,
        )
        native_fields = [
            {"fieldname": "description", "fieldtype": "Text"},
            {"fieldname": "workstation", "fieldtype": "Link", "options": "Workstation"},
            {"fieldname": "is_corrective_operation", "fieldtype": "Check"},
            {"fieldname": "batch_size", "fieldtype": "Int"},
            {"fieldname": "total_operation_time", "fieldtype": "Float"},
            {
                "fieldname": "sub_operations",
                "fieldtype": "Table",
                "options": "Sub Operation",
            },
        ]
        self.metas = {}
        for doctype, fields in (
            (NATIVE, native_fields),
            (ORDER, SCHEMA["fields"]),
            (
                "Sub Operation",
                [
                    {"fieldname": "operation", "fieldtype": "Link"},
                    {"fieldname": "time_in_mins", "fieldtype": "Float"},
                    {"fieldname": "note", "fieldtype": "Data"},
                ],
            ),
        ):
            meta = Row(fields=[Row(field) for field in fields])
            meta.get_field = lambda name, meta=meta: next(
                (df for df in meta.fields if df.fieldname == name), None
            )
            meta.get_table_fields = lambda meta=meta: [
                df for df in meta.fields if df.fieldtype == "Table"
            ]
            meta.get_valid_columns = lambda meta=meta: sorted(
                DEFAULTS
                | {df.fieldname for df in meta.fields if df.fieldtype != "Table"}
            )
            meta.get_permlevel_access = lambda action, parenttype, dt=doctype: (
                self.mask_levels.get(dt, set())
            )
            self.metas[doctype] = meta
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Row(user="manufacturing-user@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = (
            PermissionError,
            RuntimeError,
        )
        self.frappe.DoesNotExistError = LookupError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.whitelist = lambda **kwargs: lambda method: method
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(
            error(message)
        )
        self.frappe.local, self.frappe.flags = (
            types.SimpleNamespace(message_log=[]),
            Row(),
        )
        self.frappe.has_permission = Mock(
            side_effect=lambda dt, action, doc=None, throw=False: self.allowed(
                dt, action, doc
            )
        )
        self.frappe.get_meta = Mock(side_effect=lambda dt: self.metas[dt])
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(
            side_effect=AssertionError("unfiltered listing forbidden")
        )
        self.db = types.SimpleNamespace(
            get_value=Mock(side_effect=self.get_value),
            savepoint=self.savepoint,
            rollback=self.rollback,
            **{
                key: Queue()
                for key in (
                    "before_commit",
                    "after_commit",
                    "before_rollback",
                    "after_rollback",
                )
            },
        )
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            set(self.metas[dt].get_valid_columns()) - self.hidden[permission_type][dt]
        )
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        atomic = next(
            node
            for node in ast.parse(
                (ROOT / "jifeng_erp/personnel_separation.py").read_text(
                    encoding="utf-8"
                )
            ).body
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
                ast.Module(body=[atomic], type_ignores=[]), "actual_atomic", "exec"
            ),
            namespace,
        )
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(
            sys.modules,
            {
                "frappe": self.frappe,
                "frappe.model": model,
                "jifeng_erp.personnel_separation": separation,
            },
        )
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location(
            "order_operations_under_test", ROOT / "jifeng_erp/order_operations.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self

    def allowed(self, dt, action, doc=None):
        return (dt, action) not in self.denials and (
            doc is None or (dt, doc.name) not in self.document_denials
        )

    def new_doc(self, dt):
        self.assertIn(dt, (NATIVE, ORDER), "must not create another business record")
        base = Document(
            doctype=dt,
            name=None,
            modified=None,
            creation=None,
            owner=None,
            docstatus=0,
            idx=0,
            _user_tags=None,
            __islocal=1,
        )
        if dt == NATIVE:
            base.update(
                description=None,
                workstation=None,
                is_corrective_operation=0,
                batch_size=1,
                total_operation_time=None,
                sub_operations=[],
            )
        else:
            base.update(
                operation=None, sequence=0, request_id=None, request_digest=None
            )
        return base

    def get_doc(self, dt, name, **kwargs):
        self.assertIn(
            dt,
            (NATIVE, ORDER),
            "must not touch production or unrelated business documents",
        )
        if name not in self.tables[dt]:
            raise LookupError("missing native source")
        return Document(deepcopy(self.tables[dt][name]))

    def get_value(self, dt, filters, field, **kwargs):
        self.assertEqual(dt, ORDER)
        self.assertEqual(field, "name")
        return next(
            (
                name
                for name, row in self.tables[dt].items()
                if all(row.get(key) == value for key, value in filters.items())
            ),
            None,
        )

    def get_list(self, dt, fields, filters=None, **options):
        if self.list_hook:
            self.list_hook(dt, filters, options)
        rows = [
            Row(
                {
                    field: row.get(field)
                    for field in fields
                    if field not in self.omitted[dt]
                }
            )
            for name, row in sorted(self.tables[dt].items())
            if self.allowed(dt, "read", Document(row))
            and all(row.get(key) == value for key, value in (filters or {}).items())
        ]
        start, length = (
            options.get("limit_start", 0),
            options.get("limit_page_length", 20),
        )
        return rows[start : start + length]

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.tables, self.versions, self.protected))

    def rollback(self, save_point):
        self.tables, self.versions, self.protected = deepcopy(
            self.snapshots[save_point]
        )

    def state(self):
        return deepcopy((self.tables, self.versions, self.protected))

    def payload(self, **changes):
        return {
            "operation": "工序-" + str(uuid4()),
            "sequence": 0,
            "remarks": "原文说明",
            "request_id": str(uuid4()),
        } | changes

    def saved(self, **changes):
        return self.module.save_operation(self.payload(**changes))

    def update(self, row, **changes):
        return self.module.save_operation(
            {
                "operation": row["name"],
                "version": row["version"],
                "ordering_name": row["ordering_name"],
                "ordering_version": row["ordering_version"],
            }
            | changes
        )

    def existing(self, name="旧工序", **changes):
        document = self.new_doc(NATIVE)
        document.update(name=name, description="旧描述", **changes)
        document.save()
        return self.module.read_operation(name)

    def test_schema_is_separate_global_mapping_without_native_schema_or_roles(self):
        self.assertEqual(
            {field["fieldname"] for field in SCHEMA["fields"]},
            {"operation", "sequence", "request_id", "request_digest"},
        )
        self.assertEqual(
            {row["role"] for row in SCHEMA["permissions"]},
            {"Manufacturing User", "Manufacturing Manager"},
        )
        self.assertTrue(all(not row.get("delete") for row in SCHEMA["permissions"]))
        for field in ("operation", "request_id"):
            self.assertTrue(
                next(row for row in SCHEMA["fields"] if row["fieldname"] == field)[
                    "unique"
                ]
            )
        self.assertNotIn("company", self.module.init_operations())

    def test_create_native_and_receipt_share_identity_with_actual_returned_audit(self):
        payload = self.payload(operation="电镀", sequence=7, remarks=" 备注原文 ")
        row = self.module.save_operation(payload)
        self.assertEqual(
            (row["scope"], row["name"], row["sequence"], row["remarks"]),
            ("shared_operation", "电镀", 7, " 备注原文 "),
        )
        self.assertEqual(row["request_id"], payload["request_id"])
        self.assertNotEqual(row["version"], row["ordering_version"])
        self.assertEqual(row["ordering_modified_by"], self.frappe.session.user)
        self.assertNotIn("request_id", self.module.read_operation(row["name"]))
        self.assertNotIn("request_id", self.module.list_operations()["rows"][0])
        self.assertEqual(
            self.tables[ORDER][row["ordering_name"]]["operation"], row["name"]
        )

    def test_empty_description_normalizes_before_digest_and_current_read(self):
        payload = self.payload(operation="清洗", remarks="")
        row = self.module.save_operation(payload)
        self.assertEqual(row["remarks"], "清洗")
        self.assertEqual(
            self.module.resolve_creation(payload | {"remarks": None})["status"], "found"
        )
        self.assertEqual(
            self.module.resolve_creation(payload | {"remarks": "清洗"})["status"],
            "found",
        )

    def test_existing_without_mapping_reads_null_and_sequence_only_does_not_save_operation(
        self,
    ):
        row = self.existing()
        self.assertIsNone(row["sequence"])
        self.assertIsNone(row["ordering_name"])
        # Historic empty descriptions and inconsistent totals are not rewritten
        # by a sequence-only request, because it must not save Operation.
        self.tables[NATIVE][row["name"]].update(
            description=None, total_operation_time=91
        )
        native_before = deepcopy(self.tables[NATIVE])
        self.saves.clear()
        current = self.update(row, sequence=3)
        self.assertEqual(self.tables[NATIVE], native_before)
        self.assertEqual(current["version"], row["version"])
        self.assertEqual(current["sequence"], 3)
        self.assertEqual([entry[0] for entry in self.saves], [ORDER])
        self.assertIsNone(self.tables[ORDER][current["ordering_name"]]["request_id"])

    def test_sequence_only_existing_mapping_updates_its_audit_not_native(self):
        row = self.saved()
        before = deepcopy(self.tables[NATIVE])
        current = self.update(row, sequence=99)
        self.assertEqual(self.tables[NATIVE], before)
        self.assertEqual(current["version"], row["version"])
        self.assertNotEqual(current["ordering_version"], row["ordering_version"])

    def test_remarks_only_keeps_mapping_version_and_other_native_fields_and_children(
        self,
    ):
        row = self.saved()
        native = self.tables[NATIVE][row["name"]]
        native["sub_operations"] = [
            Row(
                name="SUB-1",
                operation="工步",
                time_in_mins=9,
                note="历史",
                idx=1,
                owner="owner",
                creation=datetime(2026, 10, 3),
                docstatus=0,
                _user_tags="标签",
            )
        ]
        native["total_operation_time"] = 9
        before = self.state()
        current = self.update(row, remarks="新说明")
        self.assertEqual(current["ordering_version"], row["ordering_version"])
        self.assertEqual(self.tables[ORDER], before[0][ORDER])
        self.assertEqual(
            self.tables[NATIVE][row["name"]]["sub_operations"], native["sub_operations"]
        )
        self.assertEqual(self.protected, before[2])

    def test_unrequested_total_recalculation_rolls_back_native_and_mapping_changes(
        self,
    ):
        row = self.saved()
        self.tables[NATIVE][row["name"]]["total_operation_time"] = 42
        before = self.state()
        with self.assertRaisesRegex(ValueError, "其它属性"):
            self.update(row, remarks="改备注", sequence=4)
        self.assertEqual(self.state(), before)

    def test_real_nullable_creation_default_is_normalized_but_existing_null_stays_protected(
        self,
    ):
        self.assertIsNone(self.new_doc(NATIVE).total_operation_time)
        row = self.saved()
        self.assertEqual(self.tables[NATIVE][row["name"]]["total_operation_time"], 0.0)
        self.assertIs(
            type(self.tables[NATIVE][row["name"]]["total_operation_time"]), float
        )
        self.tables[NATIVE][row["name"]]["total_operation_time"] = None
        before = self.state()
        with self.assertRaisesRegex(ValueError, "其它属性"):
            self.update(row, remarks="已有空值不放宽", sequence=4)
        self.assertEqual(self.state(), before)

    def test_unrequested_parent_or_child_or_private_field_change_rolls_back(self):
        row = self.saved()
        for mutation in (
            lambda doc: doc.update(workstation="被修改"),
            lambda doc: doc.update(_user_tags="被修改"),
            lambda doc: doc.sub_operations.append(
                Row(name="意外子行", idx=1, time_in_mins=0)
            ),
        ):
            self.native_mutation = mutation
            before = self.state()
            with self.assertRaisesRegex(ValueError, "其它属性"):
                self.update(row, remarks="修改")
            self.assertEqual(self.state(), before)

    def test_post_mapping_native_mutation_rejects_whole_save(self):
        row = self.saved()
        self.order_mutation = lambda doc: self.tables[NATIVE][row["name"]].update(
            batch_size=999
        )
        before = self.state()
        with self.assertRaisesRegex(ValueError, "未请求"):
            self.update(row, sequence=5)
        self.assertEqual(self.state(), before)

    def test_post_validation_mapping_mutation_cannot_change_receipt_or_requested_sequence(
        self,
    ):
        for mutation in (
            lambda doc: doc.update(request_digest="0" * 64),
            lambda doc: doc.update(sequence=99),
        ):
            self.order_mutation = mutation
            before = self.state()
            with self.assertRaisesRegex(ValueError, "回执出现未请求"):
                self.saved()
            self.assertEqual(self.state(), before)

    def test_uuid_recovery_is_readonly_and_repeated_post_never_recreates(self):
        payload = self.payload()
        row = self.module.save_operation(payload)
        before, saves = self.state(), len(self.saves)
        self.assertEqual(self.module.resolve_creation(payload)["status"], "found")
        self.assertEqual(
            self.module.resolve_creation(payload | {"request_id": str(uuid4())})[
                "status"
            ],
            "missing",
        )
        with self.assertRaisesRegex(ValueError, "不要重复保存"):
            self.module.save_operation(payload)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.saves), saves)
        self.assertEqual(
            self.module.resolve_creation(payload)["operation"]["name"], row["name"]
        )

    def test_uuid_digest_conflict_and_subsequent_current_changes_are_distinct(self):
        payload = self.payload()
        row = self.module.save_operation(payload)
        for changes in (
            {"operation": "另一个工序"},
            {"sequence": 9},
            {"remarks": "另一备注"},
        ):
            result = self.module.resolve_creation(payload | changes)
            self.assertEqual(result["status"], "conflict")
            self.assertIsNone(result["operation"])
        self.update(row, remarks="后续备注", sequence=8)
        result = self.module.resolve_creation(payload)
        self.assertEqual(result["status"], "changed")
        self.assertEqual(
            (result["operation"]["sequence"], result["operation"]["remarks"]),
            (8, "后续备注"),
        )

    def test_native_rename_updates_link_and_original_receipt_still_resolves(self):
        payload = self.payload(operation="原工序")
        row = self.module.save_operation(payload)
        native = self.tables[NATIVE].pop(row["name"])
        native["name"] = "原生改名后"
        self.tables[NATIVE][native["name"]] = native
        self.tables[ORDER][row["ordering_name"]]["operation"] = native["name"]
        result = self.module.resolve_creation(payload)
        self.assertEqual(result["status"], "changed")
        self.assertEqual(result["operation"]["name"], "原生改名后")

    def test_duplicate_native_name_and_hidden_receipt_collision_are_generic_atomic(
        self,
    ):
        row = self.saved(operation="唯一工序")
        payload = self.payload(operation=row["name"])
        before = self.state()
        self.frappe.local.message_log = [{"message": "已有消息"}]
        with self.assertRaisesRegex(ValueError, "不要重复保存") as error:
            self.module.save_operation(payload)
        self.assertNotIn(row["name"], str(error.exception))
        self.assertIsNone(error.exception.__context__)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.frappe.local.message_log, [{"message": "已有消息"}])
        payload = self.payload()
        existing = self.module.save_operation(payload)
        self.document_denials.add((ORDER, existing["ordering_name"]))
        self.assertEqual(self.module.resolve_creation(payload)["status"], "missing")
        with self.assertRaisesRegex(ValueError, "不要重复保存"):
            self.module.save_operation(payload | {"operation": "另一合法名称"})
        self.assertNotIn("另一合法名称", self.tables[NATIVE])

    def test_two_versions_and_mapping_presence_are_required_and_locked(self):
        row = self.saved()
        current = self.update(row, sequence=5)
        before = self.state()
        with self.assertRaises(RuntimeError):
            self.update(row, remarks="旧排序版本")
        with self.assertRaises(RuntimeError):
            self.update(current | {"version": "2026-10-02T00:00:00"}, sequence=6)
        with self.assertRaises(RuntimeError):
            self.update(
                current | {"ordering_name": None, "ordering_version": None}, sequence=6
            )
        self.assertEqual(self.state(), before)
        locked = {
            call.args[0]
            for call in self.frappe.get_doc.call_args_list
            if call.kwargs.get("for_update")
        }
        self.assertEqual(locked, {NATIVE, ORDER})

    def test_native_or_order_create_and_write_permissions_are_independent(self):
        row = self.saved()
        for dt, action in ((NATIVE, "create"), (ORDER, "create")):
            self.denials.add((dt, action))
            self.assertFalse(self.module.init_operations()["can_create"])
            before = self.state()
            with self.assertRaises(PermissionError):
                self.saved()
            self.assertEqual(self.state(), before)
            self.denials.remove((dt, action))
        for dt in (NATIVE, ORDER):
            self.denials.add((dt, "write"))
            before = self.state()
            with self.assertRaises(PermissionError):
                self.update(row, sequence=5)
            self.assertEqual(self.state(), before)
            self.denials.remove((dt, "write"))
        self.denials.add((ORDER, "write"))
        self.assertEqual(
            self.update(row, remarks="仅原生备注")["remarks"], "仅原生备注"
        )

    def test_reader_capabilities_are_quiet_and_actual_user_permission_filters_sources(
        self,
    ):
        row = self.saved()
        self.denials.update(
            {(NATIVE, "write"), (NATIVE, "create"), (ORDER, "write"), (ORDER, "create")}
        )
        self.assertFalse(self.module.init_operations()["can_create"])
        self.assertFalse(self.module.read_operation(row["name"])["can_write"])
        self.assertEqual(self.frappe.local.message_log, [])
        self.assertTrue(
            all(
                call.kwargs.get("throw") is False
                for call in self.frappe.has_permission.call_args_list
            )
        )
        self.document_denials.add((NATIVE, row["name"]))
        self.assertEqual(self.module.list_operations()["total"], 0)
        with self.assertRaises(PermissionError):
            self.module.read_operation(row["name"])

    def test_no_company_argument_or_company_permission_claim(self):
        row = self.saved()
        with self.assertRaisesRegex(ValueError, "不支持"):
            self.module.save_operation(self.payload(company="公司"))
        self.assertNotIn("company", self.module.list_operations())
        self.assertEqual(
            self.module.read_operation(row["name"])["scope"], "shared_operation"
        )

    def test_read_field_masks_omit_values_and_metadata_and_do_not_sort_hidden_sequence(
        self,
    ):
        a = self.saved(operation="A", sequence=9, remarks="私密A")
        self.saved(operation="B", sequence=1, remarks="私密B")
        self.metas[NATIVE].get_field("description").mask = 1
        self.metas[ORDER].get_field("sequence").mask = 1
        row = self.module.read_operation(a["name"])
        for field in ("remarks", "sequence"):
            self.assertNotIn(field, row)
            self.assertNotIn(field, row["field_meta"])
        rows = self.module.list_operations()["rows"]
        self.assertEqual([row["name"] for row in rows], ["A", "B"])
        with self.assertRaises(PermissionError):
            self.update(a, sequence=0)
        with self.assertRaises(PermissionError):
            self.update(a, remarks="覆盖隐藏")

    def test_read_omissions_never_use_full_doc_as_dto_fallback_or_resolution_source(
        self,
    ):
        payload = self.payload()
        row = self.module.save_operation(payload)
        self.omitted[NATIVE].update({"description", "owner", "creation"})
        self.omitted[ORDER].update({"sequence", "modified_by"})
        current = self.module.read_operation(row["name"])
        for field in (
            "remarks",
            "owner",
            "creation",
            "sequence",
            "ordering_modified_by",
        ):
            self.assertNotIn(field, current)
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload)

        before = self.state()
        for changes in ({"remarks": "覆盖"}, {"sequence": 1}):
            with self.assertRaises(PermissionError):
                self.update(row, **changes)
            self.assertEqual(self.state(), before)

    def test_hidden_mapping_is_denied_not_reported_as_absent(self):
        row = self.saved()
        self.document_denials.add((ORDER, row["ordering_name"]))
        for action in (
            lambda: self.module.read_operation(row["name"]),
            self.module.list_operations,
            lambda: self.update(row, sequence=1),
        ):
            with self.assertRaises(PermissionError):
                action()

    def test_required_projection_and_receipt_fields_cannot_be_backfilled(self):
        payload = self.payload()
        row = self.module.save_operation(payload)
        for dt, field in ((NATIVE, "modified"), (ORDER, "operation")):
            self.omitted[dt].add(field)
            with self.assertRaises(PermissionError):
                self.module.read_operation(row["name"])
            self.omitted[dt].remove(field)
        self.omitted[ORDER].add("request_digest")
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload)

    def test_write_columns_and_current_readonly_metadata_guard_both_fields(self):
        row = self.saved()
        for dt, source, alias in (
            (NATIVE, "description", "remarks"),
            (ORDER, "sequence", "sequence"),
        ):
            self.hidden["write"][dt].add(source)
            self.assertTrue(
                self.module.read_operation(row["name"])["field_meta"][alias][
                    "read_only"
                ]
            )
            before = self.state()
            with self.assertRaises(PermissionError):
                self.update(row, **{alias: "新说明" if alias == "remarks" else 1})
            self.assertEqual(self.state(), before)
            self.hidden["write"][dt].remove(source)
            self.metas[dt].get_field(source).read_only = 1
            with self.assertRaises(PermissionError):
                self.update(row, **{alias: "新说明" if alias == "remarks" else 1})
            self.metas[dt].get_field(source).read_only = 0

    def test_paging_covers_all_visible_native_rows_and_stable_equal_sequences(self):
        for index in range(1103):
            doc = self.new_doc(NATIVE)
            doc.update(
                name="ROW-" + str(index).zfill(4),
                description="说明",
                modified=datetime(2026, 10, 3),
                creation=datetime(2026, 10, 3),
                owner="owner",
                modified_by="owner",
                __islocal=0,
            )
            self.tables[NATIVE][doc.name] = dict(doc)
        row = self.module.read_operation("ROW-1102")
        self.update(row, sequence=2)
        row = self.module.read_operation("ROW-0001")
        self.update(row, sequence=2)
        result = self.module.list_operations(start="999999", page_length="100")
        self.assertEqual(
            (result["total"], result["start"], len(result["rows"])), (1103, 1100, 3)
        )
        head = self.module.list_operations()["rows"]
        self.assertEqual([row["name"] for row in head[:2]], ["ROW-0001", "ROW-1102"])
        self.frappe.get_all.assert_not_called()

    def test_second_scan_addition_or_mapping_drift_rejects_entire_page(self):
        row = self.saved()
        scans = 0

        def drift(dt, filters, options):
            nonlocal scans
            if dt == NATIVE and not filters:
                scans += 1
                if scans == 2:
                    self.tables[ORDER][row["ordering_name"]]["sequence"] += 1

        self.list_hook = drift
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.list_operations()

    def test_native_form_cannot_insert_rebind_rewrite_receipt_or_delete(self):
        native = self.existing()
        document = self.new_doc(ORDER)
        document.operation = native["name"]
        with self.assertRaisesRegex(ValueError, "入口建立"):
            document.save()
        row = self.saved()
        other = self.existing("另一原生工序")
        for field, value in (
            ("operation", other["name"]),
            ("request_id", str(uuid4())),
            ("request_digest", "0" * 64),
            ("owner", "伪造"),
        ):
            document = self.get_doc(ORDER, row["ordering_name"])
            document[field] = value
            with self.assertRaisesRegex(ValueError, "不能修改"):
                document.save()
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.module.prevent_delete(self.get_doc(ORDER, row["ordering_name"]))

    def test_native_form_sequence_write_still_checks_operation_permissions_and_field_mask(
        self,
    ):
        row = self.saved()
        document = self.get_doc(ORDER, row["ordering_name"])
        document.sequence = 4
        document.save()
        self.denials.add((NATIVE, "write"))
        document = self.get_doc(ORDER, row["ordering_name"])
        document.sequence = 5
        with self.assertRaises(PermissionError):
            document.save()
        self.denials.clear()
        self.metas[ORDER].get_field("sequence").mask = 1
        with self.assertRaises(PermissionError):
            document.save()

    def test_invalid_shapes_uuid_sequence_and_versions_fail_without_writes(self):
        before = self.state()
        for changes in (
            {"sequence": -1},
            {"sequence": True},
            {"sequence": 2147483648},
            {"sequence": 1.5},
            {"request_id": "not-a-uuid"},
            {"request_id": str(uuid4()).upper()},
            {"operation": " 工序 "},
            {"remarks": []},
            {"company": "C"},
            {"name": "fake"},
            {"request_digest": "fake"},
            {"version": "2026-10-03T00:00:00"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.save_operation(self.payload(**changes))
            self.assertEqual(self.state(), before)
        for start, length in ((True, 25), (-1, 25), (0, 26), (0, 0)):
            with self.assertRaises(ValueError):
                self.module.list_operations(start=start, page_length=length)
        row = self.saved()
        with self.assertRaisesRegex(ValueError, "两个来源版本"):
            self.module.save_operation({"operation": row["name"], "sequence": 3})

    def test_atomic_native_and_order_failure_restore_rows_versions_and_callback_context(
        self,
    ):
        def marker():
            return None

        self.db.after_commit.add(marker)
        self.frappe.flags.currently_saving = ["prior"]
        for dt in (NATIVE, ORDER):
            self.fail_type = dt
            before = self.state()
            with self.assertRaisesRegex(ValueError, "post-save"):
                self.saved()
            self.assertEqual(self.state(), before)
            self.assertEqual(self.db.after_commit._functions, [marker])
            self.assertEqual(self.frappe.flags.currently_saving, ["prior"])

    def test_guest_and_denied_native_read_do_not_reveal_sources(self):
        row = self.saved()
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.read_operation(row["name"])
        self.frappe.session.user = "manufacturing-user@example.invalid"
        self.denials.add((NATIVE, "read"))
        for action in (
            self.module.init_operations,
            self.module.list_operations,
            lambda: self.module.read_operation(row["name"]),
        ):
            with self.assertRaises(PermissionError):
                action()


if __name__ == "__main__":
    unittest.main()
