"""Rollback-only acceptance of native subcontract dispatch, receipts and reports."""

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


def _denied(call):
    try:
        call()
    except frappe.PermissionError:
        return
    raise AssertionError("An incomplete or unauthorized report must be denied")


def _invalid(call):
    try:
        call()
    except frappe.ValidationError:
        return
    raise AssertionError("An inconsistent source must not populate a report")


def _read(report, filters):
    """Every report read must leave both native posting ledgers unchanged."""
    before = {doctype: frappe.db.count(doctype) for doctype in ("Stock Ledger Entry", "GL Entry")}
    try:
        return report(filters)[1]
    finally:
        assert before == {doctype: frappe.db.count(doctype) for doctype in before}, "A report must never post"


def _quantities(row, expected):
    fields = ("planned_qty", "accepted_qty", "rejected_qty", "loss_qty", "returned_qty",
              "covered_qty", "remaining_qty", "pending_qty")
    actual = tuple(flt(row[field]) for field in fields)
    assert actual == expected, (actual, expected, row)


def smoke(company, item_group, stock_uom):
    """Explicitly submit synthetic native transactions, verify, then roll them all back."""
    frappe.only_for("System Manager")
    from erpnext.buying.doctype.purchase_order.purchase_order import make_subcontracting_order
    from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
        make_subcontracting_receipt, update_subcontracting_order_status,
    )
    from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import (
        make_subcontract_return, make_subcontract_return_against_rejected_warehouse,
    )
    from jifeng_erp.subcontracting import subcontracting_progress, subcontracting_receipt_ledger

    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not warehouse or not supplier_group:
        frappe.throw("测试公司需要启用的明细仓库和供应商分组")
    point, original_user = "jf_subcontract_smoke", frappe.session.user
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        raw, finished, service = [frappe.get_doc({
            "doctype": "Item", "item_code": f"JF-SUB-{kind}-{suffix}", "item_group": item_group,
            "stock_uom": stock_uom, "is_stock_item": int(kind != "SERVICE"),
            "is_sub_contracted_item": int(kind == "FG"),
        }).insert() for kind in ("RAW", "FG", "SERVICE")]
        supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "JF-SUB-SUP-" + suffix,
                                   "supplier_type": "Company", "supplier_group": supplier_group}).insert()
        supplier_warehouse, rejected_warehouse = [frappe.get_doc({
            "doctype": "Warehouse", "warehouse_name": f"JF-SUB-{kind}-{suffix}", "company": company,
        }).insert() for kind in ("SUP", "REJ")]
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                             "quantity": 1, "items": [{"item_code": raw.name, "qty": 1, "rate": 10}]}).insert()
        bom.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company, "item_code": finished.name,
                              "planned_qty": 10, "bom": bom.name,
                              "materials": [{"item_code": raw.name, "uom": stock_uom,
                                             "qty_per_unit": 1, "unit_price": 10}]}).insert()
        stock = frappe.get_doc({"doctype": "Stock Entry", "company": company,
                                "stock_entry_type": "Material Receipt",
                                "items": [{"item_code": raw.name, "qty": 10, "basic_rate": 10,
                                           "t_warehouse": warehouse}]}).insert()
        stock.submit()

        def make_order(qty=10):
            purchase = frappe.get_doc({"doctype": "Purchase Order", "company": company,
                                       "supplier": supplier.name, "is_subcontracted": 1,
                                       "transaction_date": add_days(today(), -1),
                                       "schedule_date": add_days(today(), -1), "set_warehouse": warehouse,
                                       "items": [{"item_code": service.name, "qty": qty, "rate": 20,
                                                  "fg_item": finished.name, "fg_item_qty": qty}]}).insert()
            purchase.submit()
            subcontract = make_subcontracting_order(purchase.name)
            subcontract.transaction_date = add_days(today(), -1)
            subcontract.schedule_date = add_days(today(), -1)
            subcontract.supplier_warehouse = supplier_warehouse.name
            subcontract.items[0].bom = bom.name
            subcontract.items[0].schedule_date = add_days(today(), -1)
            subcontract.items[0].expected_delivery_date = add_days(today(), -1)
            subcontract.items[0].jf_flow_order = flow.name
            subcontract.insert()
            subcontract.submit()
            return subcontract

        order = make_order()
        assert order.items[0].jf_flow_order == flow.name
        rm_row = order.supplied_items[0]
        transfer = frappe.get_doc(make_rm_stock_entry(order.name, [{
            "name": rm_row.name, "main_item_code": finished.name, "rm_item_code": raw.name,
            "qty": 10, "item_name": raw.name, "stock_uom": stock_uom, "warehouse": warehouse,
        }])).insert()
        transfer.submit()
        assert transfer.items[0].sco_rm_detail == rm_row.name
        assert transfer.items[0].jf_flow_order == flow.name
        order_filters = {"company": company, "from_date": add_days(today(), -1),
                         "to_date": add_days(today(), -1), "subcontracting_order": order.name}
        receipt_filters = {**order_filters, "from_date": today(), "to_date": today()}
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 0, 0, 0, 0, 0, 10, 10))

        def receive(accepted, rejected=0, loss=0, submit=True):
            receipt = make_subcontracting_receipt(order.name)
            receipt.items[0].qty = accepted
            receipt.items[0].rejected_qty = rejected
            receipt.items[0].process_loss_qty = loss
            receipt.items[0].rejected_warehouse = rejected_warehouse.name if rejected else None
            receipt.insert()
            if submit:
                receipt.submit()
            assert receipt.items[0].jf_flow_order == flow.name
            return receipt

        first = receive(2, 1, 1)
        second = receive(3, 1)
        draft = receive(1, submit=False)
        row = _read(subcontracting_progress, order_filters)[0]
        _quantities(row, (10, 5, 2, 1, 0, 8, 2, 2))
        assert row["overdue_days"] == 1
        assert len(_read(subcontracting_progress, {**order_filters, "only_pending": 1})) == 1
        assert len(_read(subcontracting_progress, {**order_filters, "only_overdue": 1})) == 1
        ledger = _read(subcontracting_receipt_ledger, receipt_filters)
        assert {entry["receipt"] for entry in ledger} == {first.name, second.name}
        assert draft.name not in {entry["receipt"] for entry in ledger}
        assert sum(flt(entry["accepted_qty"]) for entry in ledger) == 5
        assert sum(flt(entry["rejected_qty"]) for entry in ledger) == 2
        assert sum(flt(entry["loss_qty"]) for entry in ledger) == 1
        assert not _read(subcontracting_receipt_ledger, order_filters)
        assert not _read(subcontracting_progress, receipt_filters), "Order batch uses transaction_date"
        # The original receipt must remain discoverable through its readable
        # Order even when a legacy child row has lost its precise source key.
        source_row = first.items[0]
        original_order_item = source_row.subcontracting_order_item
        try:
            frappe.db.set_value("Subcontracting Receipt Item", source_row.name, "subcontracting_order_item", None)
            _invalid(lambda: _read(subcontracting_progress, order_filters))
            _invalid(lambda: _read(subcontracting_receipt_ledger, receipt_filters))
        finally:
            frappe.db.set_value("Subcontracting Receipt Item", source_row.name,
                                "subcontracting_order_item", original_order_item)
        try:
            frappe.db.set_value("Subcontracting Receipt Item", source_row.name,
                                {"subcontracting_order_item": None, "subcontracting_order": None})
            _invalid(lambda: _read(subcontracting_receipt_ledger,
                                   {**receipt_filters, "subcontracting_order": None}))
        finally:
            frappe.db.set_value("Subcontracting Receipt Item", source_row.name,
                                {"subcontracting_order_item": original_order_item,
                                 "subcontracting_order": order.name})

        def return_accepted(qty):
            returned = make_subcontract_return(first.name)
            returned.items[0].qty = -qty
            returned.items[0].rejected_qty = 0
            returned.items[0].process_loss_qty = 0
            returned.insert()
            returned.submit()
            assert returned.items[0].jf_flow_order == flow.name
            return returned

        accepted_return = return_accepted(1)
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 4, 2, 1, 1, 7, 3, 3))
        accepted_entry = next(entry for entry in _read(subcontracting_receipt_ledger, receipt_filters)
                              if entry["receipt"] == accepted_return.name)
        assert accepted_entry["entry_type"] == "实物退回"
        assert (accepted_entry["source_receipt"], accepted_entry["source_receipt_item"]) == (
            first.name, first.items[0].name)
        assert (flt(accepted_entry["accepted_qty"]), flt(accepted_entry["rejected_qty"]),
                flt(accepted_entry["loss_qty"]), flt(accepted_entry["covered_qty"])) == (-1, 0, 0, -1)
        accepted_return.cancel()
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 5, 2, 1, 0, 8, 2, 2))
        assert accepted_return.name not in {entry["receipt"] for entry in
                                          _read(subcontracting_receipt_ledger, receipt_filters)}

        rejected_return = make_subcontract_return_against_rejected_warehouse(first.name)
        rejected_return.items[0].process_loss_qty = 0
        rejected_return.insert()
        rejected_return.submit()
        assert rejected_return.items[0].return_qty_from_rejected_warehouse == 1
        assert (rejected_return.items[0].qty, rejected_return.items[0].rejected_qty) == (-1, 0)
        rejected_return.reload()
        assert not rejected_return.items[0].get("return_qty_from_rejected_warehouse")
        assert rejected_return.items[0].warehouse == first.items[0].rejected_warehouse
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 5, 1, 1, 1, 7, 3, 3))
        rejected_entry = next(entry for entry in _read(subcontracting_receipt_ledger, receipt_filters)
                              if entry["receipt"] == rejected_return.name)
        assert (flt(rejected_entry["accepted_qty"]), flt(rejected_entry["rejected_qty"]),
                flt(rejected_entry["loss_qty"]), flt(rejected_entry["covered_qty"])) == (0, -1, 0, -1)
        accepted_return = return_accepted(1)
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 4, 1, 1, 2, 6, 4, 4))
        ledger = _read(subcontracting_receipt_ledger, receipt_filters)
        assert (sum(flt(entry["accepted_qty"]) for entry in ledger),
                sum(flt(entry["rejected_qty"]) for entry in ledger),
                sum(flt(entry["loss_qty"]) for entry in ledger),
                sum(flt(entry["covered_qty"]) for entry in ledger)) == (4, 1, 1, 6)

        update_subcontracting_order_status(order.name, "Closed")
        row = _read(subcontracting_progress, order_filters)[0]
        _quantities(row, (10, 4, 1, 1, 2, 6, 4, 0))
        assert row["overdue_days"] == 0
        assert not _read(subcontracting_progress, {**order_filters, "only_pending": 1})
        assert not _read(subcontracting_progress, {**order_filters, "only_overdue": 1})
        update_subcontracting_order_status(order.name)
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 4, 1, 1, 2, 6, 4, 4))

        second.cancel()
        _quantities(_read(subcontracting_progress, order_filters)[0], (10, 1, 0, 1, 2, 2, 8, 8))
        assert second.name not in {entry["receipt"] for entry in
                                  _read(subcontracting_receipt_ledger, receipt_filters)}
        hidden_order = make_order(1)
        user = frappe.get_doc({"doctype": "User", "email": f"jf-subcontract-{suffix}@example.invalid",
                               "first_name": "委外报表验收", "send_welcome_email": 0,
                               "roles": [{"role": role} for role in
                                         ("Stock User", "Purchase User", "Manufacturing User")]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                        "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Subcontracting Order",
                        "for_value": order.name, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(user.name)
        visible = _read(subcontracting_progress, {**order_filters, "subcontracting_order": None})
        assert {entry["subcontracting_order"] for entry in visible} == {order.name}
        assert hidden_order.name not in {entry["subcontracting_order"] for entry in visible}
        assert len(_read(subcontracting_receipt_ledger, receipt_filters)) == 3
        from frappe.desk.query_report import run

        assert len(run("Jifeng Subcontracting Progress", filters=order_filters)["result"]) == 1
        assert len(run("Jifeng Subcontracting Receipt Ledger", filters=receipt_filters)["result"]) == 3
        _denied(lambda: _read(subcontracting_progress, {**order_filters,
                                                       "subcontracting_order": hidden_order.name}))

        # Native permission restrictions can hide a related return while leaving
        # the original Order readable. A misleading partial net total is denied.
        frappe.set_user(original_user)
        receipt_permission = frappe.get_doc({
            "doctype": "User Permission", "user": user.name, "allow": "Subcontracting Receipt",
            "for_value": first.name, "apply_to_all_doctypes": 0, "applicable_for": "Subcontracting Receipt",
        }).insert()
        frappe.set_user(user.name)
        assert frappe.get_doc("Subcontracting Order", order.name).has_permission("read")
        _denied(lambda: _read(subcontracting_progress, order_filters))
        _denied(lambda: _read(subcontracting_receipt_ledger, receipt_filters))
        frappe.set_user(original_user)
        receipt_permission.delete()

        # Corrupt legacy links cannot silently remove a return from the net.
        bad_row = accepted_return.items[0]
        original_source = bad_row.subcontracting_receipt_item
        try:
            frappe.db.set_value("Subcontracting Receipt Item", bad_row.name, "subcontracting_receipt_item", None)
            _invalid(lambda: _read(subcontracting_progress, order_filters))
            _invalid(lambda: _read(subcontracting_receipt_ledger, receipt_filters))
        finally:
            frappe.db.set_value("Subcontracting Receipt Item", bad_row.name,
                                "subcontracting_receipt_item", original_source)
        frappe.set_user("Guest")
        _denied(lambda: _read(subcontracting_progress, order_filters))
        _denied(lambda: _read(subcontracting_receipt_ledger, receipt_filters))
        return {"passed": True, "checks": [
            "native_service_purchase_order", "native_subcontract_order", "native_raw_material_transfer",
            "stock_source_flow_inheritance", "multi_receipt_accepted_rejected_loss", "draft_receipt_excluded",
            "order_batch_date_receipts_all_dates", "receipt_posting_date", "native_accepted_return",
            "accepted_return_signed_ledger", "cancel_return_restores_net", "native_rejected_warehouse_return",
            "rejected_return_transient_flag_absent", "rejected_return_pool_classification",
            "loss_preserved_after_returns", "signed_receipt_ledger_net",
            "native_close_keeps_remaining", "closed_order_no_pending_or_overdue", "native_reopen_restores_pending",
            "cancel_receipt_excluded", "native_role_report_access", "order_user_permission_isolation",
            "native_report_api_role_access", "missing_ledger_order_links_denied",
            "hidden_order_filter_denied", "incomplete_return_read_denied", "missing_return_source_denied",
            "missing_original_order_source_denied", "reports_do_not_post", "guest_denied",
        ], "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
