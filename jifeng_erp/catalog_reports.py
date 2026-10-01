"""Company-scoped catalog records; reference values never post stock or accounts."""

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import cint

from jifeng_erp.inventory_scope import column, decimal_number, readable_documents
from jifeng_erp.production_picture import _normalize_image_url, _readable_image


PROFILE = "Jifeng Product Profile"
MOLD = "Jifeng Mold"
PLATE = "Jifeng Plate"
ORIGINAL_FIELDS = ("legacy_number", "legacy_code", "display_name", "display_uom", "legacy_remarks")
PRODUCT_TEXT_FIELDS = ("old_number", "major_category", "minor_category", "brand", "material",
                       "style", "source", "designer", "pattern_maker", "modeler", "specification")
PRODUCT_NUMBER_FIELDS = ("list_price", "blank_price", "weight", "silver_weight", "stone_weight",
                         "material_unit_cost", "processing_unit_cost")


def _scope(filters, doctype, inactive_field, inactive_label):
    filters = frappe._dict(filters or {})
    if frappe.session.user == "Guest" or not frappe.has_permission(doctype, "read"):
        frappe.throw("没有读取该档案的权限", frappe.PermissionError)
    if not filters.company:
        frappe.throw("请选择公司")
    company = frappe.get_doc("Company", filters.company)
    company.check_permission("select")
    query = {"company": filters.company}
    if filters.get("status"):
        if filters.status not in ("在用", inactive_label):
            frappe.throw("请选择有效的档案状态")
        query[inactive_field] = int(filters.status == inactive_label)
    if filters.get("item_code"):
        _item(filters.item_code, {})
        query["item_code"] = filters.item_code
    currency = frappe.db.get_value("Company", filters.company, "default_currency")
    if not currency:
        frappe.throw("公司未配置本币")
    # Check full document access after the native list/user-permission query.
    documents = readable_documents(doctype, query)
    return filters, currency, [doc for doc in documents.values() if doc.company == filters.company]


def _item(name, cache):
    if not name:
        return None
    if name not in cache:
        documents = readable_documents("Item", {"name": name}, complete=True)
        if name not in documents:
            frappe.throw("档案关联商品不存在，请核对来源商品")
        cache[name] = documents[name]
    return cache[name]


def _reference(value):
    result = decimal_number(value)
    if result < 0:
        frappe.throw("档案参考数量或金额不能小于零")
    return result


def _image(value, cache):
    if not value:
        return {"image_url": "", "image_status": "未上传"}
    key = str(value)
    if key not in cache:
        cache[key] = _readable_image(key)
    url, status = cache[key]
    return {"image_url": url, "image_status": status}


def _profile_image(document):
    """An archive image authorizes only its exact private File binding."""
    result = {"profile_image_url": "", "profile_image_status": "未上传"}
    value = document.get("profile_image")
    if not value:
        return result
    if not isinstance(value, str) or not value.startswith("/private/files/") or _normalize_image_url(value) != (value, True):
        result["profile_image_status"] = "图片地址无效"
        return result
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": PROFILE,
               "attached_to_name": document.name, "attached_to_field": "profile_image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0)
    result["profile_image_status"] = "无附件权限" if names else "附件记录不存在"
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        # Recheck after loading, including URL. Another same-URL alias or a
        # binding changed since the list query cannot supply authorization.
        if any(attachment.get(field) != expected for field, expected in binding.items()):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            return {"profile_image_url": attachment.unique_url, "profile_image_status": "已上传"}
    return result


def _tool_image(document):
    # Tool attachments use their saved parent AND image field as identity.
    # A same-URL attachment on another record cannot supply authorization.
    from jifeng_erp.tool_archive import _image_state

    state = _image_state(document)
    return {"image_url": state["url"], "image_status": state["state"]}


def _base_columns(doctype):
    return [column("document", "来源档案", "Link", doctype, 180),
            column("company", "公司", "Link", "Company", 180),
            column("item_code", "关联商品", "Link", "Item", 180)]


