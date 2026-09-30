"""Rollback-only native manufacturing validation for an installed test site."""

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


def smoke(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    from jifeng_erp.production import job_card_progress, work_order_progress

    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    if not warehouse:
        frappe.throw("测试公司需要启用的明细仓库")
    point = "jf_production_smoke"
    original_user = frappe.session.user
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        raw, finished = [frappe.get_doc({"doctype": "Item", "item_code": f"JF-PROD-{kind}-{suffix}",
                                        "item_group": item_group, "stock_uom": stock_uom}).insert()
                         for kind in ("RAW", "FG")]
        workstation = frappe.get_doc({"doctype": "Workstation", "workstation_name": "JF-PROD-WS-" + suffix,
                                       "production_capacity": 1}).insert()
        operations = [frappe.get_doc({"doctype": "Operation", "name": f"JF-PROD-OP-{idx}-{suffix}",
                                      "workstation": workstation.name}).insert() for idx in (1, 2)]
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        settings.save()
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                             "quantity": 1, "with_operations": 1, "transfer_material_against": "Work Order",
                             "items": [{"item_code": raw.name, "qty": 1, "rate": 10}],
                             "operations": [{"operation": operation.name, "workstation": workstation.name,
                                             "time_in_mins": 1} for operation in operations]}).insert()
        bom.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                              "item_code": finished.name, "planned_qty": 5, "bom": bom.name,
                              "materials": [{"item_code": raw.name, "uom": stock_uom,
                                             "qty_per_unit": 1, "unit_price": 10}]}).insert()
        stock = frappe.get_doc({"doctype": "Stock Entry", "company": company,
                                "stock_entry_type": "Material Receipt", "items": [{
                                    "item_code": raw.name, "qty": 5, "basic_rate": 10, "t_warehouse": warehouse,
                                }]}).insert()
        stock.submit()
        work = frappe.get_doc("Work Order", flow.make_work_order())
        work.source_warehouse = warehouse
        work.fg_warehouse = warehouse
        work.skip_transfer = 1
        work.planned_start_date = add_days(today(), -2) + " 08:00:00"
        work.save()
        work.submit()
        cards = [frappe.get_doc("Job Card", name) for name in frappe.get_all(
            "Job Card", filters={"work_order": work.name}, pluck="name", order_by="operation asc")]
        assert len(cards) == 2 and all(card.jf_flow_order == flow.name for card in cards), [
            (card.name, card.jf_flow_order) for card in cards]
        filters = {"company": company, "from_date": today(), "to_date": today(), "flow_order": flow.name}
        progress = work_order_progress(filters)[1]
        assert len(progress) == 1 and progress[0]["remaining_qty"] == 5 and progress[0]["produced_qty"] == 0
        assert progress[0]["overdue_days"] == 2
        user = frappe.get_doc({"doctype": "User", "email": f"jf-production-{suffix}@example.invalid",
                               "first_name": "生产验收", "send_welcome_email": 0,
                               "roles": [{"role": "Manufacturing User"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                        "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Work Order",
                        "for_value": work.name, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(user.name)
        for idx, card in enumerate(cards):
            completed = 5 if idx == 0 else 4
            card.process_loss_qty = 0 if idx == 0 else 1
            card.append("time_logs", {"from_time": today() + f" {9 + idx}:00:00",
                                      "to_time": today() + f" {9 + idx}:30:00", "completed_qty": completed})
            card.save()
            row = next(row for row in job_card_progress(filters)[1] if row["name"] == card.name)
            assert row["draft_qty"] == completed and row["approved_qty"] == 0
            card.submit()
        detail = job_card_progress(filters)[1]
        assert len(detail) == 2 and sorted(row["approved_qty"] for row in detail) == [4, 5]
        assert all(row["draft_qty"] == 0 for row in detail)
        assert len(job_card_progress({**filters, "only_loss": 1, "approval": "已提交"})[1]) == 1
        assert all(row["hours"] == 0.5 and row["jf_flow_order"] == flow.name for row in detail)
        assert work_order_progress(filters)[1][0]["produced_qty"] == 0, "Operation completion is not stock receipt"
        frappe.set_user(original_user)
        manufacture = frappe.get_doc(make_stock_entry(work.name, "Manufacture", 5)).insert()
        manufacture.submit()
        assert all(row.jf_flow_order == flow.name for row in manufacture.items)
        progress = work_order_progress(filters)[1][0]
        assert (progress["produced_qty"], progress["process_loss_qty"], progress["remaining_qty"], progress["overdue_days"]) == (4, 1, 0, 0)
        assert len(work_order_progress({**filters, "only_loss": 1})[1]) == 1
        assert progress["status"] == "Completed" and progress["progress_percent"] == 100
        ledger = frappe.get_all("Stock Ledger Entry", filters={"voucher_no": manufacture.name, "is_cancelled": 0},
                                fields=["item_code", "actual_qty"])
        assert sum(flt(row.actual_qty) for row in ledger if row.item_code == finished.name) == 4

        hidden = frappe.copy_doc(work)
        hidden.jf_flow_order = None
        hidden.insert()
        frappe.set_user(user.name)
        visible = work_order_progress({**filters, "flow_order": None})[1]
        assert [row["name"] for row in visible] == [work.name]
        assert len(job_card_progress(filters)[1]) == 2
        assert frappe.has_permission("Jifeng Flow Order", "read", doc=flow)
        from frappe.desk.query_report import run

        assert len(run("Jifeng Production Progress", filters=filters)["result"]) == 1
        assert len(run("Jifeng Job Card Detail", filters=filters)["result"]) == 2
        frappe.set_user("Guest")
        for report in (work_order_progress, job_card_progress):
            try:
                report(filters)
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Guest cannot read production reports")
        return {"passed": True, "checks": ["native_two_operation_work_order", "job_card_flow_inheritance",
                "overdue_work_order", "draft_completed_separation", "submitted_operation_quantities",
                "operation_is_not_stock_receipt", "native_manufacture", "stock_flow_inheritance",
                "finished_progress_and_ledger", "manufacturing_user_work_order_isolation",
                "manufacturing_user_job_card_submit", "report_api_role_access", "loss_reporting", "guest_denied"],
                "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
