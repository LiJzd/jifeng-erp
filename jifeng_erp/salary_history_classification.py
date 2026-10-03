"""Explicit company/component versions for saved Salary Slip end dates.

Rules never infer a category from a component's display name. Internal EXISTS
queries protect history even when User Permissions hide a salary slip or rule;
their results only produce a generic refusal and never disclose identities.
"""

from datetime import date, datetime
import hashlib
import json

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import now_datetime, today

from jifeng_erp.archive_versions import same_version


DOCTYPE = "Jifeng Salary History Rule"
EARNING_BUCKETS = frozenset({
    "basic", "time", "piece", "housing", "full_attendance", "meal", "transport",
    "night_snack", "overtime", "introduction", "assessment", "other_earning",
})
DEDUCTION_BUCKETS = frozenset({
    "utilities", "social_insurance", "fine", "loan", "retained_pay",
    "accommodation", "other_deduction",
})
PARENTFIELDS = {"earnings": "Earning", "deductions": "Deduction"}
CLASSIFICATION_MODES = frozenset({"component", "wage_breakdown"})
CONFIG_FIELDS = (
    "company", "salary_component", "parentfield", "effective_from", "bucket",
    "classification_mode", "extra_bucket",
)
PUBLICATION_FIELDS = ("publication_reason", "published_by", "published_at", "historical_publication")
RULE_FIELDS = ("name", "modified", "docstatus", *CONFIG_FIELDS, "binding_key")
BATCH_SIZE = 200
_HISTORICAL_PUBLICATION = object()


def _get(document, field):
    return document.get(field) if isinstance(document, dict) else getattr(document, field, None)


def _deny():
    frappe.throw("没有读取或选择工资历史分类规则及其来源所需的权限", frappe.PermissionError)


def _login():
    if frappe.session.user == "Guest":
        _deny()


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 140:
        frappe.throw("请选择准确的公司、工资项目或规则编号")
    return value


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    try:
        if isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    frappe.throw("工资历史分类需要有效日期")


