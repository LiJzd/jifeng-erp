"""Journaled native wage-source fixtures for a separate browser connection.

prepare/cleanup are persistent native transactions on an isolated QA site.
inspect never writes; record_ui/reconcile write only the exact private journal.
Browser-created wages must not enter Additional Salary or payroll in this fixture.
Invoke persistent actions once from an initialized bench-env Python process;
bench execute's exception fallback is unsuitable for a persistent fixture.
"""

import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import frappe
from frappe.utils import cint, get_datetime, get_first_day, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_browser_validation import ZERO_BIN_FIELDS
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.wage_allocation import (
    NUMERIC_WAGE_FIELDS, SOURCE_FIELDS, WAGE_FIELDS, get_allocation_source, wage_values,
)


KINDS = {
    "employees": "Employee", "salary_components": "Salary Component", "items": "Item",
    "workstations": "Workstation", "operations": "Operation", "boms": "BOM",
    "flows": "Jifeng Flow Order", "work_orders": "Work Order", "job_cards": "Job Card",
    "allocations": "Jifeng Wage Allocation", "wages": "Jifeng Wage Entry",
}
UI_TYPES = {"Jifeng Wage Allocation", "Jifeng Wage Entry"}
SOURCE_KINDS = tuple(kind for kind, doctype in KINDS.items() if doctype not in UI_TYPES)
PARENT_FIELDS = {
    "Employee": ("first_name", "employee_name", "company", "gender", "date_of_birth", "date_of_joining", "user_id"),
    "Salary Component": ("salary_component", "salary_component_abbr", "type", "depends_on_payment_days", "is_tax_applicable"),
    "Item": ("item_code", "item_name", "item_group", "stock_uom", "is_stock_item", "valuation_method"),
    "Workstation": ("workstation_name", "production_capacity", "holiday_list"),
    "Operation": ("name", "workstation", "is_corrective_operation"),
    "BOM": ("company", "item", "quantity", "with_operations", "transfer_material_against"),
    "Jifeng Flow Order": ("company", "item_code", "planned_qty", "bom", "work_order", "external_order", "sales_order",
                          "sales_order_item", "source_key", "profile", "material_request", "cost_preview"),
    "Work Order": ("company", "production_item", "bom_no", "qty", "jf_flow_order", "source_warehouse", "fg_warehouse",
                   "wip_warehouse", "skip_transfer", "planned_start_date", "sales_order", "production_plan"),
    "Job Card": ("company", "work_order", "jf_flow_order", "jf_department", "operation", "operation_id", "production_item", "finished_good", "stock_uom",
                 "posting_date", "for_quantity", "total_completed_qty", "total_time_in_mins", "is_corrective_job_card"),
    "Jifeng Wage Allocation": (*SOURCE_FIELDS, "payroll_date", "salary_component", "allocated_qty", "total_amount", "notes"),
    "Jifeng Wage Entry": (*WAGE_FIELDS, "employee_name", "notes"),
}
CHILD_FIELDS = {
    "Salary Component": {"accounts": ("company", "account")},
    "Item": {"uoms": ("uom", "conversion_factor")},
    "BOM": {"items": ("item_code", "qty", "rate", "uom", "stock_uom"),
            "operations": ("operation", "workstation", "time_in_mins")},
    "Jifeng Flow Order": {"materials": ("item_code", "uom", "qty_per_unit", "unit_price"),
                          "operations": ("operation", "workstation", "rate_per_unit")},
    "Work Order": {"required_items": ("item_code", "required_qty", "stock_uom", "source_warehouse"),
                   "operations": ("operation", "workstation", "time_in_mins")},
    "Job Card": {"time_logs": ("employee", "from_time", "to_time", "time_in_mins", "completed_qty", "operation")},
    "Jifeng Wage Allocation": {"employees": (
        "employee", "employee_name", "quantity", "piece_rate", "hours", "source_hours_complete", "include_time_pay",
        "wage_hours", "hourly_rate", "extra_amount", "piece_amount", "time_amount", "total_amount", "wage_entry",
    )},
}
STAGES = {"draft", "under_total_rejected", "approved", "wages_generated", "wages_submitted"}
GENERATED_WAGE_NOTES = "来源于已审核的员工工资分配"