def product_catalog(filters=None):
    _, currency, documents = _scope(filters, PROFILE, "inactive", "停用")
    for doctype, required in (
            (PROFILE, {"name", "company", "item_code", "inactive", "profile_image",
                       *ORIGINAL_FIELDS, *PRODUCT_TEXT_FIELDS, *PRODUCT_NUMBER_FIELDS}),
            ("Item", {"name", "item_name", "stock_uom", "image"})):
        readable = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
        if not required.issubset(readable):
            frappe.throw("无法完整读取产品档案或商品字段，请核对字段读取权限", frappe.PermissionError)
    items, images, rows = {}, {}, []
    for doc in documents:
        item = _item(doc.item_code, items)
        row = {"document": doc.name, "company": doc.company, "item_code": doc.item_code,
               "product_name": item.item_name, "stock_uom": item.stock_uom,
               "status": "停用" if cint(doc.inactive) else "在用", "currency": currency,
               **_image(item.image, images), **_profile_image(doc)}
        row.update({field: doc.get(field) for field in ORIGINAL_FIELDS})
        for field in PRODUCT_TEXT_FIELDS:
            row[field] = doc.get(field)
        for field in PRODUCT_NUMBER_FIELDS:
            row[field] = _reference(doc.get(field))
        rows.append(row)
    columns = _base_columns(PROFILE) + [
        column("product_name", "商品名称", width=180), column("stock_uom", "库存单位", "Link", "UOM"),
        column("status", "档案状态"), column("image_url", "商品图片", width=125), column("image_status", "图片状态"),
        *[column(field, label, width=180) for field, label in (
            ("legacy_number", "编号原文"), ("legacy_code", "代码原文"),
            ("display_name", "名称原文"), ("display_uom", "单位原文"), ("legacy_remarks", "备注原文"))],
        column("profile_image_url", "产品档案图片", width=125), column("profile_image_status", "档案图片状态"),
        *[column(field, label) for field, label in (
            ("old_number", "旧号"), ("major_category", "档案大类"), ("minor_category", "档案小类"),
            ("brand", "档案品牌"), ("material", "材质"), ("style", "风格"), ("source", "来源"),
            ("designer", "设计员"), ("pattern_maker", "打版员"), ("modeler", "建模员"))],
        column("specification", "规格", width=220), column("currency", "参考金额币种", "Link", "Currency"),
        *[column(field, label, "Currency", "currency", 145) for field, label in (
            ("list_price", "参考单价"), ("blank_price", "参考毛胚价"),
            ("material_unit_cost", "参考单件材料费"), ("processing_unit_cost", "参考单件加工费"))],
        *[column(field, label, "Float") for field, label in (
            ("weight", "档案重量"), ("silver_weight", "档案银重"), ("stone_weight", "档案石重"))],
    ]
    return columns, sorted(rows, key=lambda row: row["document"])


def product_material_catalog(filters=None):
    _, currency, documents = _scope(filters, PROFILE, "inactive", "停用")
    items, rows = {}, []
    for doc in documents:
        _item(doc.item_code, items)
        for material in doc.materials or []:
            item = _item(material.item_code, items)
            quantity, price = _reference(material.qty_per_unit), _reference(material.unit_price)
            rows.append({"document": doc.name, "company": doc.company, "item_code": doc.item_code,
                         "status": "停用" if cint(doc.inactive) else "在用", "material_row": material.name,
                         "material_code": material.item_code, "material_name": item.item_name,
                         "uom": material.uom, "qty_per_unit": quantity, "unit_price": price,
                         "reference_amount": quantity * price, "currency": currency, "note": material.note})
    return _base_columns(PROFILE) + [
        column("status", "档案状态"), column("material_row", "档案物料行", width=180),
        column("material_code", "物料商品", "Link", "Item", 180), column("material_name", "物料名称", width=180),
        column("uom", "配方单位", "Link", "UOM"), column("qty_per_unit", "单件参考用量", "Float"),
        column("currency", "参考金额币种", "Link", "Currency"),
        column("unit_price", "参考物料单价", "Currency", "currency"),
        column("reference_amount", "单件参考材料费", "Currency", "currency"), column("note", "说明", width=220),
    ], sorted(rows, key=lambda row: (row["document"], row["material_row"]))


