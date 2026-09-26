"""Jushuitan v2 read client. No Frappe dependency; secrets never enter errors.

Protocol: https://openweb.jushuitan.com/doc?docId=30 and docId=70.
"""
import hashlib
import json
import time

import requests

HOSTS = {
    "正式": "https://openapi.jushuitan.com",
    "测试": "https://dev-api.jushuitan.com",
}
ORDER_QUERY = "/open/orders/single/query"
SKU_QUERY = "/open/sku/query"


class JushuitanError(RuntimeError):
    pass


def sign(parameters, secret):
    message = secret + "".join(
        str(key) + str(value)
        for key, value in sorted(parameters.items())
        if key != "sign" and value is not None and value != ""
    )
    return hashlib.md5(message.encode("utf-8")).hexdigest()


class Client:
    def __init__(self, app_key, app_secret, access_token, environment="测试", session=None, sleep=time.sleep):
        if environment not in HOSTS:
            raise JushuitanError("请选择正式或测试环境")
        if not all((app_key, app_secret, access_token)):
            raise JushuitanError("请配置 App Key、App Secret 和商家授权 Token")
        self.base = HOSTS[environment]
        self.app_key, self.secret, self.token = app_key, app_secret, access_token
        self.session = session or requests.Session()
        self.sleep = sleep

    def query(self, path, business):
        if path not in (ORDER_QUERY, SKU_QUERY):
            raise JushuitanError("此客户端只允许订单和商品查询")
        for attempt in range(4):
            parameters = {
                "app_key": self.app_key, "access_token": self.token,
                "timestamp": str(int(time.time())), "charset": "utf-8", "version": "2",
                "biz": json.dumps(business, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            }
            parameters["sign"] = sign(parameters, self.secret)
            # Stay below the documented 100 requests/minute limit for this worker.
            self.sleep(0.7 if not attempt else min(2 ** attempt, 8))
            try:
                response = self.session.post(
                    self.base + path, data=parameters, timeout=(10, 40), allow_redirects=False,
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                )
            except requests.RequestException:
                if attempt < 3:
                    continue
                raise JushuitanError("聚水潭查询连接失败，请检查网络和出口 IP 白名单") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    continue
            if response.status_code != 200:
                raise JushuitanError(f"聚水潭查询 HTTP 状态 {response.status_code}")
            try:
                payload = response.json()
            except (ValueError, TypeError):
                raise JushuitanError("聚水潭返回了非 JSON 响应") from None
            if not isinstance(payload, dict):
                raise JushuitanError("聚水潭响应结构无效")
            code = str(payload.get("code", "missing"))
            if code in ("199", "200") and attempt < 3:
                continue
            if code != "0":
                # Never echo API messages: some contain request parameters/tokens.
                hint = {"190": "尚未申请该接口权限", "199": "并发超限", "200": "频次超限"}.get(code, "请检查授权、权限和白名单")
                raise JushuitanError(f"聚水潭错误码 {code if code.isdigit() else 'unknown'}：{hint}")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise JushuitanError("聚水潭 data 响应结构无效")
            return data
        raise JushuitanError("聚水潭查询重试已用尽")

    def orders(self, cursor):
        return self.query(ORDER_QUERY, {"start_ts": int(cursor or 1), "is_get_total": False, "page_index": 1, "page_size": 100})

    def skus(self, sku_ids=None, begin=None, end=None, page=1):
        business = {"page_index": page, "page_size": 100, "flds": "pics"}
        if sku_ids:
            if len(sku_ids) > 20:
                raise JushuitanError("一次最多查询 20 个商品编码")
            business["sku_ids"] = ",".join(str(s) for s in sku_ids)
        elif begin and end:
            business.update(modified_begin=begin, modified_end=end)
        else:
            raise JushuitanError("商品查询需要编码或起止时间")
        return self.query(SKU_QUERY, business)

    def refresh(self, refresh_token):
        """Self-owned applications only. Never retry a token rotation blindly."""
        path = "/openWeb/auth/refreshToken" if self.base == HOSTS["正式"] else "/openWebIsv/auth/refreshToken"
        params = {"app_key": self.app_key, "timestamp": str(int(time.time())),
                  "grant_type": "refresh_token", "charset": "utf-8",
                  "refresh_token": refresh_token, "scope": "all"}
        params["sign"] = sign(params, self.secret)
        try:
            response = self.session.post(self.base + path, data=params, timeout=(10, 40), allow_redirects=False)
            payload = response.json() if response.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            raise JushuitanError("令牌刷新结果未知，请核实授权状态；系统不会自动重复刷新") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or str(payload.get("code")) != "0" or not all(data.get(k) for k in ("access_token", "refresh_token", "expires_in")):
            raise JushuitanError("令牌刷新未成功，请确认应用类型、授权有效期和刷新条件")
        try:
            if int(data["expires_in"]) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise JushuitanError("令牌有效期无效，请核实授权结果") from None
        return data


def record_key(scope, identifier):
    return hashlib.sha256(f"{scope}\0{identifier}".encode()).hexdigest()


def order_snapshot(raw):
    """Keep the business fields needed by the factory, not addresses/phones."""
    if raw.get("o_id") in (None, "") or raw.get("ts") in (None, ""):
        raise JushuitanError("订单缺少内部订单号或变更游标，停止同步以免漏单")
    try:
        cursor = int(raw["ts"])
    except (ValueError, TypeError):
        raise JushuitanError("订单变更游标不是整数") from None
    items = []
    seen = set()
    for row in raw.get("items") or []:
        # A line needs its native id; SKU is not unique within an order.
        line_id = row.get("oi_id")
        if line_id in (None, "") or str(line_id) in seen or not row.get("sku_id"):
            raise JushuitanError("订单明细缺少唯一行号/商品编码或行号重复")
        seen.add(str(line_id))
        items.append({
            "external_line_id": str(line_id), "sku_id": str(row["sku_id"]),
            "item_name": str(row.get("name") or row["sku_id"]),
            "qty": row.get("qty", 0), "price": row.get("price", 0),
            "specification": str(row.get("properties_value") or ""),
            "image_url": safe_image_url(row.get("pic")),
            "source_status": str(row.get("item_status") or ""),
        })
    return {
        "external_order_id": str(raw["o_id"]), "online_order_id": str(raw.get("so_id") or ""),
        "shop_id": str(raw.get("shop_id") or ""), "merchant_id": str(raw.get("co_id") or ""),
        "source_status": str(raw.get("status") or ""), "source_modified": raw.get("modified"),
        "source_cursor": str(cursor), "items": items,
    }


def safe_image_url(value):
    import ipaddress
    from urllib.parse import urlsplit
    value = str(value or "")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme not in ("https", "http") or not host or parsed.username is not None or parsed.password is not None:
        return ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    return value
