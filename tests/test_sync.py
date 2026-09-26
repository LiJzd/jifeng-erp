"""Exercise real page/checkpoint logic with a fake database boundary."""
import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


class Settings(types.SimpleNamespace):
    def db_set(self, field, value):
        setattr(self, field, value)


class TestSyncCheckpoints(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.whitelist = lambda: lambda fn: fn
        self.frappe.db = Mock()
        self.frappe.db.exists.return_value = True
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = lambda value: value if isinstance(value, datetime) else datetime.fromisoformat(value)
        utils.now_datetime = datetime.now
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.BLOCKED_STATUSES = {"Cancelled", "Merged", "Split"}
        workflow.create_flows = Mock()
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "jifeng_erp.workflow": workflow}):
            spec = importlib.util.spec_from_file_location("sync_under_test", pathlib.Path(__file__).parents[1] / "jifeng_erp" / "sync.py")
            self.sync = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.sync)
        self.sync.upsert_order = Mock()
        self.sync.upsert_sku = Mock()
        self.log = Mock(order_count=0, sku_count=0)

    def settings(self, **kwargs):
        return Settings(order_cursor="1", max_pages=2, merchant_id="42", sync_scope="scope",
                        sku_watermark=None, sku_begin="2026-01-01 00:00:00", sku_page=1,
                        sku_window_begin=None, sku_window_end=None, **kwargs)

    def order(self, oid, ts, merchant=42):
        return {"o_id": oid, "ts": ts, "co_id": merchant,
                "items": [{"oi_id": oid * 10, "sku_id": "A", "qty": 1}]}

    def test_inclusive_boundary_is_reprocessed_without_skipping_cursor(self):
        settings, api = self.settings(), Mock()
        api.orders.side_effect = [
            {"orders": [self.order(1, 10), self.order(2, 20)], "has_next": True},
            {"orders": [self.order(2, 20)], "has_next": False},
        ]
        self.sync.sync_orders(settings, api, self.log)
        self.assertEqual([c.args[0] for c in api.orders.call_args_list], [1, 20])
        self.assertEqual(settings.order_cursor, "20")
        self.assertEqual(self.frappe.db.commit.call_count, 2)

    def test_wrong_merchant_never_advances_checkpoint(self):
        settings, api = self.settings(), Mock()
        api.orders.return_value = {"orders": [self.order(1, 10, merchant=43)], "has_next": False}
        with self.assertRaisesRegex(self.sync.JushuitanError, "商家"):
            self.sync.sync_orders(settings, api, self.log)
        self.assertEqual(settings.order_cursor, "1")
        self.frappe.db.commit.assert_not_called()

    def test_failure_before_page_commit_does_not_advance_cursor(self):
        settings, api = self.settings(), Mock()
        api.orders.return_value = {"orders": [self.order(1, 10)], "has_next": False}
        self.sync.upsert_order.side_effect = RuntimeError("database write failed")
        with self.assertRaises(RuntimeError):
            self.sync.sync_orders(settings, api, self.log)
        self.assertEqual(settings.order_cursor, "1")
        self.frappe.db.commit.assert_not_called()

    def test_pagination_flag_is_required(self):
        with self.assertRaises(self.sync.JushuitanError):
            self.sync._page_list({"datas": []}, "datas")

    def test_sku_page_and_fixed_window_resume_after_bounded_run(self):
        settings, api = self.settings(), Mock()
        api.skus.return_value = {"datas": [{"sku_id": "A"}], "has_next": True}
        self.sync.sync_skus(settings, api, self.log)
        self.assertEqual(settings.sku_page, 3)
        begin, end = settings.sku_window_begin, settings.sku_window_end
        api.skus.reset_mock()
        self.sync.sync_skus(settings, api, self.log)
        first = api.skus.call_args_list[0].kwargs
        self.assertEqual(first["page"], 3)
        self.assertEqual(first["begin"], str(begin))
        self.assertEqual(first["end"], str(end))
        self.assertLessEqual((end - begin).days, 7)
