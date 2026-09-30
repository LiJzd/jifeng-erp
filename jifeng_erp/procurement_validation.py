"""Rollback-only native acceptance of purchase request and receipt report edges."""

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot, _read
from jifeng_erp.subcontract_validation import _denied, _invalid


def _masters(company, item_group, stock_uom):
    suffix = frappe.generate_hash(length=10)
    group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not group:
        frappe.throw("采购验收需要明细供应商分组")
    item = frappe.get_doc({"doctype": "Item", "item_code": "JF-PROC-" + suffix,
                           "item_group": item_group, "stock_uom": stock_uom,
                           "valuation_method": "Moving Average"}).insert()
    supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "JF-PROC-SUP-" + suffix,
                               "supplier_type": "Company", "supplier_group": group}).insert()
    warehouses = frappe._dict({kind: frappe.get_doc({"doctype": "Warehouse", "company": company,
                                       "warehouse_name": f"JF-PROC-{kind}-{suffix}"}).insert()
                  for kind in ("MAIN", "REJECT")})
    flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company, "item_code": item.name,
                           "planned_qty": 10, "materials": [{"item_code": item.name, "uom": stock_uom,
                                                             "qty_per_unit": 1, "unit_price": 10}]}).insert()
    # This native mode permits direct PRs; rollback and cache clearing restore it.
    frappe.db.set_single_value("Buying Settings", "po_required", "No")
    return frappe._dict(company=company, suffix=suffix, item=item, supplier=supplier,
                        warehouses=warehouses, flow=flow, stock_uom=stock_uom)


def _request(fixture, quantity=10):
    document = frappe.get_doc({"doctype": "Material Request", "company": fixture.company,
                               "material_request_type": "Purchase", "transaction_date": today(),
                               "schedule_date": today(), "items": [{"item_code": fixture.item.name,
                                   "qty": quantity, "warehouse": fixture.warehouses.MAIN.name,
                                   "jf_flow_order": fixture.flow.name}]}).insert()
    document.submit()
    return document


def _direct_receipt(fixture, accepted, rejected=0, request=None, posting_date=None,
                    currency=None, conversion_rate=1, duplicate=False):
    values = {"item_code": fixture.item.name, "qty": accepted, "rate": 10,
              "rejected_qty": rejected, "received_qty": accepted + rejected,
              "warehouse": fixture.warehouses.MAIN.name,
              "rejected_warehouse": fixture.warehouses.REJECT.name if rejected else None,
              "jf_flow_order": fixture.flow.name}
    if request:
        values.update(material_request=request.name, material_request_item=request.items[0].name)
    items = [values]
    if duplicate:
        items.append({**values, "qty": 2, "rejected_qty": 1, "received_qty": 3})
    document = frappe.get_doc({"doctype": "Purchase Receipt", "company": fixture.company,
                               "supplier": fixture.supplier.name, "posting_date": posting_date or today(),
                               "posting_time": "09:00:00", "set_posting_time": 1,
                               "currency": currency or frappe.db.get_value("Company", fixture.company, "default_currency"),
                               "conversion_rate": conversion_rate, "items": items}).insert()
    document.submit()
    return document


def _order(request, fixture):
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order

    document = make_purchase_order(request.name, args={"supplier": fixture.supplier.name})
    document.schedule_date = today()
    for row in document.items:
        row.schedule_date, row.rate = today(), 10
    document.insert()
    document.submit()
    return document


def _filters(fixture):
    return {"company": fixture.company, "from_date": add_days(today(), -1), "to_date": today(),
            "item_code": fixture.item.name, "flow_order": fixture.flow.name}


def _request_row(report, fixture, request):
    rows = [row for row in _read(report, _filters(fixture)) if row["material_request"] == request.name]
    assert len(rows) == 1, rows
    return rows[0]


def _request_quantities(row, expected):
    actual = tuple(flt(row[key]) for key in
                   ("requested_qty", "ordered_qty", "received_qty", "remaining_qty", "pending_qty"))
    assert actual == expected, (actual, expected)


