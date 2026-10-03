"""Approved sample production sources, without stock or quotation posting.

Approval freezes exact row identities and stock quantities. A production bridge
creates only a normal native draft Work Order under its actual permissions.
The quantity is a making quantity, never a commercial quotation quantity limit.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import hashlib
import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import flt, now_datetime

from jifeng_erp.archive_versions import same_version
from jifeng_erp.flow_quantity import FlowQuantityError, representable_quantity
from jifeng_erp.personnel_separation import _atomic


DOCTYPE = "Jifeng Sample Order"
CHILD = "Jifeng Sample Order Item"
WORK_ORDER = "Work Order"
PARENT_FIELDS = {"company", "customer", "transaction_date", "required_by", "remarks"}
ITEM_FIELDS = {
    "name",
    "sample_identity",
    "item_code",
    "product_profile",
    "stock_uom",
    "qty",
    "bom",
    "requirements",
    "packaging",
    "requirements_attachment",
    "notes",
    "idx",
}
APPROVAL = {"approved_snapshot", "approved_digest", "approved_by", "approved_at"}
WO_SOURCE = {
    "jf_sample_order",
    "jf_sample_order_item",
    "jf_sample_source_digest",
    "jf_sample_request_id",
    "jf_sample_request_digest",
}
WO_REQUIRED = WO_SOURCE | {
    "name",
    "modified",
    "docstatus",
    "company",
    "production_item",
    "stock_uom",
    "qty",
    "bom_no",
}
MAX_SAFE_INT = 9007199254740991
BATCH_LENGTH = 200
_APPROVING = object()
_MAKING = ContextVar("jifeng_sample_work_order_creation", default=None)


class SampleWorkOrderWriteUncertain(RuntimeError):
    """The native savepoint could not prove a rejected request was rolled back."""


def _atomic_write(method):
    atomic = _atomic(method)

    @wraps(method)
    def action(*args, **kwargs):
        try:
            return atomic(*args, **kwargs)
        except Exception as error:
            if any(
                "局部回滚清理失败" in note for note in getattr(error, "__notes__", [])
            ):
                raise SampleWorkOrderWriteUncertain(
                    "工单保存或回滚结果未知，请只读核对请求回执，不要重复提交"
                ) from error
            raise

    return action


def _deny():
    frappe.throw("没有完整读取样品及其原生来源字段的权限", frappe.PermissionError)


def _invalid(message):
    frappe.throw(message)


def _login():
    if frappe.session.user == "Guest":
        _deny()


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 140
    ):
        _invalid("请选择准确的来源名称")
    return value


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        _invalid("生成工单须提供有效 UUID4 请求身份")
    return value


def _version(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _latest(document, version):
    if not same_version(document.modified, version):
        frappe.throw("样品单已修改，请重新读取后操作", frappe.TimestampMismatchError)


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple(
            (key, _freeze(item))
            for key, item in sorted(value.items())
            if not key.startswith("__")
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    # Frappe lazily wraps the persisted integer in its DocStatus int subclass.
    # Reading that property must not look like a business source change.
    if isinstance(value, int) and not isinstance(value, bool):
        return "int", repr(int(value))
    return type(value).__name__, repr(value)


def _columns(doctype, permission="read", parenttype=None):
    names = set(
        get_permitted_fields(
            doctype,
            parenttype=parenttype,
            permission_type=permission,
            ignore_virtual=True,
        )
    )
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(
            meta.get_permlevel_access(
                permission_type="mask", parenttype=parenttype, user=frappe.session.user
            )
        )
        names = {
            name
            for name in names
            if not (
                (field := meta.get_field(name))
                and field.get("mask")
                and (field.permlevel or 0) not in levels
            )
        }
    return names


def _fields(doctype, fields, permission="read", parenttype=None):
    if not set(fields).issubset(_columns(doctype, permission, parenttype)):
        _deny()


def _table(document, fieldname, childtype, fields, permission="read"):
    df = document.meta.get_field(fieldname)
    levels = document.meta.get_permlevel_access(
        permission_type=permission, user=frappe.session.user
    )
    if (
        not df
        or df.fieldtype != "Table"
        or df.options != childtype
        or (df.permlevel or 0) not in levels
    ):
        _deny()
    if (
        frappe.session.user != "Administrator"
        and df.get("mask")
        and (df.permlevel or 0)
        not in document.meta.get_permlevel_access(
            permission_type="mask", user=frappe.session.user
        )
    ):
        _deny()
    _fields(childtype, fields, permission, document.doctype)
    names = set()
    for row in document.get(fieldname) or []:
        if (
            row.name in names
            or row.parent != document.name
            or row.parenttype != document.doctype
            or row.parentfield != fieldname
        ):
            _invalid("原生来源子表身份不完整，请重新读取")
        names.add(row.name)


def _projection(document, fields, permission="read"):
    _fields(document.doctype, fields, permission)
    rows = frappe.get_list(
        document.doctype,
        filters={"name": document.name},
        fields=sorted(fields),
        limit_page_length=1,
    )
    if len(rows) != 1 or not set(fields).issubset(rows[0]):
        _deny()
    for field in fields:
        same = (
            same_version(document.get(field), _version(rows[0][field]))
            if field == "modified"
            else document.get(field) == rows[0][field]
        )
        if not same:
            _invalid("原生来源或权限投影已改变，请重新读取")
    _fields(document.doctype, fields, permission)
    document.check_permission(permission)
    return rows[0]


class _Sources:
    """Complete source reads and a second serial permission/content check."""

    def __init__(self, company):
        self.company = _name(company)
        self.sources = {}
        self.boms = set()
        self.bom_visiting = set()
        self.source("Company", company, {"name", "modified"}, permission="select")

    def source(self, doctype, name, fields=None, full=False, permission="read"):
        name = _name(name)
        document = frappe.get_doc(doctype, name)
        document.check_permission(permission)
        if permission == "read":
            document.check_permission("select")
        fields = set(fields or {"name", "modified"})
        tables = []
        if full:
            fields.update(document.meta.get_valid_columns())
            for df in document.meta.get_table_fields():
                child_fields = set(frappe.get_meta(df.options).get_valid_columns())
                _table(document, df.fieldname, df.options, child_fields)
                tables.append((df.fieldname, df.options, child_fields))
        _projection(document, fields, permission)
        key = (doctype, name)
        previous = self.sources.get(key)
        state = _freeze(document)
        if previous and previous[0] != state:
            _invalid("样品来源在读取期间已改变，请重新核对")
        self.sources[key] = (state, fields, tables, permission)
        return document

    def item(self, name, full=False, active=False):
        document = self.source(
            "Item",
            name,
            {
                "name",
                "modified",
                "stock_uom",
                "disabled",
                "is_stock_item",
                "has_variants",
            },
            full=full,
        )
        if active and (
            document.disabled or not document.is_stock_item or document.has_variants
        ):
            _invalid("样品制作须选择启用的库存商品，不能使用非库存商品或模板")
        uom = self.source(
            "UOM", document.stock_uom, {"name", "modified", "must_be_whole_number"}
        )
        return document, uom

    def bom(self, name, item_code=None, active=True):
        document = self.source("BOM", name, full=True)
        if document.company != self.company or item_code and document.item != item_code:
            _invalid("样品 BOM 必须精确属于本公司及该商品")
        if active and (int(document.docstatus) != 1 or not document.is_active):
            _invalid("样品制作须使用已提交且启用的 BOM")
        if name in self.bom_visiting:
            _invalid("BOM 递归来源存在循环，请核对原生 BOM")
        if name in self.boms:
            return document
        self.boms.add(name)
        self.bom_visiting.add(name)
        item, _ = self.item(document.item, full=True, active=active)
        _decimal(document.quantity)
        if active and document.uom != item.stock_uom:
            _invalid("BOM 成品单位与库存单位不一致，不能推测样品工单数量换算")
        for field in ("items", "exploded_items", "scrap_items"):
            for row in document.get(field) or []:
                if row.get("item_code"):
                    item, _ = self.item(row.item_code, full=True, active=False)
                    if active and item.disabled:
                        _invalid("BOM 实际商品来源已停用，请核对原生 BOM")
                    self._item_defaults(item, active=active)
                if row.get("bom_no"):
                    self.bom(row.bom_no, row.get("item_code"), active=active)
                self._links(row, active=active)
        for row in document.get("operations") or []:
            if row.get("operation"):
                operation = self.source("Operation", row.operation, full=True)
                self._links(operation, active=active)
            if row.get("bom_no"):
                self.bom(row.bom_no, active=active)
            self._links(row, active=active)
        self.bom_visiting.remove(name)
        return document

    def _item_defaults(self, item, active=True):
        if item.get("item_group"):
            group = self.source("Item Group", item.item_group, full=True)
            self._default_links(group, active=active)
        self._default_links(item, active=active)

    def _default_links(self, document, active=True):
        for df in document.meta.get_table_fields():
            for row in document.get(df.fieldname) or []:
                if row.get("company") == self.company:
                    self._links(row, active=active)

    def _links(self, document, active=True):
        for field in (
            "workstation",
            "workstation_type",
            "operation",
            "department",
            "jf_department",
            "source_warehouse",
            "wip_warehouse",
            "fg_warehouse",
            "default_warehouse",
            "warehouse",
        ):
            name = document.get(field)
            if not name:
                continue
            df = document.meta.get_field(field)
            if not df or df.fieldtype != "Link":
                continue
            source = self.source(df.options, name, full=True)
            if (
                df.options in ("Warehouse", "Department")
                and source.get("company") != self.company
            ):
                _invalid("样品工单仓库或部门必须属于本公司")
            if (
                active
                and df.options == "Warehouse"
                and (source.is_group or source.disabled)
            ):
                _invalid("样品工单仓库须为启用的明细仓库")

    def check(self):
        for (doctype, name), (state, fields, tables, permission) in list(
            self.sources.items()
        ):
            document = frappe.get_doc(doctype, name)
            document.check_permission(permission)
            if permission == "read":
                document.check_permission("select")
            _projection(document, fields, permission)
            for field, childtype, child_fields in tables:
                _table(document, field, childtype, child_fields)
            if _freeze(document) != state:
                _invalid("样品的原生来源在操作期间已改变，请重新核对")


def _decimal(value):
    if isinstance(value, bool) or value in (None, ""):
        _invalid("样品制作数量必须是大于零的有限库存数量")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        _invalid("样品制作数量必须是大于零的有限库存数量")
    if not result.is_finite() or result <= 0:
        _invalid("样品制作数量必须是大于零的有限库存数量")
    return result


def _quantity(value, unit=None, precision=None):
    quantity = _decimal(value)
    if precision is None:
        precision = frappe.new_doc(WORK_ORDER).precision("qty")
    try:
        quantity = representable_quantity(quantity, "样品制作库存数量", precision)
    except FlowQuantityError as error:
        _invalid(str(error))
    if Decimal(str(flt(quantity, precision))) != quantity:
        _invalid("样品数量无法按原生数量字段准确保存，请明确调整数量")
    if unit and unit.must_be_whole_number and quantity != quantity.to_integral_value():
        _invalid("此库存单位的样品制作数量必须为准确整数")
    return quantity


def _number_text(value):
    number = format(_decimal(value), "f")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _date(value, required=False):
    if value in (None, "") and not required:
        return None
    try:
        result = value if type(value) is date else date.fromisoformat(value)
    except (ValueError, TypeError):
        _invalid("请填写准确日期")
    return result.isoformat()


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        _invalid("样品要求、包装、附件和备注须保留文本原文")
    return value


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _content(value):
    """BOM content fingerprint excludes volatile audit timestamps at all levels."""
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return {
            key: _content(item)
            for key, item in value.items()
            if key not in {"modified", "modified_by", "creation", "owner"}
            and not key.startswith("__")
        }
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


def _bom_digest(sources, name, item_code=None):
    """Bind the complete nested BOM content, not only the root's row links."""
    contents = {}

    def visit(bom_name, item=None):
        if bom_name in contents:
            return
        document = sources.bom(bom_name, item)
        contents[bom_name] = _content(document)
        for field in ("items", "exploded_items", "scrap_items", "operations"):
            for row in document.get(field) or []:
                if row.get("bom_no"):
                    visit(row.bom_no, row.get("item_code"))

    visit(name, item_code)
    return _hash(contents)


