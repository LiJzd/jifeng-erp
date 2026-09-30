"""Verify production pictures never bypass source or native attachment access."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestProductionPicture(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.has_permission = Mock(return_value=True)
        self.sources = {
            "Jifeng Flow Order": [Record(name="FLOW-1", item_code="RING", image="")],
            "Item": [Record(name="RING", image="")],
        }
        self.files = {}
        self.frappe.get_list = Mock(side_effect=lambda doctype, **kwargs: self.sources[doctype])
        self.frappe.get_all = Mock(side_effect=lambda doctype, **kwargs: self.files.get(kwargs["filters"]["file_url"], []))
        self.documents = {}
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents.get(
            (doctype, name), types.SimpleNamespace(has_permission=lambda permission: True)))
        utils = types.ModuleType("frappe.utils")
        utils.get_url = lambda: "https://erp.example.com"
        production = types.ModuleType("jifeng_erp.production")
        self.rows = [Record(name="WO-1", jf_flow_order="FLOW-1", production_item="RING", stock_uom="Nos", qty=5)]
        production.work_order_progress = Mock(side_effect=lambda filters: ([{"fieldname": "qty"}], self.rows))
        production.column = lambda fieldname, label, **kwargs: {"fieldname": fieldname, "label": label, **kwargs}
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "production_picture.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                      "jifeng_erp.production": production}):
            spec = importlib.util.spec_from_file_location("production_picture_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.work_order_progress = production.work_order_progress

    def add_file(self, url, name="FILE-1", allowed=True, private=False):
        self.files[url] = [name]
        self.documents[("File", name)] = types.SimpleNamespace(
            is_downloadable=Mock(return_value=allowed),
            unique_url=url + "?fid=" + name if private else url,
        )

    def test_missing_picture_is_explicit_and_keeps_units(self):
        self.rows.append(Record(name="WO-2", production_item="SILVER", stock_uom="Gram", qty=20))
        self.sources["Item"].append(Record(name="SILVER", image=""))
        filters = {"company": "JF", "only_overdue": 1}
        columns, rows = self.module.production_picture(filters)
        self.work_order_progress.assert_called_once_with(filters)
        self.assertEqual([row["image_status"] for row in rows], ["未上传", "未上传"])
        self.assertEqual([(row["qty"], row["stock_uom"]) for row in rows], [(5, "Nos"), (20, "Gram")])
        self.assertEqual(columns[0]["fieldname"], "image_url")

    def test_private_file_denied_url_is_not_returned(self):
        url = "/private/files/private-ring.png"
        self.sources["Jifeng Flow Order"][0]["image"] = url
        self.add_file(url, allowed=False, private=True)
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_url"], "")
        self.assertEqual(rows[0]["image_status"], "无附件权限")
        self.assertNotIn(url, repr(rows))

    def test_authorized_private_file_uses_native_unique_url(self):
        url = "/private/files/private-ring.png"
        self.sources["Jifeng Flow Order"][0]["image"] = url
        self.add_file(url, allowed=True, private=True)
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_url"], url + "?fid=FILE-1")
        self.assertEqual(rows[0]["image_source"], "流程单")
        self.documents[("File", "FILE-1")].is_downloadable.assert_called_once_with()

    def test_denied_flow_picture_can_fall_back_to_readable_item_picture(self):
        self.sources["Jifeng Flow Order"][0]["image"] = "/private/files/private-ring.png"
        self.add_file("/private/files/private-ring.png", allowed=False, private=True)
        self.sources["Item"][0]["image"] = "/files/public-ring.png"
        self.add_file("/files/public-ring.png", name="PUBLIC-FILE")
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_url"], "/files/public-ring.png")
        self.assertEqual(rows[0]["image_source"], "商品档案")

    def test_unreadable_source_never_looks_up_attachment(self):
        self.sources["Jifeng Flow Order"][0]["image"] = "/private/files/private-ring.png"
        self.sources["Item"][0]["image"] = "https://cdn.example.com/ring.png"
        self.frappe.has_permission.return_value = False
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_status"], "无图片来源权限")
        self.frappe.get_all.assert_not_called()

    def test_document_read_permission_still_applies_after_list_query(self):
        self.sources["Jifeng Flow Order"][0]["image"] = "/private/files/private-ring.png"
        self.documents[("Jifeng Flow Order", "FLOW-1")] = types.SimpleNamespace(has_permission=lambda permission: False)
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_url"], "")
        self.frappe.get_all.assert_not_called()

    def test_flow_picture_of_another_item_is_ignored(self):
        self.sources["Jifeng Flow Order"][0].update(item_code="OTHER", image="/private/files/other.png")
        _, rows = self.module.production_picture()
        self.assertEqual(rows[0]["image_url"], "")
        self.frappe.get_all.assert_not_called()

    def test_local_image_requires_file_record_and_same_origin_is_normalized(self):
        self.assertEqual(self.module._readable_image("/files/unknown.png"), ("", "附件记录不存在"))
        self.add_file("/files/known.png")
        self.assertEqual(self.module._readable_image("https://erp.example.com/files/known.png?old=1"),
                         ("/files/known.png", "已上传"))
        self.assertEqual(self.module._readable_image("https://erp.example.com:443/files/known.png"),
                         ("/files/known.png", "已上传"))

    def test_external_https_source_is_supported_without_network_request(self):
        self.assertEqual(self.module._readable_image("https://cdn.example.com/ring.png?size=100"),
                         ("https://cdn.example.com/ring.png?size=100", "已上传"))

    def test_external_file_record_permission_cannot_be_bypassed(self):
        url = "https://cdn.example.com/ring.png"
        self.add_file(url, allowed=False)
        self.assertEqual(self.module._readable_image(url), ("", "无附件权限"))

    def test_unsafe_urls_are_rejected_before_attachment_lookup(self):
        urls = ["javascript:alert(1)", "data:image/png;base64,AA", "//cdn.example.com/a.png",
                "https://user:password@cdn.example.com/a.png", "http://cdn.example.com/a.png",
                "https://cdn.example.com/a.png\n", "https://127.0.0.1/a.png", "https://[::1]/a.png",
                "https://10.0.0.1/a.png", "https://localhost/a.png", "https://localhost./a.png",
                "https://127.1/a.png", "https://2130706433/a.png", "https://0x7f000001/a.png",
                "https://0x7f.0x0.0x0.0x1/a.png",
                "https://erp.internal/a.png", "https://cdn.example.com/%70rivate/files/a.png",
                "https://cdn.example.com/private/files/a.png", "https://cdn.example.com:bad/a.png",
                "/files/../x.png", "/files/%2e%2e%2fx.png", "/files/%5cx.png", "/files/%00x.png",
                "/files/%252e%252e%252fx.png",
                "/files/x.png#fragment", "/other/x.png"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.module._readable_image(url), ("", "图片地址无效"))
        self.frappe.get_all.assert_not_called()

    def test_duplicate_image_uses_request_local_permission_cache(self):
        self.sources["Jifeng Flow Order"][0]["image"] = "/files/ring.png"
        self.add_file("/files/ring.png")
        self.rows.append(Record(name="WO-2", jf_flow_order="FLOW-1", production_item="RING", stock_uom="Nos", qty=2))
        self.module.production_picture()
        self.frappe.get_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
