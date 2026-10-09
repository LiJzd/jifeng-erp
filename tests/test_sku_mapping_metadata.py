"""Protect image provenance at the actual SKU Mapping controller boundary."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_sku_mapping/jifeng_sku_mapping.py"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    def get_doc_before_save(self):
        return self.get("_previous")


class SKUImageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.flags = types.SimpleNamespace()
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        model = types.ModuleType("frappe.model.document")
        model.Document = Document
        images = types.ModuleType("jifeng_erp.images")
        self.validate_candidates = images.validate_source_image_candidates = Mock()
        images.valid_image_source_url = lambda value: isinstance(value, str) and value.startswith("https://")
        modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model.document": model,
                                          "jifeng_erp.images": images})
        modules.start(); self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("sku_mapping_controller_under_test", CONTROLLER)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.Mapping = module.JifengSKUMapping
        self.original = Record(name="scope-and-sku", scope="scope", sku_id="SKU-1", item_code="ITEM-1",
                               managed_item=1, source_image_candidates="saved-candidates",
                               archived_image_source="https://cdn.example/saved.jpg", archived_image="/files/saved.jpg")

    def document(self, **changes):
        doc = self.Mapping(deepcopy(self.original))
        doc.update(_previous=deepcopy(self.original), flags=Record())
        doc.update(changes)
        return doc

    def authorize(self, fields, name="scope-and-sku"):
        self.frappe.flags.jifeng_image_metadata_write = {"mapping_name": name, "fields": fields}

    def test_ordinary_and_incoming_document_flags_cannot_forge_metadata(self):
        for flags in (Record(), Record(from_sync=True), Record(from_image_archive=True),
                      Record(jifeng_image_metadata_write={"mapping_name": "scope-and-sku",
                                                         "fields": ["archived_image_source"]})):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "只能由"):
                self.document(flags=flags, archived_image_source="https://cdn.example/forged.jpg").validate()

    def test_authority_is_limited_to_exact_document_and_fields(self):
        self.authorize(["source_image_candidates"], name="another-mapping")
        with self.assertRaisesRegex(ValueError, "只能由"):
            self.document(source_image_candidates="new-candidates").validate()
        self.authorize(["source_image_candidates"])
        with self.assertRaisesRegex(ValueError, "只能由"):
            self.document(archived_image_source="https://cdn.example/new.jpg").validate()

    def test_legitimate_sync_validates_candidate_payload_and_preserves_other_provenance(self):
        self.authorize(["source_image_candidates"])
        doc = self.document(source_image_candidates="real-source-candidates", flags=Record(from_sync=True))
        doc.validate()
        self.validate_candidates.assert_called_once_with(doc, "real-source-candidates")
        self.assertEqual(doc.archived_image_source, self.original.archived_image_source)
        self.assertEqual(doc.archived_image, self.original.archived_image)

    def test_invalid_candidate_or_archive_url_rejected_in_authorized_operation(self):
        self.authorize(["source_image_candidates", "archived_image_source"])
        self.validate_candidates.side_effect = ValueError("图片候选商品编码不一致。")
        with self.assertRaisesRegex(ValueError, "编码不一致"):
            self.document(source_image_candidates="wrong-sku-candidates").validate()
        with self.assertRaisesRegex(ValueError, "地址无效"):
            self.document(archived_image_source="http://cdn.example/unsafe.jpg").validate()

    def test_new_mapping_empty_metadata_allowed_but_nonempty_needs_authority(self):
        doc = self.Mapping(name="scope-and-sku", item_code="ITEM-1", flags=Record(),
                           source_image_candidates=None, archived_image_source=None)
        doc.validate()
        doc.source_image_candidates = "real-source-candidates"
        with self.assertRaisesRegex(ValueError, "只能由"):
            doc.validate()
        self.authorize(["source_image_candidates"])
        doc.validate()
        self.validate_candidates.assert_called_once_with(doc, "real-source-candidates")

    def test_manual_relink_clears_old_image_lineage_and_managed_item(self):
        doc = self.document(item_code="ITEM-MANUAL")
        doc.validate()
        for field in ("archived_image", "source_image_candidates", "archived_image_source"):
            self.assertIsNone(doc.get(field))
        self.assertEqual(doc.managed_item, 0)
        with self.assertRaisesRegex(ValueError, "只能由"):
            self.document(item_code="ITEM-MANUAL", source_image_candidates="forged").validate()

    def test_unrelated_manual_edit_keeps_managed_item_and_existing_metadata(self):
        doc = self.document(managed_item=0, supplier_name="本地说明")
        doc.validate()
        self.assertEqual(doc.managed_item, 1)
        self.assertEqual(doc.source_image_candidates, self.original.source_image_candidates)
        self.validate_candidates.assert_not_called()

    def test_schema_uses_readonly_small_text_and_retains_existing_role_permissions(self):
        schema = json.loads(CONTROLLER.with_suffix(".json").read_text("utf-8"))
        fields = {field["fieldname"]: field for field in schema["fields"]}
        for name in ("source_image_candidates", "archived_image_source"):
            self.assertEqual(fields[name]["fieldtype"], "Small Text")
            self.assertEqual(fields[name]["read_only"], 1)
            self.assertEqual(schema["field_order"].count(name), 1)
        self.assertEqual({row["role"] for row in schema["permissions"]},
                         {"System Manager", "Stock Manager", "Manufacturing Manager"})
        self.assertTrue(all(not row.get("create") for row in schema["permissions"]))


if __name__ == "__main__":
    unittest.main()