def _number(value):
    number = Decimal(str(value or 0))
    assert number.is_finite(), "验收数量或金额必须为有限数值"
    return number


def _path(data):
    match = re.fullmatch(r"JF-WAGE-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "wage_browser" or data.get("fixture_version") != 1:
        raise ValueError("不是受支持的工资浏览器验收夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("wage-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("工资验收日志路径与夹具身份不一致")
    return path


def _journal(data):
    path = _path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["artifact_path"], data["updated_at"] = str(path), now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _load(data):
    if isinstance(data, str):
        data = json.loads(data)
    canonical = json.loads(_path(data).read_text(encoding="utf-8"))
    # UI lists evolve only in the private journal; an older prepare result can
    # identify the fixture but cannot supply or replace the cleanup allowlist.
    for field in ("prefix", "fixture_type", "fixture_version", "company", "item_group", "stock_uom",
                  "created_at", "created_by", "browser_user", "warehouse", "ready_at", "source_snapshot",
                  "bin_records", *SOURCE_KINDS):
        if data.get(field) != canonical.get(field):
            raise ValueError("调用参数与私有日志中的工资夹具身份或准备来源集合不一致")
    return canonical


def _snapshot(doc):
    result = {field: doc.get(field) for field in PARENT_FIELDS[doc.doctype]}
    for table, fields in CHILD_FIELDS.get(doc.doctype, {}).items():
        result[table] = [{"name": row.name, "parent": row.parent, "parenttype": row.parenttype,
                          **{field: row.get(field) for field in fields}} for row in doc.get(table) or []]
    return json.loads(json.dumps(result, default=str))


def _record(data, doc):
    return {"doctype": doc.doctype, "name": doc.name, "owner": doc.owner, "creation": str(doc.creation),
            "created_roles": list(data["browser_roles"] if doc.doctype in UI_TYPES else data["creator_roles"]),
            "fixture_marker": data["prefix"], "docstatus": cint(doc.docstatus), "identity": _snapshot(doc)}


def _track(data, doc):
    kind = next(kind for kind, doctype in KINDS.items() if doctype == doc.doctype)
    assert doc.name not in data[kind], "重复的工资验收来源身份"
    data[kind].append(doc.name)
    data["records"].append(_record(data, doc))
    _journal(data)
    return doc


def _begin(point):
    if getattr(frappe.db, "_disable_transaction_control", 0):
        frappe.throw("持久工资夹具不能在其他禁止提交的事务中创建或撤销")
    _begin_rollback_only(point)


def _state(data):
    state = []
    for record in data["records"]:
        row = {"doctype": record["doctype"], "name": record["name"],
               "exists": bool(frappe.db.exists(record["doctype"], record["name"]))}
        if row["exists"]:
            doc = frappe.get_doc(record["doctype"], record["name"])
            row.update({"owner": doc.owner, "creation": str(doc.creation), "docstatus": cint(doc.docstatus),
                        "identity": _snapshot(doc), "lifecycle": {field: doc.get(field) for field in
                            ("disabled", "status", "is_active", "is_default") if doc.meta.has_field(field)}})
        state.append(row)
    return json.loads(json.dumps(state, default=str))


def _commit(data, phase):
    data["commit_phase"], data["pending_state"] = phase, _state(data)
    data["phase"] = phase + "_commit_pending"
    _journal(data)
    assert frappe.db._disable_transaction_control == 1, "工资验收提交屏障状态不一致"
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = phase + "_commit_unknown"
        _journal(data)
        exc.add_note("提交结果未知，先inspect并reconcile，不能重复创建或清理: " + data["artifact_path"])
        raise


def _assert_ledger(data):
    ledger = _ledger_snapshot()
    assert ledger == data["ledger_before"], "工资浏览器夹具的完整库存或总账内容发生变化，保留并核对"
    return ledger


def _bin_rows(data):
    return frappe.get_all("Bin", filters={"item_code": ["in", data["items"]]},
        fields=["name", "creation", "item_code", "warehouse", *ZERO_BIN_FIELDS],
        order_by="name", limit_page_length=0)


def _validate_bins(data, restored=False):
    rows = _bin_rows(data)
    records = {row["name"]: row for row in data["bin_records"]}
    assert set(records) == {row.name for row in rows}, "验收商品出现未登记的原生Bin"
    for row in rows:
        record = records[row.name]
        assert (row.item_code, row.warehouse, str(row.creation)) == (
            record["item_code"], data["warehouse"], record["creation"]), "验收Bin身份或仓库变化"
        if restored:
            assert all(abs(_number(row.get(field))) <= Decimal("0.000001") for field in ZERO_BIN_FIELDS), \
                "工资来源取消后，验收商品的库存、计划或预留余额尚未归零"
    return [dict(row) for row in rows]


def _validate_sources(data):
    expected = {(row["doctype"], row["name"]): row for row in data["records"]}
    assert len(expected) == len(data["records"]), "工资夹具日志有重复记录"
    assert set(expected) == {(doctype, name) for kind, doctype in KINDS.items() for name in data[kind]}, \
        "工资夹具精确来源集合与日志不一致"
    deleted = {(row["doctype"], row["name"]) for row in data.get("deleted_document_log", [])}
    for key, record in expected.items():
        exists = bool(frappe.db.exists(*key))
        if key in deleted:
            assert not exists, "已登记删除的工资草稿重新出现，拒绝继续清理"
            continue
        assert exists, "工资验收来源未按日志删除: " + record["name"]
        doc = frappe.get_doc(*key)
        assert doc.owner == record["owner"] and str(doc.creation) == record["creation"], "工资验收来源创建身份变化"
        assert _snapshot(doc) == record["identity"], "工资验收来源业务身份或内容变化: " + record["name"]
        assert cint(doc.docstatus) in ({0, 1, 2} if doc.doctype in UI_TYPES else
                                       ({1, 2} if record["docstatus"] == 1 else {record["docstatus"]})), \
            "工资验收来源审核状态不受支持"
    assert len(data["employees"]) == 2 and len(data["job_cards"]) == 1 and len(data["work_orders"]) == 1
    assert set(data["items"]) == {data["prefix"] + "-RAW", data["prefix"] + "-FG"}
    assert data["salary_components"] == [data["prefix"] + "-PIECE"]
    assert data["operations"] == [data["prefix"] + "-OP"]
    assert data["workstations"] == [data["prefix"] + "-WS"]
    _validate_bins(data, restored=bool(data.get("cleanup_complete")))


def _ui_names(data):
    # A failed prepare can leave a private journal before its Job Card existed.
    # inspect must still show that rolled-back source set without adopting UI IDs.
    if not data["job_cards"]:
        return {"allocations": [], "wages": []}
    allocations = frappe.get_all("Jifeng Wage Allocation", filters={"job_card": data["job_cards"][0]},
                                 pluck="name", order_by="name", limit_page_length=0)
    wages = set(frappe.get_all("Jifeng Wage Entry", filters={"job_card": data["job_cards"][0]},
                               pluck="name", limit_page_length=0))
    if allocations:
        wages.update(frappe.get_all("Jifeng Wage Entry", filters={"wage_allocation": ["in", allocations]},
                                    pluck="name", limit_page_length=0))
    return {"allocations": allocations, "wages": sorted(wages)}


def _validate_ui_owner(data, doc):
    assert doc.owner == data["browser_user"], "UI工资单不是指定浏览器用户创建"
    assert get_datetime(data["ready_at"]) <= get_datetime(doc.creation) <= get_datetime(now()), \
        "UI工资单创建时间不在本次夹具准备后的范围内"


def _validate_allocation(data, allocation):
    _validate_ui_owner(data, allocation)
    assert allocation.notes == data["ui_contract"]["allocation_notes"], "工资分配缺少精确fixture备注标记"
    for field in SOURCE_FIELDS:
        actual, value = allocation.get(field), data["source_snapshot"][field]
        assert (_number(actual) == _number(value) if field == "source_completed_qty" else
                str(actual or "") == str(value or "")), "UI工资分配来源与准备的报工身份不一致: " + field
    assert allocation.salary_component == data["salary_components"][0]
    assert str(allocation.payroll_date) == data["payroll_date"]
    assert len(allocation.employees) == 2 and {row.employee for row in allocation.employees} == set(data["employees"])
    expected = {row["employee"]: row for row in data["ui_contract"]["correct_shares"]}
    for row in allocation.employees:
        spec = expected[row.employee]
        assert Decimal(0) <= _number(row.quantity) <= 5 and _number(row.piece_rate) in (0, _number(spec["piece_rate"]))
        assert _number(row.hours) == 1 and cint(row.source_hours_complete) == 1
        assert not cint(row.include_time_pay) and not any(_number(row.get(field)) for field in
                                                       ("wage_hours", "hourly_rate", "extra_amount"))
    if allocation.docstatus == 1:
        assert all(_number(row.quantity) == _number(expected[row.employee]["quantity"]) and
                   _number(row.piece_rate) == _number(expected[row.employee]["piece_rate"])
                   for row in allocation.employees), "已审核UI分配的个人份额或工价不符合本次验收"
        assert _number(allocation.allocated_qty) == 5 and _number(allocation.total_amount) == 18


def _validate_wage(data, wage, allocation):
    _validate_ui_owner(data, wage)
    assert get_datetime(wage.creation) >= get_datetime(allocation.creation)
    assert wage.notes == GENERATED_WAGE_NOTES, "UI生成工资备注不是业务原生固定值"
    row = next((row for row in allocation.employees if row.name == wage.wage_allocation_employee), None)
    assert row and wage.employee == row.employee and row.wage_entry == wage.name, "工资来源员工行或反向引用不一致"
    values = wage_values(allocation, row)
    for field in WAGE_FIELDS:
        actual, value = wage.get(field), values[field]
        assert (_number(actual) == _number(value) if field in NUMERIC_WAGE_FIELDS else
                str(actual or "") == str(value or "")), "UI工资与已登记分配的业务身份不一致: " + field


def _foreign_references(data):
    from frappe.model.delete_doc import get_dynamic_linked_docs, get_linked_docs

    allowed = {(record["doctype"], record["name"]) for record in data["records"]}
    allowed.update(("Bin", row["name"]) for row in data["bin_records"])
    foreign = []
    for doctype, name in sorted(allowed):
        if doctype == "Bin" or not frappe.db.exists(doctype, name):
            continue
        doc = frappe.get_doc(doctype, name)
        for link in get_linked_docs(doc) + get_dynamic_linked_docs(doc):
            reference = (link["reference_doctype"], link["reference_docname"])
            if reference not in allowed:
                foreign.append({"source_doctype": doctype, "source_name": name, **link})
    return foreign


def _validate_registered_ui(data):
    actual = _ui_names(data)
    deleted = {(row["doctype"], row["name"]) for row in data.get("deleted_document_log", [])}
    for kind in ("allocations", "wages"):
        expected = {name for name in data[kind] if (KINDS[kind], name) not in deleted}
        assert set(actual[kind]) == expected, "出现未登记的UI工资身份，先inspect核对并record_ui，拒绝自动清理"
    if actual["allocations"]:
        allocation = frappe.get_doc("Jifeng Wage Allocation", actual["allocations"][0])
        _validate_allocation(data, allocation)
        for name in actual["wages"]:
            _validate_wage(data, frappe.get_doc("Jifeng Wage Entry", name), allocation)
    assert not _foreign_references(data), "验收员工、工资或生产来源被日志外单据引用，拒绝自动撤销或停用"


def prepare(company, item_group, stock_uom, browser_user="Administrator"):
    """Commit only a minimal two-person submitted Job Card and its native sources."""
    frappe.only_for("System Manager")
    for doctype, name in (("Company", company), ("Item Group", item_group), ("UOM", stock_uom)):
        frappe.get_doc(doctype, name).check_permission("read")
    user = frappe.get_doc("User", browser_user)
    assert user.enabled and "System Manager" in frappe.get_roles(browser_user), "工资UI验收需要启用的指定System Manager用户"
    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    gender = frappe.db.get_value("Gender", {}, "name")
    assert warehouse and gender, "工资夹具需要已启用的明细仓库和原生性别字典"
    suffix = frappe.generate_hash(length=12)
    prefix = "JF-WAGE-BROWSER-" + suffix
    data = {"fixture_type": "wage_browser", "fixture_version": 1, "prefix": prefix,
            "company": company, "item_group": item_group, "stock_uom": stock_uom, "warehouse": warehouse,
            "created_at": now(), "created_by": frappe.session.user, "creator_roles": frappe.get_roles(),
            "browser_user": browser_user, "browser_roles": frappe.get_roles(browser_user), "phase": "preparing",
            "settings_restored": False, "cleanup_complete": False, "records": [], "bin_records": [],
            "ui_stage_log": [], "deleted_document_log": [], "ledger_before": _ledger_snapshot(),
            **{kind: [] for kind in KINDS}}
    settings = frappe.get_single("Manufacturing Settings")
    original = settings.disable_capacity_planning
    data["original_manufacturing_settings"] = {"disable_capacity_planning": original}
    _journal(data)
    point, original_test = "jf_wage_browser_prepare_" + suffix, frappe.in_test
    _begin(point)
    try:
        frappe.in_test = True
        if cint(original) != 1:
            settings.disable_capacity_planning = 1
            settings.save()
        employees = [_track(data, frappe.get_doc({"doctype": "Employee", "first_name": prefix + f"-E{idx}",
            "company": company, "gender": gender, "date_of_birth": "1990-01-01",
            "date_of_joining": str(get_first_day(today())), "status": "Active"}).insert()) for idx in (1, 2)]
        component = _track(data, frappe.get_doc({"doctype": "Salary Component", "salary_component": prefix + "-PIECE",
            "salary_component_abbr": "JFWB" + suffix, "type": "Earning", "depends_on_payment_days": 0,
            "is_tax_applicable": 0}).insert())
        raw, finished = [_track(data, frappe.get_doc({"doctype": "Item", "item_code": prefix + "-" + kind,
            "item_name": "工资浏览器合成验收 " + kind, "item_group": item_group, "stock_uom": stock_uom}).insert())
            for kind in ("RAW", "FG")]
        workstation = _track(data, frappe.get_doc({"doctype": "Workstation", "workstation_name": prefix + "-WS",
                                                   "production_capacity": 1}).insert())
        operation = _track(data, frappe.get_doc({"doctype": "Operation", "name": prefix + "-OP",
                                                 "workstation": workstation.name}).insert())
        bom = _track(data, frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
            "quantity": 1, "with_operations": 1, "transfer_material_against": "Work Order",
            "items": [{"item_code": raw.name, "qty": 1, "rate": 1}],
            "operations": [{"operation": operation.name, "workstation": workstation.name, "time_in_mins": 1}]}).insert())
        bom.submit()
        flow = _track(data, frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
            "item_code": finished.name, "planned_qty": 5, "bom": bom.name,
            "materials": [{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 1}]}).insert())
        work = _track(data, frappe.get_doc("Work Order", flow.make_work_order()))
        work.source_warehouse, work.fg_warehouse, work.skip_transfer = warehouse, warehouse, 1
        work.planned_start_date = today() + " 09:00:00"
        work.save()
        work.submit()
        card_names = frappe.get_all("Job Card", filters={"work_order": work.name}, pluck="name", limit_page_length=0)
        assert len(card_names) == 1
        card = _track(data, frappe.get_doc("Job Card", card_names[0]))
        card.start_timer(start_time=today() + " 09:00:00", employees=[{"employee": employee.name} for employee in employees])
        card.reload()
        card.complete_job_card(qty=5, for_quantity=5, pending_qty=0, process_loss_qty=0, end_time=today() + " 10:00:00")
        card.reload()
        card.submit()
        assert card.total_completed_qty == 5 and card.total_time_in_mins == 120
        assert [row.completed_qty for row in card.time_logs] == [0, 5]
        assert [row.time_in_mins for row in card.time_logs] == [60, 60]
        source = get_allocation_source(card.name)
        data["source_snapshot"] = {field: source[field] for field in SOURCE_FIELDS}
        data["payroll_date"], data["ready_at"] = today(), now()
        data["ui_contract"] = {
            "job_card": card.name, "allocation_notes": prefix + " 工资浏览器验收（合成夹具）",
            "salary_component": component.name, "source_completed_qty": 5, "personal_hours": [1, 1],
            "incorrect_shares": [{"employee": employees[0].name, "quantity": 1, "piece_rate": 3},
                                 {"employee": employees[1].name, "quantity": 3, "piece_rate": 4}],
            "correct_shares": [{"employee": employees[0].name, "quantity": 2, "piece_rate": 3},
                               {"employee": employees[1].name, "quantity": 3, "piece_rate": 4}],
            "expected_wages": [{"employee": employees[0].name, "quantity": 2, "total_amount": 6},
                               {"employee": employees[1].name, "quantity": 3, "total_amount": 12}],
            "expected_total": 18, "list_route": "/app/jifeng-wage-entry",
            "report_route": "/app/query-report/Jifeng%20Wage%20Analysis",
            "report_filters": {"company": company, "from_date": today(), "to_date": today(), "salary_component": component.name},
            "steps": ["新建分配，实际选择job_card，填写唯一notes和salary_component", "填写incorrect_shares并保存，审核必须拒绝合计4",
                      "改为correct_shares后审核；原生on_submit已生成两笔工资，用inspect核对精确ID后显式record_ui(stage=approved,wages=[两ID])",
                      "点击生成或查看计薪记录，列表应仍为相同两笔草稿6/12；不得重复新建工资", "原生提交两笔工资，再查看分析，两行总18"],
            "financial_scope": "本夹具只提交工资，不生成Additional Salary或Payroll Entry",
        }
        settings.reload()
        if settings.disable_capacity_planning != original:
            settings.disable_capacity_planning = original
            settings.save()
        assert settings.disable_capacity_planning == original
        data["settings_restored"] = True
        data["records"] = [_record(data, frappe.get_doc(row["doctype"], row["name"])) for row in data["records"]]
        data["bin_records"] = [{"name": row.name, "creation": str(row.creation), "item_code": row.item_code,
                                 "warehouse": row.warehouse} for row in _bin_rows(data)]
        _validate_sources(data)
        _validate_registered_ui(data)
        _assert_ledger(data)
    except Exception as exc:
        _end_rollback_only(point)
        data["phase"], data["prepare_error"], data["settings_restored"] = "prepare_failed_rolled_back", str(exc), True
        _journal(data)
        exc.add_note("工资浏览器准备已回滚，私有日志: " + data["artifact_path"])
        raise
    finally:
        frappe.in_test = original_test
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
    _commit(data, "prepare")
    data["phase"] = "ready"
    _journal(data)
    return data