def _tool_catalog(filters, doctype, inactive_field, inactive_label):
    from jifeng_erp.catalog_access import check_employee

    is_mold = doctype == MOLD
    required = {"name", "company", "item_code", "legacy_number", "legacy_copy", "copy_from",
                "maker", "location", "image", "quantity"}
    required.update({"mold_no", "product_code", "mold_date", "amount", "scrapped", "scrap_date", "scrap_reason", "remark"}
                    if is_mold else {"plate_no", "plate_type", "product_code", "assessment_score", "plate_date", "modeler",
                                    "reference_cost", "retired", "retired_date", "retired_reason", "notes"})
    readable = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
    if not required.issubset(readable):
        frappe.throw("无法完整读取模具或版种字段，请核对字段读取权限", frappe.PermissionError)
    _, currency, documents = _scope(filters, doctype, inactive_field, inactive_label)
    items, rows = {}, []
    for doc in documents:
        _item(doc.item_code, items)
        check_employee(doc.maker, doc.company, "压模人" if is_mold else "制版人")
        if not is_mold:
            check_employee(doc.modeler, doc.company, "建模人")
        row = {"document": doc.name, "company": doc.company, "item_code": doc.item_code,
               "number": doc.mold_no if is_mold else doc.plate_no,
               "catalog_date": doc.mold_date if is_mold else doc.plate_date,
               "maker": doc.maker, "modeler": None if is_mold else doc.modeler, "location": doc.location,
               "status": inactive_label if cint(doc.get(inactive_field)) else "在用",
               "inactive_date": doc.scrap_date if is_mold else doc.retired_date,
               "inactive_reason": doc.scrap_reason if is_mold else doc.retired_reason,
               "currency": currency, "reference_amount": _reference(doc.amount if is_mold else doc.reference_cost),
               "legacy_number": doc.get("legacy_number"), "legacy_copy": cint(doc.get("legacy_copy")),
               "catalog_notes": doc.get("remark" if is_mold else "notes"),
               **_tool_image(doc)}
        if is_mold:
            row.update(product_code=doc.product_code, quantity=_reference(doc.quantity))
        else:
            row.update(plate_type=doc.get("plate_type"), product_code=doc.get("product_code"),
                       assessment_score=doc.get("assessment_score"), quantity=doc.get("quantity"))
        # A hidden source must not be exposed by a readable copied catalog.
        source = doc.get("copy_from")
        original = readable_documents(doctype, {"name": source}).get(source) if source else None
        row["copy_from"] = original.name if original and original.company == doc.company else None
        rows.append(row)
    columns = _base_columns(doctype) + [column("number", "模具号" if is_mold else "版种编号"),
        column("legacy_number", "编号原文"),
        column("catalog_date", "建档日期", "Date"), column("status", "档案状态"),
        column("image_url", "档案图片", width=125), column("image_status", "图片状态"),
        column("maker", "压模人" if is_mold else "制版人", "Link", "Employee"),
        *([] if is_mold else [column("modeler", "建模人", "Link", "Employee")]),
        column("location", "存放位置"), column("currency", "参考金额币种", "Link", "Currency"),
        column("reference_amount", "档案参考金额", "Currency", "currency"),
        column("inactive_date", "报废日期" if is_mold else "停用日期", "Date"),
        column("inactive_reason", "报废原因" if is_mold else "停用原因", width=220),
        column("catalog_notes", "说明", width=220), column("legacy_copy", "复制勾选记录", "Check")]
    if is_mold:
        columns += [column("product_code", "档案产品号"), column("quantity", "模具数量", "Float")]
    else:
        columns += [column("plate_type", "类型原文"), column("product_code", "产品编号原文"),
                    column("assessment_score", "考核分原文"), column("quantity", "数量原文")]
    columns += [column("copy_from", "复制来源", "Link", doctype, 180)]
    return columns, sorted(rows, key=lambda row: row["document"])


def mold_catalog(filters=None):
    return _tool_catalog(filters, MOLD, "scrapped", "报废")


def plate_catalog(filters=None):
    return _tool_catalog(filters, PLATE, "retired", "停用")
