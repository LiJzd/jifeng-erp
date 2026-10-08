"""Archive explicitly trusted supplier images through verified direct egress."""
import ipaddress
import os
import socket
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlsplit

import frappe
import requests
from PIL import Image
from frappe.utils import get_datetime, now_datetime
from frappe.utils.file_manager import save_file


PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)


def _direct_transfer_block_reason():
    if any(os.environ.get(key, "").strip() for key in PROXY_ENV_VARS):
        return "服务器配置了代理，已停止图片下载。"
    if getattr(frappe, "conf", {}).get("jifeng_image_direct_confirmed") is not True:
        return "服务器出网尚未核实为直连，已停止图片下载。"
    return ""


def _mark_image_state(mapping_name, source_url, item_code, status, reason=""):
    mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
    if (mapping.source_image or "") != (source_url or "") or mapping.item_code != item_code:
        return False
    mapping.image_sync_status = status
    mapping.image_sync_error = reason
    mapping.image_sync_attempted_at = now_datetime()
    mapping.save(ignore_permissions=True)
    return True


def _image_failure(mapping_name, source_url, item_code, reason):
    _mark_image_state(mapping_name, source_url, item_code, "失败", reason)


def _allowed_source_url(url, hosts):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() in hosts
                and port in (None, 443) and not parsed.username and not parsed.password)


def _validated_image_bytes(url, hosts):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None, None, "图片链接无效。"
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in hosts
            or port not in (None, 443) or parsed.username or parsed.password):
        return None, None, "图片域名或链接不在允许范围内。"

    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            return None, None, "图片域名解析到非公网地址，已停止下载。"

        # Do not inherit process or system proxy settings. The explicit site
        # confirmation gate above must be enabled only after operations verifies
        # the actual egress path is DIRECT, including any TUN routing.
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(10, 25), stream=True, allow_redirects=False,
                             proxies={"http": "", "https": ""}) as response:
                if response.status_code != 200:
                    return None, None, "图片服务器未返回成功响应。"
                data = bytearray()
                for part in response.iter_content(65536):
                    data.extend(part)
                    if len(data) > 8 * 1024 * 1024:
                        return None, None, "图片超过 8 MiB 限额。"

        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                return None, None, "图片像素超过限额。"
            if image.format not in ("JPEG", "PNG", "WEBP"):
                return None, None, "图片格式仅支持 JPEG、PNG、WEBP。"
            extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format]
            image.verify()
        return bytes(data), extension, ""
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
        return None, None, "图片下载或校验失败。"


def archive_image(mapping_name):
    with frappe.cache.lock("jifeng:image:" + mapping_name, timeout=100, blocking_timeout=1):
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name)
        settings = frappe.get_single("Jushuitan Settings")
        url = mapping.source_image or ""
        original_item = mapping.item_code
        if mapping.scope != settings.sync_scope:
            return
        if not settings.enabled:
            _image_failure(mapping_name, url, original_item, "聚水潭同步已停用。")
            return
        if not url:
            _mark_image_state(mapping_name, url, original_item, "未配置")
            return
        if mapping.archived_image and mapping.image_sync_status == "已完成":
            _mark_image_state(mapping_name, url, original_item, "已完成")
            return
        proxy_reason = _direct_transfer_block_reason()
        if proxy_reason:
            _image_failure(mapping_name, url, original_item, proxy_reason)
            return
        hosts = {host.strip().lower() for host in (settings.image_hosts or "").splitlines() if host.strip()}
        if not hosts:
            _image_failure(mapping_name, url, original_item, "未配置图片允许域名。")
            return

        data, extension, error = _validated_image_bytes(url, hosts)
        if error:
            _image_failure(mapping_name, url, original_item, error)
            return

        # Recheck under a row lock before attaching a completed download. A stale
        # worker never overwrites a newer source image or Item link.
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
        if ((mapping.source_image or "") != url or mapping.item_code != original_item
                or (mapping.archived_image and mapping.image_sync_status == "已完成")):
            return
        name = "".join(c for c in mapping.sku_id if c.isalnum() or c in "-_")[:100] or mapping.name[:20]
        file = save_file(name + "." + extension, data, "Item", mapping.item_code, is_private=0)
        previous_archived_image = mapping.archived_image
        mapping.archived_image = file.file_url
        mapping.image_sync_status = "已完成"
        item_image_updated = False
        if mapping.managed_item:
            item = frappe.get_doc("Item", mapping.item_code)
            if not item.image or item.image == previous_archived_image:
                frappe.db.set_value("Item", mapping.item_code, "image", file.file_url)
                item_image_updated = True
        mapping.image_sync_error = "" if item_image_updated or not mapping.managed_item else "源图片已归档，保留了人工商品图片。"
        mapping.image_sync_attempted_at = now_datetime()
        mapping.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def retry_image(mapping_name):
    mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name)
    mapping.check_permission("write")
    settings = frappe.get_single("Jushuitan Settings")
    if mapping.scope != settings.sync_scope:
        frappe.throw("商品映射与当前同步范围不一致，不能重试。")
    if not settings.enabled:
        frappe.throw("聚水潭同步已停用，不能重试图片。")
    if not mapping.source_image:
        frappe.throw("该商品没有可重试的源图片。")
    if mapping.archived_image and mapping.image_sync_status == "已完成":
        return {"status": "completed", "message": "图片已同步完成。"}
    proxy_reason = _direct_transfer_block_reason()
    if proxy_reason:
        frappe.throw(proxy_reason)
    hosts = {host.strip().lower() for host in (settings.image_hosts or "").splitlines() if host.strip()}
    if not hosts or not _allowed_source_url(mapping.source_image, hosts):
        frappe.throw("源图片域名不在允许列表中，不能重试。")

    with frappe.cache.lock("jifeng:image-queue:" + mapping_name, timeout=20, blocking_timeout=1):
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
        mapping.check_permission("write")
        if mapping.scope != settings.sync_scope or not settings.enabled:
            frappe.throw("商品映射与当前启用的同步范围不一致，不能重试。")
        if not mapping.source_image:
            frappe.throw("该商品没有可重试的源图片。")
        proxy_reason = _direct_transfer_block_reason()
        if proxy_reason:
            frappe.throw(proxy_reason)
        if not _allowed_source_url(mapping.source_image, hosts):
            frappe.throw("源图片域名不在允许列表中，不能重试。")
        if mapping.image_sync_status == "待处理" and mapping.image_sync_attempted_at:
            queued_at = get_datetime(mapping.image_sync_attempted_at)
            if queued_at > now_datetime() - timedelta(minutes=10):
                return {"status": "pending", "message": "图片同步已在队列中，请稍后刷新查看。"}
        mapping.image_sync_status = "待处理"
        mapping.image_sync_error = ""
        mapping.image_sync_attempted_at = now_datetime()
        mapping.save(ignore_permissions=True)
        frappe.enqueue("jifeng_erp.images.archive_image", mapping_name=mapping.name,
                       queue="long", timeout=120, enqueue_after_commit=True)
    return {"status": "pending", "message": "图片重试已排队。"}