def _stamp(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.isoformat()
    if isinstance(value, str) and same_version(value, value):
        return datetime.fromisoformat(value).isoformat()
    frappe.throw("工资历史分类规则版本无效，请重新查询")


def _fields(doctype, fields, permission="read"):
    permitted = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if not set(fields).issubset(permitted):
        _deny()
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        if any((field := meta.get_field(name)) and field.get("mask")
               and (field.permlevel or 0) not in levels for name in fields):
            _deny()


def _access(document, *permissions):
    if not all(frappe.has_permission(document.doctype, permission, doc=document, throw=False)
               for permission in permissions):
        _deny()


def _load(doctype, name):
    try:
        document = frappe.get_doc(doctype, _name(name))
    except (frappe.DoesNotExistError, frappe.PermissionError):
        _deny()
    _access(document, "read", "select")
    return document


def _company(company, *, lock=False):
    document = _load("Company", company)
    _fields("Company", {"name"})
    if document.name != company:
        _deny()
    if lock:
        # Existing native Rule submit/cancel locks the Rule before this Company.
        # Explicit publication follows that same order. Native Salary Slip
        # submission is independent and is not modified here.
        if not frappe.db.get_value("Company", company, "name", for_update=True):
            _deny()
    return document


def _component(component):
    document = _load("Salary Component", component)
    _fields("Salary Component", {"name", "type"})
    if document.name != component:
        _deny()
    return document


def binding_key(company, component, effective_from):
    """The unique business key intentionally excludes parentfield and bucket."""
    return hashlib.sha256(json.dumps([
        _name(company), _name(component), _day(effective_from),
    ], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _config(document):
    result = {field: _get(document, field) for field in CONFIG_FIELDS}
    _name(result["company"])
    _name(result["salary_component"])
    result["effective_from"] = _day(result["effective_from"])
    if result["parentfield"] not in PARENTFIELDS:
        frappe.throw("工资历史分类只支持收入或员工扣款明细")
    buckets = EARNING_BUCKETS if result["parentfield"] == "earnings" else DEDUCTION_BUCKETS
    if result["bucket"] not in buckets:
        frappe.throw("请选择与收入或扣款明细一致的工资历史分类")
    if result["classification_mode"] not in CLASSIFICATION_MODES:
        frappe.throw("请选择工资项目分类或准确计薪来源拆分模式")
    result["extra_bucket"] = result["extra_bucket"] or ""
    if result["classification_mode"] == "wage_breakdown":
        if result["parentfield"] != "earnings" or result["bucket"] != "other_earning":
            frappe.throw("准确计薪来源拆分须选择收入明细和其他收入分类")
        if result["extra_bucket"] not in EARNING_BUCKETS:
            frappe.throw("准确计薪来源拆分必须明确附加费的收入分类")
    elif result["extra_bucket"]:
        frappe.throw("工资项目分类模式不填写附加费分类")
    return result


def _pack(document):
    _access(document, "read", "select")
    _fields(DOCTYPE, RULE_FIELDS)
    values = _config(document)
    _company(values["company"])
    _component(values["salary_component"])
    expected = binding_key(values["company"], values["salary_component"], values["effective_from"])
    if _get(document, "binding_key") != expected or int(_get(document, "docstatus") or 0) != 1:
        frappe.throw("工资历史分类规则已改变或未发布，请重新查询")
    return {"name": _name(document.name), "modified": _stamp(document.modified), "docstatus": 1,
            **values, "binding_key": expected}


def _complete(company, names):
    """A missing readable rule must not turn into an unclassified salary row."""
    parameters = {"company": company}
    excluded = ""
    if names:
        parameters["names"] = tuple(names)
        excluded = " AND name NOT IN %(names)s"
    result = frappe.db.sql(
        "SELECT EXISTS(SELECT 1 FROM `tabJifeng Salary History Rule`"
        " WHERE company = %(company)s AND docstatus = 1" + excluded + ")",
        parameters,
    )
    if result and result[0][0]:
        _deny()


def load_rules(company):
    """Read all submitted company rules, with native permissions and masking."""
    _login()
    _company(_name(company))
    if not frappe.has_permission(DOCTYPE, "read") or not frappe.has_permission(DOCTYPE, "select"):
        _deny()
    _fields(DOCTYPE, RULE_FIELDS)
    rules, offset = [], 0
    while True:
        listed = frappe.get_list(DOCTYPE, filters={"company": company, "docstatus": 1},
                                 fields=list(RULE_FIELDS), order_by="effective_from asc, name asc",
                                 limit_start=offset, limit_page_length=BATCH_SIZE)
        for row in listed:
            if not set(RULE_FIELDS).issubset(row):
                _deny()
            document = _load(DOCTYPE, row["name"])
            current = _pack(document)
            projected = {field: row[field] for field in RULE_FIELDS}
            projected["effective_from"], projected["modified"] = (
                _day(projected["effective_from"]), _stamp(projected["modified"]))
            projected["extra_bucket"] = projected["extra_bucket"] or ""
            if current != projected or current["company"] != company:
                frappe.throw("工资历史分类规则已改变，请重新查询")
            rules.append(current)
        if len(listed) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    names = [rule["name"] for rule in rules]
    if len(names) != len(set(names)):
        frappe.throw("工资历史分类规则重复，请重新查询")
    _complete(company, names)
    return rules


def resolve_rule(rules, component, parentfield, as_of_date):
    """Select the latest exact binding at saved Salary Slip.end_date; no writes."""
    _name(component)
    as_of_date = _day(as_of_date)
    if parentfield not in PARENTFIELDS:
        frappe.throw("工资历史分类只支持收入或员工扣款明细")
    if len({_get(rule, "company") for rule in rules}) > 1:
        frappe.throw("工资历史分类规则不能混用公司")
    matches = []
    for rule in rules:
        if _get(rule, "salary_component") == component and _get(rule, "parentfield") == parentfield:
            values = _config(rule)
            if values["effective_from"] <= as_of_date:
                matches.append(rule)
    if not matches:
        return None
    matches.sort(key=lambda rule: _day(_get(rule, "effective_from")), reverse=True)
    if len(matches) > 1 and _day(_get(matches[0], "effective_from")) == _day(_get(matches[1], "effective_from")):
        frappe.throw("工资历史分类存在重复生效日期，无法准确分类")
    return matches[0]


def guard_rule_snapshot(rules, company=None):
    """Recheck sources and complete visibility before returning or exporting."""
    _login()
    companies = {_get(rule, "company") for rule in rules}
    if company is not None:
        _company(_name(company))
        companies.add(company)
    if len(companies) > 1:
        frappe.throw("工资历史分类规则不能混用公司")
    for rule in rules:
        if _pack(_load(DOCTYPE, _get(rule, "name"))) != dict(rule):
            frappe.throw("工资历史分类规则已改变，请重新查询")
    if companies:
        _complete(next(iter(companies)), [_get(rule, "name") for rule in rules])
    return True


def _exists(sql, parameters):
    result = frappe.db.sql("SELECT EXISTS(" + sql + ")", parameters)
    return bool(result and result[0][0])


def _previous_publication(document):
    return _exists("SELECT 1 FROM `tabJifeng Salary History Rule` WHERE company = %(company)s"
                   " AND salary_component = %(component)s AND docstatus IN (1, 2) AND name != %(name)s",
                   {"company": document.company, "component": document.salary_component, "name": document.name or ""})


def _salary_exists(document):
    # No Salary Slip read permission is used here. Hidden submitted history is
    # equally protected; only a boolean remains inside this module.
    return _exists("SELECT 1 FROM `tabSalary Slip` ss INNER JOIN `tabSalary Detail` sd"
                   " ON sd.parent = ss.name AND sd.parenttype = 'Salary Slip'"
                   " WHERE ss.docstatus = 1 AND ss.company = %(company)s"
                   " AND ss.end_date >= %(effective)s AND sd.salary_component = %(component)s"
                   " AND sd.parentfield = %(parentfield)s",
                   {"company": document.company, "component": document.salary_component,
                    "effective": _day(document.effective_from), "parentfield": document.parentfield})


def validate_rule(document):
    _login()
    before = getattr(document, "get_doc_before_save", lambda: None)()
    if before and int(_get(before, "docstatus") or 0) in (1, 2):
        frappe.throw("已发布工资历史分类规则不能修改，请追加未来版本")
    document.check_permission("create" if document.is_new() else "write")
    _fields(DOCTYPE, CONFIG_FIELDS, "write")
    values = _config(document)
    _company(values["company"])
    component = _component(values["salary_component"])
    if component.type != PARENTFIELDS[values["parentfield"]]:
        frappe.throw("工资项目类型与收入或员工扣款明细不一致")
    for field, value in values.items():
        setattr(document, field, value)
    document.binding_key = binding_key(document.company, document.salary_component, document.effective_from)
    for field in PUBLICATION_FIELDS:
        # Publication audit fields cannot be supplied through forms or imports.
        setattr(document, field, _get(before, field) if before else None)


def before_submit_rule(document):
    _login()
    document.check_permission("submit")
    _access(document, "read", "select")
    _fields(DOCTYPE, CONFIG_FIELDS)
    _config(document)
    _company(document.company, lock=True)
    _component(document.salary_component)
    parameters = {"company": document.company, "component": document.salary_component,
                  "effective": _day(document.effective_from), "name": document.name or ""}
    duplicate = _exists("SELECT 1 FROM `tabJifeng Salary History Rule` WHERE binding_key = %(key)s"
                        " AND name != %(name)s", {"key": document.binding_key, "name": document.name or ""})
    if duplicate:
        frappe.throw("该公司工资项目已有相同生效日期的分类规则")
    previous = _previous_publication(document)
    if previous:
        if _day(document.effective_from) <= _day(today()):
            frappe.throw("工资历史分类后续版本必须从未来日期生效")
        if _exists("SELECT 1 FROM `tabJifeng Salary History Rule` WHERE company = %(company)s"
                   " AND salary_component = %(component)s AND docstatus IN (1, 2)"
                   " AND effective_from >= %(effective)s AND name != %(name)s", parameters):
            frappe.throw("工资历史分类后续版本必须追加在已发布版本之后")
        if _salary_exists(document):
            frappe.throw("该生效日期已覆盖已提交工资期间，请选择未来未使用期间")
    elif _salary_exists(document) and getattr(document.flags, "salary_history_publish_token", None) is not _HISTORICAL_PUBLICATION:
        frappe.throw("首次分类已有工资历史须由主管明确发布并填写原因")
    # A JSON payload can supply flag names, but cannot forge this in-process
    # object. Only the explicit, permission-checked native action sets it.
    requested = getattr(document.flags, "salary_history_publish_token", None) is _HISTORICAL_PUBLICATION
    if requested and previous:
        frappe.throw("历史首次发布仅用于尚未发布规则的工资项目")
    reason = getattr(document.flags, "salary_history_publication_reason", "")
    if requested and (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500):
        frappe.throw("历史首次发布须填写一至五百字的分类依据")
    document.publication_reason = reason.strip() if requested else ""
    document.published_by = frappe.session.user
    document.published_at = now_datetime()
    document.historical_publication = int(bool(requested))


def prevent_rule_update(document):
    frappe.throw("已发布工资历史分类规则不能修改，请追加未来版本")


def before_cancel_rule(document):
    _login()
    # savedocs cancellation can skip validate and carry altered JSON. Use the
    # persisted original to guard every classification and publication field.
    original = _load(DOCTYPE, document.name)
    _fields(DOCTYPE, {*RULE_FIELDS, *PUBLICATION_FIELDS})
    if int(_get(original, "docstatus") or 0) != 1:
        frappe.throw("工资历史分类规则已改变，请重新读取")
    for field in (*CONFIG_FIELDS, "binding_key", *PUBLICATION_FIELDS):
        current, saved = _get(document, field), _get(original, field)
        if field == "effective_from":
            current, saved = _day(current), _day(saved)
        elif field == "published_at":
            current, saved = _stamp(current), _stamp(saved)
        else:
            current, saved = str(current or ""), str(saved or "")
        if current != saved:
            frappe.throw("取消工资历史分类规则不能修改已发布内容")
    _company(original.company, lock=True)
    _component(original.salary_component)
    used = _exists("SELECT 1 FROM `tabSalary Slip` ss INNER JOIN `tabSalary Detail` sd"
                   " ON sd.parent = ss.name AND sd.parenttype = 'Salary Slip'"
                   " WHERE ss.docstatus = 1 AND ss.company = %(company)s"
                   " AND ss.end_date >= %(effective)s AND sd.salary_component = %(component)s"
                   " AND sd.parentfield = %(parentfield)s AND NOT EXISTS"
                   " (SELECT 1 FROM `tabJifeng Salary History Rule` newer"
                   " WHERE newer.company = ss.company AND newer.salary_component = sd.salary_component"
                   " AND newer.parentfield = sd.parentfield AND newer.docstatus = 1"
                   " AND newer.effective_from > %(effective)s AND newer.effective_from <= ss.end_date)",
                   {"company": original.company, "component": original.salary_component,
                    "effective": _day(original.effective_from), "parentfield": original.parentfield})
    if used:
        frappe.throw("已用于已提交工资期间的分类规则不能取消")


def prevent_rule_delete(document):
    _login()
    original = frappe.db.get_value(DOCTYPE, document.name, "docstatus")
    if original is None or int(original) != 0:
        frappe.throw("已发布工资历史分类规则必须保留，不能删除")


def prevent_rule_rename(document, *args, **kwargs):
    frappe.throw("工资历史分类规则编号必须保留，不能重命名或合并")


def publish_historical(document, reason):
    """Explicit native supervisor action; submit creates the tracked Version."""
    _login()
    document.check_permission("read")
    document.check_permission("write")
    document.check_permission("submit")
    _fields(DOCTYPE, {*CONFIG_FIELDS, "name", "modified", "docstatus"})
    if document.is_new() or int(_get(document, "docstatus") or 0) != 0:
        frappe.throw("请先保存草稿，再明确发布首次历史分类")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        frappe.throw("历史首次发布须填写一至五百字的分类依据")
    original = _load(DOCTYPE, document.name)
    # Frappe Document._save.check_if_latest first locks its existing Rule row
    # (load_doc_before_save(for_update=True)). Match native submit/cancel's
    # Rule -> Company order rather than introducing an inverse lock order.
    saved = frappe.db.get_value(DOCTYPE, document.name, ["modified", "docstatus"], as_dict=True, for_update=True)
    if (not saved or int(_get(saved, "docstatus") or 0) != 0
            or not same_version(_get(saved, "modified"), _stamp(document.modified))):
        frappe.throw("工资历史分类草稿已改变，请重新读取后发布")
    original.reload()
    _company(original.company, lock=True)
    original.check_permission("write")
    original.check_permission("submit")
    original.flags.salary_history_publish_token = _HISTORICAL_PUBLICATION
    original.flags.salary_history_publication_reason = reason.strip()
    original.submit()
    return {"name": original.name, "modified": _stamp(original.modified), "docstatus": 1}