def _sample_fields(document, permission="read"):
    _fields(
        DOCTYPE,
        PARENT_FIELDS | APPROVAL | {"name", "modified", "docstatus"},
        permission,
    )
    _table(
        document,
        "items",
        CHILD,
        ITEM_FIELDS | {"parent", "parenttype", "parentfield"},
        permission,
    )


def _authorize(document, active=False):
    document.check_permission("read")
    _sample_fields(document)
    sources = _Sources(document.company)
    if document.customer:
        sources.source(
            "Customer",
            document.customer,
            {"name", "modified", "disabled"},
            permission="read",
        )
    if not document.get("items"):
        _invalid("样品单至少需要一条准确制作明细")
    for row in document.items:
        item, unit = sources.item(row.item_code, active=active)
        if active and row.stock_uom != item.stock_uom:
            _invalid("样品行库存单位必须与当前商品库存单位准确一致")
        if active:
            _quantity(row.qty, unit)
        else:
            _decimal(row.qty)
        if row.product_profile:
            profile = sources.source(
                "Jifeng Product Profile",
                row.product_profile,
                {"name", "modified", "company", "item_code"},
            )
            if (
                profile.company != document.company
                or profile.item_code != row.item_code
            ):
                _invalid("商品档案须显式匹配当前公司和该样品商品")
        if row.bom:
            sources.bom(row.bom, row.item_code, active=active)
        if row.requirements_attachment:
            files = frappe.get_list(
                "File",
                filters={"file_url": row.requirements_attachment},
                pluck="name",
                limit_page_length=2,
            )
            if len(files) != 1:
                _deny()
            attachment = sources.source(
                "File",
                files[0],
                {
                    "name",
                    "modified",
                    "file_url",
                    "is_private",
                    "attached_to_doctype",
                    "attached_to_name",
                },
            )
            if (
                not attachment.is_private
                or attachment.attached_to_doctype != DOCTYPE
                or attachment.attached_to_name != document.name
            ):
                _invalid("要求附件须为已保存本样品单的可读私有附件")
    return sources


