"""Rollback-only native acceptance of subcontracting material dispatch and returns."""

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.subcontract_validation import _denied, _invalid, _read


def smoke(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    from erpnext.buying.doctype.purchase_order.purchase_order import make_subcontracting_order
    from erpnext.controllers.subcontracting_controller import get_materials_from_supplier, make_rm_stock_entry
    from frappe.desk.query_report import run
    from jifeng_erp.subcontracting_material import subcontracting_material_ledger

    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not warehouse or not supplier_group:
        frappe.throw("原料台账验收需要测试公司的明细仓库和供应商分组")
    point, original_user, original_args = "jf_subcontract_material_smoke", frappe.session.user, frappe.flags.args
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        raw, finished, service = [frappe.get_doc({
            "doctype": "Item", "item_code": f"JF-SUBMAT-{kind}-{suffix}", "item_group": item_group,
            "stock_uom": stock_uom, "is_stock_item": int(kind != "SERVICE"),
            "is_sub_contracted_item": int(kind == "FG"),
        }).insert() for kind in ("RAW", "FG", "SERVICE")]
        suppliers = [frappe.get_doc({
            "doctype": "Supplier", "supplier_name": f"JF-SUBMAT-SUP-{number}-{suffix}",
            "supplier_type": "Company", "supplier_group": supplier_group,
        }).insert() for number in (1, 2)]
        supplier_warehouse = frappe.get_doc({
            "doctype": "Warehouse", "warehouse_name": "JF-SUBMAT-SUP-" + suffix, "company": company,
        }).insert()
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                             "quantity": 1, "items": [{"item_code": raw.name, "qty": 1, "rate": 10}]}).insert()
        bom.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                              "item_code": finished.name, "planned_qty": 10, "bom": bom.name,
                              "materials": [{"item_code": raw.name, "uom": stock_uom,
                                             "qty_per_unit": 1, "unit_price": 10}]}).insert()
        stock = frappe.get_doc({"doctype": "Stock Entry", "company": company,
                                "stock_entry_type": "Material Receipt",
                                "items": [{"item_code": raw.name, "qty": 20, "basic_rate": 10,
                                           "t_warehouse": warehouse}]}).insert()
        stock.submit()

        def dispatch(supplier, qty):
            purchase = frappe.get_doc({
                "doctype": "Purchase Order", "company": company, "supplier": supplier.name,
                "is_subcontracted": 1, "transaction_date": add_days(today(), -1),
                "schedule_date": add_days(today(), -1), "set_warehouse": warehouse,
                "items": [{"item_code": service.name, "qty": qty, "rate": 20,
                           "fg_item": finished.name, "fg_item_qty": qty}],
            }).insert()
            purchase.submit()
            order = make_subcontracting_order(purchase.name)
            order.transaction_date = add_days(today(), -1)
            order.schedule_date = add_days(today(), -1)
            order.supplier_warehouse = supplier_warehouse.name
            order.items[0].bom = bom.name
            order.items[0].jf_flow_order = flow.name
            order.insert()
            order.submit()
            rm = order.supplied_items[0]
            sent = frappe.get_doc(make_rm_stock_entry(order.name, [{
                "name": rm.name, "main_item_code": finished.name, "rm_item_code": raw.name,
                "qty": qty, "item_name": raw.name, "stock_uom": stock_uom, "warehouse": warehouse,
            }])).insert()
            sent.submit()
            return order, sent

        order, sent = dispatch(suppliers[0], 10)
        other_order, other_sent = dispatch(suppliers[1], 2)
        filters = {"company": company, "from_date": today(), "to_date": today(),
                   "subcontracting_order": order.name}

        def make_return(qty, submit=True):
            frappe.flags.args = {"rm_details": [order.supplied_items[0].name]}
            returned = get_materials_from_supplier(order.name)
            assert returned.purpose == "Material Transfer" and returned.is_return
            assert len(returned.items) == 1
            returned.items[0].qty = qty
            returned.insert()
            if submit:
                returned.submit()
            return returned

        returned = make_return(3)
        draft = make_return(1, submit=False)
        assert returned.items[0].jf_flow_order == flow.name
        rows = _read(subcontracting_material_ledger, filters)
        assert {row["stock_entry"] for row in rows} == {sent.name, returned.name}
        assert draft.name not in {row["stock_entry"] for row in rows}
        assert (sum(flt(row["issued_qty"]) for row in rows),
                sum(flt(row["returned_qty"]) for row in rows),
                sum(flt(row["net_qty"]) for row in rows)) == (10, 3, 7)
        assert {row["stock_uom"] for row in rows} == {stock_uom}
        assert {row["order_item"] for row in rows} == {order.items[0].name}
        assert {row["supplied_item"] for row in rows} == {order.supplied_items[0].name}
        assert len(_read(subcontracting_material_ledger, {**filters, "flow_order": flow.name,
                                                        "item_code": finished.name, "rm_item_code": raw.name})) == 2
        assert not _read(subcontracting_material_ledger, {**filters, "item_code": raw.name})
        assert not _read(subcontracting_material_ledger, {**filters, "rm_item_code": finished.name})
        assert not _read(subcontracting_material_ledger, {**filters, "from_date": add_days(today(), -1),
                                                        "to_date": add_days(today(), -1)})
        supplier_rows = _read(subcontracting_material_ledger, {**filters, "subcontracting_order": None,
                                                             "supplier": suppliers[0].name})
        assert {row["stock_entry"] for row in supplier_rows} == {sent.name, returned.name}
        assert other_sent.name not in {row["stock_entry"] for row in supplier_rows}
        returned.cancel()
        assert {row["stock_entry"] for row in _read(subcontracting_material_ledger, filters)} == {sent.name}
        returned = make_return(3)
        for bad_source in (None, other_order.supplied_items[0].name):
            try:
                frappe.db.set_value("Stock Entry Detail", sent.items[0].name, "sco_rm_detail", bad_source)
                _invalid(lambda: _read(subcontracting_material_ledger, filters))
            finally:
                frappe.db.set_value("Stock Entry Detail", sent.items[0].name, "sco_rm_detail",
                                    order.supplied_items[0].name)
        user = frappe.get_doc({
            "doctype": "User", "email": f"jf-submat-{suffix}@example.invalid",
            "first_name": "委外原料台账验收", "send_welcome_email": 0,
            "roles": [{"role": role} for role in ("Stock User", "Purchase User", "Manufacturing User")],
        }).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                        "for_value": company, "apply_to_all_doctypes": 1}).insert()
        permission = frappe.get_doc({
            "doctype": "User Permission", "user": user.name, "allow": "Subcontracting Order",
            "for_value": order.name, "apply_to_all_doctypes": 1,
        }).insert()
        frappe.set_user(user.name)
        visible = _read(subcontracting_material_ledger, {**filters, "subcontracting_order": None})
        assert {row["stock_entry"] for row in visible} == {sent.name, returned.name}
        assert len(run("Jifeng Subcontracting Material Ledger", filters=filters)["result"]) == 2
        _denied(lambda: _read(subcontracting_material_ledger, {**filters, "subcontracting_order": other_order.name}))
        frappe.set_user(original_user)
        permission.apply_to_all_doctypes = 0
        permission.applicable_for = "Subcontracting Order"
        permission.save()
        frappe.set_user(user.name)
        assert frappe.get_doc("Stock Entry", other_sent.name).has_permission("read")
        _denied(lambda: _read(subcontracting_material_ledger, {**filters, "subcontracting_order": None}))
        frappe.set_user("Guest")
        _denied(lambda: _read(subcontracting_material_ledger, filters))
        return {"passed": True, "checks": [
            "native_raw_material_dispatch", "native_material_return", "source_flow_inheritance",
            "signed_stock_unit_issued_returned_net", "exact_supplied_and_finished_item_sources",
            "draft_excluded", "cancel_return_excluded", "posting_date_period", "finished_item_filter",
            "raw_material_filter", "flow_filter", "supplier_filter_multiple_orders",
            "missing_supplied_source_denied", "foreign_supplied_source_denied",
            "native_role_report_api", "global_order_permission_isolation", "hidden_order_filter_denied",
            "readable_stock_hidden_source_denied", "guest_denied", "reports_do_not_post",
        ], "business_data": "rolled_back"}
    finally:
        frappe.flags.args = original_args
        frappe.set_user(original_user)
        _end_rollback_only(point)
