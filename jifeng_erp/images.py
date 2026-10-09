"""Archive explicitly trusted supplier images through verified direct egress."""
import ipaddress
import json
import os
import socket
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlsplit

import frappe
import requests
from PIL import Image
from frappe.utils import get_datetime, now_datetime
from frappe.utils.file_manager import save_file
from jifeng_erp.jushuitan import safe_image_url


PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)
IMAGE_METADATA_FIELDS = ("source_image_candidates", "archived_image_source")
MAX_IMAGE_CANDIDATES = 8
MAX_CANDIDATE_BYTES = 16 * 1024
IMAGE_JOB_TIMEOUT = 300
_UNSET = object()


def _require_image_metadata_fields(frappe_context=None):
    runtime = frappe_context or frappe
    meta = runtime.get_meta("Jifeng SKU Mapping")
    if any(not meta.has_field(field) for field in IMAGE_METADATA_FIELDS):
        runtime.throw("图片来源追溯字段尚未安装，已停止图片处理。")


@contextmanager
def image_metadata_write(mapping_name, fields, frappe_context=None):
    """Authorize only this server operation; document flags are not authority."""
    key = "jifeng_image_metadata_write"
    sentinel = object()
    flags = (frappe_context or frappe).flags
    is_dictionary = isinstance(flags, dict)
    previous = flags.get(key, sentinel) if is_dictionary else getattr(flags, key, sentinel)
    scope = {"mapping_name": mapping_name, "fields": list(fields)}
    if is_dictionary:
        flags[key] = scope
    else:
        setattr(flags, key, scope)
    try:
        yield
    finally:
        if previous is sentinel:
            if is_dictionary:
                flags.pop(key, None)
            else:
                delattr(flags, key)
        else:
            if is_dictionary:
                flags[key] = previous
            else:
                setattr(flags, key, previous)


def _candidate_envelope(mapping, sources):
    return {"version": 1, "scope": mapping.scope, "sku_id": str(mapping.sku_id), "sources": sources}


