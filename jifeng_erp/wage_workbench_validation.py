"""Rollback-only native acceptance of classic wage entry, not browser acceptance."""

import copy
import uuid
from decimal import Decimal

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.wage_workbench import batch_status, get_entry_source, save_drafts


def smoke(company, item_group, stock_uom, foreign_company=None):
    frappe.only_for("System Manager")
    meta = frappe.get_meta("Jifeng Wage Entry")
    if not meta.has_field("workbench_request_key") or not meta.get_field("workbench_request_key").unique:
        frappe.throw("请先迁移正式工资操作台请求字段")
    if not frappe.db.exists("Page", "jifeng-wage-workbench"):
        frappe.throw("请先迁移正式工资操作台页面")
    for doctype in ("Jifeng Wage Entry", "Jifeng Wage Allocation"):
        amendment = frappe.get_meta(doctype).get_field("amended_from")
        if not amendment or amendment.fieldtype != "Link" or amendment.options != doctype:
            frappe.throw("请先迁移工资单据的原生修改来源字段")
    original_user, original_test = frappe.session.user, frappe.in_test
    point = "jf_wage_workbench_native"
    suffix = frappe.generate_hash(length=9)
    users, created, created_allocations, checks = [], [], [], []
    ledgers_before = _ledger_snapshot()
    wage_count_before = frappe.db.count("Jifeng Wage Entry")
    allocation_count_before = frappe.db.count("Jifeng Wage Allocation")
    employee_count_before = frappe.db.count("Employee")
    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        gender = frappe.db.get_value("Gender", {}, "name")
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
        if not gender or not warehouse:
            frappe.throw("验收公司需要已启用的仓库和性别字典")
        employees = [frappe.get_doc({"doctype": "Employee", "first_name": f"JF-WWB-{index}-{suffix}",
            "company": company, "gender": gender, "date_of_birth": "1990-01-01",
            "date_of_joining": today(), "status": "Active"}).insert() for index in range(2)]
        for employee in employees:
            frappe.get_doc({"doctype": "Jifeng Employee Supplement", "employee": employee.name,
                            "wage_method": "计件"}).insert()
        component = frappe.get_doc({"doctype": "Salary Component", "salary_component": "JF-WWB-PAY-" + suffix,
            "salary_component_abbr": "WB" + suffix, "type": "Earning", "depends_on_payment_days": 0,
            "is_tax_applicable": 0}).insert()
        raw, finished = [frappe.get_doc({"doctype": "Item", "item_code": f"JF-WWB-{kind}-{suffix}",
            "item_group": item_group, "stock_uom": stock_uom}).insert() for kind in ("RAW", "FG")]
        workstation = frappe.get_doc({"doctype": "Workstation", "workstation_name": "JF-WWB-WS-" + suffix,
            "production_capacity": 1}).insert()
        operation = frappe.get_doc({"doctype": "Operation", "name": "JF-WWB-OP-" + suffix,
            "workstation": workstation.name}).insert()
        operation_two = frappe.get_doc({"doctype": "Operation", "name": "JF-WWB-OP2-" + suffix,
            "workstation": workstation.name}).insert()
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        settings.save()
        boms = []
        for index, selected in enumerate((operation, operation_two)):
            bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                "quantity": 1, "is_default": int(index == 0), "with_operations": 1,
                "transfer_material_against": "Work Order",
                "items": [{"item_code": raw.name, "qty": 1, "rate": 1}],
                "operations": [{"operation": selected.name, "workstation": workstation.name,
                                "time_in_mins": 1}]}).insert()
            bom.submit()
            boms.append(bom)
        bom, bom_two = boms

        def card(hour, people, selected_operation=operation, selected_bom=bom):
            flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                "item_code": finished.name, "planned_qty": 5, "bom": selected_bom.name,
                "materials": [{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 1}]}).insert()
            work = frappe.get_doc("Work Order", flow.make_work_order())
            work.source_warehouse = work.fg_warehouse = warehouse
            work.skip_transfer = 1
            work.planned_start_date = today() + f" {hour:02d}:00:00"
            work.save()
            work.submit()
            source = frappe.get_doc("Job Card", frappe.get_all("Job Card", filters={
                "work_order": work.name, "operation": selected_operation.name}, pluck="name")[0])
            source.start_timer(start_time=today() + f" {hour:02d}:00:00", employees=[{"employee": person.name} for person in people])
            source.reload()
            source.complete_job_card(qty=5, for_quantity=5, pending_qty=0, process_loss_qty=0,
                end_time=today() + f" {hour + 1:02d}:00:00")
            source.reload()
            source.submit()
            return source

        first, second, team = card(8, employees[:1]), card(10, employees[:1], operation_two, bom_two), card(12, employees)

        def actor(kind, role):
            user = frappe.get_doc({"doctype": "User", "email": f"jf-wwb-{kind}-{suffix}@example.invalid",
                "first_name": "工资操作台原生验收", "send_welcome_email": 0, "roles": [{"role": role}]}).insert()
            users.append(user.name)
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                "for_value": company, "apply_to_all_doctypes": 1}).insert()
            return user

        manager, accountant, clerk = (actor("manager", "HR Manager"), actor("accounts", "Accounts User"),
                                      actor("clerk", "HR User"))
        frappe.set_user(manager.name)
        assert not frappe.has_permission("Operation", "write")
        assert not frappe.has_permission("Job Card", "write", doc=first)

        def row(source=None, qty=0, rate=0, hours=0, hourly=0):
            identity = get_entry_source(source.name, company, employees[0].name) if source else {}
            return {"work_date": today(), "operation": identity.get("operation", operation.name), "job_card": source.name if source else "",
                "source_modified": identity.get("source_modified", ""),
                "work_order_modified": identity.get("work_order_modified", ""), "quantity": qty,
                "stock_uom": identity.get("stock_uom", ""), "piece_rate": rate,
                "hours": hours, "hourly_rate": hourly, "extra_amount": 0, "notes": "JF-WWB-" + suffix}

        def payload(rows):
            return {"request_id": str(uuid.uuid4()), "company": company, "employee": employees[0].name,
                "payroll_date": today(), "salary_component": component.name, "rows": rows}

        def blocked(action, error=frappe.ValidationError):
            checkpoint = "jf_wwb_expected_failure"
            frappe.db.savepoint(checkpoint)
            try:
                action()
            except error:
                pass
            else:
                raise AssertionError("Invalid native wage request was accepted")
            finally:
                frappe.db.rollback(save_point=checkpoint)

        def native_amendment(source):
            # Native Desk from_amend keeps no_copy fields; in_test Python copies
            # also keep docstatus, whereas Desk starts the parent/children at 0.
            amended = frappe.copy_doc(source)
            amended.docstatus = 0
            for child in amended.get_all_children():
                child.docstatus = 0
            amended.amended_from = source.name
            return amended

        source = get_entry_source(first.name, company, employees[0].name)
        assert source["source_modified"] == str(first.modified)
        checks.append("pure_hr_manager_select_operation_and_read_explicit_source")
        request = payload([row(first, 2, 3), row(second, 1, 4)])
        saved = save_drafts(request)
        names = [entry["name"] for entry in saved["wages"]]
        created.extend(names)
        documents = [frappe.get_doc("Jifeng Wage Entry", name) for name in names]
        assert [Decimal(str(doc.total_amount)) for doc in documents] == [Decimal(6), Decimal(4)]
        assert [doc.job_card for doc in documents] == [first.name, second.name]
        assert len({doc.operation for doc in documents}) == 2
        assert len({doc.flow_order for doc in documents}) == 2
        checks.append("two_explicit_operation_sources_native_drafts_and_amounts")
        assert save_drafts(copy.deepcopy(request))["wages"] == saved["wages"]
        assert len(batch_status(request["request_id"], company, employees[0].name)["wages"]) == 2
        checks.append("same_request_reuses_exact_two_drafts_and_status")
        changed = copy.deepcopy(request)
        changed["rows"][0]["piece_rate"] = 8
        blocked(lambda: save_drafts(changed))
        checks.append("same_request_changed_content_rejected")
        timing_request = payload([row(hours=1.5, hourly=10)])
        timing = save_drafts(timing_request)
        timing_doc = frappe.get_doc("Jifeng Wage Entry", timing["wages"][0]["name"])
        created.append(timing_doc.name)
        assert not timing_doc.job_card and Decimal(str(timing_doc.time_amount)) == Decimal(15)
        checks.append("manual_timing_uses_explicit_hours_and_rate")
        frappe.delete_doc("Jifeng Wage Entry", timing_doc.name)
        blocked(lambda: batch_status(timing_request["request_id"], company, employees[0].name))
        blocked(lambda: save_drafts(timing_request))
        checks.append("native_draft_delete_tombstone_prevents_absent_or_recreate")
        deleted_name = frappe.get_all("Deleted Document", filters={"deleted_doctype": "Jifeng Wage Entry",
            "deleted_name": timing_doc.name}, pluck="name")[0]
        frappe.set_user(original_user)
        from frappe.core.doctype.deleted_document.deleted_document import restore
        restore(deleted_name, alert=False)
        restored_name = frappe.db.get_value("Deleted Document", deleted_name, "new_name")
        created.append(restored_name)
        frappe.set_user(manager.name)
        assert batch_status(timing_request["request_id"], company, employees[0].name)["wages"][0]["name"] == restored_name
        assert save_drafts(timing_request)["reused"]
        checks.append("system_manager_native_restore_preserves_request_and_resolves_status")
        restored = frappe.get_doc("Jifeng Wage Entry", restored_name)
        restored.submit()
        restored.cancel()
        frappe.set_user(clerk.name)
        assert frappe.has_permission("Jifeng Wage Entry", "create")
        assert not frappe.has_permission("Jifeng Wage Entry", "amend")
        blocked(native_amendment(restored).insert, frappe.PermissionError)
        frappe.set_user(manager.name)
        checks.append("hr_user_create_cannot_forge_native_wage_amendment")
        amended = native_amendment(restored)
        assert amended.workbench_request_key == restored.workbench_request_key
        amended.insert()
        created.append(amended.name)
        assert not amended.workbench_request_key and not amended.workbench_request_fingerprint
        assert amended.amended_from == restored.name
        amended.submit()
        amended.cancel()
        old_receipt = batch_status(timing_request["request_id"], company, employees[0].name)["wages"]
        assert len(old_receipt) == 1 and old_receipt[0]["name"] == restored.name and old_receipt[0]["docstatus"] == 2
        checks.append("native_amendment_releases_copied_request_and_keeps_original_receipt")
        ordinary = []
        for index in range(2):
            doc = frappe.get_doc({"doctype": "Jifeng Wage Entry", "company": company, "employee": employees[0].name,
                "operation": operation.name, "work_date": today(), "payroll_date": today(), "salary_component": component.name,
                "hours": 1, "hourly_rate": 1, "workbench_request_key": "", "workbench_request_fingerprint": "",
                "notes": f"JF-WWB-ordinary-{index}-{suffix}"}).insert()
            ordinary.append(doc)
            created.append(doc.name)
        assert all(doc.workbench_request_key is None for doc in ordinary)
        checks.append("ordinary_native_drafts_normalize_empty_request_to_nullable_unique")
        partial = payload([row(hours=1, hourly=1), row()])
        blocked(lambda: save_drafts(partial))
        assert not frappe.get_all("Jifeng Wage Entry", filters={"workbench_request_key": ["like", partial["request_id"] + ":%"]})
        checks.append("last_native_row_failure_rolls_back_whole_batch")
        stale = payload([row(first, 1, 1)])
        stale["rows"][0]["source_modified"] = "2000-01-01 00:00:00"
        blocked(lambda: save_drafts(stale))
        checks.append("stale_source_version_rejected_before_insert")
        blocked(lambda: save_drafts(payload([row(team, 1, 1)])))
        checks.append("team_piece_wages_require_supervisor_allocation")
        allocation = frappe.get_doc({"doctype": "Jifeng Wage Allocation", "job_card": team.name,
            "payroll_date": today(), "salary_component": component.name,
            "employees": [{"employee": employees[index].name, "quantity": qty, "piece_rate": rate}
                          for index, (qty, rate) in enumerate(((2, 3), (3, 4)))]}).insert()
        created_allocations.append(allocation.name)
        allocation.submit()
        old_names = allocation.make_wages()
        created.extend(old_names)
        for name in old_names:
            wage = frappe.get_doc("Jifeng Wage Entry", name)
            wage.submit()
            wage.cancel()
        allocation.reload()
        allocation.cancel()
        old_rows = {entry.name for entry in allocation.employees}
        old_references = [entry.wage_entry for entry in allocation.employees]
        frappe.set_user(clerk.name)
        assert frappe.has_permission("Jifeng Wage Allocation", "create")
        assert not frappe.has_permission("Jifeng Wage Allocation", "amend")
        blocked(native_amendment(allocation).insert, frappe.PermissionError)
        frappe.set_user(manager.name)
        checks.append("hr_user_create_cannot_forge_native_allocation_amendment")
        revised = native_amendment(allocation)
        assert [entry.wage_entry for entry in revised.employees] == old_references
        for entry, qty, rate in zip(revised.employees, (1, 4), (5, 2)):
            entry.quantity, entry.piece_rate = qty, rate
        revised.insert()
        created_allocations.append(revised.name)
        assert revised.amended_from == allocation.name
        assert all(not entry.wage_entry for entry in revised.employees)
        assert old_rows.isdisjoint({entry.name for entry in revised.employees})
        revised.submit()
        new_names = revised.make_wages()
        created.extend(new_names)
        assert len(new_names) == 2 and set(new_names).isdisjoint(old_names)
        assert new_names == revised.make_wages()
        new_wages = [frappe.get_doc("Jifeng Wage Entry", name) for name in new_names]
        assert [Decimal(str(wage.total_amount)) for wage in new_wages] == [Decimal(5), Decimal(8)]
        assert all(wage.wage_allocation == revised.name and wage.wage_allocation_employee in
                   {entry.name for entry in revised.employees} for wage in new_wages)
        original = frappe.get_doc("Jifeng Wage Allocation", allocation.name)
        assert original.docstatus == 2 and [entry.wage_entry for entry in original.employees] == old_references
        assert all(frappe.get_doc("Jifeng Wage Entry", name).docstatus == 2 for name in old_names)
        checks.append("native_allocation_amendment_generates_new_exact_wage_sources_and_preserves_history")
        blocked(revised.cancel)
        for wage in new_wages:
            wage.submit()
            wage.cancel()
        revised.reload()
        revised.cancel()
        checks.append("amended_allocation_keeps_native_wage_reversal_order")
        blocked(lambda: get_entry_source(first.name, company, employees[1].name))
        checks.append("employee_not_in_explicit_source_rejected")
        forged = frappe.get_doc({"doctype": "Jifeng Wage Entry", "company": company, "employee": employees[0].name,
            "operation": operation.name, "work_date": today(), "payroll_date": today(), "salary_component": component.name,
            "hours": 1, "hourly_rate": 1, "workbench_request_key": "forged", "workbench_request_fingerprint": "fake"})
        blocked(forged.insert)
        documents[0].workbench_request_key = ""
        blocked(documents[0].save)
        checks.append("native_controller_denies_forged_or_replaced_request_identity")
        if foreign_company:
            blocked(lambda: batch_status(request["request_id"], foreign_company, employees[0].name), frappe.PermissionError)
            checks.append("company_user_permission_denies_foreign_batch_check")
        frappe.set_user(accountant.name)
        blocked(lambda: save_drafts(payload([row(hours=1, hourly=1)])), frappe.PermissionError)
        checks.append("accountant_without_native_wage_create_denied")
        frappe.set_user(original_user)
        assert _ledger_snapshot() == ledgers_before
        checks.append("no_stock_or_general_ledger_changes")
        result = {"ok": True, "checks": checks, "check_count": len(checks), "company": company,
                  "native_wages": frappe.db.count("Jifeng Wage Entry") - wage_count_before,
                  "owned_wage_identities": len(set(created)), "browser_verified": False, "fixtures_committed": False}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.in_test = original_test
        for user in users:
            frappe.clear_cache(user=user)
        for doctype in ("Employee", "Operation", "Jifeng Wage Entry"):
            frappe.clear_cache(doctype=doctype)
    assert frappe.db.count("Jifeng Wage Entry") == wage_count_before
    assert frappe.db.count("Jifeng Wage Allocation") == allocation_count_before
    assert frappe.db.count("Employee") == employee_count_before
    assert not any(frappe.db.exists("Jifeng Wage Entry", name) for name in created)
    assert not any(frappe.db.exists("Jifeng Wage Allocation", name) for name in created_allocations)
    assert _ledger_snapshot() == ledgers_before
    result["rollback_verified"] = True
    return result
