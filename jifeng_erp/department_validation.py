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
    from erpnext.manufacturing.doctype.job_card.job_card import make_corrective_job_card
    from erpnext.manufacturing.doctype.work_order.work_order import make_job_card
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
        corrective_operation = frappe.get_doc({"doctype": "Operation", "name": f"JF-DEPT-CORRECT-{suffix}",
                                               "workstation": workstations[1].name,
                                               "jf_department": departments[1].name,
                                               "is_corrective_operation": 1}).insert()
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
        workstations[0].reload()
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
            # Native Job Card treats the uncompleted balance as process loss
            # unless the operator explicitly records it as pending production.
            card.pending_qty = 0 if idx == 0 else 4
            card.append("time_logs", {"from_time": today() + f" {9 + idx}:00:00",
                                      "to_time": today() + f" {9 + idx}:30:00", "completed_qty": 5 if idx == 0 else 1})
            card.save()
            draft = department_progress({**filters, "department": departments[idx].name})[1][0]
            assert draft["draft_qty"] == (5 if idx == 0 else 1) and draft["approved_qty"] == 0
            card.submit()
        summary = department_progress(filters)[1]
        assert len(summary) == 2 and sorted(row["approved_percent"] for row in summary) == [20, 100], summary
        assert all(row["for_quantity"] == 5 and row["hours"] == 0.5 for row in summary)
        overdue = department_progress({**filters, "only_overdue": 1})[1]
        assert (len(overdue) == 1 and overdue[0]["jf_department"] == departments[1].name
                and overdue[0]["remaining_qty"] == 4 and overdue[0]["overdue_count"] == 1)
        assert work_order_progress(filters)[1][0]["produced_qty"] == 0
        from frappe.desk.query_report import run

        assert len(run("Jifeng Department Progress", filters=filters)["result"]) == 2

        # A submitted card's pending quantity is delegated to another card. The
        # native dispatch entry point accepts the remaining quantity and the
        # source Work Order Operation name, whose parent supplies plan quantity.
        # Work Order Operation has no independent planned-quantity field.
        prior_cards = set(frappe.get_list("Job Card", filters={"work_order": work.name}, pluck="name"))
        make_job_card(work.name, [{"name": cards[1].operation_id, "operation": cards[1].operation,
                                  "qty": 4, "pending_qty": 4}])
        continued_names = set(frappe.get_list("Job Card", filters={"work_order": work.name}, pluck="name")) - prior_cards
        assert len(continued_names) == 1
        continued = frappe.get_doc("Job Card", next(iter(continued_names)))
        assert (continued.for_quantity == 4 and continued.operation_id == cards[1].operation_id
                and continued.jf_department == departments[1].name)
        continued.pending_qty = 0
        continued.expected_start_date = add_days(today(), -1) + " 08:00:00"
        continued.expected_end_date = add_days(today(), -1) + " 09:00:00"
        continued.append("time_logs", {"from_time": today() + " 11:00:00",
                                       "to_time": today() + " 11:30:00", "completed_qty": 4})
        continued.save()
        continued.submit()
        work.reload()
        source_operation = next(row for row in work.operations if row.name == cards[1].operation_id)
        assert source_operation.completed_qty == work.qty == 5
        continued_summary = department_progress({**filters, "department": departments[1].name})[1]
        assert len(continued_summary) == 1
        normal = continued_summary[0]
        assert (normal["for_quantity"] == 5 and normal["approved_qty"] == 5
                and normal["remaining_qty"] == 0 and normal["approved_percent"] == 100
                and normal["hours"] == 1.0 and normal["overdue_count"] == 0), normal
        assert not department_progress({**filters, "only_overdue": 1})[1]
        assert not job_card_progress({**filters, "only_overdue": 1})[1]

        # Use the same native mapper offered by the submitted Job Card form.
        # Its operation_id still identifies the original Work Order Operation;
        # operation is the corrective task, and for_operation is the source.
        corrective = make_corrective_job_card(cards[1].name, operation=corrective_operation.name,
                                              for_operation=cards[1].operation)
        assert (corrective.is_corrective_job_card == 1 and corrective.for_job_card == cards[1].name
                and corrective.operation_id == cards[1].operation_id
                and corrective.operation == corrective_operation.name
                and corrective.for_operation == cards[1].operation)
        corrective.for_quantity = 1
        corrective.pending_qty = 0
        wrong_operation = frappe.copy_doc(corrective)
        wrong_operation.work_order = work.name
        wrong_operation.operation_id = cards[1].operation_id
        wrong_operation.for_job_card = cards[1].name
        wrong_operation.for_operation = cards[0].operation
        _reject(lambda: wrong_operation.insert(), "工序、公司不一致")
        wrong_source = frappe.copy_doc(corrective)
        wrong_source.work_order = work.name
        wrong_source.operation_id = cards[1].operation_id
        wrong_source.for_operation = cards[1].operation
        wrong_source.for_job_card = cards[0].name
        _reject(lambda: wrong_source.insert(), "返工报工来源与原工单工序不一致")
        corrective.append("time_logs", {"from_time": today() + " 12:00:00",
                                        "to_time": today() + " 12:15:00", "completed_qty": 1})
        corrective.insert()
        assert corrective.jf_department == departments[1].name
        corrective.submit()
        assert corrective.docstatus == 1 and corrective.total_time_in_mins == 15
        corrected_summary = department_progress({**filters, "department": departments[1].name})[1]
        assert len(corrected_summary) == 1
        normal = corrected_summary[0]
        assert (normal["operation"] == cards[1].operation and normal["for_quantity"] == 5
                and normal["approved_qty"] == 5 and normal["remaining_qty"] == 0
                and normal["approved_percent"] == 100), normal
        assert (normal["hours"] == 1.0 and normal["corrective_hours"] == 0.25
                and normal["corrective_job_card_count"] == 1), normal
        work.reload()
        source_operation = next(row for row in work.operations if row.name == cards[1].operation_id)
        assert source_operation.completed_qty == 5 and source_operation.actual_operation_time == 60

        frappe.set_user(original_user)
        department_permission = frappe.get_doc({
            "doctype": "User Permission", "user": operator.name, "allow": "Department",
            "for_value": departments[0].name, "apply_to_all_doctypes": 0, "applicable_for": "Job Card",
        }).insert()
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
        try:
            make_corrective_job_card(cards[1].name, operation=corrective_operation.name,
                                     for_operation=cards[1].operation)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Hidden department Job Card cannot be copied as a corrective source")
        frappe.set_user(original_user)
        department_permission.apply_to_all_doctypes = 1
        department_permission.applicable_for = None
        department_permission.save()
        frappe.set_user(operator.name)
        assert not frappe.get_doc("Work Order", work.name).has_permission("read")
        assert not job_card_progress(filters)[1] and not department_progress(filters)[1]
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
                "guest_denied", "continuation_native_dispatch", "continuation_plan_quantity_deduplicated",
                "continuation_completed_without_false_overdue", "corrective_native_mapping",
                "corrective_native_save_submit", "corrective_quantity_excluded", "corrective_time_separation",
                "corrective_native_operation_unchanged", "corrective_source_permission",
                "corrective_for_operation_guard", "corrective_for_job_card_guard",
                "global_department_parent_read_guard"], "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