def _candidate_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def valid_image_source_url(value, *, https_only=True):
    """Check a literal URL offline; actual allowlist/DNS/TLS checks are separate."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or safe_image_url(value) != value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(parsed.scheme in (("https",) if https_only else ("https", "http"))
                and parsed.hostname and parsed.username is None and parsed.password is None
                and port in ((None, 443) if https_only else (None, 443, 80)))


def validate_source_image_candidates(mapping, value):
    """Validate persisted provenance without downloading or accepting a caller URL."""
    if not value:
        return _candidate_envelope(mapping, [])
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise ValueError("图片候选记录超过限额。")
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        raise ValueError("图片候选记录不是有效 JSON。") from None
    if (not isinstance(data, dict) or set(data) != {"version", "scope", "sku_id", "sources"}
            or type(data.get("version")) is not int or data["version"] != 1
            or data["scope"] != mapping.scope or data["sku_id"] != str(mapping.sku_id)
            or not isinstance(data["sources"], list) or len(data["sources"]) > MAX_IMAGE_CANDIDATES):
        raise ValueError("图片候选版本或商品归属不一致。")
    for source in data["sources"]:
        if not isinstance(source, dict) or source.get("sku_id") != str(mapping.sku_id):
            raise ValueError("图片候选商品编码不一致。")
        url = source.get("url")
        if not valid_image_source_url(url, https_only=False):
            raise ValueError("图片候选地址无效。")
        if source.get("kind") == "sku":
            if set(source) != {"kind", "sku_id", "field", "url"} or source["field"] not in ("pic_big", "pic"):
                raise ValueError("图片候选不是已知商品图片字段。")
        elif source.get("kind") == "order":
            expected = {"kind", "sku_id", "field", "url", "order_name", "line_name", "external_line_id"}
            if (set(source) != expected or source["field"] != "image_url"
                    or any(not isinstance(source.get(key), str) or not source[key]
                           for key in ("order_name", "line_name", "external_line_id"))):
                raise ValueError("图片候选缺少准确订单行来源。")
        else:
            raise ValueError("图片候选来源类型无效。")
    return data


def source_image_candidates_from_sku(mapping, raw):
    """Pure builder for the actual SKU response, also used by precise backfills."""
    if str(raw.get("sku_id") or "") != str(mapping.sku_id):
        raise ValueError("商品响应与图片候选编码不一致。")
    sources = []
    for field in ("pic_big", "pic"):
        value = safe_image_url(raw.get(field))
        if value:
            sources.append({"kind": "sku", "sku_id": str(mapping.sku_id), "field": field, "url": value})
    result = _candidate_json(_candidate_envelope(mapping, sources))
    validate_source_image_candidates(mapping, result)
    return result


def image_candidates_for_mapping(mapping, settings):
    """Use saved SKU fields and current, exact same-SKU business order rows."""
    saved = validate_source_image_candidates(mapping, mapping.source_image_candidates)
    sources = [s for s in saved["sources"] if s["kind"] == "sku"]
    rows = frappe.db.sql("""
        SELECT child.name AS line_name, child.parent AS order_name,
               child.external_line_id, child.image_url
        FROM `tabJifeng External Order Item` child
        JOIN `tabJifeng External Order` parent ON parent.name = child.parent
        WHERE parent.scope=%s AND parent.company=%s AND parent.merchant_id=%s
          AND child.sku_id=%s AND child.item_code=%s
          AND child.parenttype='Jifeng External Order' AND child.parentfield='items'
          AND child.image_url IS NOT NULL AND child.image_url!=''
        ORDER BY parent.source_modified DESC, parent.name, child.idx, child.name
        LIMIT 32
    """, (mapping.scope, settings.company, str(settings.merchant_id), str(mapping.sku_id), mapping.item_code), as_dict=True)
    for row in rows:
        sources.append({"kind": "order", "sku_id": str(mapping.sku_id), "field": "image_url",
                        "url": row["image_url"], "order_name": row["order_name"],
                        "line_name": row["line_name"], "external_line_id": str(row["external_line_id"])})
    unique, seen = [], set()
    for source in sources:
        if source["url"] not in seen:
            seen.add(source["url"])
            unique.append(source)
        if len(unique) == MAX_IMAGE_CANDIDATES:
            break
    result = _candidate_json(_candidate_envelope(mapping, unique))
    validate_source_image_candidates(mapping, result)
    return result


def _save_image_metadata(mapping, fields):
    with image_metadata_write(mapping.name, fields):
        mapping.save(ignore_permissions=True)


def _direct_transfer_block_reason():
    if any(os.environ.get(key, "").strip() for key in PROXY_ENV_VARS):
        return "服务器配置了代理，已停止图片下载。"
    if getattr(frappe, "conf", {}).get("jifeng_image_direct_confirmed") is not True:
        return "服务器出网尚未核实为直连，已停止图片下载。"
    return ""


def _mark_image_state(mapping_name, source_url, item_code, status, reason="", candidate_state=_UNSET):
    mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
    if ((mapping.source_image or "") != (source_url or "") or mapping.item_code != item_code
            or (candidate_state is not _UNSET and mapping.source_image_candidates != candidate_state)):
        return False
    mapping.image_sync_status = status
    mapping.image_sync_error = reason
    mapping.image_sync_attempted_at = now_datetime()
    mapping.save(ignore_permissions=True)
    return True


def _image_failure(mapping_name, source_url, item_code, reason, candidate_state=_UNSET):
    _mark_image_state(mapping_name, source_url, item_code, "失败", reason, candidate_state)


def _allowed_source_url(url, hosts):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() in hosts
                and port in (None, 443) and parsed.username is None and parsed.password is None)


def _response_peer_address(response):
    """Read the connected socket before urllib3 consumes or releases its body."""
    raw = getattr(response, "raw", None)
    sockets = [getattr(getattr(raw, "_connection", None), "sock", None)]
    # Some urllib3 responses release _connection after receiving headers. The
    # HTTP response can still own the same TLS socket through its buffered file.
    try:
        sockets.append(raw._fp.fp.raw._sock)
    except AttributeError:
        pass
    for connected_socket in sockets:
        if connected_socket is None:
            continue
        try:
            return ipaddress.ip_address(connected_socket.getpeername()[0])
        except (AttributeError, OSError, TypeError, ValueError):
            continue
    return None


def _validated_image_bytes(url, hosts):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None, None, "图片链接无效。"
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in hosts
            or port not in (None, 443) or parsed.username is not None or parsed.password is not None):
        return None, None, "图片域名或链接不在允许范围内。"

    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        public_addresses = {ipaddress.ip_address(address[4][0]) for address in addresses}
        if not public_addresses or any(not address.is_global for address in public_addresses):
            return None, None, "图片域名解析到非公网地址，已停止下载。"

        # Do not inherit process or system proxy settings. The explicit site
        # confirmation gate above must be enabled only after operations verifies
        # the actual egress path is DIRECT, including any TUN routing.
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(10, 25), stream=True, allow_redirects=False,
                             proxies={"http": "", "https": ""}) as response:
                peer_address = _response_peer_address(response)
                if peer_address is None:
                    return None, None, f"无法核实图片连接的实际对端，已停止下载（HTTP {response.status_code}）。"
                if peer_address not in public_addresses:
                    return None, None, f"图片连接对端与本次公网解析地址不一致，已停止下载（HTTP {response.status_code}）。"
                if response.status_code != 200:
                    return None, None, f"图片服务器返回 HTTP {response.status_code}，已停止下载。"
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
    _require_image_metadata_fields()
    with frappe.cache.lock("jifeng:image:" + mapping_name, timeout=360, blocking_timeout=1):
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name)
        settings = frappe.get_single("Jushuitan Settings")
        url = mapping.source_image or ""
        original_item = mapping.item_code
        original_candidates = mapping.source_image_candidates
        if mapping.scope != settings.sync_scope:
            return
        if not settings.enabled:
            _image_failure(mapping_name, url, original_item, "聚水潭同步已停用。", original_candidates)
            return
        if not url:
            _mark_image_state(mapping_name, url, original_item, "未配置")
            return
        if mapping.archived_image and mapping.image_sync_status == "已完成":
            _mark_image_state(mapping_name, url, original_item, "已完成")
            return
        proxy_reason = _direct_transfer_block_reason()
        if proxy_reason:
            _image_failure(mapping_name, url, original_item, proxy_reason, original_candidates)
            return
        hosts = {host.strip().lower() for host in (settings.image_hosts or "").splitlines() if host.strip()}
        if not hosts:
            _image_failure(mapping_name, url, original_item, "未配置图片允许域名。", original_candidates)
            return
        try:
            candidates = image_candidates_for_mapping(mapping, settings)
        except ValueError as exc:
            _image_failure(mapping_name, url, original_item, str(exc), original_candidates)
            return
        data, extension, error = _validated_image_bytes(url, hosts)
        used_url = url
        primary_missing = error in (
            "图片服务器返回 HTTP 403，已停止下载。", "图片服务器返回 HTTP 404，已停止下载。")
        if primary_missing:
            primary_error = error
            for source in validate_source_image_candidates(mapping, candidates)["sources"]:
                alternate = source["url"]
                if alternate == url:
                    continue
                if not _allowed_source_url(alternate, hosts):
                    error = primary_error + " 备用图片域名或链接不在允许范围内，未下载。"
                    break
                data, extension, alternate_error = _validated_image_bytes(alternate, hosts)
                if not alternate_error:
                    used_url, error = alternate, ""
                    break
                error = primary_error + " 备用图片：" + alternate_error
                if alternate_error not in (
                        "图片服务器返回 HTTP 403，已停止下载。", "图片服务器返回 HTTP 404，已停止下载。"):
                    break

        # Recheck under a row lock before attaching a completed download. A stale
        # worker never overwrites a newer source image or Item link.
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
        if ((mapping.source_image or "") != url or mapping.item_code != original_item
                or mapping.source_image_candidates != original_candidates
                or (mapping.archived_image and mapping.image_sync_status == "已完成")):
            return
        # Order source rows can change independently of the Mapping. Re-read
        # their exact ownership/URL before accepting either success or failure.
        if image_candidates_for_mapping(mapping, settings) != candidates:
            return
        fields = []
        if mapping.source_image_candidates != candidates:
            mapping.source_image_candidates = candidates
            fields.append("source_image_candidates")
        if error:
            mapping.image_sync_status = "失败"
            mapping.image_sync_error = error
            mapping.image_sync_attempted_at = now_datetime()
            _save_image_metadata(mapping, fields)
            return
        name = "".join(c for c in mapping.sku_id if c.isalnum() or c in "-_")[:100] or mapping.name[:20]
        file = save_file(name + "." + extension, data, "Item", mapping.item_code, is_private=0)
        previous_archived_image = mapping.archived_image
        mapping.archived_image = file.file_url
        if mapping.archived_image_source != used_url:
            mapping.archived_image_source = used_url
            fields.append("archived_image_source")
        mapping.image_sync_status = "已完成"
        item_image_updated = False
        if mapping.managed_item:
            item = frappe.get_doc("Item", mapping.item_code, for_update=True)
            if not item.image or item.image == previous_archived_image:
                frappe.db.set_value("Item", mapping.item_code, "image", file.file_url)
                item_image_updated = True
        note = "主图片不可用，已归档准确同商品的备用图片。" if used_url != url else ""
        if mapping.managed_item and not item_image_updated:
            note += "源图片已归档，保留了人工商品图片。"
        mapping.image_sync_error = note
        mapping.image_sync_attempted_at = now_datetime()
        _save_image_metadata(mapping, fields)


@frappe.whitelist(methods=["POST"])
def retry_image(mapping_name):
    _require_image_metadata_fields()
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
                       queue="long", timeout=IMAGE_JOB_TIMEOUT, enqueue_after_commit=True)
    return {"status": "pending", "message": "图片重试已排队。"}
