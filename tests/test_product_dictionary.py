"""Mutable product words, permission projections and stable creation receipts."""

import ast
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid4


ROOT = Path(__file__).parents[1]
DOCTYPE = "Jifeng Product Dictionary"
SCHEMA = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_product_dictionary/jifeng_product_dictionary.json").read_text(encoding="utf-8"))


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, fn):
        self._functions.append(fn)

    def reset(self):
        self._functions.clear()


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.meta

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, permission):
        env = self.environment
        return permission not in env.role_denials and self.name not in env.document_denials and self.company not in env.denied_companies

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native dictionary " + permission)

    def get_doc_before_save(self):
        row = self.environment.documents.get(self.name)
        return NativeDocument(deepcopy(row)) if row else None

    def save(self):
        env = self.environment
        new = self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            env.module.before_insert_dictionary(self)
        env.module.validate_dictionary(self)
        if any(row["request_id"] == self.request_id and row["name"] != self.name for row in env.documents.values()):
            env.frappe.local.message_log = list(getattr(env.frappe.local, "message_log", None) or []) + [{"message": "private unique-index SQL"}]
            raise KeyError("private native identity")
        env.tick += 1
        self.name = self.name or "PROD-D-" + str(env.tick)
        self.modified = datetime(2026, 10, 1, 10, 0, 0, env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append((self.name, deepcopy(dict(self))))
        if env.fail_after_save:
            env.db.after_commit.add(lambda: None)
            env.frappe.local._realtime_log = ["unexpected event"]
            env.frappe.flags.currently_saving = ["unexpected context"]
            raise ValueError("native post-save failure")
        return self


class NativeDictionarySnapshot(unittest.TestCase):
    def setUp(self):
        self.timestamp = datetime(2026, 10, 1, 10, 0, 0, 123456)
        self.parent = {"name": "QA-PROFILE", "style": "旧原文", "creation": self.timestamp,
                       "modified": self.timestamp, "owner": "qa@example.invalid", "private_extra_column": "kept"}
        self.children = [Record(name="QA-MAT", parent="QA-PROFILE", parenttype="Jifeng Product Profile",
                               parentfield="materials", idx=1, item_code="MAT", qty_per_unit=Decimal("1.00"),
                               creation=self.timestamp, modified=self.timestamp),
                         Record(name="QA-OP", parent="QA-PROFILE", parenttype="Jifeng Product Profile",
                                parentfield="operations", idx=1, item_code="OP", qty_per_unit=Decimal("2.00"),
                                creation=self.timestamp, modified=self.timestamp),
                         Record(name="OTHER-PARENT", parent="OTHER", parenttype="Jifeng Product Profile", parentfield="materials", idx=1),
                         Record(name="OTHER-TYPE", parent="QA-PROFILE", parenttype="OTHER", parentfield="materials", idx=1)]
        self.frappe = types.SimpleNamespace(db=types.SimpleNamespace(sql=Mock(side_effect=self.sql)),
            get_meta=Mock(side_effect=self.meta), get_doc=Mock(side_effect=AssertionError("as_dict controller reads are forbidden")))
        tree = ast.parse((ROOT / "jifeng_erp/personnel_changes_validation.py").read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("_serialize", "_digest")]
        namespace = {"frappe": self.frappe, "date": date, "datetime": datetime, "Decimal": Decimal,
                     "hashlib": hashlib, "json": json}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual_native_digest", "exec"), namespace)
        tree = ast.parse((ROOT / "jifeng_erp/product_dictionary_validation.py").read_text(encoding="utf-8"))
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name in ("_sql_row_hash", "_document_snapshot")]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "actual_native_snapshot", "exec"), namespace)
        self.snapshot, self.digest, self.sql_digest = namespace["_document_snapshot"], namespace["_digest"], namespace["_sql_row_hash"]

    def meta(self, doctype):
        fields = [Record(fieldname=field, options="Shared Child Table", is_virtual=0) for field in ("materials", "operations")]
        return Record(is_virtual=0, get_table_fields=lambda: fields if doctype == "Jifeng Product Profile" else [])

    def sql(self, query, values, as_dict):
        self.assertTrue(as_dict)
        self.assertTrue(query.startswith("SELECT * FROM "), "snapshot must keep every physical column")
        if len(values) == 1:
            return [deepcopy(self.parent)] if self.parent else []
        self.assertIn("WHERE parent=%s AND parenttype=%s AND parentfield=%s ORDER BY idx, name", query)
        return deepcopy([row for row in self.children if (row.parent, row.parenttype, row.parentfield) == values])

    def test_complete_sql_parent_and_all_child_fields_preserve_exact_identity_and_columns(self):
        snapshot = self.snapshot("Jifeng Product Profile", "QA-PROFILE")
        self.assertEqual(snapshot["parent"], self.sql_digest([self.parent]))
        self.assertEqual(set(snapshot["children"]), {"materials", "operations"})
        self.assertEqual(snapshot["children"]["materials"], self.sql_digest([self.children[0]]))
        self.assertEqual(snapshot["children"]["operations"], self.sql_digest([self.children[1]]))
        self.frappe.get_doc.assert_not_called()
        self.assertEqual(self.frappe.db.sql.call_count, 3)

    def test_insert_string_and_reread_datetime_representation_cannot_change_persisted_snapshot(self):
        inserted = {"modified": str(self.timestamp)}
        reread = {"modified": self.timestamp}
        self.assertNotEqual(self.digest(inserted), self.digest(reread), "this is the real prior oracle mismatch")
        before = self.snapshot("Jifeng Product Profile", "QA-PROFILE")
        self.assertEqual(self.snapshot("Jifeng Product Profile", "QA-PROFILE"), before)
        self.frappe.get_doc.assert_not_called()

    def test_parent_modified_creation_or_extra_physical_column_changes_are_detected(self):
        before = self.snapshot("Jifeng Product Profile", "QA-PROFILE")
        for field, value in (("modified", self.timestamp.replace(microsecond=123457)),
                             ("creation", self.timestamp.replace(microsecond=123457)),
                             ("private_extra_column", "changed")):
            original = self.parent[field]
            self.parent[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.snapshot("Jifeng Product Profile", "QA-PROFILE"), before)
            self.parent[field] = original

    def test_exact_native_decimal_low_digits_are_kept_without_float_rounding(self):
        left, right = Decimal("999999999999.000000001"), Decimal("999999999999.000000002")
        self.assertEqual(float(left), float(right))
        self.assertNotEqual(self.sql_digest([{"rate": left}]), self.sql_digest([{"rate": right}]))

    def test_child_values_timestamps_identity_additions_and_deletions_are_detected(self):
        before = self.snapshot("Jifeng Product Profile", "QA-PROFILE")
        for field, value in (("qty_per_unit", Decimal("1.1")), ("modified", self.timestamp.replace(microsecond=123457)),
                             ("creation", self.timestamp.replace(microsecond=123457)), ("name", "DIFFERENT-CHILD")):
            original = self.children[0][field]
            self.children[0][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.snapshot("Jifeng Product Profile", "QA-PROFILE"), before)
            self.children[0][field] = original
        row = self.children.pop(0)
        self.assertNotEqual(self.snapshot("Jifeng Product Profile", "QA-PROFILE"), before)
        self.children.insert(0, row)
        self.children.append(Record(row | {"name": "EXTRA-CHILD", "idx": 2}))
        self.assertNotEqual(self.snapshot("Jifeng Product Profile", "QA-PROFILE"), before)

    def test_missing_source_or_virtual_child_never_looks_unchanged(self):
        self.parent = None
        with self.assertRaisesRegex(AssertionError, "准确的一条已保存来源"):
            self.snapshot("Jifeng Product Profile", "QA-PROFILE")
        self.parent = {"name": "QA-PROFILE"}
        self.frappe.get_meta.side_effect = None
        self.frappe.get_meta.return_value = Record(is_virtual=1, get_table_fields=lambda: [Record(
            fieldname="materials", options="Shared Child Table", is_virtual=0)])
        with self.assertRaisesRegex(AssertionError, "不能遗漏虚拟子表"):
            self.snapshot("Jifeng Product Profile", "QA-PROFILE")

    def test_native_whole_audit_keeps_series_without_doctype_metadata_and_low_decimal_digits(self):
        series = [{"name": "QA-SERIES", "current": 9, "extra_installed_column": "kept"}]
        item = [{"name": "QA-ITEM", "extra_decimal": Decimal("999999999999.000000001")}]
        database = types.SimpleNamespace(
            exists=Mock(side_effect=lambda dt, name: name != "Series"),
            sql=Mock(side_effect=lambda query, **kwargs: deepcopy(
                series if "`tabSeries`" in query else item if "`tabItem`" in query else [])))
        metadata = Mock(side_effect=lambda dt: self.assertNotEqual(dt, "Series") or Record(get_table_fields=lambda: []))
        runtime = types.SimpleNamespace(db=database, get_meta=metadata)
        tree = ast.parse((ROOT / "jifeng_erp/product_dictionary_validation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_native_contents")
        namespace = {"frappe": runtime, "DOCTYPE": DOCTYPE, "PROFILE": "Jifeng Product Profile", "_sql_row_hash": self.sql_digest}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_whole_native_audit", "exec"), namespace)
        audit = namespace["_native_contents"]
        before = audit()
        self.assertEqual(before["Series"], self.sql_digest(series))
        self.assertNotIn(unittest.mock.call("DocType", "Series"), database.exists.call_args_list)
        series[0]["extra_installed_column"] = "changed"
        self.assertNotEqual(audit()["Series"], before["Series"])
        item[0]["extra_decimal"] = Decimal("999999999999.000000002")
        self.assertNotEqual(audit()["Item"], before["Item"])


class TestProductDictionary(unittest.TestCase):
    def setUp(self):
        self.documents, self.versions, self.series = {}, [], {}
        self.role_denials, self.document_denials, self.denied_companies = set(), set(), set()
        self.hidden = {"read": set(), "write": set()}
        self.tick, self.fail_after_save, self.forced_list = 1, False, None
        self.meta = types.SimpleNamespace(get_field=lambda name: next((Record(field) for field in SCHEMA["fields"] if field["fieldname"] == name), None))
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="stock-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: permission not in self.role_denials)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped listing is forbidden"))
        self.snapshots = {}
        self.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            get_value=Mock(side_effect=lambda dt, name, field, **kwargs: self.series.get(name) if dt == "Series" else None),
            **{key: Queue() for key in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            {row["fieldname"] for row in SCHEMA["fields"]} | {"name", "modified", "creation", "owner", "modified_by"}) - self.hidden[permission_type]
        naming = types.ModuleType("frappe.model.naming")
        self.allocate = naming.getseries = Mock(side_effect=self.next_number)
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_native_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
            "frappe.model.naming": naming, "jifeng_erp.personnel_separation": separation})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("product_dictionary_under_test", ROOT / "jifeng_erp/product_dictionary.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        NativeDocument.environment = self
        self.company_calls = []

    def new_doc(self, doctype):
        self.assertEqual(doctype, DOCTYPE)
        return NativeDocument(doctype=DOCTYPE, name=None, company="DEFAULT-DENIED", dictionary_type="风格",
                              numbering_mode=None, inactive=0, __islocal=1)

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            self.company_calls.append(name)
            def permission(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company user permission")
            return Record(name=name, check_permission=permission)
        self.assertEqual(doctype, DOCTYPE, "dictionary must not load or write Item/Profile/Employee")
        return NativeDocument(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, DOCTYPE)
        if self.forced_list is not None:
            return self.forced_list
        def matches(row, field, value):
            return value[1].strip("%") in (row.get(field) or "") if isinstance(value, list) else row.get(field) == value
        result = []
        for name, row in self.documents.items():
            if name in self.document_denials or row["company"] in self.denied_companies:
                continue
            if not all(matches(row, field, value) for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(matches(row, field, [operator, value]) for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Record({field: row[field] for field in fields if field in row and field not in self.hidden["read"]}))
        return sorted(result, key=lambda row: row.get("value", row.name))

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.documents, self.versions, self.series))

    def rollback(self, save_point):
        self.documents, self.versions, self.series = deepcopy(self.snapshots[save_point])

    def next_number(self, key, digits):
        self.series[key] = self.series.get(key, 0) + 1
        return str(self.series[key]).zfill(digits)

    def automatic_payload(self, **changes):
        return {key: value for key, value in self.payload(numbering_mode=self.module.AUTO_MODE, **changes).items()
                if key != "legacy_number"}

    def payload(self, **changes):
        return {"request_id": str(uuid4()), "company": "C-1", "dictionary_type": "风格", "legacy_number": "001",
                "value": " 典雅 ", "remarks": " 原始备注\n第二行 ", "abbreviation": " DY ", "inactive": 0, **changes}

    def saved(self, **changes):
        return self.module.save_dictionary(self.payload(**changes))

    def update(self, archive, **changes):
        return self.module.save_dictionary({field: archive[field] for field in ("name", "version", *self.module.FIELDS) if field in archive} | changes)

    def test_schema_is_three_raw_profile_words_with_only_request_uuid_unique(self):
        self.assertEqual(self.module.TYPES, {"风格": "style", "来源": "source", "品牌": "brand"})
        profile = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_product_profile/jifeng_product_profile.json").read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in profile["fields"]}
        self.assertTrue(all(fields[field]["fieldtype"] == "Data" for field in self.module.TYPES.values()))
        fields = {row["fieldname"]: row for row in SCHEMA["fields"]}
        self.assertEqual({name for name, field in fields.items() if field.get("unique")}, {"request_id"})
        self.assertTrue(SCHEMA["track_changes"])
        self.assertTrue(all(fields[field]["set_only_once"] for field in self.module.IDENTITY))
        self.assertFalse(fields["value"].get("set_only_once"))
        self.assertTrue(fields["legacy_number"]["no_copy"])
        self.assertEqual({row["role"] for row in SCHEMA["permissions"]}, {"System Manager", "Stock Manager", "Manufacturing Manager"})
        self.assertTrue(all({key for key, value in row.items() if value == 1} == {"read", "select", "create", "write"} for row in SCHEMA["permissions"]))

    def test_init_clears_native_company_defaults_and_exposes_actual_field_meta(self):
        archive = self.module.init_dictionary("品牌")
        self.assertIsNone(archive["company"])
        self.assertIsNone(archive["name"])
        self.assertIsNone(archive["version"])
        self.assertEqual(archive["dictionary_type"], "品牌")
        self.assertTrue(archive["can_write"])
        self.assertEqual(self.company_calls, [])
        self.assertEqual(set(archive["field_meta"]), set(self.module.FIELDS))

    def test_optional_original_words_preserve_whitespace_without_deriving_number_or_pinyin(self):
        archive = self.saved()
        self.assertEqual((archive["legacy_number"], archive["value"], archive["remarks"], archive["abbreviation"]),
                         ("001", " 典雅 ", " 原始备注\n第二行 ", " DY "))
        empty = self.saved(legacy_number=None, remarks=None, abbreviation=None)
        self.assertIsNone(empty["legacy_number"])
        self.assertIsNone(empty["abbreviation"])
        self.assertNotIn("request_id", archive)
        self.assertNotIn("request_digest", archive)

    def test_same_number_and_text_are_allowed_across_same_scope_and_types(self):
        names = [self.saved(dictionary_type=kind)["name"] for kind in self.module.TYPES]
        names.append(self.saved()["name"])
        names.append(self.saved(company="C-2")["name"])
        self.assertEqual(len(set(names)), 5)

    def test_native_new_form_generates_server_uuid_and_digest(self):
        document = self.new_doc(DOCTYPE)
        document.company, document.value = "C-1", "原生新建"
        document.save()
        self.assertEqual(UUID(document.request_id).version, 4)
        self.assertEqual(document.numbering_mode, self.module.AUTO_MODE)
        self.assertEqual(document.legacy_number, "001")
        self.assertEqual(document.request_digest, self.module._digest("C-1", "风格", self.module._values(document), self.module.AUTO_MODE))

    def test_native_new_form_rejects_server_metadata_or_json_token_injection(self):
        for field, value in (("request_id", str(uuid4())), ("request_digest", "a" * 64),
                             ("numbering_mode", self.module.AUTO_MODE)):
            document = self.new_doc(DOCTYPE)
            document.company, document.value = "C-1", "原生新建"
            document.set(field, value)
            document.__dict__["_jf_dictionary_server"] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "只能由服务器生成"):
                document.save()
        document = self.new_doc(DOCTYPE)
        document.company, document.value = "C-1", "原生新建"
        document.request_id, document.request_digest = str(uuid4()), "a" * 64
        document.__dict__["_jf_dictionary_server"] = "client-token"
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            self.module.validate_dictionary(document)

    def test_automatic_preview_is_only_advisory_and_does_not_allocate_or_expose_unreadable_number(self):
        empty = self.module.init_dictionary("风格")
        self.assertIsNone(empty["number_preview"])
        for _ in range(2):
            preview = self.module.init_dictionary("风格", "C-1")
            self.assertEqual(preview["number_preview"], "001")
            self.assertTrue(preview["number_preview_is_provisional"])
            self.assertIsNone(preview["legacy_number"])
            self.assertTrue(preview["field_meta"]["legacy_number"]["read_only"])
            self.assertEqual(self.db.get_value.call_args.kwargs, {"order_by": "name"})
        self.assertFalse(self.series)
        self.allocate.assert_not_called()
        self.hidden["read"].add("legacy_number")
        self.db.get_value.reset_mock()
        self.assertIsNone(self.module.init_dictionary("风格", "C-1")["number_preview"])
        self.db.get_value.assert_not_called()

    def test_automatic_final_number_is_allocated_at_save_and_scoped_by_company_and_type(self):
        preview = self.module.init_dictionary("风格", "C-1")["number_preview"]
        first = self.module.save_dictionary(self.automatic_payload())
        second = self.module.save_dictionary(self.automatic_payload())
        brand = self.module.save_dictionary(self.automatic_payload(dictionary_type="品牌"))
        foreign = self.module.save_dictionary(self.automatic_payload(company="C-2"))
        self.assertEqual((preview, first["legacy_number"], second["legacy_number"], brand["legacy_number"], foreign["legacy_number"]),
                         ("001", "001", "002", "001", "001"))
        self.assertEqual(first["numbering_mode"], self.module.AUTO_MODE)
        self.assertIsNone(second["number_preview"])
        self.assertFalse(second["number_preview_is_provisional"])
        self.assertEqual(len(self.series), 3)

    def test_automatic_retry_after_rename_keeps_original_digest_final_number_and_counter(self):
        payload = self.automatic_payload()
        archive = self.module.save_dictionary(payload)
        original_digest = self.documents[archive["name"]]["request_digest"]
        changed = self.update(archive, value="后续改名", remarks="后续备注")
        before = deepcopy((self.documents, self.versions, self.series))
        count = self.allocate.call_count
        self.assertEqual(self.module.save_dictionary(payload), changed)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "风格", "C-1")["archive"], changed)
        self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertEqual(self.allocate.call_count, count)
        self.assertEqual(self.documents[archive["name"]]["request_digest"], original_digest)
        with self.assertRaisesRegex(ValueError, "新建请求已使用"):
            self.module.save_dictionary(payload | {"value": "改初始输入"})
        with self.assertRaisesRegex(ValueError, "新建请求已使用"):
            self.module.save_dictionary({key: value for key, value in payload.items() if key != "numbering_mode"})
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_automatic_number_mode_and_receipt_are_fixed_through_api_and_native_save(self):
        archive = self.module.save_dictionary(self.automatic_payload())
        for field, value in (("legacy_number", "999"), ("numbering_mode", None), ("numbering_mode", "bad")):
            with self.subTest(api=field), self.assertRaises(ValueError):
                self.update(archive, **{field: value})
            document = self.get_doc(DOCTYPE, archive["name"])
            document.set(field, value)
            with self.subTest(native=field), self.assertRaises(ValueError):
                document.save()
        self.assertEqual(self.documents[archive["name"]]["legacy_number"], "001")
        self.assertEqual(self.series, {self.module._series_key("C-1", "风格"): 1})

    def test_automatic_creation_rejects_client_number_mode_and_before_validate_marker_reuse(self):
        for changes in ({"legacy_number": None}, {"legacy_number": "001"}, {"numbering_mode": "bad"}):
            payload = self.automatic_payload() | changes
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.save_dictionary(payload)
        self.allocate.assert_not_called()
        direct = self.new_doc(DOCTYPE)
        direct.company, direct.value, direct.legacy_number = "C-1", "直接伪造编号", "001"
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            direct.save()
        valid = self.new_doc(DOCTYPE)
        valid.company, valid.value = "C-1", "原生初始化"
        self.module.before_insert_dictionary(valid)
        forged = self.new_doc(DOCTYPE)
        forged.update(valid)
        forged.__dict__["_jf_dictionary_server"] = True
        forged.__dict__["_jf_dictionary_number"] = (True, valid.legacy_number)
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            self.module.validate_dictionary(forged)
        valid.legacy_number = "999"
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            self.module.validate_dictionary(valid)

    def test_automatic_permission_or_post_save_failure_rolls_back_series_and_all_data(self):
        self.hidden["write"].add("value")
        with self.assertRaises(PermissionError):
            self.module.save_dictionary(self.automatic_payload())
        self.allocate.assert_not_called()
        self.hidden["write"].clear()
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.module.save_dictionary(self.automatic_payload())
        self.assertTrue(self.allocate.called)
        self.assertEqual((self.documents, self.versions, self.series), ({}, [], {}))

    def test_server_number_does_not_require_client_write_permission_but_mode_read_still_guards(self):
        self.hidden["write"].add("legacy_number")
        archive = self.module.save_dictionary(self.automatic_payload())
        self.assertEqual(archive["legacy_number"], "001")
        self.assertTrue(archive["field_meta"]["legacy_number"]["read_only"])
        self.hidden["read"].add("numbering_mode")
        before = deepcopy((self.documents, self.versions, self.series))
        with self.assertRaises(PermissionError):
            self.module.save_dictionary(self.automatic_payload())
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_legacy_empty_mode_digest_and_duplicate_original_numbers_remain_compatible(self):
        payload = self.payload()
        old_body = {"company": "C-1", "dictionary_type": "风格", "fields": {
            "legacy_number": "001", "value": " 典雅 ", "remarks": " 原始备注\n第二行 ", "abbreviation": " DY ", "inactive": 0}}
        digest = hashlib.sha256(json.dumps(old_body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        archive = self.module.save_dictionary(payload)
        self.assertEqual(self.documents[archive["name"]]["request_digest"], digest)
        self.assertIsNone(archive["numbering_mode"])
        another = self.module.save_dictionary(self.payload())
        self.assertEqual(archive["legacy_number"], another["legacy_number"])
        changed = self.update(archive, legacy_number="历史仍可修改")
        self.assertEqual(self.module.save_dictionary(payload), changed)
        self.assertEqual(self.documents[archive["name"]]["request_digest"], digest)
        self.allocate.assert_not_called()

    def test_rename_keeps_stable_name_receipt_and_generates_history(self):
        archive = self.saved()
        receipt = tuple(self.documents[archive["name"]][field] for field in self.module.REQUEST_FIELDS)
        changed = self.update(archive, value="改名原文", legacy_number="X", remarks="新备注", abbreviation="手工简拼")
        self.assertEqual(changed["name"], archive["name"])
        self.assertEqual(tuple(self.documents[archive["name"]][field] for field in self.module.REQUEST_FIELDS), receipt)
        self.assertEqual(len(self.versions), 1)
        self.assertFalse(changed["field_meta"]["value"]["read_only"])
        self.assertTrue(changed["field_meta"]["company"]["read_only"])
        self.assertIn("modified_by", changed)

    def test_native_saved_form_rename_is_allowed_and_identity_is_fixed(self):
        archive = self.saved()
        document = self.get_doc(DOCTYPE, archive["name"])
        document.value = "原生修改"
        document.save()
        self.assertEqual(self.documents[document.name]["value"], "原生修改")
        for field, value in (("company", "C-2"), ("dictionary_type", "来源"),
                             ("request_id", str(uuid4())), ("request_digest", "b" * 64)):
            document = self.get_doc(DOCTYPE, archive["name"])
            document.set(field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "不能修改"):
                document.save()

    def test_creation_retry_after_rename_reads_current_record_without_reverting_or_new_version(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        current = self.update(archive, value="后续已改名", remarks="后续备注")
        before = deepcopy((self.documents, self.versions))
        self.assertEqual(self.module.save_dictionary(payload), current)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "风格", "C-1")["archive"], current)
        self.assertEqual((self.documents, self.versions), before)
        with self.assertRaisesRegex(ValueError, "新建请求已使用"):
            self.module.save_dictionary(payload | {"value": "别的初始内容"})

    def test_hidden_request_collision_is_generic_and_preserves_old_ui_messages(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "风格", "C-1"), {"status": "not_found"})
        self.frappe.local.message_log = [{"message": "earlier independent message"}]
        before = deepcopy((self.documents, self.versions))
        with self.assertRaisesRegex(ValueError, "新建请求已使用") as caught:
            self.module.save_dictionary(payload)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(archive["name"], str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, [{"message": "earlier independent message"}])
        self.assertEqual((self.documents, self.versions), before)

    def test_stale_write_has_lock_and_never_applies_original_words(self):
        archive = self.saved()
        changed = self.update(archive, value="最新")
        with self.assertRaises(RuntimeError):
            self.update(archive, value="过时覆盖")
        self.assertEqual(self.documents[archive["name"]]["value"], "最新")
        self.assertTrue(any(call.kwargs.get("for_update") for call in self.frappe.get_doc.call_args_list))
        self.assertEqual(self.update(changed, inactive=1)["inactive"], 1)

    def test_list_contains_version_and_manual_search_only_reads_permitted_number_or_abbreviation(self):
        archive = self.saved()
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "DY")[0]["version"], archive["version"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "001")[0]["name"], archive["name"])
        self.hidden["read"].add("abbreviation")
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "DY"), [])
        self.assertTrue(all(row[0] != "abbreviation" for row in self.frappe.get_list.call_args.kwargs["or_filters"]))

    def test_optional_hidden_values_are_never_backfilled_from_full_document(self):
        archive = self.saved()
        self.hidden["read"].update(("remarks", "legacy_number", "abbreviation"))
        for response in (self.module.read_dictionary(archive["name"]), self.module.list_dictionary("风格", "C-1")[0]):
            self.assertTrue(set(self.hidden["read"]).isdisjoint(response))
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"])["field_meta"])
        before = deepcopy(self.documents)
        with self.assertRaises(PermissionError):
            self.module.save_dictionary({"name": archive["name"], "version": archive["version"], "company": "C-1",
                                         "dictionary_type": "风格", "remarks": "不可读字段盲写"})
        self.assertEqual(self.documents, before)

    def test_optional_projection_omission_stays_absent_but_missing_required_column_rejects(self):
        archive = self.saved()
        row = Record(self.documents[archive["name"]])
        row.pop("remarks")
        self.forced_list = [row]
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"]))
        self.assertNotIn("remarks", self.module.list_dictionary("风格", "C-1")[0])
        row.pop("value")
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"])
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("风格", "C-1")

    def test_required_field_denial_blocks_queries_and_creation_receipt_read_permission_is_separate(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.hidden["read"].add("value")
        self.frappe.get_list.reset_mock()
        for action in (lambda: self.module.list_dictionary("风格", "C-1"), lambda: self.module.read_dictionary(archive["name"]),
                       lambda: self.module.resolve_creation(payload["request_id"], "风格", "C-1")):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.get_list.assert_not_called()
        self.hidden["read"].clear()
        self.hidden["read"].add("request_digest")
        self.assertEqual(self.module.read_dictionary(archive["name"])["value"], archive["value"])
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "风格", "C-1")

    def test_native_form_cannot_modify_unreadable_or_unwritable_original_fields(self):
        archive = self.saved()
        for permission in ("read", "write"):
            self.hidden[permission].add("remarks")
            document = self.get_doc(DOCTYPE, archive["name"])
            document.remarks = "hidden replacement"
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                document.save()
            self.hidden[permission].clear()

    def test_partial_visible_edit_does_not_normalize_an_unchanged_hidden_empty_original(self):
        archive = self.saved()
        self.documents[archive["name"]]["remarks"] = ""
        self.hidden["read"].add("remarks")
        self.hidden["write"].add("remarks")
        self.module.save_dictionary({"name": archive["name"], "version": archive["version"], "company": "C-1",
                                     "dictionary_type": "风格", "value": "仅改可见名称"})
        self.assertEqual(self.documents[archive["name"]]["remarks"], "")

    def test_checking_actual_company_and_document_user_permissions_keeps_rows_scoped(self):
        archive = self.saved()
        self.denied_companies.add("C-2")
        for action in (lambda: self.module.init_dictionary("品牌", "C-2"), lambda: self.saved(company="C-2"),
                       lambda: self.module.list_dictionary("风格", "C-2")):
            with self.assertRaises(PermissionError):
                action()
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"])
        self.frappe.get_all.assert_not_called()

    def test_scope_guard_filters_misleading_native_list_rows_without_backfill(self):
        archive = self.saved()
        self.forced_list = [Record(self.documents[archive["name"]])]
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        self.document_denials.clear()
        self.documents[archive["name"]]["company"] = "C-2"
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])

    def test_inactive_is_filter_only_and_raw_history_remains_independent(self):
        archive = self.saved()
        independent_profile = {"style": archive["value"], "source": "未登记来源", "brand": "未登记品牌"}
        before = deepcopy(independent_profile)
        stopped = self.update(archive, value="新词条名", inactive=1)
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        self.assertEqual(self.module.list_dictionary("风格", "C-1", include_inactive=True)[0]["value"], "新词条名")
        self.assertEqual(independent_profile, before)
        self.assertEqual(self.module.read_dictionary(stopped["name"])["inactive"], 1)
        self.frappe.get_doc.assert_any_call(DOCTYPE, archive["name"], for_update=True)

    def test_guest_and_no_manager_role_cannot_read_create_or_modify(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.role_denials.update(("read", "create", "write"))
        actions = (lambda: self.module.init_dictionary("品牌"), lambda: self.module.list_dictionary("风格", "C-1"),
                   lambda: self.module.read_dictionary(archive["name"]), lambda: self.saved(),
                   lambda: self.module.resolve_creation(payload["request_id"], "风格", "C-1"))
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
        self.role_denials.clear()
        self.frappe.session.user = "Guest"
        for action in actions:
            with self.assertRaises(PermissionError):
                action()

    def test_delete_is_explicitly_unimplemented_for_every_actor_not_a_permanent_identity_policy(self):
        for user in ("stock-manager@example.invalid", "Administrator"):
            self.frappe.session.user = user
            with self.assertRaisesRegex(ValueError, "删除尚未实现"):
                self.module.prevent_delete(None)

    def test_wrong_type_company_versions_and_server_payload_fields_reject(self):
        archive = self.saved()
        for changes in ({"company": "C-2"}, {"dictionary_type": "品牌"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "不能修改"):
                self.update(archive, **changes)
        with self.assertRaisesRegex(ValueError, "类型与当前"):
            self.module.read_dictionary(archive["name"], "来源")
        invalid = ({"dictionary_type": "类别"}, {"value": " "}, {"value": "a" * 141}, {"legacy_number": "a" * 141},
                   {"remarks": 1}, {"abbreviation": []}, {"inactive": "yes"}, {"inactive": 1.0}, {"request_id": str(uuid4()).upper()},
                   {"request_id": "not-a-uuid"}, {"request_digest": "client-forged"}, {"version": "old"})
        before = deepcopy(self.documents)
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.saved(**changes)
        self.assertEqual(self.documents, before)

    def test_post_save_failure_rolls_back_record_history_and_preserves_prior_callbacks_and_context(self):
        def initial():
            pass
        self.db.after_commit.add(initial)
        self.frappe.local._realtime_log = ["original event"]
        self.frappe.flags.currently_saving = ["original context"]
        archive = self.saved()
        before = deepcopy((self.documents, self.versions))
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.update(archive, value="failed rename")
        self.assertEqual((self.documents, self.versions), before)
        self.assertEqual(self.db.after_commit._functions, [initial])
        self.assertEqual(self.frappe.local._realtime_log, ["original event"])
        self.assertEqual(self.frappe.flags.currently_saving, ["original context"])

    def test_required_creation_write_permission_and_absent_context_are_preserved_on_failure(self):
        self.hidden["write"].add("value")
        self.assertFalse(self.module.init_dictionary("风格")["can_write"])
        self.hidden["write"].clear()
        self.fail_after_save = True
        with self.assertRaises(ValueError):
            self.saved()
        self.assertFalse(self.documents)
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))

    def test_success_keeps_preexisting_and_new_native_commit_callbacks(self):
        def existing():
            pass
        def native_notification():
            pass
        self.db.after_commit.add(existing)
        original = NativeDocument.save
        def notify(document):
            result = original(document)
            self.db.after_commit.add(native_notification)
            return result
        with patch.object(NativeDocument, "save", notify):
            archive = self.saved()
        self.assertIn(archive["name"], self.documents)
        self.assertEqual(self.db.after_commit._functions, [existing, native_notification])


if __name__ == "__main__":
    unittest.main()