def inspect(data):
    """Read DB and the private journal only, including unknown commit evidence."""
    frappe.only_for("System Manager")
    data = _load(data)
    ledger = _ledger_snapshot()
    errors = []
    try:
        _validate_sources(data)
    except Exception as exc:
        errors.append(str(exc))
    actual = _ui_names(data)
    ui_records = []
    for kind in ("allocations", "wages"):
        for name in actual[kind]:
            doc = frappe.get_doc(KINDS[kind], name)
            ui_records.append(_record(data, doc))
    return {"prefix": data["prefix"], "phase": data["phase"], "artifact_path": data["artifact_path"],
            "state": _state(data), "observed_ui": ui_records, "registered_ui": {kind: data[kind] for kind in ("allocations", "wages")},
            "unregistered_ui": {kind: sorted(set(actual[kind]) - set(data[kind])) for kind in ("allocations", "wages")},
            "ledger": ledger, "ledger_unchanged": ledger == data["ledger_before"],
            "source_validation_errors": errors, "external_references": _foreign_references(data),
            "cleanup_complete": bool(data.get("cleanup_complete"))}


def record_ui(data, allocation, wages=None, stage="draft"):
    """Register explicit browser IDs in the private journal; never change DB rows."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert stage in STAGES and data["phase"] in ({"ready"} | {"ui_" + name for name in STAGES}), "先核对并reconcile提交状态"
    assert not data["cleanup_complete"] and data["settings_restored"]
    assert isinstance(allocation, str) and allocation and (not data["allocations"] or data["allocations"] == [allocation])
    if stage == "approved":
        assert isinstance(wages, list) and len(wages) == 2, "审核阶段已原生生成两笔工资，必须显式登记两笔精确Wage ID"
    wages = data["wages"] if wages is None else wages
    assert isinstance(wages, list) and all(isinstance(name, str) and name for name in wages) and len(set(wages)) == len(wages)
    assert len(wages) in (0, 2), "工资UI验收只允许两名来源员工各一笔工资"
    actual = _ui_names(data)
    assert actual["allocations"] == [allocation] and set(actual["wages"]) == set(wages), "未知UI身份不在显式登记集合中"
    doc = frappe.get_doc("Jifeng Wage Allocation", allocation)
    _validate_allocation(data, doc)
    if stage == "under_total_rejected":
        assert doc.docstatus == 0 and _number(doc.allocated_qty) == 4, "合计错误的拒绝阶段应仍是合计4的草稿"
    if stage in {"approved", "wages_generated", "wages_submitted"}:
        assert doc.docstatus == 1, "正确分配尚未原生审核"
    wage_docs = [frappe.get_doc("Jifeng Wage Entry", name) for name in wages]
    for wage in wage_docs:
        _validate_wage(data, wage, doc)
    if stage in {"approved", "wages_generated", "wages_submitted"}:
        assert len(wage_docs) == 2 and {wage.employee for wage in wage_docs} == set(data["employees"])
        assert all(wage.docstatus == (1 if stage == "wages_submitted" else 0) for wage in wage_docs)
    _assert_ledger(data)
    data["allocations"], data["wages"] = [allocation], sorted(wages)
    data["records"] = [row for row in data["records"] if row["doctype"] not in UI_TYPES]
    records = [_record(data, row) for row in [doc, *wage_docs]]
    data["records"].extend(records)
    _validate_sources(data)
    _validate_registered_ui(data)
    data["ui_stage_log"].append({"stage": stage, "recorded_at": now(), "recorded_by": frappe.session.user,
                                  "records": records, "ledger": _ledger_snapshot()})
    data["phase"] = "ui_" + stage
    _journal(data)
    return data


def reconcile(data):
    """Confirm a pending/unknown outcome by exact observed state; private log only."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert data["phase"].endswith(("_commit_pending", "_commit_unknown")), "当前没有待核实的提交"
    state = _state(data)
    _assert_ledger(data)
    if state == data["pending_state"]:
        _validate_sources(data)
        _validate_registered_ui(data)
        phase = "ready" if data["commit_phase"] == "prepare" else "cancelled_history_retained"
        data["cleanup_complete"] = data["commit_phase"] == "cleanup"
    elif data["commit_phase"] == "prepare" and all(not row["exists"] for row in state):
        phase = "prepare_failed_rolled_back"
    elif data["commit_phase"] == "cleanup" and state == data["cleanup_before_state"]:
        data = deepcopy(data["cleanup_before_journal"])
        phase = data["phase"]
    else:
        frappe.throw("提交结果与精确前后身份均不一致，保留夹具并人工核对，不自动重试")
    data.setdefault("reconciliation_log", []).append({"confirmed_at": now(), "confirmed_by": frappe.session.user,
                                                      "phase": phase, "state": state})
    data["phase"] = phase
    _journal(data)
    return data