def _snapshot(document, sources=None):
    rows = []
    for row in document.get("items") or []:
        _uuid(row.sample_identity)
        values = {field: row.get(field) for field in ITEM_FIELDS - {"qty"}}
        values["qty"] = _number_text(row.qty)
        for field in ("requirements", "packaging", "requirements_attachment", "notes"):
            values[field] = _text(values[field])
        values["bom_content_digest"] = (
            _bom_digest(sources, row.bom, row.item_code)
            if sources and row.bom
            else None
        )
        rows.append(values)
    return {
        "schema": "sample_approval_v1",
        "name": document.name,
        "company": document.company,
        "customer": document.customer or None,
        "transaction_date": _date(document.transaction_date, required=True),
        "required_by": _date(document.required_by),
        "remarks": _text(document.remarks),
        "items": rows,
    }


def _approved(document, active=False, sources=None):
    if (
        not document.approved_snapshot
        or not document.approved_digest
        or not document.approved_by
        or not document.approved_at
    ):
        _invalid("样品单缺少服务器批准依据，请核对迁移记录")
    try:
        snapshot = json.loads(document.approved_snapshot)
    except (TypeError, ValueError):
        _invalid("样品批准快照不完整，请核对来源")
    if not isinstance(snapshot, dict) or _hash(snapshot) != document.approved_digest:
        _invalid("样品批准内容校验不完整")
    current = _snapshot(document)
    old_rows = snapshot.get("items")
    if not isinstance(old_rows, list) or len(old_rows) != len(current["items"]):
        _invalid("样品批准行身份不完整")
    for before, row in zip(old_rows, current["items"]):
        if not isinstance(before, dict):
            _invalid("样品批准明细不完整")
        row["bom_content_digest"] = before.get("bom_content_digest")
        if (
            active
            and row.get("bom")
            and sources
            and before.get("bom_content_digest")
            != _bom_digest(sources, row["bom"], row["item_code"])
        ):
            _invalid("已批准样品的 BOM 内容已变更，请重新核对批准依据")
    if snapshot != current:
        _invalid("批准后不能修改样品制作依据和行身份")
    return snapshot


