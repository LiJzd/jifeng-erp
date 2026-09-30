"""Count readable product profiles and expand each group to its exact sources."""

import frappe
from frappe.utils import cint

from jifeng_erp.catalog_reports import PROFILE, _item, _scope
from jifeng_erp.inventory_scope import column


DIMENSIONS = {
    "品牌": (("brand", "品牌"),),
    "类别": (("major_category", "档案大类"), ("minor_category", "档案小类")),
    "材质": (("material", "材质"),),
    "来源": (("source", "来源"),),
    "设计": (("designer", "设计员"),),
    "打版": (("pattern_maker", "打版员"),),
    "建模": (("modeler", "建模员"),),
    "风格": (("style", "风格"),),
}


def _value(value):
    # None is an internal empty key; it cannot collide with any stored text.
    return None if value is None or not str(value).strip() else str(value)


def product_analysis(filters=None):
    filters = frappe._dict(filters or {})
    dimension = filters.get("dimension", "品牌")
    if not isinstance(dimension, str) or dimension not in DIMENSIONS:
        frappe.throw("请选择有效的分析维度")
    _, _, documents = _scope(filters, PROFILE, "inactive", "停用")
    fields = DIMENSIONS[dimension]
    groups, items = {}, {}
    for doc in sorted({doc.name: doc for doc in documents}.values(), key=lambda doc: doc.name):
        item = _item(doc.item_code, items)
        key = tuple(_value(doc.get(field)) for field, _ in fields)
        group = groups.setdefault(key, [])
        group.append({"document": doc.name, "company": doc.company,
                      "item_code": doc.item_code, "product_name": item.item_name,
                      "status": "停用" if cint(doc.inactive) else "在用"})

    rows = []
    # Sort empties separately while preserving the exact nonempty stored text.
    keys = sorted(groups, key=lambda key: tuple((value is None, value or "") for value in key))
    for index, key in enumerate(keys, 1):
        sources = groups[key]
        values = {field: value for (field, _), value in zip(fields, key, strict=True)}
        empty_note = "；".join(f"{label}未填写" for (_, label), value
                             in zip(fields, key, strict=True) if value is None)
        inactive_count = sum(row["status"] == "停用" for row in sources)
        rows.append({"group_label": f"分组 {index}", "dimension": dimension,
                     **values, "empty_note": empty_note,
                     "record_count": len(sources), "active_count": len(sources) - inactive_count,
                     "inactive_count": inactive_count, "indent": 0, "is_group": 1})
        for source in sources:
            rows.append({"group_label": source["document"], "dimension": dimension,
                         **values, "empty_note": empty_note, **source,
                         "indent": 1, "is_group": 0})

    columns = [
        column("group_label", "分组 / 档案", width=180),
        *[column(field, label, width=160) for field, label in fields],
        column("empty_note", "未填写项", width=180),
        column("record_count", "可读档案数", "Int", width=115),
        column("active_count", "在用档案数", "Int", width=115),
        column("inactive_count", "停用档案数", "Int", width=115),
        column("document", "来源档案", "Link", PROFILE, 180),
        column("company", "公司", "Link", "Company", 180),
        column("item_code", "关联商品", "Link", "Item", 180),
        column("product_name", "商品名称", width=180),
        column("status", "档案状态"),
    ]
    return columns, rows
