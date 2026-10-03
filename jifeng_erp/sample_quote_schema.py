"""Traceability fields only; installing these fields grants no native roles."""


def _source_fields():
    return [
        {"fieldname": "jf_sample_order", "fieldtype": "Link", "options": "Jifeng Sample Order",
         "label": "样品制作单", "read_only": 1, "no_copy": 1, "search_index": 1},
        {"fieldname": "jf_sample_order_item", "fieldtype": "Data", "label": "准确样品明细",
         "read_only": 1, "no_copy": 1, "hidden": 1, "print_hide": 1, "search_index": 1},
        {"fieldname": "jf_sample_source_digest", "fieldtype": "Data", "label": "样品审核快照校验",
         "read_only": 1, "no_copy": 1, "hidden": 1, "print_hide": 1},
    ]


def _request_fields():
    return [
        {"fieldname": "jf_sample_request_id", "fieldtype": "Data", "label": "创建请求回执",
         "read_only": 1, "no_copy": 1, "hidden": 1, "print_hide": 1, "unique": 1},
        {"fieldname": "jf_sample_request_digest", "fieldtype": "Data", "label": "创建请求校验",
         "read_only": 1, "no_copy": 1, "hidden": 1, "print_hide": 1},
    ]


def sample_custom_fields():
    def snapshot():
        return {"fieldname": "jf_sample_source_snapshot", "fieldtype": "Long Text",
                "label": "准确样品来源快照", "read_only": 1, "no_copy": 1,
                "hidden": 1, "print_hide": 1}

    return {
        "Work Order": _source_fields() + _request_fields(),
        "Quotation": [_source_fields()[0]] + _request_fields(),
        "Quotation Item": _source_fields() + [snapshot(),
            {"fieldname": "jf_requirements", "fieldtype": "Small Text", "label": "定制/工艺要求"},
            {"fieldname": "jf_packaging", "fieldtype": "Small Text", "label": "包装要求"},
            {"fieldname": "jf_requirements_attachment", "fieldtype": "Attach", "label": "要求附件"},
            {"fieldname": "jf_make_flow", "fieldtype": "Check", "label": "生成集沣流程单"}],
        "Sales Order Item": _source_fields() + [snapshot()],
        "Jifeng Flow Order": _source_fields() + [snapshot(),
            {"fieldname": "jf_source_quotation", "fieldtype": "Link", "options": "Quotation",
             "label": "来源报价单", "read_only": 1, "no_copy": 1},
            {"fieldname": "jf_source_quotation_item", "fieldtype": "Data", "label": "准确报价明细",
             "read_only": 1, "no_copy": 1, "hidden": 1, "print_hide": 1}],
    }