def validate_sample_order(document):
    _login()
    previous = document.get_doc_before_save()
    submitted_before = bool(previous and int(previous.docstatus) > 0)
    if submitted_before:
        if any(document.get(field) != previous.get(field) for field in APPROVAL):
            _invalid("服务器批准追溯字段不能修改")
        _approved(document)
        _authorize(document).check()
        return
    if any(document.get(field) not in (None, "") for field in APPROVAL):
        _invalid("样品批准追溯字段只能由服务器生成")
    document.check_permission("create" if document.is_new() else "write")
    _fields(DOCTYPE, PARENT_FIELDS, "write")
    _table(
        document,
        "items",
        CHILD,
        ITEM_FIELDS - {"stock_uom", "sample_identity", "name", "idx"},
        "write",
    )
    old = {row.name: row for row in previous.items} if previous else {}
    identities = set()
    sources = _Sources(document.company)
    for row in document.get("items") or []:
        saved = old.get(row.name)
        if saved:
            if row.sample_identity != saved.sample_identity:
                _invalid("已有样品明细身份不能修改")
        else:
            if row.sample_identity:
                _invalid("新样品明细身份只能由服务器生成")
            owner = frappe.db.get_value(CHILD, row.name, "parent")
            if owner and owner != document.name:
                _invalid("样品明细不能移用其它单据的行身份")
            row.sample_identity = str(uuid4())
        _uuid(row.sample_identity)
        if row.sample_identity in identities:
            _invalid("每条样品明细必须有独立身份")
        identities.add(row.sample_identity)
        item, unit = sources.item(row.item_code, active=True)
        if row.stock_uom and row.stock_uom != item.stock_uom:
            _invalid("样品数量只使用商品库存单位，不推测换算")
        row.stock_uom = item.stock_uom
        row.qty = float(_quantity(row.qty, unit))
        for field in ("requirements", "packaging", "requirements_attachment", "notes"):
            row.set(field, _text(row.get(field)))
    transaction_date, required_by = (
        _date(document.transaction_date, True),
        _date(document.required_by),
    )
    if required_by and required_by < transaction_date:
        _invalid("要求完成日期不能早于样品日期")
    sources.check()
    _authorize(document, active=True).check()