def cleanup(data):
    """Reverse registered native documents, retain cancelled history and disable QA masters."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert data["phase"] in ({"ready", "cleanup_failed_rolled_back", "cancelled_history_retained"} |
                             {"ui_" + name for name in STAGES}), "提交未知或准备失败，先inspect/reconcile，不能自动清理"
    assert data["settings_restored"], "准备期间的制造设置尚未恢复"
    _validate_sources(data)
    _validate_registered_ui(data)
    _assert_ledger(data)
    if data["cleanup_complete"]:
        return data
    before = deepcopy(data)
    data["cleanup_before_journal"], data["cleanup_before_state"] = before, _state(data)
    suffix = data["prefix"].removeprefix("JF-WAGE-BROWSER-")
    point, original_test = "jf_wage_browser_cleanup_" + suffix, frappe.in_test
    _begin(point)
    try:
        frappe.in_test = True
        changes, disabled, retained = [], [], []
        for kind in ("wages", "allocations", "job_cards", "work_orders", "boms"):
            for name in reversed(data[kind]):
                doc = frappe.get_doc(KINDS[kind], name)
                if doc.docstatus == 1:
                    doc.cancel()
                    changes.append({"doctype": doc.doctype, "name": name, "action": "cancel", "changed_at": now()})
                elif doc.docstatus == 0 and doc.doctype in UI_TYPES:
                    frappe.delete_doc(doc.doctype, name, ignore_missing=False)
                    deletion = {"doctype": doc.doctype, "name": name, "action": "delete_draft", "changed_at": now()}
                    data["deleted_document_log"].append(deletion)
                    changes.append(deletion)
        for kind, field, value in (("employees", "status", "Inactive"), ("salary_components", "disabled", 1),
                                  ("items", "disabled", 1), ("workstations", "disabled", 1)):
            for name in data[kind]:
                doc = frappe.get_doc(KINDS[kind], name)
                definition = doc.meta.get_field(field)
                if not definition or (field == "disabled" and definition.fieldtype != "Check"):
                    retained.append({"doctype": doc.doctype, "name": name, "reason": "原生主档没有受支持的停用字段，保留唯一QA身份"})
                    continue
                previous = doc.get(field)
                if previous != value:
                    setattr(doc, field, value)
                    doc.save()
                    assert frappe.db.get_value(doc.doctype, name, field) == value
                    disabled.append({"doctype": doc.doctype, "name": name, "field": field,
                                     "previous_value": previous, "value": value, "changed_at": now()})
        retained.extend({"doctype": KINDS[kind], "name": name,
                         "reason": "保留唯一前缀的原生工序或非审核流程来源，私有日志可追溯"}
                        for kind in ("operations", "flows") for name in data[kind])
        assert all(frappe.db.get_value(KINDS[kind], name, "docstatus") == 2 for kind in
                   ("job_cards", "work_orders", "boms") for name in data[kind])
        assert all(not frappe.db.exists(KINDS[kind], name) or frappe.db.get_value(KINDS[kind], name, "docstatus") == 2
                   for kind in ("wages", "allocations") for name in data[kind])
        data["cleanup_complete"] = True
        data["native_action_log"], data["disabled_master_records"], data["retained_master_records"] = changes, disabled, retained
        data["remaining_bins"] = _validate_bins(data, restored=True)
        _validate_sources(data)
        _validate_registered_ui(data)
        data["ledger_after"] = _assert_ledger(data)
        data["retained_history_records"] = [row for row in _state(data) if row["exists"]]
    except Exception as exc:
        _end_rollback_only(point)
        data = before
        data["phase"], data["cleanup_error"] = "cleanup_failed_rolled_back", str(exc)
        _journal(data)
        exc.add_note("工资浏览器清理已回滚，保留原生数据并核对私有日志: " + data["artifact_path"])
        raise
    finally:
        frappe.in_test = original_test
    _commit(data, "cleanup")
    data["phase"] = "cancelled_history_retained"
    _journal(data)
    return data
