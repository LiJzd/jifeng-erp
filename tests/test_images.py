"""Verify native fallback provenance and the risks of additional image requests."""
import json
import types
import unittest
from unittest.mock import Mock, patch

import test_sync


class TestNativeImageAlternatives(unittest.TestCase):
    def setUp(self):
        case = test_sync.TestImageRetryLifecycle()
        case.setUp()
        self.__dict__.update(case.__dict__)
        self.mapping = case.mapping
        self.settings = case.settings
        self.no_proxy_environment = case.no_proxy_environment
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {"jifeng_image_direct_confirmed": True}
        self.item = types.SimpleNamespace(image="")
        self.file_manager.save_file.return_value = types.SimpleNamespace(file_url="/files/real.jpg")

    def candidate_mapping(self, **overrides):
        mapping = self.mapping(**overrides)
        mapping.source_image_candidates = self.images.source_image_candidates_from_sku(mapping, {
            "sku_id": mapping.sku_id, "pic_big": mapping.source_image,
            "pic": "https://cdn.example.com/real-original-small.jpg",
        })
        return mapping

    def arrange_docs(self, mapping, refreshed=None):
        refreshed = refreshed or mapping
        self.frappe.get_doc.side_effect = [mapping, refreshed, self.item]

    def test_primary_404_uses_literal_same_sku_and_records_actual_source(self):
        mapping = self.candidate_mapping()
        original_primary = mapping.source_image
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes", side_effect=[
                (None, None, "图片服务器返回 HTTP 404，已停止下载。"), (b"photo", "jpg", "")]) as download:
            self.images.archive_image(mapping.name)
        self.assertEqual([c.args[0] for c in download.call_args_list],
                         [original_primary, "https://cdn.example.com/real-original-small.jpg"])
        self.assertEqual(mapping.source_image, original_primary)
        self.assertEqual(mapping.archived_image_source, "https://cdn.example.com/real-original-small.jpg")
        self.assertEqual(mapping.archived_image, "/files/real.jpg")
        self.assertEqual(mapping.image_sync_status, "已完成")
        self.assertNotIn("https://", mapping.image_sync_error)
        self.frappe.db.set_value.assert_called_once_with("Item", "ITEM-1", "image", "/files/real.jpg")
        self.assertFalse(hasattr(self.frappe.flags, "jifeng_image_metadata_write"))

    def test_primary_403_can_use_current_exact_order_and_persist_provenance(self):
        mapping = self.mapping()
        self.frappe.db.sql.return_value = [{"line_name": "ROW-1", "order_name": "ORDER-1",
            "external_line_id": "SOURCE-ROW-1", "image_url": "https://cdn.example.com/order-real.jpg"}]
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes", side_effect=[
                (None, None, "图片服务器返回 HTTP 403，已停止下载。"), (b"photo", "jpg", "")]):
            self.images.archive_image(mapping.name)
        sources = json.loads(mapping.source_image_candidates)["sources"]
        self.assertEqual(sources[0]["line_name"], "ROW-1")
        self.assertEqual(sources[0]["sku_id"], mapping.sku_id)
        self.assertEqual(mapping.archived_image_source, sources[0]["url"])
        for call in self.frappe.db.sql.call_args_list:
            self.assertEqual(call.args[1], ("scope", "QA", "42", "SKU-1", "ITEM-1"))

    def test_network_peer_redirect_and_invalid_body_do_not_trigger_alternate(self):
        errors = ["无法核实图片连接的实际对端，已停止下载（HTTP 404）。",
                  "图片连接对端与本次公网解析地址不一致，已停止下载（HTTP 403）。",
                  "图片服务器返回 HTTP 302，已停止下载。", "图片下载或校验失败。",
                  "图片超过 8 MiB 限额。", "图片像素超过限额。"]
        for error in errors:
            with self.subTest(error=error):
                mapping = self.candidate_mapping()
                self.arrange_docs(mapping)
                with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                        return_value=(None, None, error)) as download:
                    self.images.archive_image(mapping.name)
                download.assert_called_once()
                self.assertEqual(mapping.image_sync_status, "失败")
                self.assertIsNone(mapping.archived_image_source)
        self.file_manager.save_file.assert_not_called()

    def test_primary_success_does_not_request_alternates(self):
        mapping = self.candidate_mapping()
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                return_value=(b"photo", "jpg", "")) as download:
            self.images.archive_image(mapping.name)
        download.assert_called_once()
        self.assertEqual(mapping.archived_image_source, mapping.source_image)

    def test_unapproved_host_is_not_requested_even_after_primary_missing(self):
        mapping = self.mapping()
        mapping.source_image_candidates = self.images.source_image_candidates_from_sku(mapping, {
            "sku_id": "SKU-1", "pic": "https://unknown.example.com/original.jpg"})
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                return_value=(None, None, "图片服务器返回 HTTP 404，已停止下载。")) as download:
            self.images.archive_image(mapping.name)
        download.assert_called_once()
        self.assertIn("不在允许范围", mapping.image_sync_error)
        self.file_manager.save_file.assert_not_called()

    def test_stale_candidate_or_changed_order_does_not_attach_download(self):
        mapping = self.candidate_mapping()
        newer = self.candidate_mapping()
        newer.source_image_candidates = self.images.source_image_candidates_from_sku(newer, {
            "sku_id": "SKU-1", "pic": "https://cdn.example.com/new.jpg"})
        self.arrange_docs(mapping, newer)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                return_value=(b"photo", "jpg", "")):
            self.images.archive_image(mapping.name)
        self.file_manager.save_file.assert_not_called()
        mapping = self.mapping()
        self.arrange_docs(mapping)
        self.frappe.db.sql.side_effect = [
            [{"line_name": "R", "order_name": "O", "external_line_id": "L",
              "image_url": "https://cdn.example.com/before.jpg"}],
            [{"line_name": "R", "order_name": "O", "external_line_id": "L",
              "image_url": "https://cdn.example.com/after.jpg"}]]
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                return_value=(b"photo", "jpg", "")):
            self.images.archive_image(mapping.name)
        self.file_manager.save_file.assert_not_called()

    def test_manual_image_survives_fallback_and_is_locked_before_comparison(self):
        mapping = self.candidate_mapping()
        self.item.image = "/files/manual.jpg"
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes", side_effect=[
                (None, None, "图片服务器返回 HTTP 404，已停止下载。"), (b"photo", "jpg", "")]):
            self.images.archive_image(mapping.name)
        self.frappe.db.set_value.assert_not_called()
        self.frappe.get_doc.assert_any_call("Item", "ITEM-1", for_update=True)
        self.assertIn("保留了人工", mapping.image_sync_error)

    def test_saved_order_url_is_not_used_after_row_is_removed(self):
        mapping = self.mapping()
        mapping.source_image_candidates = json.dumps({"version": 1, "scope": "scope", "sku_id": "SKU-1",
            "sources": [{"kind": "order", "sku_id": "SKU-1", "field": "image_url", "url": "https://cdn.example.com/removed.jpg",
                         "order_name": "O", "line_name": "R", "external_line_id": "L"}]})
        self.arrange_docs(mapping)
        with self.no_proxy_environment(), patch.object(self.images, "_validated_image_bytes",
                return_value=(None, None, "图片服务器返回 HTTP 404，已停止下载。")) as download:
            self.images.archive_image(mapping.name)
        download.assert_called_once()
        self.assertEqual(json.loads(mapping.source_image_candidates)["sources"], [])

    def test_builder_is_exact_sku_and_parser_rejects_scope_and_size_mismatches(self):
        mapping = self.mapping()
        with self.assertRaises(ValueError):
            self.images.source_image_candidates_from_sku(mapping, {"sku_id": "OTHER", "pic": "https://cdn.example.com/other.jpg"})
        data = json.loads(self.images.source_image_candidates_from_sku(mapping, {"sku_id": "SKU-1", "pic": "https://cdn.example.com/a.jpg"}))
        data["scope"] = "OTHER"
        with self.assertRaises(ValueError):
            self.images.validate_source_image_candidates(mapping, json.dumps(data))
        with self.assertRaises(ValueError):
            self.images.validate_source_image_candidates(mapping, " " * 16385)

    def test_scope_restores_nested_value_and_exception_and_schema_missing_stops(self):
        prior = {"mapping_name": "outer", "fields": ["source_image_candidates"]}
        self.frappe.flags.jifeng_image_metadata_write = prior
        with self.assertRaises(RuntimeError):
            with self.images.image_metadata_write("inner", ["archived_image_source"]):
                self.assertEqual(self.frappe.flags.jifeng_image_metadata_write["mapping_name"], "inner")
                raise RuntimeError("save failed")
        self.assertIs(self.frappe.flags.jifeng_image_metadata_write, prior)
        self.frappe.get_meta.return_value.has_field = lambda field: field != "archived_image_source"
        with self.assertRaisesRegex(RuntimeError, "尚未安装"):
            self.images.archive_image("mapping-1")
        self.frappe.get_doc.assert_not_called()

    def test_frappe_dict_scope_removes_absent_key_and_restores_nested_scope(self):
        class FrappeDict(dict):
            __getattr__ = dict.get

        self.frappe.flags = FrappeDict()
        key = "jifeng_image_metadata_write"
        with self.images.image_metadata_write("outer", ["source_image_candidates"]):
            outer = self.frappe.flags[key]
            with self.assertRaises(RuntimeError):
                with self.images.image_metadata_write("inner", ["archived_image_source"]):
                    self.assertEqual(self.frappe.flags[key]["mapping_name"], "inner")
                    raise RuntimeError("save failed")
            self.assertIs(self.frappe.flags[key], outer)
        self.assertNotIn(key, self.frappe.flags)
        prior = {"mapping_name": "prior", "fields": ["source_image_candidates"]}
        self.frappe.flags[key] = prior
        with self.images.image_metadata_write("new", ["archived_image_source"]):
            self.assertEqual(self.frappe.flags[key]["mapping_name"], "new")
        self.assertIs(self.frappe.flags[key], prior)


if __name__ == "__main__":
    unittest.main()