def before_submit_sample_order(document):
    _login()
    document.check_permission("submit")
    if document.is_new():
        _invalid("请先保存样品草稿再批准")
    stored = frappe.get_doc(DOCTYPE, document.name, for_update=True)
    stored.check_permission("read")
    previous = document.get_doc_before_save()
    if (
        int(stored.docstatus) != 0
        or not previous
        or not same_version(stored.modified, _version(previous.modified))
    ):
        _invalid("样品草稿已改变，请重新打开后批准")
    if any(document.get(field) not in (None, "") for field in APPROVAL):
        _invalid("批准依据只能由服务器生成")
    sources = _authorize(document, active=True)
    snapshot = _snapshot(document, sources)
    sources.check()
    document.approved_snapshot = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    document.approved_digest = _hash(snapshot)
    document.approved_by = frappe.session.user
    document.approved_at = now_datetime()
    document.flags.jf_sample_approved = _APPROVING


def _downstream(document):
    """Internal raw completeness guard, including hidden downstream documents."""
    rows = [row.name for row in document.items]
    for doctype, field in (
        (WORK_ORDER, "jf_sample_order"),
        ("Quotation", "jf_sample_order"),
    ):
        meta = frappe.get_meta(doctype)
        if not meta.has_field(field):
            continue
        if frappe.db.get_value(
            doctype, {field: document.name, "docstatus": ["<", 2]}, "name"
        ):
            _invalid("存在未取消的样品工单或报价，请先处理下游单据")
        if (
            doctype == WORK_ORDER
            and rows
            and meta.has_field("jf_sample_order_item")
            and frappe.db.get_value(
                WORK_ORDER,
                {"jf_sample_order_item": ["in", rows], "docstatus": ["<", 2]},
                "name",
            )
        ):
            _invalid("存在未取消或来源需核对的样品工单，请先处理下游单据")
    meta = frappe.get_meta("Quotation Item")
    if meta.has_field("jf_sample_order") or meta.has_field("jf_sample_order_item"):
        tests, params = [], []
        if meta.has_field("jf_sample_order"):
            tests.append("q.jf_sample_order=%s")
            params.append(document.name)
        if meta.has_field("jf_sample_order_item") and rows:
            tests.append(
                "q.jf_sample_order_item IN (" + ",".join(["%s"] * len(rows)) + ")"
            )
            params.extend(rows)
        if tests and frappe.db.sql(
            "SELECT q.name FROM `tabQuotation Item` q LEFT JOIN `tabQuotation` p ON p.name=q.parent "
            "WHERE ("
            + " OR ".join(tests)
            + ") AND (p.name IS NULL OR p.docstatus<2) LIMIT 1",
            params,
        ):
            _invalid("存在未取消或无法完整核对的样品报价，请先处理下游单据")


def before_cancel_sample_order(document):
    document.check_permission("cancel")
    stored = frappe.get_doc(DOCTYPE, document.name, for_update=True)
    stored.check_permission("read")
    _approved(document)
    if any(stored.get(field) != document.get(field) for field in APPROVAL):
        _invalid("取消不得改写样品批准追溯信息")
    _authorize(document).check()
    _downstream(stored)


def prevent_sample_order_delete(document):
    _invalid("样品单须保留批准和下游追溯，不能删除")


def prevent_sample_order_rename(document, *args, **kwargs):
    _invalid("样品单不能改名或合并，以保留行身份追溯")


def sample_order_onload(document):
    _login()
    sources = _authorize(document)
    if int(document.docstatus) > 0:
        _approved(document)
    sources.check()


def read_sample_source(sample_order, version=None, for_update=False):
    """Non-whitelisted shared source gate for the WO and quotation bridges."""
    _login()
    document = frappe.get_doc(DOCTYPE, _name(sample_order), for_update=for_update)
    document.check_permission("read")
    if int(document.docstatus) != 1:
        _invalid("请使用已批准且未取消的样品单")
    if version is not None:
        _latest(document, version)
    _projection(document, PARENT_FIELDS | APPROVAL | {"name", "modified", "docstatus"})
    sources = _authorize(document, active=True)
    _approved(document, active=True, sources=sources)
    sources.check()
    document.check_permission("read")
    _sample_fields(document)
    return document


def _request_digest(document, row, bom, bom_digest):
    return _hash(
        {
            "sample_order": document.name,
            "sample_item": row.name,
            "sample_identity": row.sample_identity,
            "approved_digest": document.approved_digest,
            "company": document.company,
            "item_code": row.item_code,
            "stock_uom": row.stock_uom,
            "qty": _number_text(row.qty),
            "bom": bom,
            "bom_content_digest": bom_digest,
        }
    )


