"""Archive trusted supplier image URLs without allowing arbitrary URL fetches."""
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlsplit

import frappe
import requests
from PIL import Image
from frappe.utils.file_manager import save_file


def archive_image(mapping_name):
    with frappe.cache.lock("jifeng:image:" + mapping_name, timeout=100, blocking_timeout=1):
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name)
        settings = frappe.get_single("Jushuitan Settings")
        if mapping.scope != settings.sync_scope or mapping.archived_image or not settings.enabled:
            return
        url = mapping.source_image or ""
        original_item = mapping.item_code
        parsed = urlsplit(url)
        hosts = {h.strip().lower() for h in (settings.image_hosts or "").splitlines()}
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in (None, 443) or parsed.username:
            return
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return
            with requests.get(url, timeout=(10, 25), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    return
                data = bytearray()
                for part in response.iter_content(65536):
                    data.extend(part)
                    if len(data) > 8 * 1024 * 1024:
                        return
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > 25_000_000 or image.format not in ("JPEG", "PNG", "WEBP"):
                    return
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format]
                image.verify()
        except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
            return
        name = "".join(c for c in mapping.sku_id if c.isalnum() or c in "-_")[:100] or mapping.name[:20]
        # Recheck under a row lock before attaching a completed download.
        mapping = frappe.get_doc("Jifeng SKU Mapping", mapping_name, for_update=True)
        if mapping.source_image != url or mapping.item_code != original_item or mapping.archived_image:
            return
        file = save_file(name + "." + extension, bytes(data), "Item", mapping.item_code, is_private=0)
        mapping.archived_image = file.file_url
        mapping.save(ignore_permissions=True)
        if mapping.managed_item:
            frappe.db.set_value("Item", mapping.item_code, "image", file.file_url)
