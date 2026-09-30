"""Production pictures with the same quantities and permissions as the work-order report."""

import ipaddress
from urllib.parse import unquote, urlsplit

import frappe
from frappe.utils import get_url

from jifeng_erp.production import column, work_order_progress


def production_picture(filters=None):
    columns, work_orders = work_order_progress(filters)
    flow_names = {row.get("jf_flow_order") for row in work_orders if row.get("jf_flow_order")}
    item_names = {row.get("production_item") for row in work_orders if row.get("production_item")}
    flows = _readable_sources("Jifeng Flow Order", flow_names, ["name", "item_code", "image"])
    items = _readable_sources("Item", item_names, ["name", "image"])
    image_cache = {}
    rows = []
    for work_order in work_orders:
        candidates = []
        flow = flows.get(work_order.get("jf_flow_order"))
        if flow and flow.get("item_code") == work_order.get("production_item"):
            candidates.append((flow.get("image"), "流程单"))
        item = items.get(work_order.get("production_item"))
        if item:
            candidates.append((item.get("image"), "商品档案"))
        image_url, image_source, image_status = "", "", "未上传"
        if not candidates:
            image_status = "无图片来源权限"
        for image, source in candidates:
            if not image:
                continue
            cache_key = str(image)
            if cache_key not in image_cache:
                image_cache[cache_key] = _readable_image(cache_key)
            accessible_url, reason = image_cache[cache_key]
            if accessible_url:
                image_url, image_source, image_status = accessible_url, source, "已上传"
                break
            if image_status == "未上传":
                image_status = reason
        rows.append({**work_order, "image_url": image_url,
                     "image_source": image_source, "image_status": image_status})

    # Keep quantities per work order and per stock unit; a cross-unit total is misleading.
    picture_columns = [
        column("image_url", "商品图片", width=125),
        column("image_status", "图片状态", width=130),
        column("image_source", "图片来源", width=100),
    ]
    return picture_columns + columns, rows


def _readable_sources(doctype, names, fields):
    if not names or not frappe.has_permission(doctype, "read"):
        return {}
    # get_list also applies User Permissions and the field's permission level.
    rows = frappe.get_list(doctype, filters={"name": ["in", sorted(names)]},
                           fields=fields, limit_page_length=0)
    return {row.name: row for row in rows if frappe.get_doc(doctype, row.name).has_permission("read")}


def _readable_image(value):
    """Never return a local URL without the native File download permission check."""
    normalized = _normalize_image_url(value)
    if not normalized:
        return "", "图片地址无效"
    url, local = normalized
    files = frappe.get_all("File", filters={"file_url": url}, pluck="name", limit_page_length=0)
    for name in files:
        file = frappe.get_doc("File", name)
        # This is the same guard as Frappe's private-file download handler, including
        # owner, document read permission and individual File sharing.
        if file.is_downloadable():
            return file.unique_url, "已上传"
    if files:
        return "", "无附件权限"
    if local:
        return "", "附件记录不存在"
    # Item supports external image URLs without a File record. Only a safe HTTPS
    # URL from a readable source is shown. This code does not fetch remote images.
    return url, "已上传"


def _normalize_image_url(value):
    value = str(value or "")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        origin = urlsplit(get_url())
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            return None
        same_origin = parsed.scheme and (parsed.scheme, parsed.hostname, _effective_port(parsed)) == (
            origin.scheme, origin.hostname, _effective_port(origin))
        local = (value.startswith("/") and not value.startswith("//")) or same_origin
        if local:
            if not parsed.path.startswith(("/files/", "/private/files/")):
                return None
            path = unquote(parsed.path)
            # No alternate encoding of separators or traversal is accepted. Native
            # uploads use a single filename below these exact URL prefixes.
            prefix = "/private/files/" if path.startswith("/private/files/") else "/files/"
            filename = path.removeprefix(prefix)
            if not filename or any(char in filename for char in ("/", "\\", "%")) or filename in (".", ".."):
                return None
            if any(ord(char) < 32 for char in path):
                return None
            return parsed.path, True
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or any(char.isspace() for char in value):
            return None
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        if unquote(parsed.path).startswith("/private/"):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            # Browsers interpret abbreviated, decimal and hexadecimal IPv4
            # hosts as addresses; don't mistake them for external DNS names.
            numeric_host = all(part.isdecimal() or (
                part.startswith("0x") and part[2:] and all(char in "0123456789abcdef" for char in part[2:])
            ) for part in host.split("."))
            if "." not in host or numeric_host or "%" in host:
                return None
        # Accessing .port validates malformed port declarations without a request.
        parsed.port
        return value, False
    except ValueError:
        return None


def _effective_port(parsed):
    return parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