def _work_source(document):
    if document.get("jf_flow_order"):
        _invalid("工单不能同时使用样品和流程单两种来源身份")
    _fields(WORK_ORDER, WO_REQUIRED)
    sample = read_sample_source(document.jf_sample_order, for_update=True)
    rows = [row for row in sample.items if row.name == document.jf_sample_order_item]
    if len(rows) != 1:
        _invalid("工单必须准确关联本批准单的一条明细，不能按商品合并")
    row = rows[0]
    if (
        document.company != sample.company
        or document.production_item != row.item_code
        or document.stock_uom != row.stock_uom
        or _decimal(document.qty) != _decimal(row.qty)
        or document.jf_sample_source_digest != sample.approved_digest
    ):
        _invalid("样品工单的公司、商品、单位、制作数量或批准依据不一致")
    if row.bom and document.bom_no != row.bom:
        _invalid("工单不能替换样品已批准的 BOM")
    _uuid(document.jf_sample_request_id)
    sources = _Sources(sample.company)
    bom = sources.bom(_name(document.bom_no), row.item_code)
    if document.jf_sample_request_digest != _request_digest(
        sample, row, bom.name, _bom_digest(sources, bom.name, row.item_code)
    ):
        _invalid("样品工单请求内容与服务器来源不一致")
    _generated_sources(document, sources)
    sources.check()
    return sample, row


def validate_sample_work_order(document, method=None):
    previous = document.get_doc_before_save()
    values = [document.get(field) for field in WO_SOURCE]
    previous_values = [previous.get(field) for field in WO_SOURCE] if previous else []
    if not any(values) and not any(previous_values):
        return
    if not all(values):
        _invalid("样品工单追溯字段必须完整，不允许残缺身份")
    if not document.is_new():
        if not previous or any(
            previous.get(field) != document.get(field) for field in WO_SOURCE
        ):
            _invalid("样品工单追溯身份不能修改")
        progress_update = (
            int(previous.docstatus) == 1
            and int(document.docstatus) == 1
            and (
                getattr(document, "_action", None) == "update_after_submit"
                or method == "before_update_after_submit"
            )
        )
        if int(document.docstatus) == 2 or progress_update:
            if any(
                previous.get(field) != document.get(field)
                for field in (
                    "company",
                    "production_item",
                    "stock_uom",
                    "qty",
                    "bom_no",
                    "jf_flow_order",
                )
            ):
                _invalid("取消或报工进度更新不能改写既有样品制作依据")
            history = frappe.get_doc(DOCTYPE, document.jf_sample_order, for_update=True)
            history.check_permission("read")
            _sample_fields(history)
            _approved(history)
            if history.approved_digest != document.jf_sample_source_digest:
                _invalid("工单历史批准来源不一致")
            return
    elif _MAKING.get() is not document:
        _invalid("样品工单须从批准样品入口生成，不能伪造服务器请求身份")
    sample, row = _work_source(document)
    other = frappe.db.get_value(
        WORK_ORDER,
        {
            "jf_sample_order": sample.name,
            "jf_sample_order_item": row.name,
            "docstatus": ["<", 2],
            "name": ["!=", document.name or ""],
        },
        "name",
    )
    if other:
        _invalid("该批准样品行已有有效工单，请读取已有工单")


def prevent_sample_work_order_delete(document, method=None):
    persisted = (
        frappe.db.get_value(WORK_ORDER, document.name, "jf_sample_order")
        if document.name
        else None
    )
    if persisted or any(document.get(field) for field in WO_SOURCE):
        _invalid("样品工单须保留请求和取消历史，不能删除")


def prevent_sample_work_order_rename(document, method=None, *args, **kwargs):
    persisted = (
        frappe.db.get_value(WORK_ORDER, document.name, "jf_sample_order")
        if document.name
        else None
    )
    if persisted or any(document.get(field) for field in WO_SOURCE):
        _invalid("样品工单不能改名或合并，以保留回执追溯")


def _receipt(document):
    document.check_permission("read")
    document.check_permission("select")
    _projection(document, WO_REQUIRED)
    sample = frappe.get_doc(DOCTYPE, document.jf_sample_order)
    sample.check_permission("read")
    _sample_fields(sample)
    _authorize(sample).check()
    _approved(sample)
    rows = [row for row in sample.items if row.name == document.jf_sample_order_item]
    if sample.approved_digest != document.jf_sample_source_digest or len(rows) != 1:
        _invalid("样品工单回执来源不完整")
    row = rows[0]
    if (
        document.company != sample.company
        or document.production_item != row.item_code
        or document.stock_uom != row.stock_uom
        or _decimal(document.qty) != _decimal(row.qty)
        or row.bom
        and document.bom_no != row.bom
        or not isinstance(document.jf_sample_request_digest, str)
        or len(document.jf_sample_request_digest) != 64
        or any(
            letter not in "0123456789abcdef"
            for letter in document.jf_sample_request_digest
        )
    ):
        _invalid("样品工单回执的制作内容或校验不完整")
    _uuid(document.jf_sample_request_id)
    return {
        "status": "found",
        "doctype": WORK_ORDER,
        "name": document.name,
        "request_id": document.jf_sample_request_id,
    }