def coverage_probe(company, item_group, stock_uom):
    """Observe native direct receipt followed by a PO for the unreceived remainder."""
    frappe.only_for("System Manager")
    from jifeng_erp.procurement_reports import purchase_request_ledger

    point, original_user = "jf_procurement_coverage_probe", frappe.session.user
    _begin_rollback_only(point)
    try:
        fixture = _masters(company, item_group, stock_uom)
        request = _request(fixture)
        receipt = _direct_receipt(fixture, 5, request=request)
        request.reload()
        assert (flt(request.items[0].ordered_qty), flt(request.items[0].received_qty)) == (0, 5)
        purchase = _order(request, fixture)
        request.reload()
        assert flt(purchase.items[0].stock_qty) == 5
        assert purchase.items[0].material_request_item == request.items[0].name
        assert (flt(request.items[0].ordered_qty), flt(request.items[0].received_qty)) == (5, 5)
        row = _request_row(purchase_request_ledger, fixture, request)
        return {"native_combination_allowed": True, "business_data": "rolled_back",
                "requested_stock_qty": 10, "direct_received_stock_qty": 5,
                "po_committed_stock_qty": 5, "po_received_stock_qty": 0,
                "expected_pending_qty": 0, "reported_pending_qty": flt(row["pending_qty"]),
                "exact_request_item_matches": bool(receipt.items[0].material_request_item ==
                                                    purchase.items[0].material_request_item)}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")


def _return(original, index=0, rejected=False):
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
        make_purchase_return, make_purchase_return_against_rejected_warehouse,
    )

    mapper = make_purchase_return_against_rejected_warehouse if rejected else make_purchase_return
    document = mapper(original.name)
    source = original.items[index]
    row = next(row for row in document.items if row.purchase_receipt_item == source.name)
    row.qty, row.rejected_qty, row.received_qty, row.stock_qty = -1, 0, -1, -1
    document.set("items", [row])
    document.posting_date, document.posting_time, document.set_posting_time = today(), "11:00:00", 1
    document.insert()
    document.submit()
    document.reload()
    assert row.purchase_receipt_item == source.name
    assert document.items[0].return_qty_from_rejected_warehouse == int(rejected)
    return document


def _api(name, filters):
    from frappe.desk.query_report import run

    before = _ledger_snapshot()
    try:
        return run(name, filters=filters)["result"]
    finally:
        assert _ledger_snapshot() == before, "A report API must leave all ledger contents unchanged"


def _source_corruption(receipt, field, value, read):
    row = receipt.items[0]
    original = row.get(field)
    try:
        frappe.db.set_value("Purchase Receipt Item", row.name, field, value)
        _invalid(read)
    finally:
        frappe.db.set_value("Purchase Receipt Item", row.name, field, original)


