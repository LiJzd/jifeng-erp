"""Category query/selection models; no database or browser acceptance."""

from copy import deepcopy
import hashlib
import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import patch

import test_inventory_archive as fixture


class TestInventoryCategory(unittest.TestCase):
    def setUp(self):
        self.env = fixture.TestInventoryArchive(methodName="runTest")
        self.env.setUp()
        self.env.module.get_permitted_fields = lambda doctype, permission_type, **options: (
            self.env.columns(doctype, permission_type, **options) | {"name", "modified", "owner", "creation", "modified_by"}
        ) - self.env.hidden.get((doctype, permission_type), set())
        self.env.frappe.get_list.side_effect = self.get_list
        with patch.dict(sys.modules, {"frappe": self.env.frappe,
                                      "jifeng_erp.inventory_archive": self.env.module,
                                      "jifeng_erp.personnel_separation": types.SimpleNamespace(_atomic=self.env.module._atomic)}):
            spec = importlib.util.spec_from_file_location("category_query_under_test",
                fixture.ROOT / "jifeng_erp/inventory_category.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.major = self.add("M1", "大类", "大类原文", legacy_code="A", owner_name="负责原文", region="区域原文")
        self.add("M2", "大类", "第二大类", legacy_code="B")
        self.child = self.add("C1", "小类", "小类原文", parent_dictionary="M1", material="材质原文",
                              display_uom="单位原文", conversion_uom_text="换算单位原文", conversion_value="1.2500")
        self.add("C2", "小类", "第二小类", parent_dictionary="M2", conversion_value="0")

    def add(self, name, record_type, value, company="JF", kind="物料", **values):
        fields = {"company": company, "archive_kind": kind, "dictionary_type": record_type,
                  "value": value, "inactive": 0, "parent_dictionary": None, **values}
        return self.env.add(self.env.module.DICTIONARY, name, **fields)

    def get_list(self, doctype, filters, **options):
        names = self.env.get_list(doctype, filters, **options)
        names.sort()
        if options.get("or_filters"):
            names = [name for name in names if any(str(value).strip("%").casefold() in str(
                self.env.documents[(doctype, name)].get(field) or "").casefold()
                for field, operation, value in options["or_filters"])]
        start, limit = options.get("limit_start", 0), options.get("limit_page_length", len(names))
        return names[start:start + limit]

    def select(self, **changes):
        args = {"kind": "物料", "company": "JF", "name": "C1", "version": self.child["modified"],
                "category_version": self.major["modified"]}
        args.update(changes)
        return self.module.select_category(**args)

    def test_paged_major_query_and_exact_child_association(self):
        first = self.module.query_grid("物料", "JF", page_size=1)
        self.assertEqual([row["name"] for row in first["rows"]], ["M1"])
        self.assertTrue(first["has_more"])
        second = self.module.query_grid("物料", "JF", offset=1, page_size=1)
        self.assertEqual([row["name"] for row in second["rows"]], ["M2"])
        self.assertFalse(second["has_more"])
        children = self.module.query_grid("物料", "JF", category="M1")
        self.assertEqual([row["name"] for row in children["rows"]], ["C1"])
        self.assertEqual(children["category_version"], self.major["modified"])

    def test_selection_preserves_all_original_text_and_numeric_precision(self):
        result = self.select()
        self.assertEqual(result["profile"], {"major_category": "大类原文", "minor_category": "小类原文",
            "material": "材质原文", "display_uom": "单位原文", "conversion_uom": "换算单位原文",
            "conversion_value": "1.2500", "owner_name": "负责原文", "region": "区域原文"})
        self.assertEqual(self.env.saves, [])

    def test_material_scope_cannot_receive_stock_category_or_other_company(self):
        for args in ({"kind": "材料"}, {"company": "OTHER"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.select(**args)
        with self.assertRaises(ValueError):
            self.module.query_grid("物料", "OTHER", category="M1")

    def test_parent_and_child_source_versions_both_rechecked(self):
        for args in ({"version": "2026-10-02T00:00:00"}, {"category_version": "2026-10-02T00:00:00"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.select(**args)

    def test_parent_child_permissions_and_hidden_fill_fields_refuse_selection(self):
        self.env.denied_names.add("M1")
        with self.assertRaises(PermissionError):
            self.select()
        self.env.denied_names.clear()
        self.env.hidden[(self.env.module.DICTIONARY, "read")] = {"owner_name"}
        with self.assertRaises(PermissionError):
            self.select()

    def test_required_identity_field_mask_refuses_grid(self):
        self.env.hidden[(self.env.module.DICTIONARY, "read")] = {"parent_dictionary"}
        with self.assertRaises(PermissionError):
            self.module.query_grid("物料", "JF")

    def test_stopped_and_unlinked_children_are_not_selected(self):
        for name in ("M1", "C1"):
            self.env.documents[(self.env.module.DICTIONARY, name)]["inactive"] = 1
            with self.assertRaises(ValueError):
                self.select()
            self.env.documents[(self.env.module.DICTIONARY, name)]["inactive"] = 0
        self.child["parent_dictionary"] = None
        with self.assertRaises(ValueError):
            self.select()

    def test_new_schema_is_required_without_falling_back_to_flat_dictionary(self):
        meta = self.env.metas[self.env.module.DICTIONARY]
        meta.fields = [field for field in meta.fields if field.fieldname != "parent_dictionary"]
        with self.assertRaises(ValueError):
            self.module.init_grid("物料")

    def test_query_validates_offsets_limits_and_search(self):
        for args in ({"offset": -1}, {"offset": True}, {"page_size": 501}, {"page_size": 1.5}, {"search": []}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.module.query_grid("物料", "JF", **args)
        result = self.module.query_grid("物料", "JF", search="B")
        self.assertEqual([row["name"] for row in result["rows"]], ["M2"])

    def test_view_only_user_gets_readonly_field_meta(self):
        self.env.denials.add((self.env.module.DICTIONARY, "write"))
        rows = self.module.query_grid("物料", "JF")["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(not row["can_write"] for row in rows))
        self.assertTrue(all(field["read_only"] for row in rows for field in row["field_meta"].values()))

    def test_same_minor_text_can_exist_under_distinct_parents(self):
        first = self.env.get_doc(self.env.module.DICTIONARY, "C1")
        second = self.env.get_doc(self.env.module.DICTIONARY, "C2")
        second.value = first.value
        self.env.module.validate_dictionary(first)
        self.env.module.validate_dictionary(second)
        self.assertNotEqual(first.dictionary_key, second.dictionary_key)

    def test_legacy_flat_identity_is_retained_and_parent_validation_is_closed(self):
        original = self.env.get_doc(self.env.module.DICTIONARY, "M1")
        self.env.module.validate_dictionary(original)
        legacy_key = hashlib.sha256(json.dumps(["JF", "物料", "大类", "大类原文"],
                                               ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(original.dictionary_key, legacy_key)
        for field, value in (("parent_dictionary", "C2"), ("parent_dictionary", "M1")):
            bad = fixture.NativeDocument(deepcopy(dict(original)))
            bad[field] = value
            with self.assertRaises(ValueError):
                self.env.module.validate_dictionary(bad)
        child = self.env.get_doc(self.env.module.DICTIONARY, "C1")
        for value in ("NaN", "Infinity", True):
            child.conversion_value = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.env.module.validate_dictionary(child)


if __name__ == "__main__":
    unittest.main()