@frappe.whitelist(methods=["GET"])
def get_sample_work_order_request(request_id):
    _login()
    request_id = _uuid(request_id)
    if not frappe.has_permission(WORK_ORDER, "read", throw=False):
        _deny()
    _fields(WORK_ORDER, WO_REQUIRED)
    name = frappe.db.get_value(WORK_ORDER, {"jf_sample_request_id": request_id}, "name")
    if not name:
        return {"status": "absent", "doctype": WORK_ORDER, "request_id": request_id}
    document = frappe.get_doc(WORK_ORDER, name)
    return _receipt(document)


@frappe.whitelist(methods=["POST"])
@_atomic_write
def make_sample_work_order(
    sample_order, sample_item, version, bom=None, request_id=None
):
    _login()
    request_id = _uuid(request_id)
    _fields(WORK_ORDER, WO_REQUIRED)
    # Scope lock precedes receipt and effective-row checks. All native creation
    # paths call the same source validator, so two requests cannot create twice.
    sample = read_sample_source(sample_order, version, for_update=True)
    rows = [row for row in sample.items if row.name == _name(sample_item)]
    if len(rows) != 1:
        _invalid("请选择当前批准单的准确样品明细")
    row = rows[0]
    bom_name = _name(bom or row.bom)
    if row.bom and bom_name != row.bom:
        _invalid("不能替换样品已批准的 BOM")
    sources = _Sources(sample.company)
    native_bom = sources.bom(bom_name, row.item_code)
    digest = _request_digest(
        sample, row, bom_name, _bom_digest(sources, native_bom.name, row.item_code)
    )
    name = frappe.db.get_value(WORK_ORDER, {"jf_sample_request_id": request_id}, "name")
    if name:
        existing = frappe.get_doc(WORK_ORDER, name)
        receipt = _receipt(existing)
        if (
            existing.jf_sample_request_digest != digest
            or existing.jf_sample_order != sample.name
            or existing.jf_sample_order_item != row.name
        ):
            _invalid("该请求已用于不同样品内容，请只读核对原回执")
        return receipt
    active = frappe.db.get_value(
        WORK_ORDER,
        {
            "jf_sample_order": sample.name,
            "jf_sample_order_item": row.name,
            "docstatus": ["<", 2],
        },
        "name",
    )
    if active:
        _invalid("该样品行已有有效工单，请读取已有工单；当前新请求未记录")
    work = frappe.new_doc(WORK_ORDER)
    work.check_permission("create")
    _fields(
        WORK_ORDER,
        {"company", "production_item", "stock_uom", "qty", "bom_no"},
        "write",
    )
    work.company, work.production_item, work.stock_uom = (
        sample.company,
        row.item_code,
        row.stock_uom,
    )
    unit = sources.source(
        "UOM", row.stock_uom, {"name", "modified", "must_be_whole_number"}
    )
    work.qty, work.bom_no = (
        float(_quantity(row.qty, unit, work.precision("qty"))),
        bom_name,
    )
    # Native standard creation sets this before expansion. Link fetching during
    # insert is too late for operation sequence and multi-level BOM semantics.
    work.track_semi_finished_goods = native_bom.track_semi_finished_goods
    work.jf_sample_order, work.jf_sample_order_item = sample.name, row.name
    work.jf_sample_source_digest = sample.approved_digest
    work.jf_sample_request_id, work.jf_sample_request_digest = request_id, digest
    context = _MAKING.set(work)
    try:
        work.get_items_and_operations_from_bom()
        # Native expansion uses raw sources; authorize every generated source
        # before normal insertion and recheck defaults added by native validate.
        _generated_sources(work, sources)
        validate_sample_work_order(work)
        sources.check()
        work.insert()
        _generated_sources(work, sources)
        sources.check()
    finally:
        _MAKING.reset(context)
    stored = frappe.get_doc(WORK_ORDER, work.name)
    if int(stored.docstatus) != 0:
        _invalid("样品桥只能生成草稿工单，不得自动批准或过账")
    _work_source(stored)
    return _receipt(stored)


def _generated_sources(work, sources):
    sources._links(work)
    for row in work.get("required_items") or []:
        item, _ = sources.item(row.item_code, full=True)
        sources._item_defaults(item)
        sources._links(row)
    for row in work.get("operations") or []:
        sources._links(row)


