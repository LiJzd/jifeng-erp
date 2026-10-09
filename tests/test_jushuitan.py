import unittest
from unittest.mock import Mock

import requests

from jifeng_erp.jushuitan import Client, JushuitanError, order_snapshot, record_key, safe_image_url, sign


def response(data=None, code=0, status=200):
    value = Mock(status_code=status)
    value.json.return_value = {"code": code, "data": data or {"orders": [], "has_next": False}}
    return value


class TestJushuitan(unittest.TestCase):
    def make_client(self, values):
        session = Mock()
        session.post.side_effect = values
        return Client("test-key", "test-secret", "test-token", session=session, sleep=lambda _: None), session

    def test_official_public_signature_vector(self):
        # Public fake credentials copied from official docId=70, not account secrets.
        params = {"app_key": "5b53060f23d84ddf9703056e84fa5a2d",
                  "access_token": "d7b01bf0842a4742a9450e21ffd95f60", "timestamp": "1639128407",
                  "version": "2", "charset": "utf-8",
                  "biz": '{"page_index":"1","page_size":"100","nicks":["老板"]}'}
        self.assertEqual(sign(params, "e9c5ca33fecb404b8e6cdbd0ef4a6d25"), "395f5a78b446be465ac03a02491296c7")

    def test_sku_ids_use_comma_separated_string_and_form_post(self):
        import json
        api, session = self.make_client([response({"datas": [], "has_next": False})])
        api.skus(sku_ids=["A", "B"])
        args, kwargs = session.post.call_args
        self.assertEqual(json.loads(kwargs["data"]["biz"])["sku_ids"], "A,B")
        self.assertNotIn("params", kwargs)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(args[0], "https://dev-api.jushuitan.com/open/sku/query")

    def test_sku_queries_keep_complete_business_fields_and_native_pagination(self):
        import json

        source = {"sku_id": "SKU-A", "name": "测试手链", "unit": "Nos",
                  "supplier_sku_id": "SUP-001", "supplier_name": "测试供应商",
                  "properties_value": "银色", "modified": "2026-01-02 12:00:00",
                  "pic": "https://cdn.example.com/thumb.jpg",
                  "pic_big": "https://cdn.example.com/full.jpg"}
        for arguments, expected_business in (
            ({"sku_ids": ["SKU-A"]},
             {"sku_ids": "SKU-A", "page_index": 1, "page_size": 100}),
            ({"begin": "2026-01-01 00:00:00", "end": "2026-01-07 00:00:00", "page": 2},
             {"modified_begin": "2026-01-01 00:00:00", "modified_end": "2026-01-07 00:00:00",
              "page_index": 2, "page_size": 100}),
        ):
            with self.subTest(arguments=arguments):
                api, session = self.make_client([])

                def provider_reply(url, **kwargs):
                    business = json.loads(kwargs["data"]["biz"])
                    # The real provider omits basic product fields when the
                    # caller requests only its pics projection.
                    row = ({key: source[key] for key in ("sku_id", "pic", "pic_big")}
                           if business.get("flds") == "pics" else dict(source))
                    return response({"datas": [row], "has_next": True})

                session.post.side_effect = provider_reply
                result = api.skus(**arguments)
                self.assertEqual(result["datas"], [source])
                self.assertTrue(result["has_next"])
                self.assertEqual(json.loads(session.post.call_args.kwargs["data"]["biz"]),
                                 expected_business)

    def test_order_cursor_keeps_full_integer_precision(self):
        import json
        api, session = self.make_client([response()])
        api.orders("9007199254740999")
        biz = json.loads(session.post.call_args.kwargs["data"]["biz"])
        self.assertEqual(biz["start_ts"], 9007199254740999)
        self.assertEqual(biz["page_index"], 1)

    def test_rate_limit_is_retried_but_permission_failure_is_not(self):
        api, session = self.make_client([response(code=200), response()])
        api.orders(1)
        self.assertEqual(session.post.call_count, 2)
        api, session = self.make_client([response(code=190)])
        with self.assertRaisesRegex(JushuitanError, "权限"):
            api.orders(1)
        self.assertEqual(session.post.call_count, 1)

    def test_error_never_echoes_token_or_remote_message(self):
        bad = response(code=999)
        bad.json.return_value["msg"] = "test-token test-secret private data"
        api, _ = self.make_client([bad])
        with self.assertRaises(JushuitanError) as caught:
            api.orders(1)
        self.assertNotIn("test-token", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_token_rotation_is_not_retried_after_network_error(self):
        api, session = self.make_client([requests.Timeout()])
        with self.assertRaisesRegex(JushuitanError, "结果未知"):
            api.refresh("test-refresh")
        self.assertEqual(session.post.call_count, 1)

    def test_initial_authorization_is_explicit_single_signed_post(self):
        import re
        from unittest.mock import patch

        api, session = self.make_client([response({
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 2592000,
        })])
        api.token = None
        with patch("jifeng_erp.jushuitan.time.time", return_value=1000):
            result = api.get_initial_token()
        self.assertEqual(result["access_token"], "new-access")
        self.assertEqual(session.post.call_count, 1)
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://dev-api.jushuitan.com/openWeb/auth/getInitToken")
        params = kwargs["data"]
        self.assertEqual(params["timestamp"], "1000")
        self.assertEqual(params["grant_type"], "authorization_code")
        self.assertEqual(params["charset"], "utf-8")
        self.assertRegex(params["code"], re.compile(r"^[a-f0-9]{6}$"))
        self.assertEqual(params["sign"], sign({k: v for k, v in params.items() if k != "sign"}, "test-secret"))
        self.assertFalse(kwargs["allow_redirects"])

    def test_initial_authorization_unknown_result_is_not_retried(self):
        api, session = self.make_client([requests.Timeout()])
        with self.assertRaisesRegex(JushuitanError, "结果未知"):
            api.get_initial_token()
        self.assertEqual(session.post.call_count, 1)

    def test_query_still_requires_access_token(self):
        api, session = self.make_client([])
        api.token = None
        with self.assertRaisesRegex(JushuitanError, "Access Token"):
            api.orders(1)
        session.post.assert_not_called()

    def test_no_write_endpoint_or_unknown_host_is_allowed(self):
        api, session = self.make_client([])
        with self.assertRaises(JushuitanError):
            api.query("/write", {})
        with self.assertRaises(JushuitanError):
            Client("k", "s", "t", environment="https://untrusted.invalid")
        session.post.assert_not_called()

    def test_order_keeps_line_ids_and_discards_customer_pii(self):
        raw = {"o_id": 123, "so_id": "online", "ts": 99, "co_id": 1,
               "receiver_mobile": "private", "receiver_address": "private",
               "items": [{"oi_id": 10, "sku_id": "same", "qty": 1}, {"oi_id": 11, "sku_id": "same", "qty": 2}]}
        normalized = order_snapshot(raw)
        self.assertEqual(len(normalized["items"]), 2)
        self.assertNotIn("receiver_mobile", normalized)
        self.assertNotIn("receiver_address", normalized)
        self.assertNotEqual(record_key("merchantA", "123"), record_key("merchantB", "123"))

    def test_missing_line_identity_fails_instead_of_merging_skus(self):
        with self.assertRaises(JushuitanError):
            order_snapshot({"o_id": 123, "ts": 99, "items": [{"sku_id": "A"}]})

    def test_image_url_drops_unsafe_or_malformed_sources(self):
        for value in ("javascript:alert(1)", "http://127.0.0.1/x", "https://localhost/x", "https://user:pw@example.com/x", "http://["):
            with self.subTest(value=value):
                self.assertEqual(safe_image_url(value), "")
        self.assertEqual(safe_image_url("https://cdn.example.com/A.jpg"), "https://cdn.example.com/A.jpg")
