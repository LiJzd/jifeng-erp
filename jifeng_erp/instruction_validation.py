"""Native production instruction checks, limited to an isolated test site."""

import frappe
from frappe.utils import add_days, now_datetime, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


def _reject(call, fragment):
    try:
        call()
    except frappe.ValidationError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError("Expected rejection: " + fragment)


def smoke(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    from jifeng_erp.production_instruction import flow_plan_rows, validate_plan, validate_work_order

    point = "jf_instruction_smoke"
    user = frappe.session.user
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        before_stock = frappe.db.count("Stock Ledger Entry")
        before_gl = frappe.db.count("GL Entry")
        raw, first, second = [frappe.get_doc({"doctype": "Item", "item_code": f"JF-INSTR-{kind}-{suffix}",
                                             "item_group": item_group, "stock_uom": stock_uom}).insert()
                              for kind in ("RAW", "FG1", "FG2")]
        flows = []
        for item, qty in ((first, 5), (second, 3)):
            bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": item.name,
                                 "quantity": 1, "items": [{"item_code": raw.name, "qty": 1, "rate": 10}]}).insert()
            bom.submit()
            flows.append(frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                                         "item_code": item.name, "planned_qty": qty, "bom": bom.name}).insert())
        rows = flow_plan_rows(company, [flow.name for flow in flows])
        assert sorted(row["planned_qty"] for row in rows) == [3, 5]
        first_row = next(row for row in rows if row["jf_flow_order"] == flows[0].name)
        first_row["planned_qty"] = 2
        plan = frappe.get_doc({"doctype": "Production Plan", "company": company, "po_items": rows,
                               "jf_instruction_no": "TEST-<instruction>", "jf_instruction_weight": 7,
                               "jf_instruction_amount": 120, "jf_instruction_remark": "<script>test</script>"})
        _reject(lambda: validate_plan(plan), "重量单位")
        plan.jf_instruction_weight_uom = stock_uom
        plan.insert()
        assert plan.jf_instruction_currency == frappe.get_cached_value("Company", company, "default_currency")
        plan.jf_instruction_remark = "<script>updated</script>"
        plan.save()
        assert frappe.db.exists("Version", {"ref_doctype": "Production Plan", "docname": plan.name})
        for row in plan.po_items:
            row.planned_start_date = add_days(today(), 2) + " 09:00:00"
        plan.save()
        plan.submit()
        plan.make_work_order()
        works = [frappe.get_doc("Work Order", name) for name in frappe.get_all(
            "Work Order", filters={"production_plan": plan.name}, pluck="name")]
        assert len(works) == 2
        assert sorted(work.qty for work in works) == [2, 3]
        assert {work.jf_flow_order for work in works} == {flow.name for flow in flows}
        assert all(str(work.planned_start_date) == add_days(today(), 2) + " 09:00:00" for work in works)
        plan.make_work_order()
        assert frappe.db.count("Work Order", {"production_plan": plan.name}) == 2
        remaining = flow_plan_rows(company, [flows[0].name])
        assert remaining[0]["planned_qty"] == 3
        second_plan = frappe.get_doc({"doctype": "Production Plan", "company": company, "po_items": remaining}).insert()
        second_plan.submit()
        second_plan.make_work_order()
        split = frappe.get_doc("Work Order", frappe.db.get_value("Work Order", {"production_plan": second_plan.name}))
        assert split.qty == 3 and split.jf_flow_order == flows[0].name
        _reject(lambda: flow_plan_rows(company, [flows[0].name]), "全部安排")
        extra = frappe.copy_doc(split)
        extra.production_plan = None
        extra.production_plan_item = None
        extra.qty = 1
        _reject(lambda: extra.insert(), "数量合计超过")
        conflict = frappe.copy_doc(split)
        conflict.jf_flow_order = flows[1].name
        _reject(lambda: validate_work_order(conflict), "与生产计划来源不一致")
        flows[0].planned_qty = 4
        _reject(lambda: flows[0].save(), "不能小于已有")
        flows[0].planned_qty = 5
        flows[0].bom = flows[1].bom
        _reject(lambda: flows[0].save(), "已有有效生产工单")
        flows[0].bom = split.bom_no
        print_html = frappe.get_print("Production Plan", plan.name, print_format="Jifeng Production Instruction")
        assert "集沣生产指令单" in print_html and all(flow.name in print_html for flow in flows)
        assert "&lt;script&gt;updated&lt;/script&gt;" in print_html and "<script>updated</script>" not in print_html
        assert "TEST-&lt;instruction&gt;" in print_html and "参考金额" in print_html
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0})
        split.source_warehouse = warehouse
        split.fg_warehouse = warehouse
        split.wip_warehouse = warehouse
        split.save()
        split.submit()
        split.cancel()
        assert flow_plan_rows(company, [flows[0].name])[0]["planned_qty"] == 3
        assert frappe.db.count("Stock Ledger Entry") == before_stock and frappe.db.count("GL Entry") == before_gl
        plan.jf_instruction_amount = -1
        _reject(lambda: validate_plan(plan), "非负有限")
        plan.jf_instruction_amount = 120
        plan.po_items[0].jf_flow_order = flows[1].name if plan.po_items[0].item_code == first.name else flows[0].name
        _reject(lambda: validate_plan(plan), "商品、BOM")
        _reject(lambda: flow_plan_rows(company, [flows[0].name, flows[0].name]), "重复选择")
        frappe.set_user("Guest")
        try:
            flow_plan_rows(company, [flows[0].name])
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest must not import flow plans")
        return {"passed": True, "checks": ["multi_product_plan", "weight_unit_required", "company_currency",
                "plan_change_version", "schedule_inheritance", "native_factory_flow_inheritance",
                "repeat_factory_no_duplicates", "partial_flow_allocation", "fully_planned_guard",
                "excess_work_order_guard", "source_conflict_guard", "flow_quantity_reduction_guard",
                "flow_bom_change_guard", "native_instruction_print", "escaped_print_text",
                "cancel_releases_allocation", "no_stock_or_gl_posting", "negative_amount_guard",
                "plan_row_identity_guard", "duplicate_selection_guard", "guest_denied"],
                "business_data": "rolled_back"}
    finally:
        frappe.set_user(user)
        _end_rollback_only(point)