def _dto(document, include_work_orders=True):
    sample_order_onload(document)
    _projection(document, PARENT_FIELDS | APPROVAL | {"name", "modified", "docstatus"})
    result = {
        "name": document.name,
        "version": _version(document.modified),
        "docstatus": int(document.docstatus),
        **{field: _version(document.get(field)) for field in PARENT_FIELDS},
        "approved_digest": document.approved_digest or None,
        "items": [
            {
                **{field: _version(row.get(field)) for field in ITEM_FIELDS - {"qty"}},
                "qty": _number_text(row.qty),
            }
            for row in document.items
        ],
    }
    result["work_orders"] = []
    result["can_view_work_orders"] = bool(
        include_work_orders
        and frappe.has_permission(WORK_ORDER, "read", throw=False)
        and WO_REQUIRED.issubset(_columns(WORK_ORDER))
    )
    if result["can_view_work_orders"] and frappe.get_meta(WORK_ORDER).has_field(
        "jf_sample_order"
    ):
        names = frappe.get_list(
            WORK_ORDER,
            filters={"jf_sample_order": document.name},
            pluck="name",
            limit_page_length=0,
        )
        for name in names:
            work = frappe.get_doc(WORK_ORDER, name)
            _receipt(work)
            result["work_orders"].append(
                {
                    "name": name,
                    "sample_item": work.jf_sample_order_item,
                    "request_id": work.jf_sample_request_id,
                    "docstatus": int(work.docstatus),
                }
            )
    return result


@frappe.whitelist(methods=["GET"])
def get_sample_order(name):
    _login()
    document = frappe.get_doc(DOCTYPE, _name(name))
    result = _dto(document)
    current = frappe.get_doc(DOCTYPE, name)
    if _freeze(current) != _freeze(document) or result != _dto(current):
        _invalid("样品单或来源权限已改变，请重新读取")
    return result


def _integer(value, label, maximum=MAX_SAFE_INT, zero=False):
    if (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 16
    ):
        value = int(value)
    if type(value) is not int or not (0 if zero else 1) <= value <= maximum:
        _invalid(label + "无效")
    return value


@frappe.whitelist(methods=["GET"])
def query_sample_orders(company, filters=None, start=0, page_length=25):
    _login()
    _Sources(company).check()
    if not frappe.has_permission(DOCTYPE, "read", throw=False):
        _deny()
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except (ValueError, TypeError):
            _invalid("请输入有效样品筛选对象")
    filters = filters or {}
    if not isinstance(filters, dict) or not set(filters).issubset(
        {"docstatus", "customer", "search"}
    ):
        _invalid("样品筛选包含不支持的参数")
    selected = {"company": company}
    if "docstatus" in filters:
        status = _integer(filters["docstatus"], "批准状态", maximum=2, zero=True)
        selected["docstatus"] = status
    if filters.get("customer"):
        selected["customer"] = _name(filters["customer"])
    search = filters.get("search", "")
    if not isinstance(search, str) or len(search) > 140:
        _invalid("样品关键词过长或无效")
    start = _integer(start, "起点", zero=True)
    page_length = _integer(page_length, "每页条数", maximum=100)
    if page_length not in (25, 50, 100):
        _invalid("样品每页条数仅支持25、50、100")
    query = {
        "filters": selected,
        "pluck": "name",
        "order_by": "transaction_date desc, name asc",
    }
    if search:
        _fields(DOCTYPE, {"name", "remarks"})
        query["or_filters"] = {
            field: ["like", "%" + search + "%"] for field in ("name", "remarks")
        }

    def scan():
        names, offset = [], 0
        while True:
            batch = frappe.get_list(
                DOCTYPE, **query, limit_start=offset, limit_page_length=BATCH_LENGTH
            )
            if len(batch) != len(set(batch)) or set(batch) & set(names):
                _invalid("样品查询来源已改变，请重新读取")
            names.extend(batch)
            if len(batch) < BATCH_LENGTH:
                return names
            offset += len(batch)

    names, checked = scan(), []
    for name in names:
        try:
            document = frappe.get_doc(DOCTYPE, name)
            view = _dto(document, include_work_orders=False)
        except frappe.PermissionError:
            continue
        checked.append((document, view))
    if names != scan():
        _invalid("样品查询范围已改变，请重新读取")
    for document, view in checked:
        current = frappe.get_doc(DOCTYPE, document.name)
        if _freeze(current) != _freeze(document) or view != _dto(
            current, include_work_orders=False
        ):
            _invalid("样品查询的来源或权限已改变，请重新读取")
    for document, _ in checked:
        document.check_permission("read")
        _sample_fields(document)
    total = len(checked)
    start = min(start, ((total - 1) // page_length) * page_length) if total else 0
    return {
        "company": company,
        "filters": filters,
        "start": start,
        "page_length": page_length,
        "total": total,
        "rows": [row[1] for row in checked[start : start + page_length]],
        "readonly": True,
    }
