"""Private inventory pictures staged in the browser and saved with the archive.

Selecting a local picture does not upload it. File insertion uses the native
controller in the archive transaction, including its rollback cleanup hooks.
"""

import base64
import binascii
from contextvars import ContextVar
import hashlib
from io import BytesIO
import re
import warnings

import frappe
from frappe.model import get_permitted_fields


PROFILE = "Jifeng Inventory Profile"
FIELD = "image"
RECEIPT = "image_change_request_id"
_CHANGING = ContextVar("jifeng_inventory_image_change", default=None)
MAX_BYTES = 10 * 1024 * 1024
FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF", ".webp": "WEBP"}
FILE_FIELDS = {"name", "file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field"}
HAN = re.compile("[\u2e80-\u2eff\u2f00-\u2fdf\u3005\u3007\u3021-\u3029\u3038-\u303b"
                 "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00016fe2-\U00016fe3"
                 "\U00016ff0-\U00016ff1\U00020000-\U000323af]")


def _columns(doctype, permission="read"):
    readable = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if permission != "read" or frappe.session.user == "Administrator":
        return readable
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    masked = {field.fieldname for field in meta.fields
              if field.get("mask") and (field.permlevel or 0) not in levels}
    return readable - masked


def maximum_bytes():
    from frappe.utils.file_manager import get_max_file_size

    # Bound a JSON/base64 request before decoding; preserve stricter site limits.
    return min(MAX_BYTES, int(get_max_file_size()))


def options(profile, can_write):
    field = profile.meta.get_field(FIELD)
    writable = bool(can_write and profile.has_permission("write") and field and not field.read_only
                    and FIELD in _columns(PROFILE) & _columns(PROFILE, "write")
                    and RECEIPT in _columns(PROFILE))
    return {"can_clear": writable,
            "can_choose": bool(writable and frappe.has_permission("File", "create")
                               and FILE_FIELDS.issubset(_columns("File"))),
            "maximum_bytes": maximum_bytes(), "extensions": list(FORMATS)}


def image_state(profile, value=None):
    value = profile.get(FIELD) if value is None else value
    state = {"url": "", "state": "附件不可用" if value else "未上传"}
    if not value or profile.is_new() or FIELD not in _columns(PROFILE):
        return state
    from jifeng_erp.product_archive import _private_url

    if not _private_url(value) or not FILE_FIELDS.issubset(_columns("File")):
        return state
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": PROFILE,
               "attached_to_name": profile.name, "attached_to_field": FIELD}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=101)
    if len(names) > 100:
        return state
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if (FILE_FIELDS.issubset(_columns("File")) and FIELD in _columns(PROFILE)
                and all(attachment.get(key) == expected for key, expected in binding.items())
                and profile.has_permission("read")
                and attachment.has_permission("read") and attachment.is_downloadable()):
            return {"url": attachment.unique_url, "state": "已上传"}
    return state


def validate_image(profile):
    if profile.get(FIELD) and image_state(profile)["state"] != "已上传":
        frappe.throw("图片必须是可读取和下载、绑定当前库存档案的私有附件", frappe.PermissionError)


def validate_receipt(profile):
    before = profile.get_doc_before_save() if not profile.is_new() else None
    if _CHANGING.get() is profile:
        return
    if profile.get(RECEIPT) != (before.get(RECEIPT) if before else None):
        frappe.throw("图片保存请求身份只能由服务器记录")
    # A native attachment edit is valid, but cannot retain an earlier request's
    # proof. Merely retaining an unchanged image preserves its receipt.
    if before and before.get(FIELD) != profile.get(FIELD):
        profile.set(RECEIPT, None)


def save_image_change(profile, request_id):
    profile.set(RECEIPT, request_id)
    context = _CHANGING.set(profile)
    try:
        profile.save()
    finally:
        _CHANGING.reset(context)


def prepare_upload(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"file_name", "content"}:
        frappe.throw("图片请求必须包含文件名和图片内容")
    name, encoded = value["file_name"], value["content"]
    if (not isinstance(name, str) or not name or name != name.strip() or len(name) > 140
            or name in (".", "..") or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(char in name for char in "/\\%?#")):
        frappe.throw("请选择有效的图片文件名")
    if HAN.search(name):
        frappe.throw("图片名称不能含有中文")
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in FORMATS:
        frappe.throw("请选择 PNG、JPG、GIF 或 WebP 图片")
    maximum = maximum_bytes()
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((maximum + 2) // 3):
        frappe.throw("图片内容为空或超过上传大小限制")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        frappe.throw("图片内容不是有效的 Base64")
    if (not content or len(content) > maximum
            or base64.b64encode(content).decode("ascii") != encoded):
        frappe.throw("图片内容为空、编码不规范或超过上传大小限制")
    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format != FORMATS[extension]:
                    frappe.throw("图片扩展名与实际格式不一致")
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        frappe.throw("图片内容无效或尺寸过大")
    return {"file_name": name, "content": content, "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest()}


def upload_identity(upload):
    return {key: upload[key] for key in ("file_name", "size", "sha256")} if upload else None


def store_upload(profile, upload, request_id):
    if not upload:
        return
    if not options(profile, True)["can_choose"]:
        frappe.throw("没有上传库存档案图片的权限", frappe.PermissionError)
    attachment = frappe.new_doc("File")
    attachment.file_name, attachment.content = upload["file_name"], upload["content"]
    attachment.is_private = 1
    attachment.attached_to_doctype, attachment.attached_to_name = PROFILE, profile.name
    attachment.attached_to_field = FIELD
    # Native File insert checks permissions, size, extensions and filesystem
    # rollback callbacks. No file-manager ignore_permissions shortcut is used.
    attachment.insert()
    binding = {"is_private": 1, "attached_to_doctype": PROFILE, "attached_to_name": profile.name,
               "attached_to_field": FIELD}
    if any(attachment.get(key) != expected for key, expected in binding.items()):
        frappe.throw("图片附件绑定不一致，请重新核对", frappe.PermissionError)
    profile.set(FIELD, attachment.file_url)
    validate_image(profile)
    save_image_change(profile, request_id)
