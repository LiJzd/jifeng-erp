"""Exercise real page/checkpoint logic with a fake database boundary."""
import importlib.util
import pathlib
import sys
import types
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from unittest.mock import Mock, patch


class Settings(types.SimpleNamespace):
    def db_set(self, field, value):
        setattr(self, field, value)


class TestSyncCheckpoints(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        def whitelist(*, methods=None):
            def decorate(fn):
                fn.allowed_http_methods = methods
                return fn
            return decorate
        self.frappe.whitelist = whitelist
        self.frappe.db = Mock()
        self.frappe.db.exists.return_value = True
        self.frappe.enqueue = Mock()
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
        self.upsert_sku = self.sync.upsert_sku
        self.upsert_order_impl = self.sync.upsert_order
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

    def test_initial_authorization_saves_only_validated_token_pair(self):
        settings = Settings(app_type="自有商城", app_key="public-key", environment="测试",
                            access_token="", refresh_token="", token_expires_at=None)
        settings.get_password = lambda field, **kwargs: "app-secret" if field == "app_secret" else None
        settings.save = Mock()
        self.frappe.only_for = Mock()
        self.frappe.cache = types.SimpleNamespace(lock=lambda *args, **kwargs: nullcontext())
        self.frappe.get_single = Mock(return_value=settings)
        self.frappe.throw = lambda message: (_ for _ in ()).throw(RuntimeError(message))
        timestamp = datetime(2026, 10, 8, 10, 0, 0)
        self.sync.now_datetime = lambda: timestamp
        fake_client = Mock()
        fake_client.get_initial_token.return_value = {
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600,
        }
        self.sync.Client = Mock(return_value=fake_client)

        result = self.sync.acquire_initial_authorization()

        self.assertIn("已加密保存", result)
        self.assertEqual(settings.access_token, "new-access")
        self.assertEqual(settings.refresh_token, "new-refresh")
        self.assertEqual(settings.token_expires_at, timestamp + timedelta(seconds=3600))
        settings.save.assert_called_once_with()
        self.frappe.only_for.assert_called_once_with("System Manager")

    def test_token_acquisition_and_rotation_are_post_only(self):
        self.assertEqual(self.sync.acquire_initial_authorization.allowed_http_methods, ["POST"])
        self.assertEqual(self.sync.refresh_authorization.allowed_http_methods, ["POST"])

    def test_initial_authorization_rejects_provider_app_without_saving(self):
        settings = Settings(app_type="服务商", app_key="public-key", environment="测试",
                            access_token="old-access", refresh_token="old-refresh")
        self.frappe.only_for = Mock()
        self.frappe.cache = types.SimpleNamespace(lock=lambda *args, **kwargs: nullcontext())
        self.frappe.get_single = Mock(return_value=settings)
        self.frappe.throw = lambda message: (_ for _ in ()).throw(RuntimeError(message))
        self.sync.Client = Mock()
        with self.assertRaisesRegex(RuntimeError, "自有商城"):
            self.sync.acquire_initial_authorization()
        self.sync.Client.assert_not_called()

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

    def test_new_sku_keeps_remote_image_out_of_public_item_until_archived(self):
        class Doc:
            def __init__(self, values=None):
                self.__dict__.update(values or {})
                self.flags = types.SimpleNamespace()
                self.name = getattr(self, "item_code", None) or getattr(self, "uom_name", None) or "mapping"

            def get(self, fieldname):
                return getattr(self, fieldname, None)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def update(self, values):
                self.__dict__.update(values)

            def insert(self, **kwargs):
                inserted.append(self)
                return self

            def save(self, **kwargs):
                self.save_calls = getattr(self, "save_calls", 0) + 1
                return self

        inserted = []
        mapping = Doc({"source_image": None, "archived_image": None, "managed_item": 0,
                       "supplier_sku_id": None, "supplier_name": None, "source_modified": None,
                       "source_specification": None, "image_sync_status": "未配置",
                       "image_sync_error": "", "image_sync_attempted_at": None})
        settings = self.settings(item_group="Products", stock_uom="Nos", company="QA",
                                 image_hosts="cdn.example.com")
        self.frappe.db.exists.side_effect = lambda doctype, value=None: doctype == "UOM"
        self.frappe.new_doc = Mock(return_value=mapping)
        self.frappe.get_doc = Mock(side_effect=lambda values: Doc(values) if isinstance(values, dict) else None)
        self.frappe.utils = types.SimpleNamespace(escape_html=lambda value: value)
        self.frappe.enqueue = Mock()

        item_code = self.upsert_sku(settings, {
            "sku_id": "SKU-1", "name": "商品1", "unit": "Nos", "pic": "https://cdn.example.com/1.jpg",
        })

        item = next(doc for doc in inserted if getattr(doc, "doctype", None) == "Item")
        self.assertEqual(item_code, "SKU-1")
        self.assertEqual(item.image, "")
        self.assertEqual(mapping.source_image, "https://cdn.example.com/1.jpg")
        self.assertEqual(mapping.image_sync_status, "待处理")
        self.assertIsNone(mapping.archived_image)
        self.frappe.enqueue.assert_called_once_with(
            "jifeng_erp.images.archive_image", mapping_name="mapping", queue="long", timeout=120,
            enqueue_after_commit=True)

    def test_changed_sku_image_clears_stale_sync_image_but_preserves_manual_item_image(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def get(self, fieldname):
                return getattr(self, fieldname, None)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def save(self, **kwargs):
                self.save_calls = getattr(self, "save_calls", 0) + 1
                return self

        mapping = Doc(name="mapping", managed_item=1, item_code="SKU-1", source_image="https://cdn.example.com/old.jpg",
                      archived_image="/files/old.jpg", supplier_sku_id="SUP-1", supplier_name="Vendor",
                      source_modified=None, source_specification="蓝色", image_sync_status="已完成",
                      image_sync_error="", image_sync_attempted_at=None)
        item = Doc(name="SKU-1", item_name="旧名称", image="/files/old.jpg")
        profile = Doc(name="profile-1", company="QA", supplier_product_code="SUP-1", specification="蓝色",
                      material="保留人工材质")
        settings = self.settings(company="QA", image_hosts="cdn.example.com")
        self.frappe.db.exists.return_value = True
        self.frappe.get_doc = Mock(side_effect=[mapping, item, profile])
        self.frappe.enqueue = Mock()

        self.upsert_sku(settings, {"sku_id": "SKU-1", "name": "新名称", "supplier_sku_id": "SUP-2",
                                   "properties_value": "黑色", "pic_big": "https://cdn.example.com/new.jpg"})

        self.assertEqual(item.item_name, "新名称")
        self.assertEqual(item.save_calls, 1)
        self.assertEqual(profile.supplier_product_code, "SUP-2")
        self.assertEqual(profile.specification, "黑色")
        self.assertEqual(profile.material, "保留人工材质")
        self.assertEqual(item.image, "/files/old.jpg")
        self.assertEqual(mapping.source_image, "https://cdn.example.com/new.jpg")
        self.assertEqual(mapping.archived_image, "/files/old.jpg")
        self.assertEqual(mapping.image_sync_status, "待处理")
        self.frappe.enqueue.assert_called_once()

        item.image = "/files/manual.jpg"
        self.frappe.get_doc.side_effect = [mapping, item, profile]
        self.frappe.enqueue.reset_mock()
        self.upsert_sku(settings, {"sku_id": "SKU-1", "pic_big": "https://cdn.example.com/next.jpg"})
        self.assertEqual(item.image, "/files/manual.jpg")
        self.frappe.enqueue.assert_called_once()

        # Legacy remote URLs must never be retained as the visible image.
        item.image = "https://cdn.example.com/unarchived.jpg"
        self.frappe.get_doc.side_effect = [mapping, item, profile]
        self.frappe.enqueue.reset_mock()
        self.upsert_sku(settings, {"sku_id": "SKU-1", "supplier_sku_id": "SUP-3",
                                   "pic_big": "https://cdn.example.com/last.jpg"})
        self.assertEqual(item.image, "")
        self.frappe.enqueue.assert_called_once()

        mapping.archived_image = "/files/last.jpg"
        item.image = "/files/last.jpg"
        self.frappe.get_doc.side_effect = [mapping, item, profile]
        self.frappe.enqueue.reset_mock()
        self.upsert_sku(settings, {"sku_id": "SKU-1", "pic": ""})
        self.assertIsNone(mapping.source_image)
        self.assertEqual(mapping.image_sync_status, "未配置")
        self.assertEqual(mapping.archived_image, "/files/last.jpg")
        self.assertEqual(item.image, "/files/last.jpg")
        self.frappe.enqueue.assert_not_called()

        # Invalid replacement sources also leave the last verified attachment
        # available while the mapping reports the current source failure.
        self.frappe.get_doc.side_effect = [mapping, item, profile]
        self.upsert_sku(settings, {"sku_id": "SKU-1", "pic_big": "https://localhost/image.jpg"})
        self.assertEqual(mapping.image_sync_status, "失败")
        self.assertEqual(mapping.archived_image, "/files/last.jpg")
        self.assertEqual(item.image, "/files/last.jpg")

    def test_unmanaged_mapping_never_reads_or_mutates_existing_item(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def save(self, **kwargs):
                return self

        mapping = Doc(name="mapping", managed_item=0, item_code="MANUAL-ITEM",
                      source_image="https://cdn.example.com/old.jpg", archived_image="/files/old.jpg",
                      supplier_sku_id="SUP-1", supplier_name=None, source_modified=None,
                      source_specification="旧规格", image_sync_status="已完成", image_sync_error="",
                      image_sync_attempted_at=None)
        profile = Doc(name="profile", company="QA", supplier_product_code="SUP-1", specification="旧规格")
        self.frappe.db.exists.side_effect = lambda doctype, filters=None: {
            "Jifeng SKU Mapping": "mapping", "Jifeng Product Profile": "profile"}.get(doctype)
        self.frappe.get_doc = Mock(side_effect=[mapping, profile])
        settings = self.settings(company="QA", image_hosts="cdn.example.com")

        self.upsert_sku(settings, {"sku_id": "SKU-1", "name": "来源名称",
                                   "pic_big": "https://cdn.example.com/new.jpg"})

        self.assertEqual(mapping.image_sync_status, "待处理")
        self.assertEqual(mapping.archived_image, "/files/old.jpg")
        self.assertEqual([call.args[0] for call in self.frappe.get_doc.call_args_list],
                         ["Jifeng SKU Mapping", "Jifeng Product Profile"])
        self.frappe.enqueue.assert_called_once()

    def test_same_source_pending_state_is_not_marked_complete_from_old_archive(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def save(self, **kwargs):
                return self

        mapping = Doc(name="mapping", managed_item=0, item_code="SKU-1",
                      source_image="https://cdn.example.com/current.jpg", archived_image="/files/old.jpg",
                      supplier_sku_id=None, supplier_name=None, source_modified=None,
                      source_specification=None, image_sync_status="待处理", image_sync_error="",
                      image_sync_attempted_at=None)
        profile = Doc(name="profile", company="QA", supplier_product_code="", specification="")
        self.frappe.db.exists.side_effect = lambda doctype, filters=None: {
            "Jifeng SKU Mapping": "mapping", "Jifeng Product Profile": "profile"}.get(doctype)
        self.frappe.get_doc = Mock(side_effect=[mapping, profile])

        self.upsert_sku(self.settings(company="QA", image_hosts="cdn.example.com"), {
            "sku_id": "SKU-1", "pic_big": "https://cdn.example.com/current.jpg",
        })

        self.assertEqual(mapping.image_sync_status, "待处理")
        self.assertEqual(mapping.archived_image, "/files/old.jpg")
        self.frappe.enqueue.assert_called_once()

        self.frappe.get_doc.side_effect = [mapping, profile]
        self.upsert_sku(self.settings(company="QA", image_hosts="cdn.example.com"), {
            "sku_id": "SKU-1", "pic_big": "https://cdn.example.com/current.jpg",
        })
        self.assertEqual(mapping.image_sync_status, "待处理")
        self.assertEqual(mapping.archived_image, "/files/old.jpg")
        self.assertEqual(self.frappe.enqueue.call_count, 2)

    def test_legacy_default_status_with_source_and_archive_is_migrated_to_complete(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def save(self, **kwargs):
                return self

        mapping = Doc(name="mapping", managed_item=0, item_code="SKU-1",
                      source_image="https://cdn.example.com/current.jpg", archived_image="/files/current.jpg",
                      supplier_sku_id=None, supplier_name=None, source_modified=None,
                      source_specification=None, image_sync_status="未配置", image_sync_error="",
                      image_sync_attempted_at=None)
        profile = Doc(name="profile", company="QA", supplier_product_code="", specification="")
        self.frappe.db.exists.side_effect = lambda doctype, filters=None: {
            "Jifeng SKU Mapping": "mapping", "Jifeng Product Profile": "profile"}.get(doctype)
        self.frappe.get_doc = Mock(side_effect=[mapping, profile])

        self.upsert_sku(self.settings(company="QA", image_hosts="cdn.example.com"), {
            "sku_id": "SKU-1", "pic_big": "https://cdn.example.com/current.jpg",
        })

        self.assertEqual(mapping.image_sync_status, "已完成")
        self.assertEqual(mapping.archived_image, "/files/current.jpg")
        self.frappe.enqueue.assert_not_called()

    def test_order_fallback_without_sku_details_preserves_source_baseline_then_full_sku_updates(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def get(self, fieldname):
                return getattr(self, fieldname, None)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def save(self, **kwargs):
                return self

        mapping = Doc(name="mapping", managed_item=0, item_code="SKU-1", source_image=None,
                      archived_image=None, supplier_sku_id="SUP-OLD", supplier_name="Supplier",
                      source_modified=None, source_specification="旧规格", image_sync_status="未配置",
                      image_sync_error="", image_sync_attempted_at=None)
        profile = Doc(name="profile", company="QA", supplier_product_code="SUP-OLD", specification="旧规格",
                      material="人工材质")
        settings = self.settings(company="QA", image_hosts="")

        def exists(doctype, filters=None):
            return {"Jifeng SKU Mapping": "mapping", "Jifeng Product Profile": "profile"}.get(doctype)

        self.frappe.db.exists.side_effect = exists
        self.frappe.get_doc = Mock()
        self.frappe.get_doc.side_effect = [mapping, profile, mapping, profile]

        self.upsert_sku(settings, {"sku_id": "SKU-1", "name": "订单补查商品", "pic": ""})
        self.assertEqual(profile.supplier_product_code, "SUP-OLD")
        self.assertEqual(profile.specification, "旧规格")
        self.assertEqual(profile.material, "人工材质")
        self.assertEqual(mapping.source_specification, "旧规格")

        # An explicitly blank source specification is no more authoritative
        # than an omitted field: keep the source baseline and managed profile.
        self.frappe.get_doc.side_effect = [mapping, profile]
        self.upsert_sku(settings, {"sku_id": "SKU-1", "properties_value": ""})
        self.assertEqual(profile.specification, "旧规格")
        self.assertEqual(mapping.source_specification, "旧规格")

        self.frappe.get_doc.side_effect = [mapping, profile]
        self.upsert_sku(settings, {"sku_id": "SKU-1", "name": "商品完整来源", "supplier_sku_id": "SUP-NEW",
                                   "properties_value": "新规格"})
        self.assertEqual(profile.supplier_product_code, "SUP-NEW")
        self.assertEqual(profile.specification, "新规格")
        self.assertEqual(profile.material, "人工材质")
        self.assertEqual(mapping.source_specification, "新规格")

    def test_order_mapping_fallback_sends_only_observed_fields_to_sku_sync(self):
        class Doc:
            def __init__(self):
                self.flags = types.SimpleNamespace()
                self.items = []
                self.source_cursor = 0

            def update(self, values):
                self.__dict__.update(values)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def append(self, fieldname, value):
                getattr(self, fieldname).append(types.SimpleNamespace(**value))

            def save(self, **kwargs):
                return self

        settings = self.settings(company="QA", auto_create_flows=False, flow_statuses="")
        order = Doc()
        self.frappe.db.exists.return_value = True
        self.frappe.get_doc = Mock()
        self.frappe.get_doc.return_value = order
        self.frappe.db.get_value.side_effect = [None, None]
        self.frappe.get_all = Mock(return_value=[])
        self.sync.upsert_sku = Mock(return_value="SKU-1")

        self.upsert_order_impl(settings, {
            "external_order_id": "ORDER-1", "source_cursor": 1, "source_status": "待处理",
            "items": [{"sku_id": "SKU-1", "item_name": "订单商品", "image_url": "", "qty": 1,
                       "external_line_id": "LINE-1", "source_status": "待处理"}],
        })

        self.sync.upsert_sku.assert_called_once_with(settings, {
            "sku_id": "SKU-1", "name": "订单商品", "pic": "",
        })

    def test_order_refresh_preserves_manual_line_requirements_and_packaging(self):
        class Child:
            def __init__(self, **values):
                self.__dict__.update(values)

            def get(self, fieldname):
                return getattr(self, fieldname, None)

        class Order:
            def __init__(self):
                self.source_cursor = 1
                self.items = [Child(external_line_id="LINE-1", jf_requirements="手工定制",
                                    jf_packaging="手工包装")]
                self.flags = types.SimpleNamespace()

            def update(self, values):
                self.__dict__.update(values)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def append(self, fieldname, values):
                getattr(self, fieldname).append(Child(**values))

            def save(self, **kwargs):
                return self

        order = Order()
        self.frappe.db.exists.return_value = True
        self.frappe.get_doc = Mock(return_value=order)
        self.frappe.db.get_value.side_effect = ["SKU-ITEM", None]
        self.frappe.get_all = Mock(return_value=[])
        self.sync.upsert_sku = Mock()

        self.upsert_order_impl(self.settings(company="QA", auto_create_flows=False, flow_statuses=""), {
            "external_order_id": "ORDER-1", "source_cursor": 2, "source_status": "待处理",
            "items": [{"sku_id": "SKU-1", "item_name": "源商品", "image_url": "", "qty": 1,
                       "external_line_id": "LINE-1", "source_status": "待处理",
                       "jf_requirements": "平台不应覆盖", "jf_packaging": "平台不应覆盖"}],
        })

        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].jf_requirements, "手工定制")
        self.assertEqual(order.items[0].jf_packaging, "手工包装")

    def test_sku_sync_refuses_profile_owned_by_another_company(self):
        class Doc:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace()

            def save(self, **kwargs):
                return self

        mapping = Doc(name="mapping", managed_item=0, item_code="SKU-1", source_image=None,
                      archived_image=None, supplier_sku_id="SUP-OLD", supplier_name=None,
                      source_modified=None, source_specification="旧规格", image_sync_status="未配置",
                      image_sync_error="", image_sync_attempted_at=None)
        profile = Doc(name="profile", company="OTHER", supplier_product_code="SUP-OLD",
                      specification="旧规格")
        self.frappe.db.exists.side_effect = lambda doctype, filters=None: {
            "Jifeng SKU Mapping": "mapping", "Jifeng Product Profile": "profile"}.get(doctype)
        self.frappe.get_doc = Mock(side_effect=[mapping, profile])

        with self.assertRaisesRegex(self.sync.JushuitanError, "公司与当前同步公司不一致"):
            self.upsert_sku(self.settings(company="QA"), {
                "sku_id": "SKU-1", "supplier_sku_id": "SUP-NEW", "properties_value": "新规格",
            })

        self.assertEqual(profile.supplier_product_code, "SUP-OLD")
        self.assertEqual(profile.specification, "旧规格")


class TestSkuProfileSyncPolicy(unittest.TestCase):
    def setUp(self):
        self.sync_case = TestSyncCheckpoints()
        self.sync_case.setUp()

    def test_previous_source_value_updates_but_manual_value_is_preserved(self):
        class Profile:
            def __init__(self, value):
                self.value = value

            def get(self, fieldname):
                return self.value

            def set(self, fieldname, value):
                self.value = value

        profile = Profile("SUP-OLD")
        self.sync_case.sync._sync_profile_source_value(profile, "supplier_product_code", "SUP-OLD", "SUP-NEW")
        self.assertEqual(profile.value, "SUP-NEW")

        profile.value = "人工货号"
        self.sync_case.sync._sync_profile_source_value(profile, "supplier_product_code", "SUP-OLD", "SUP-NEW")
        self.assertEqual(profile.value, "人工货号")

    def test_missing_legacy_baseline_only_fills_blank_profile_value(self):
        class Profile:
            def __init__(self, value):
                self.value = value

            def get(self, fieldname):
                return self.value

            def set(self, fieldname, value):
                self.value = value

        blank = Profile("")
        self.sync_case.sync._sync_profile_source_value(blank, "specification", None, "新规格")
        self.assertEqual(blank.value, "新规格")

        manual = Profile("人工规格")
        self.sync_case.sync._sync_profile_source_value(manual, "specification", None, "新规格")
        self.assertEqual(manual.value, "人工规格")


class TestImageRetryLifecycle(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.cache = types.SimpleNamespace(lock=lambda *args, **kwargs: nullcontext())
        self.frappe.get_doc = Mock()
        self.frappe.get_single = Mock()
        self.frappe.db = Mock()
        self.frappe.conf = {}
        self.frappe.enqueue = Mock()
        self.frappe.throw = lambda message: (_ for _ in ()).throw(RuntimeError(message))

        requests = types.ModuleType("requests")
        requests.RequestException = type("RequestException", (Exception,), {})
        requests.Session = Mock()
        pil = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        image_module.DecompressionBombError = type("DecompressionBombError", (Exception,), {})
        image_module.open = Mock()
        pil.Image = image_module
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = datetime.now
        utils.get_datetime = lambda value: value if isinstance(value, datetime) else datetime.fromisoformat(value)
        file_manager = types.ModuleType("frappe.utils.file_manager")
        file_manager.save_file = Mock()

        modules = {"frappe": self.frappe, "requests": requests, "PIL": pil, "PIL.Image": image_module,
                   "frappe.utils": utils, "frappe.utils.file_manager": file_manager}
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                "images_under_test", pathlib.Path(__file__).parents[1] / "jifeng_erp" / "images.py")
            self.images = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.images)
        self.file_manager = file_manager

    def mapping(self, **overrides):
        values = {"name": "mapping-1", "scope": "scope", "sku_id": "SKU-1", "item_code": "ITEM-1",
                  "managed_item": 1, "source_image": "https://cdn.example.com/image.jpg",
                  "archived_image": None, "image_sync_status": "待处理", "image_sync_error": "",
                  "image_sync_attempted_at": None}
        values.update(overrides)

        class Mapping(types.SimpleNamespace):
            def save(self, **kwargs):
                return self

            def check_permission(self, permission):
                if getattr(self, "deny_write", False):
                    raise PermissionError("write denied")

        return Mapping(**values)

    def settings(self):
        return types.SimpleNamespace(sync_scope="scope", enabled=True, image_hosts="cdn.example.com")

    def no_proxy_environment(self):
        return patch.dict("os.environ", {key: "" for key in self.images.PROXY_ENV_VARS})

    def test_unverified_direct_egress_fails_closed_without_network_request(self):
        mapping = self.mapping()
        self.frappe.get_doc.side_effect = [mapping, mapping]
        self.frappe.get_single.return_value = self.settings()

        self.images.archive_image("mapping-1")

        self.assertEqual(mapping.image_sync_status, "失败")
        self.assertIn("尚未核实为直连", mapping.image_sync_error)
        self.assertNotIn("cdn.example.com", mapping.image_sync_error)
        self.assertFalse(self.images.requests.Session.called)

    def test_proxy_environment_fails_closed_even_with_direct_confirmation(self):
        mapping = self.mapping()
        self.frappe.get_doc.side_effect = [mapping, mapping]
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {"jifeng_image_direct_confirmed": True}

        with self.no_proxy_environment(), patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.invalid"}):
            self.images.archive_image("mapping-1")

        self.assertEqual(mapping.image_sync_status, "失败")
        self.assertIn("配置了代理", mapping.image_sync_error)
        self.assertNotIn("proxy.invalid", mapping.image_sync_error)
        self.assertFalse(self.images.requests.Session.called)

    def test_stale_worker_does_not_attach_image_after_source_changes(self):
        mapping = self.mapping()
        newer = self.mapping(source_image="https://cdn.example.com/new.jpg")
        self.frappe.get_doc.side_effect = [mapping, newer]
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {"jifeng_image_direct_confirmed": True}

        with self.no_proxy_environment(), patch.object(
                self.images, "_validated_image_bytes", return_value=(b"image", "jpg", "")):
            self.images.archive_image("mapping-1")

        self.file_manager.save_file.assert_not_called()
        self.frappe.db.set_value.assert_not_called()
        self.assertEqual(newer.source_image, "https://cdn.example.com/new.jpg")

    def test_worker_does_not_replace_manual_item_image_changed_after_queueing(self):
        mapping = self.mapping(archived_image="/files/previous-source.jpg")
        self.frappe.get_doc.side_effect = [mapping, mapping, types.SimpleNamespace(image="/files/manual-after-queue.jpg")]
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {"jifeng_image_direct_confirmed": True}
        self.file_manager.save_file.return_value = types.SimpleNamespace(file_url="/files/new-source.jpg")

        with self.no_proxy_environment(), patch.object(
                self.images, "_validated_image_bytes", return_value=(b"image", "jpg", "")):
            self.images.archive_image("mapping-1")

        self.assertEqual(mapping.archived_image, "/files/new-source.jpg")
        self.assertEqual(mapping.image_sync_status, "已完成")
        self.assertIn("保留了人工商品图片", mapping.image_sync_error)
        self.frappe.db.set_value.assert_not_called()

    def test_stale_worker_failure_does_not_change_new_mapping_state(self):
        mapping = self.mapping()
        newer = self.mapping(source_image="https://cdn.example.com/new.jpg", image_sync_status="待处理",
                             image_sync_error="")
        self.frappe.get_doc.side_effect = [mapping, newer]
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {}

        self.images.archive_image("mapping-1")

        self.assertEqual(newer.source_image, "https://cdn.example.com/new.jpg")
        self.assertEqual(newer.image_sync_status, "待处理")
        self.assertEqual(newer.image_sync_error, "")

    def test_retry_requires_write_and_recent_pending_request_is_not_duplicated(self):
        mapping = self.mapping(image_sync_status="失败", image_sync_error="校验失败")
        self.frappe.get_doc.return_value = mapping
        self.frappe.get_single.return_value = self.settings()
        self.frappe.conf = {"jifeng_image_direct_confirmed": True}

        with self.no_proxy_environment():
            result = self.images.retry_image("mapping-1")
            repeated = self.images.retry_image("mapping-1")

        self.assertEqual(result["status"], "pending")
        self.assertEqual(repeated["status"], "pending")
        self.assertEqual(self.frappe.enqueue.call_count, 1)
        mapping.check_permission("write")

    def test_retry_denies_user_without_mapping_write_permission(self):
        mapping = self.mapping(image_sync_status="失败", deny_write=True)
        self.frappe.get_doc.return_value = mapping

        with self.assertRaises(PermissionError):
            self.images.retry_image("mapping-1")
        self.frappe.enqueue.assert_not_called()
