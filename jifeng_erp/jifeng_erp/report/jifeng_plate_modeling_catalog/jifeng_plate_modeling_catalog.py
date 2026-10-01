from jifeng_erp.plate_modeling import DOCTYPE, EDIT_FIELDS, _columns, _fields, _read_permission, list_records


LABELS = {"legacy_number": "编号原文", "model_number": "建模号", "product_code": "产品编号原文",
          "reference_unit_price": "单价参考", "modeler_text": "建模原文", "model_date": "日期",
          "remarks": "备注", "legacy_copy": "复制勾选记录", "item_code": "关联商品", "plate": "关联版种档案",
          "expense_voucher": "实际费用来源"}


def execute(filters=None):
    filters = filters or {}
    if not isinstance(filters, dict) or not set(filters).issubset({"company", "search"}):
        import frappe
        frappe.throw("查询包含不支持的条件")
    _read_permission()
    _fields({"name"}, "read")
    readable = _columns("read")
    columns = [{"fieldname": "name", "label": "建模记录", "fieldtype": "Link", "options": DOCTYPE, "width": 170}]
    for field in EDIT_FIELDS:
        if field not in readable or field == "design_image":
            continue
        # Reference amount and codes stay text; no totals or implicit payments.
        columns.append({"fieldname": field, "label": LABELS[field],
                        "fieldtype": "Date" if field == "model_date" else "Check" if field == "legacy_copy" else "Data", "width": 140})
    if "expense_voucher" in readable:
        columns.append({"fieldname": "expense_voucher_state", "label": "费用来源当前状态", "fieldtype": "Data", "width": 140})
    if "design_image" in readable:
        columns.append({"fieldname": "image_url", "label": "设计原图", "fieldtype": "Data", "width": 120})
        columns.append({"fieldname": "image_status", "label": "图片状态", "fieldtype": "Data", "width": 110})
    rows = list_records(filters.get("company"), filters.get("search", ""))
    for row in rows:
        image = row.pop("image_state", None)
        row.pop("design_image", None)
        if image:
            row.update(image_url=image["url"], image_status=image["state"])
    return columns, rows
