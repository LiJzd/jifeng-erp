"""Rollback-only validation of native dispatch departments and real reporting permissions."""

import frappe
from frappe.utils import add_days, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


def _reject(call, text):
    try:
        call()
    except frappe.ValidationError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("Expected rejection: " + text)


def smoke(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    from jifeng_erp.production import department_progress, job_card_progress, work_order_progress
    from jifeng_erp.production_department import validate_job_card_department, validate_work_order_departments

    point = "jf_department_smoke"
    original_user = frappe.session.user
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        departments = [frappe.get_doc({"doctype": "Department", "department_name": f"JF-DEPT-{idx}-{suffix}",
                                       "company": company, "is_group": 0}).insert() for idx in (1, 2)]
        workstations = [frappe.get_doc({"doctype": "Workstation", "workstation_name": f"JF-DEPT-WS-{idx}-{suffix}",
                                        "production_capacity": 1, "jf_department": department.name}).insert()
                        for idx, department in enumerate(departments)]
        operations = [frappe.get_doc({"doctype": "Operation", "name": f"JF-DEPT-OP-{idx}-{suffix}",
                                      "workstation": station.name, "jf_department": departments[1 - idx].name}).insert()
                      for idx, station in enumerate(workstations)]
        raw, finished = [frappe.get_doc({"doctype": "Item", "item_code": f"JF-DEPT-{kind}-{suffix}",
                                        "item_group": item_group, "stock_uom": stock_uom}).insert()
                         for kind in ("RAW", "FG")]
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        settings.save()
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name, "quantity": 1,
                             "with_operations": 1, "transfer_material_against": "Work Order",
                             "items": [{"item_code": raw.name, "qty": 1, "rate": 10}],
                             "operations": [{"operation": operation.name, "workstation": station.name,
                                             "time_in_mins": 1} for operation, station in zip(operations, workstations)]}).insert()
        bom.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                              "item_code": finished.name, "planned_qty": 5, "bom": bom.name}).insert()
        work = frappe.get_doc("Work Order", flow.make_work_order())
        assert [row.jf_department for row in work.operations] == [department.name for department in departments]
        explicit = frappe.copy_doc(work)
        explicit.operations[0].jf_department = departments[1].name
        validate_work_order_departments(explicit)
        assert explicit.operations[0].jf_department == departments[1].name
        explicit.operations[0].jf_department = None
        explicit.operations[0].workstation = None
        validate_work_order_departments(explicit)
        assert explicit.operations[0].jf_department == departments[1].name
        explicit.company = "OTHER-COMPANY"
        _reject(lambda: validate_work_order_departments(explicit), "不属于工单公司")
        frappe.db.set_value("Department", departments[0].name, "disabled", 1)
        _reject(lambda: validate_work_order_departments(work), "启用的明细")
        frappe.db.set_value("Department", departments[0].name, "disabled", 0)
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0})
        work.source_warehouse = warehouse
        work.fg_warehouse = warehouse
        work.skip_transfer = 1
        work.save()
        before_stock, before_gl = frappe.db.count("Stock Ledger Entry"), frappe.db.count("GL Entry")
        work.submit()
        cards = [frappe.get_doc("Job Card", name) for name in frappe.get_all(
            "Job Card", filters={"work_order": work.name}, pluck="name", order_by="operation_row_id asc")]
        assert len(cards) == 2 and [card.jf_department for card in cards] == [department.name for department in departments]
        conflict = frappe.copy_doc(cards[0])
        conflict.operation_id = cards[0].operation_id
        conflict.work_order = work.name
        conflict.jf_department = departments[1].name
        _reject(lambda: validate_job_card_department(conflict), "与派工工序不一致")
        conflict.jf_department = None
        conflict.operation_id = cards[1].operation_id
        _reject(lambda: validate_job_card_department(conflict), "工序、公司不一致")
        workstations[0].jf_department = departments[1].name
        workstations[0].save()
        cards[0].reload()
        work.reload()
        assert cards[0].jf_department == work.operations[0].jf_department == departments[0].name
        filters = {"company": company, "from_date": today(), "to_date": today(), "flow_order": flow.name}
        frappe.db.set_value("Job Card", cards[0].name, "jf_department", None)
        unassigned = department_progress({**filters, "only_unassigned": 1})[1]
        assert len(unassigned) == 1 and unassigned[0]["department_label"] == "未指定部门"
        frappe.db.set_value("Job Card", cards[0].name, "jf_department", departments[0].name)
        operator = frappe.get_doc({"doctype": "User", "email": f"jf-department-{suffix}@example.invalid",
                                   "first_name": "部门生产验收", "send_welcome_email": 0,
                                   "roles": [{"role": "Manufacturing User"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": operator.name, "allow": "Company",
                        "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(operator.name)
        assert frappe.has_permission("Department", "select") and not frappe.has_permission("Department", "read")
        for idx, card in enumerate(cards):
            card.reload()
            card.expected_start_date = add_days(today(), -1) + " 08:00:00"
            card.expected_end_date = add_days(today(), -1) + " 09:00:00"
            card.append("time_logs", {"from_time": today() + f" {9 + idx}:00:00",
                                      "to_time": today() + f" {9 + idx}:30:00", "completed_qty": 5 if idx == 0 else 1})
            card.save()
            draft = department_progress({**filters, "department": departments[idx].name})[1][0]
            assert draft["draft_qty"] == (5 if idx == 0 else 1) and draft["approved_qty"] == 0
            card.submit()
        summary = department_progress(filters)[1]
        assert len(summary) == 2 and sorted(row["approved_percent"] for row in summary) == [20, 100]
        assert all(row["for_quantity"] == 5 and row["hours"] == 0.5 for row in summary)
        overdue = job_card_progress({**filters, "only_overdue": 1})[1]
        assert len(overdue) == 1 and overdue[0]["jf_department"] == departments[1].name and overdue[0]["remaining_qty"] == 4
        assert work_order_progress(filters)[1][0]["produced_qty"] == 0
        from frappe.desk.query_report import run

        assert len(run("Jifeng Department Progress", filters=filters)["result"]) == 2
        frappe.set_user(original_user)
        frappe.get_doc({"doctype": "User Permission", "user": operator.name, "allow": "Department",
                        "for_value": departments[0].name, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(operator.name)
        visible = job_card_progress(filters)[1]
        assert len(visible) == 1 and visible[0]["jf_department"] == departments[0].name
        assert len(department_progress(filters)[1]) == 1
        try:
            job_card_progress({**filters, "department": departments[1].name})
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Hidden department cannot be queried")
        assert frappe.db.count("Stock Ledger Entry") == before_stock and frappe.db.count("GL Entry") == before_gl
        frappe.set_user("Guest")
        try:
            department_progress(filters)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest cannot read department production")
        return {"passed": True, "checks": ["workstation_priority", "explicit_dispatch_department",
                "operation_default_fallback", "company_guard", "disabled_department_guard", "job_card_inheritance",
                "department_conflict_guard", "operation_source_guard", "historical_department_snapshot",
                "unassigned_department_visible", "manufacturing_department_select_only", "draft_approval_separation",
                "department_operation_unit_grouping", "department_overdue", "report_is_not_stock_receipt",
                "native_report_api", "department_user_permission", "hidden_department_denied", "no_posting",
                "guest_denied"], "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