def smoke(company, item_group, stock_uom, report_api=True):
    """Verify actual native side effects and permission boundaries, then roll back."""
    frappe.only_for("System Manager")
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.material_request.material_request import update_status
    from jifeng_erp.procurement_reports import purchase_receipt_ledger, purchase_request_ledger

    point, original_user = "jf_procurement_reports", frappe.session.user
    _begin_rollback_only(point)
    try:
        fixture = _masters(company, item_group, stock_uom)
        request = _request(fixture)
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 0, 10, 10))
        direct = _direct_receipt(fixture, 5, request=request)
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 5, 10, 5))
        update_status(request.name, "Stopped")
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 5, 10, 0))
        assert not _read(purchase_request_ledger, {**_filters(fixture), "only_pending": 1})
        update_status(request.name, "Pending")
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 5, 10, 5))
        direct.cancel()
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 0, 10, 10))
        direct = _direct_receipt(fixture, 5, request=request)
        purchase = _order(request, fixture)
        assert flt(purchase.items[0].stock_qty) == 5
        # Direct receipts cover a different half from the remaining PO promise.
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 5, 5, 5, 0))
        po_receipt = make_purchase_receipt(purchase.name).insert()
        po_receipt.submit()
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 5, 10, 5, 0))
        po_receipt.cancel()
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 5, 5, 5, 0))
        purchase.cancel()
        _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 5, 10, 5))

        company_currency = frappe.db.get_value("Company", company, "default_currency")
        foreign_currency = "USD" if company_currency != "USD" else "CNY"
        original = _direct_receipt(fixture, 6, 2, posting_date=add_days(today(), -1),
                                   currency=foreign_currency, conversion_rate=7, duplicate=True)
        accepted_return = _return(original)
        rejected_return = _return(original, rejected=True)
        second_return = _return(original, index=1)
        assert not frappe.get_meta("Purchase Receipt").has_field("return_qty_from_rejected_warehouse")
        assert frappe.get_meta("Purchase Receipt Item").has_field("return_qty_from_rejected_warehouse")
        filters = {**_filters(fixture), "from_date": today(), "only_returns": 1}
        returns = _read(purchase_receipt_ledger, filters)
        by_receipt = {row["purchase_receipt"]: row for row in returns}
        assert set(by_receipt) == {accepted_return.name, rejected_return.name, second_return.name}
        assert (flt(by_receipt[accepted_return.name]["accepted_qty"]),
                flt(by_receipt[rejected_return.name]["rejected_qty"])) == (-1, -1)
        assert flt(by_receipt[rejected_return.name]["accepted_qty"]) == 0
        assert all(row["source_receipt"] == original.name for row in returns)
        assert by_receipt[second_return.name]["source_receipt_item"] == original.items[1].name
        assert by_receipt[accepted_return.name]["source_receipt_item"] == original.items[0].name
        rows = _read(purchase_receipt_ledger, _filters(fixture))
        originals = [row for row in rows if row["purchase_receipt"] == original.name]
        assert {row["receipt_item"] for row in originals} == {row.name for row in original.items}
        source = next(row for row in originals if row["receipt_item"] == original.items[0].name)
        assert tuple(flt(source[key]) for key in ("accepted_qty", "rejected_qty", "physical_stock_qty")) == (6, 2, 8)
        native = original.items[0]
        assert tuple(flt(source[key]) for key in ("amount", "base_amount", "net_amount", "base_net_amount")) == (
            native.amount, native.base_amount, native.net_amount, native.base_net_amount)
        assert (source["currency"], source["company_currency"], flt(source["conversion_rate"])) == (
            foreign_currency, company_currency, 7)
        for warehouse, expected in ((fixture.warehouses.MAIN.name, (6, 0, 6)),
                                    (fixture.warehouses.REJECT.name, (0, 2, 2))):
            selected = next(row for row in _read(purchase_receipt_ledger, {**_filters(fixture), "warehouse": warehouse})
                            if row["receipt_item"] == native.name)
            assert tuple(flt(selected[key]) for key in ("accepted_qty", "rejected_qty", "physical_stock_qty")) == expected
            assert all(selected[key] == source[key] for key in ("amount", "base_amount", "net_amount", "base_net_amount"))
        rejected_rows = _read(purchase_receipt_ledger, {**filters, "warehouse": fixture.warehouses.REJECT.name})
        assert len(rejected_rows) == 1 and rejected_rows[0]["purchase_receipt"] == rejected_return.name
        def read_returns():
            return _read(purchase_receipt_ledger, filters)

        _source_corruption(accepted_return, "purchase_receipt_item", None, read_returns)
        _source_corruption(accepted_return, "purchase_receipt_item", "MISSING-EXACT-SOURCE", read_returns)

        for role in ("Purchase User", "Stock User"):
            user = frappe.get_doc({"doctype": "User", "email": f"jf-proc-{role.split()[0].lower()}-{fixture.suffix}@example.invalid",
                                   "first_name": "采购报表验收", "send_welcome_email": 0,
                                   "roles": [{"role": role}, {"role": "Manufacturing User"}]}).insert()
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                            "for_value": company, "apply_to_all_doctypes": 1}).insert()
            frappe.set_user(user.name)
            _request_quantities(_request_row(purchase_request_ledger, fixture, request), (10, 0, 5, 10, 5))
            assert len(_read(purchase_receipt_ledger, filters)) == 3
            if report_api:
                assert len(_api("Jifeng Purchase Request Ledger", _filters(fixture))) == 1
                assert len(_api("Jifeng Purchase Receipt Ledger", filters)) == 3
            frappe.set_user(original_user)
            permission = frappe.get_doc({"doctype": "User Permission", "user": user.name,
                "allow": "Purchase Receipt", "for_value": accepted_return.name,
                "apply_to_all_doctypes": 0, "applicable_for": "Purchase Receipt"}).insert()
            frappe.set_user(user.name)
            _denied(read_returns)
            frappe.set_user(original_user)
            permission.delete()
        frappe.set_user("Guest")
        _denied(lambda: _read(purchase_request_ledger, _filters(fixture)))
        _denied(read_returns)
        return {"passed": True, "business_data": "rolled_back", "checks": [
            "native_direct_receipt_updates_request_stock_received", "native_stop_pending_zero_history_retained",
            "native_reopen_restores_pending", "cancel_direct_receipt_releases_request_qty",
            "direct_receipt_and_remaining_po_are_distinct_coverage", "po_receipt_not_double_counted",
            "cancel_po_receipt_retains_order_commitment", "cancel_po_releases_order_commitment",
            "accepted_and_rejected_returns_same_source", "persistent_child_rejected_return_flag_reload",
            "return_sources_outside_selected_dates", "duplicate_item_rows_exact_source_identity",
            "warehouse_filter_clips_only_own_quantity", "warehouse_filter_keeps_whole_voucher_money",
            "native_transaction_and_company_currency_values", "missing_exact_return_source_denied",
            "purchase_and_stock_user_report_access", "hidden_return_source_denied",
            "all_ledger_contents_unchanged_by_reads", "guest_denied",
        ] + (["native_report_api_role_access"] if report_api else [])}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
