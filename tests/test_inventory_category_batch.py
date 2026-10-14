"""Native-save transaction models; live database/concurrency acceptance is separate."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_inventory_archive as archive_fixture
import test_inventory_category as category_fixture


class BatchDocument(archive_fixture.NativeDocument):
    def save(self):
        result = super().save()
        if self.value == self.environment.fail_category_value:
            self.environment.frappe.db.after_commit.add(self.environment.new_callback)
            self.environment.frappe.local._realtime_log = ["failed-category-event"]
            raise ValueError("later native save failure")
        return result

    def insert(self, ignore_permissions=False):
        env, module = self.environment, self.environment.category_module
        if self.doctype != module.RECEIPT or not ignore_permissions:
            raise AssertionError("Only the guarded service receipt bypasses client create permission")
        module.validate_batch_receipt(self)
        if (self.doctype, self.name) in env.documents:
            raise env.frappe.DuplicateEntryError("duplicate request primary key")
        if env.fail_receipt:
            raise ValueError("receipt native insert failed")
        self["__islocal"] = 0
        env.documents[(self.doctype, self.name)] = deepcopy(dict(self))
        env.receipt_inserts.append(self.name)
        return self


class TestInventoryCategoryBatch(unittest.TestCase):
    def setUp(self):
        self.query_env = category_fixture.TestInventoryCategory(methodName="runTest")
        self.query_env.setUp()
        self.module, self.env = self.query_env.module, self.query_env.env
        self.env.category_module = self.module
        self.env.fail_category_value, self.env.fail_receipt = "NEVER_FAIL", False
        self.env.receipt_inserts, self.env.deletions = [], []
        folder = archive_fixture.ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_inventory_category_batch"
        schema = json.loads((folder / "jifeng_inventory_category_batch.json").read_text(encoding="utf-8"))
        self.env.metas[self.module.RECEIPT] = archive_fixture.Meta(schema["fields"])
        original_get_doc, original_new_doc = self.env.get_doc, self.env.new_doc
        self.env.frappe.get_doc.side_effect = lambda *args, **kwargs: BatchDocument(original_get_doc(*args, **kwargs))
        self.env.frappe.new_doc.side_effect = lambda *args, **kwargs: BatchDocument(original_new_doc(*args, **kwargs))
        self.env.frappe.delete_doc = self.delete_doc
        self.env.frappe.get_list.side_effect = self.get_list

    def get_list(self, doctype, filters, **options):
        actual = {field: value for field, value in filters.items() if not isinstance(value, list)}
        names = self.query_env.get_list(doctype, actual, **{key: value for key, value in options.items()
                                                        if key not in ("limit_start", "limit_page_length")})
        for field, value in filters.items():
            if not isinstance(value, list):
                continue
            if value in (["is", "set"], ["is", "not set"]):
                names = [name for name in names if bool(self.env.documents[(doctype, name)].get(field)) == (value[1] == "set")]
            elif value[0] == "in":
                names = [name for name in names if self.env.documents[(doctype, name)].get(field) in value[1]]
            else:
                raise AssertionError("Unsupported fixture filter " + str(value))
        start, limit = options.get("limit_start", 0), options.get("limit_page_length", len(names))
        return names[start:start + limit]

    def delete_doc(self, doctype, name):
        if doctype != self.env.module.DICTIONARY:
            raise AssertionError("No native business record deletion is allowed")
        document = self.env.frappe.get_doc(doctype, name)
        document.check_permission("delete")
        if any(row.get("parent_dictionary") == name for (kind, _), row in self.env.documents.items() if kind == doctype):
            raise ValueError("native linked dictionary refuses deletion")
        del self.env.documents[(doctype, name)]
        self.env.deletions.append(name)

    def row(self, name=None, record_type="major", operation="upsert", values=None, **changes):
        row = {"row_id": str(uuid4()), "record_type": record_type, "operation": operation}
        if name:
            row.update(name=name, version=self.env.documents[(self.module.DICTIONARY, name)]["modified"])
        if operation == "upsert":
            row["values"] = values or {"value": "新类别"}
        if record_type == "minor":
            row.update(category="M1", category_version=self.env.documents[(self.module.DICTIONARY, "M1")]["modified"])
        row.update(changes)
        return row

    def payload(self, *rows, **changes):
        return {"kind": "物料", "company": "JF", "request_id": str(uuid4()), "rows": list(rows), **changes}

    def test_new_parent_and_children_are_saved_together_and_share_exact_link(self):
        parent = self.row(values={"value": "新大类", "legacy_code": "T01", "owner_name": "负责", "region": "区域"})
        child = self.row(record_type="minor", values={"value": "新小类", "material": "材质", "display_uom": "个",
            "conversion_uom_text": "箱", "conversion_value": "1.2500"}, category=None, category_version=None,
            parent_row_id=parent["row_id"])
        payload = self.payload(child, parent)
        result = self.module.save_batch(payload)
        self.assertEqual(result["status"], "applied")
        saved = {row["row_id"]: self.env.documents[(self.module.DICTIONARY, row["name"])] for row in result["rows"]}
        self.assertEqual(saved[child["row_id"]]["parent_dictionary"], saved[parent["row_id"]]["name"])
        self.assertEqual(saved[child["row_id"]]["conversion_value"], "1.2500")
        self.assertEqual(self.env.receipt_inserts, [payload["request_id"]])
        self.assertEqual(result["digest"], self.module._digest(payload))
        self.assertEqual(self.env.saves, [self.module.DICTIONARY, self.module.DICTIONARY])

    def test_parent_and_child_changed_in_same_batch_check_original_versions_once(self):
        parent = self.row("M1", values={"owner_name": "新负责"})
        child = self.row("C1", "minor", values={"conversion_value": "0"})
        self.module.save_batch(self.payload(child, parent))
        self.assertEqual(self.env.documents[(self.module.DICTIONARY, "M1")]["owner_name"], "新负责")
        self.assertEqual(self.env.documents[(self.module.DICTIONARY, "C1")]["conversion_value"], "0")

    def test_duplicate_replay_and_get_resolution_do_not_write_again(self):
        payload = self.payload(self.row("M1", values={"region": "新区域"}), self.row(values={"value": "第二新增"}))
        result = self.module.save_batch(payload)
        snapshot, writes = deepcopy(self.env.documents), list(self.env.saves)
        self.assertEqual(self.module.save_batch(deepcopy(payload)), result)
        self.assertEqual(self.module.resolve_batch(payload["request_id"], "物料", "JF", result["digest"]), result)
        self.assertEqual(self.env.documents, snapshot)
        self.assertEqual(self.env.saves, writes)
        payload["rows"][0]["values"]["region"] = "另一内容"
        with self.assertRaisesRegex(ValueError, "不同内容"):
            self.module.save_batch(payload)

    def test_missing_receipt_is_only_a_read_result_and_never_authorizes_retry(self):
        request = str(uuid4())
        self.assertEqual(self.module.resolve_batch(request, "物料", "JF", "a" * 64),
                         {"status": "not_found", "request_id": request, "digest": "a" * 64})
        self.assertEqual(self.env.saves, [])

    def test_receipt_replay_checks_actor_scope_and_current_row_access(self):
        payload = self.payload(self.row("M1", values={"region": "已保存"}))
        result = self.module.save_batch(payload)
        original_user = self.env.frappe.session.user
        self.env.frappe.session.user = "another@example.invalid"
        with self.assertRaises(PermissionError):
            self.module.resolve_batch(payload["request_id"], "物料", "JF", result["digest"])
        self.env.frappe.session.user = original_user
        self.env.denied_names.add("M1")
        with self.assertRaises(PermissionError):
            self.module.save_batch(payload)
        self.env.denied_names.clear()
        with self.assertRaises(PermissionError):
            self.module.resolve_batch(payload["request_id"], "材料", "JF", result["digest"])

    def test_later_native_failure_rolls_back_all_rows_receipt_and_callbacks(self):
        before = deepcopy(self.env.documents)
        def callback():
            pass
        self.env.frappe.db.after_commit.add(callback)
        self.env.frappe.local._realtime_log = ["existing-event"]
        self.env.fail_category_value = "FAIL"
        payload = self.payload(self.row("M1", values={"region": "暂存"}), self.row("C1", "minor", values={"value": "FAIL"}))
        with self.assertRaisesRegex(ValueError, "later native"):
            self.module.save_batch(payload)
        self.assertEqual(self.env.documents, before)
        self.assertEqual(self.env.frappe.db.after_commit._functions, [callback])
        self.assertEqual(self.env.frappe.local._realtime_log, ["existing-event"])
        self.assertEqual(self.env.receipt_inserts, [])

    def test_receipt_insert_failure_is_an_atomic_commit_gate(self):
        before = deepcopy(self.env.documents)
        self.env.fail_receipt = True
        with self.assertRaisesRegex(ValueError, "receipt native"):
            self.module.save_batch(self.payload(self.row("M1", values={"region": "暂存"}), self.row()))
        self.assertEqual(self.env.documents, before)

    def test_all_versions_checked_before_any_save(self):
        for key in ("version", "category_version"):
            row = self.row("C1", "minor", values={"material": "修改"})
            row[key] = "2026-10-01T00:00:01"
            before = deepcopy(self.env.documents)
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.module.save_batch(self.payload(self.row("M1", values={"region": "暂存"}), row))
            self.assertEqual(self.env.documents, before)
            self.assertEqual(self.env.saves, [])

    def test_field_permission_and_record_permission_precede_mutation(self):
        for failure in ("hidden", "write", "company"):
            if failure == "hidden":
                self.env.hidden[(self.module.DICTIONARY, "write")] = {"region"}
            elif failure == "write":
                self.env.denials.add((self.module.DICTIONARY, "write"))
            else:
                self.env.denied_companies.add("JF")
            with self.subTest(failure=failure), self.assertRaises(PermissionError):
                self.module.save_batch(self.payload(self.row("M1", values={"region": "不应写入"})))
            self.env.hidden.clear()
            self.env.denials.clear()
            self.env.denied_companies.clear()
        self.assertEqual(self.env.saves, [])

    def test_exact_scope_and_original_child_parent_cannot_be_forged(self):
        for change in ({"kind": "材料"}, {"company": "OTHER"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.module.save_batch(self.payload(self.row("M1"), **change))
        wrong_parent = self.row("C1", "minor", category="M2", category_version=self.env.documents[(self.module.DICTIONARY, "M2")]["modified"])
        with self.assertRaisesRegex(ValueError, "归属"):
            self.module.save_batch(self.payload(wrong_parent))

    def test_explicit_association_keeps_old_flat_row_and_never_guesses_parent(self):
        orphan = self.query_env.add("ORPHAN", "小类", "未关联原文", notes="保留", conversion_value="2.00")
        self.module.save_batch(self.payload(self.row("ORPHAN", "minor", "associate")))
        saved = self.env.documents[(self.module.DICTIONARY, "ORPHAN")]
        self.assertEqual(saved["parent_dictionary"], "M1")
        self.assertEqual(saved["value"], orphan["value"])
        self.assertEqual(saved["notes"], "保留")
        self.assertEqual(saved["conversion_value"], "2.00")
        with self.assertRaisesRegex(ValueError, "已有.*关联"):
            self.module.save_batch(self.payload(self.row("ORPHAN", "minor", "associate")))

    def test_native_deletion_requires_permission_and_preserves_link_checks(self):
        before = deepcopy(self.env.documents)
        with self.assertRaisesRegex(ValueError, "native linked"):
            self.module.save_batch(self.payload(self.row("M1", operation="delete")))
        self.assertEqual(self.env.documents, before)
        self.env.denials.add((self.module.DICTIONARY, "delete"))
        with self.assertRaises(PermissionError):
            self.module.save_batch(self.payload(self.row("C1", "minor", "delete")))
        self.env.denials.clear()
        result = self.module.save_batch(self.payload(self.row("M1", operation="delete"), self.row("C1", "minor", "delete")))
        self.assertEqual(self.env.deletions, ["C1", "M1"])
        self.assertTrue(all(row["operation"] == "delete" for row in result["rows"]))
        self.assertNotIn((self.module.DICTIONARY, "M1"), self.env.documents)

    def test_new_child_cannot_reference_deleted_or_unknown_new_parent(self):
        with self.assertRaisesRegex(ValueError, "同时删除"):
            self.module.save_batch(self.payload(self.row("M1", operation="delete"), self.row(record_type="minor")))
        with self.assertRaisesRegex(ValueError, "新增小类"):
            self.module.save_batch(self.payload(self.row(record_type="minor", category=None, category_version=None,
                                                        parent_row_id=str(uuid4()))))

    def test_receipt_controller_blocks_client_insert_update_delete_and_rename(self):
        fake = self.env.frappe.new_doc(self.module.RECEIPT)
        with self.assertRaises(PermissionError):
            self.module.validate_batch_receipt(fake)
        payload = self.payload(self.row("M1", values={"region": "保存"}))
        self.module.save_batch(payload)
        receipt = self.env.frappe.get_doc(self.module.RECEIPT, payload["request_id"])
        with self.assertRaises(PermissionError):
            self.module.validate_batch_receipt(receipt)
        with self.assertRaises(PermissionError):
            self.module.prevent_receipt_change()

    def test_duplicate_identity_empty_unknown_fields_and_nonfinite_values_fail_closed(self):
        row = self.row("M1")
        bad_payloads = [self.payload(), self.payload(row, deepcopy(row)), self.payload(self.row("M1"), self.row("M1")),
                        self.payload(self.row(values={"dictionary_key": "forged"})),
                        self.payload(self.row(record_type="minor", values={"value": "X", "conversion_value": "NaN"})),
                        self.payload(self.row(), request_id="invalid"), self.payload(self.row(), unknown=True)]
        before = deepcopy(self.env.documents)
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.module.save_batch(payload)
            self.assertEqual(self.env.documents, before)

    def test_chooser_pagination_includes_parent_fields_and_masks_denied_parents(self):
        first = self.module.query_selection("物料", "JF", page_size=1)
        self.assertEqual(first["rows"][0]["name"], "C1")
        self.assertTrue(first["has_more"])
        self.assertEqual(first["rows"][0]["legacy_code"], "A")
        self.assertEqual(first["rows"][0]["major_category"], "大类原文")
        self.env.denied_names.add("M1")
        masked = self.module.query_selection("物料", "JF", page_size=1)
        self.assertEqual(masked["rows"], [])
        self.assertTrue(masked["has_more"])
        self.assertEqual(self.env.saves, [])

    def test_preview_digest_unlinked_query_and_pinyin_are_reads_only(self):
        payload = self.payload(self.row("M1", values={"region": "草稿"}))
        before = deepcopy(self.env.documents)
        self.assertEqual(self.module.preview_batch(payload)["digest"], self.module._digest(payload))
        self.query_env.add("ORPHAN", "小类", "未关联")
        unlinked = self.module.query_grid("物料", "JF", unlinked="1")
        self.assertEqual([row["name"] for row in unlinked["rows"]], ["ORPHAN"])
        with patch.dict("sys.modules", {"pypinyin": None}), self.assertRaisesRegex(ValueError, "尚未安装"):
            self.module.preview_pinyin("物料", "JF", [{"row_id": str(uuid4()), "value": "测试"}])
        self.assertEqual(self.env.saves, [])
        for key, document in before.items():
            self.assertEqual(self.env.documents[key], document)


if __name__ == "__main__":
    unittest.main()
