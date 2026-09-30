"""Rollback-only acceptance against native purchasing and inventory documents."""

import hashlib
import json

import frappe
from frappe.utils import add_days, cstr, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.subcontract_validation import _denied, _invalid


def _ledger_snapshot():
    """Detect edited or removed ledger contents as well as newly posted rows."""
    return {
        doctype: hashlib.sha256(json.dumps(
            frappe.db.sql(f"select * from `tab{doctype}` order by name", as_dict=True),
            default=cstr, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        for doctype in ("Stock Ledger Entry", "GL Entry")
    }


def _read(report, filters):
    before = _ledger_snapshot()
    try:
        return report(filters)[1]
    finally:
        assert _ledger_snapshot() == before, "A read must not insert, edit or delete ledger rows"


def _latest(item, warehouse, posting_date):
    entries = frappe.get_all("Stock Ledger Entry", filters={
        "item_code": item, "warehouse": warehouse, "posting_date": ["<=", posting_date],
        "is_cancelled": 0, "docstatus": ["<", 2],
    }, fields=["qty_after_transaction", "stock_value", "valuation_rate", "stock_uom"],
        order_by="posting_datetime desc, creation desc, name desc", limit_page_length=1)
    return entries[0] if entries else frappe._dict(qty_after_transaction=0, stock_value=0)


def _stock(item, warehouse, posting_date, qty, value):
    entry = _latest(item, warehouse, posting_date)
    actual = (flt(entry.qty_after_transaction, 6), flt(entry.stock_value, 6))
    assert actual == (qty, value), (item, warehouse, posting_date, actual, (qty, value))


def _finish_fixture_reposts(company, items, warehouses, vouchers):
    """Complete only this fixture's native valuation jobs before reading reports."""
    from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

    jobs = frappe.get_all("Repost Item Valuation", filters={"company": company, "docstatus": 1,
                          "status": ["in", ["Queued", "In Progress"]]}, fields=["name", "based_on",
                          "item_code", "warehouse", "voucher_type", "voucher_no"], order_by="creation asc")
    own = [job for job in jobs if ((job.based_on == "Item and Warehouse" and job.item_code in items
                                  and job.warehouse in warehouses) or
                                 (job.based_on == "Transaction" and (job.voucher_type, job.voucher_no) in vouchers))]
    original_test, original_repost = frappe.in_test, frappe.flags.through_repost_item_valuation
    original_max_writes = frappe.db.MAX_WRITES_PER_TRANSACTION
    try:
        # Native test mode executes the same ledger recalculation synchronously
        # and propagates failures; it never marks a task completed without work.
        frappe.in_test = True
        for job in own:
            document = frappe.get_doc("Repost Item Valuation", job.name)
            if document.status in ("Queued", "In Progress"):
                repost(document)
            document.reload()
            assert document.status == "Completed", (document.name, document.status)
    finally:
        frappe.in_test = original_test
        frappe.flags.through_repost_item_valuation = original_repost
        frappe.db.MAX_WRITES_PER_TRANSACTION = original_max_writes
    return len(own)


def _fixture(company, item_group, stock_uom):
    """Build a chronological physical-stock story with independent expected balances."""
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
        make_purchase_return, make_purchase_return_against_rejected_warehouse,
    )
    from erpnext.stock.doctype.stock_entry.stock_entry import make_stock_in_entry

    suffix = frappe.generate_hash(length=10)
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not supplier_group:
        frappe.throw("库存验收需要明细供应商分组")
    alt_uom = frappe.get_doc({"doctype": "UOM", "uom_name": "JF-INV-PAIR-" + suffix,
                             "must_be_whole_number": 0}).insert()
    item = frappe.get_doc({"doctype": "Item", "item_code": "JF-INV-" + suffix,
                           "item_group": item_group, "stock_uom": stock_uom,
                           "valuation_method": "Moving Average",
                           "uoms": [{"uom": stock_uom, "conversion_factor": 1},
                                    {"uom": alt_uom.name, "conversion_factor": 2}]}).insert()
    hidden_item = frappe.get_doc({"doctype": "Item", "item_code": "JF-INV-HIDDEN-" + suffix,
                                  "item_group": item_group, "stock_uom": stock_uom}).insert()
    if not frappe.db.exists("Warehouse Type", "Transit"):
        frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
    warehouses = {kind: frappe.get_doc({
        "doctype": "Warehouse", "warehouse_name": f"JF-INV-{kind}-{suffix}", "company": company,
        "warehouse_type": "Transit" if kind == "TRANSIT" else None,
    }).insert() for kind in ("A", "B", "REJECTED", "TRANSIT")}
    supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "JF-INV-SUP-" + suffix,
                               "supplier_type": "Company", "supplier_group": supplier_group}).insert()
    dates = {number: add_days(today(), -number) for number in range(11)}
    # Pin rejected-material valuation within this rollback transaction, so
    # quantity AND value have a deterministic independent expected story.
    frappe.db.set_single_value("Buying Settings", "set_valuation_rate_for_rejected_materials", 1)

    def entry(purpose, qty, day, clock, source=None, target=None, submit=True, **extra):
        document = frappe.get_doc({
            "doctype": "Stock Entry", "company": company, "stock_entry_type": purpose,
            "posting_date": dates[day], "posting_time": clock, "set_posting_time": 1,
            "items": [{"item_code": item.name, "qty": qty, "basic_rate": 10,
                       "s_warehouse": source, "t_warehouse": target}], **extra,
        })
        document.insert()
        if submit:
            document.submit()
        return document

    initial = frappe.get_doc({
        "doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
        "posting_date": dates[10], "posting_time": "08:00:00", "set_posting_time": 1,
        "items": [{"item_code": item.name, "qty": 15, "uom": alt_uom.name, "conversion_factor": 2,
                   "basic_rate": 10, "t_warehouse": warehouses["A"].name}],
    }).insert()
    initial.submit()
    assert initial.items[0].transfer_qty == 30
    _stock(item.name, warehouses["A"].name, dates[10], 30, 300)
    request = frappe.get_doc({
        "doctype": "Material Request", "company": company, "material_request_type": "Purchase",
        "transaction_date": dates[9], "schedule_date": dates[9],
        "items": [{"item_code": item.name, "qty": 10, "warehouse": warehouses["A"].name}],
    }).insert()
    request.submit()
    purchase = make_purchase_order(request.name, args={"supplier": supplier.name})
    purchase.transaction_date = dates[9]
    purchase.schedule_date = dates[9]
    purchase.items[0].schedule_date = dates[9]
    purchase.items[0].qty = 5
    purchase.items[0].uom = alt_uom.name
    purchase.items[0].conversion_factor = 2
    purchase.items[0].rate = 20
    purchase.insert()
    purchase.submit()
    assert purchase.items[0].stock_qty == 10
    assert purchase.items[0].material_request_item == request.items[0].name

    def receive(accepted, rejected, clock, submit=True):
        receipt = make_purchase_receipt(purchase.name)
        receipt.posting_date, receipt.posting_time, receipt.set_posting_time = dates[9], clock, 1
        receipt.items[0].qty = accepted / 2
        receipt.items[0].rejected_qty = rejected / 2
        receipt.items[0].received_qty = (accepted + rejected) / 2
        receipt.items[0].rejected_warehouse = warehouses["REJECTED"].name if rejected else None
        receipt.insert()
        if submit:
            receipt.submit()
        return receipt

    first, second = receive(4, 1, "09:00:00"), receive(3, 1, "10:00:00")
    draft_receipt = receive(1, 0, "11:00:00", submit=False)
    accepted_return = make_purchase_return(first.name)
    accepted_return.posting_date = dates[9]
    accepted_return.posting_time, accepted_return.set_posting_time = "12:00:00", 1
    accepted_return.items[0].qty = -0.5
    accepted_return.items[0].rejected_qty = 0
    accepted_return.items[0].received_qty = -0.5
    accepted_return.items[0].stock_qty = -1
    accepted_return.insert()
    accepted_return.submit()
    # Exercise each native return path on a separate source receipt. The
    # installed controller shares its already-returned bound across warehouse
    # paths; that separate native limitation must not be masked by QA flags.
    rejected_return = make_purchase_return_against_rejected_warehouse(second.name)
    rejected_return.posting_date = dates[9]
    rejected_return.posting_time, rejected_return.set_posting_time = "13:00:00", 1
    rejected_return.items[0].stock_qty = -1
    rejected_return.insert()
    rejected_return.submit()
    assert rejected_return.items[0].return_qty_from_rejected_warehouse == 1
    canceled_return = make_purchase_return(second.name)
    canceled_return.posting_date = dates[9]
    canceled_return.posting_time, canceled_return.set_posting_time = "14:00:00", 1
    canceled_return.items[0].qty, canceled_return.items[0].rejected_qty = -0.5, 0
    canceled_return.items[0].received_qty = -0.5
    canceled_return.items[0].stock_qty = -1
    canceled_return.insert()
    canceled_return.submit()
    canceled_return.cancel()
    _stock(item.name, warehouses["A"].name, dates[9], 36, 360)
    _stock(item.name, warehouses["REJECTED"].name, dates[9], 1, 10)
    canceled_receipt = entry("Material Receipt", 1, 8, "07:00:00", target=warehouses["A"].name)
    canceled_receipt.cancel()
    draft_stock = entry("Material Receipt", 1, 8, "07:30:00", target=warehouses["A"].name, submit=False)
    issued = entry("Material Issue", 2, 8, "08:00:00", source=warehouses["A"].name)
    transfer = entry("Material Transfer", 3, 8, "09:00:00", source=warehouses["A"].name,
                     target=warehouses["B"].name)
    outward = entry("Material Transfer", 4, 7, "09:00:00", source=warehouses["A"].name,
                    target=warehouses["TRANSIT"].name, add_to_transit=1)
    _stock(item.name, warehouses["TRANSIT"].name, dates[7], 4, 40)
    inward = make_stock_in_entry(outward.name)
    inward.posting_date, inward.posting_time, inward.set_posting_time = dates[6], "09:00:00", 1
    inward.items[0].t_warehouse = warehouses["B"].name
    inward.insert()
    inward.submit()
    assert inward.outgoing_stock_entry == outward.name
    assert inward.items[0].ste_detail == outward.items[0].name
    _stock(item.name, warehouses["A"].name, dates[6], 27, 270)
    _stock(item.name, warehouses["B"].name, dates[6], 7, 70)
    _stock(item.name, warehouses["TRANSIT"].name, dates[6], 0, 0)

    def reconcile(qty, rate, day, submit=True):
        reconciliation = frappe.get_doc({
            "doctype": "Stock Reconciliation", "company": company, "purpose": "Stock Reconciliation",
            "posting_date": dates[day], "posting_time": "09:00:00", "set_posting_time": 1,
            "items": [{"item_code": item.name, "warehouse": warehouses["A"].name,
                       "qty": qty, "valuation_rate": rate}],
        }).insert()
        if submit:
            reconciliation.submit()
        return reconciliation

    gain = reconcile(30, 10, 5)
    loss = reconcile(25, 10, 4)
    revalue = reconcile(25, 12, 3)
    zero = reconcile(0, 12, 2)
    restored = reconcile(8, 12, 1)
    canceled_reconciliation = reconcile(6, 12, 0)
    canceled_reconciliation.cancel()
    draft_reconciliation = reconcile(9, 12, 0, submit=False)
    for day, qty, value in ((5, 30, 300), (4, 25, 250), (3, 25, 300), (2, 0, 0), (1, 8, 96), (0, 8, 96)):
        _stock(item.name, warehouses["A"].name, dates[day], qty, value)
    hidden_stock = frappe.get_doc({
        "doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
        "items": [{"item_code": hidden_item.name, "qty": 1, "basic_rate": 10,
                   "t_warehouse": warehouses["A"].name}],
    }).insert()
    hidden_stock.submit()
    fixture_docs = (initial, first, second, accepted_return, rejected_return, canceled_return,
                    canceled_receipt, issued, transfer, outward, inward, gain, loss, revalue,
                    zero, restored, canceled_reconciliation, hidden_stock)
    native_reposts = _finish_fixture_reposts(company, {item.name, hidden_item.name},
                                            {warehouse.name for warehouse in warehouses.values()},
                                            {(document.doctype, document.name) for document in fixture_docs})
    for day, qty, value in ((5, 30, 300), (4, 25, 250), (3, 25, 300), (2, 0, 0), (1, 8, 96), (0, 8, 96)):
        _stock(item.name, warehouses["A"].name, dates[day], qty, value)
    return frappe._dict(locals())


def fixture_smoke(company, item_group, stock_uom):
    """Validate the native fixture before depending on any custom report implementation."""
    frappe.only_for("System Manager")
    point, original_user = "jf_inventory_fixture", frappe.session.user
    _begin_rollback_only(point)
    try:
        fixture = _fixture(company, item_group, stock_uom)
        return {"passed": True, "checks": ["native_purchase_request_order_receipts",
                "native_accepted_and_rejected_returns", "native_return_cancel_restores_stock",
                "native_alternative_uom", "native_material_receipt_and_issue", "native_direct_transfer",
                "native_two_leg_transit_transfer", "native_gain_loss_revalue_zero_restore",
                "native_reconciliation_cancel_restores_stock"], "business_data": "rolled_back",
                "native_fixture_reposts_completed": fixture.native_reposts,
                "ending_qty": _latest(fixture.item.name, fixture.warehouses["A"].name, today()).qty_after_transaction}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")


def smoke(company, item_group, stock_uom, report_api=True):
    """Verify complete historical balances and readable, physically posted adjustment rows."""
    frappe.only_for("System Manager")
    from frappe.desk.query_report import run
    from jifeng_erp.inventory_reports import historical_stock_balance, stock_reconciliation_ledger
    from jifeng_erp.procurement_reports import purchase_receipt_ledger, purchase_request_ledger
    from jifeng_erp.stock_transaction_reports import stock_entry_ledger

    point, original_user = "jf_inventory_reports", frappe.session.user
    _begin_rollback_only(point)
    try:
        fixture = _fixture(company, item_group, stock_uom)
        item, warehouses, dates = fixture.item, fixture.warehouses, fixture.dates
        filters = {"company": company, "from_date": dates[10], "to_date": today(), "item_code": item.name}

        requests = _read(purchase_request_ledger, filters)
        assert len(requests) == 1 and requests[0]["material_request"] == fixture.request.name
        assert tuple(flt(requests[0][key]) for key in ("requested_qty", "ordered_qty", "remaining_qty", "pending_qty")) == (10, 10, 0, 0)
        receipts = _read(purchase_receipt_ledger, filters)
        assert {row["purchase_receipt"] for row in receipts} == {
            fixture.first.name, fixture.second.name, fixture.accepted_return.name, fixture.rejected_return.name,
        }
        assert tuple(sum(flt(row[key]) for row in receipts) for key in ("accepted_qty", "rejected_qty", "physical_stock_qty")) == (6, 1, 7)
        rejected_return = next(row for row in receipts if row["purchase_receipt"] == fixture.rejected_return.name)
        assert (flt(rejected_return["accepted_qty"]), flt(rejected_return["rejected_qty"])) == (0, -1)
        assert all(row["stock_uom"] == stock_uom for row in receipts)
        for row in receipts:
            stock = frappe.get_all("Stock Ledger Entry", filters={"voucher_no": row["purchase_receipt"],
                                   "is_cancelled": 0}, fields=["actual_qty", "stock_uom"])
            assert sum(flt(sle.actual_qty) for sle in stock) == flt(row["physical_stock_qty"])
            assert {sle.stock_uom for sle in stock} == {stock_uom}
        stock_receipts = _read(stock_entry_ledger, {**filters, "mode": "receipt"})
        assert len(stock_receipts) == 1 and stock_receipts[0]["stock_entry"] == fixture.initial.name
        assert (flt(stock_receipts[0]["document_qty"]), flt(stock_receipts[0]["movement_qty"])) == (15, 30)
        issued = _read(stock_entry_ledger, {**filters, "mode": "issue"})
        assert len(issued) == 1 and issued[0]["stock_entry"] == fixture.issued.name
        assert tuple(flt(issued[0][key]) for key in ("movement_qty", "source_qty", "target_qty", "net_qty")) == (2, -2, 0, -2)
        transfers = _read(stock_entry_ledger, {**filters, "mode": "transfer"})
        assert {row["stock_entry"] for row in transfers} == {fixture.transfer.name, fixture.outward.name, fixture.inward.name}
        assert sorted(flt(row["movement_qty"]) for row in transfers) == [3, 4, 4]
        assert all(flt(row["net_qty"]) == 0 and flt(row["net_value"]) == 0 for row in transfers)
        inward = next(row for row in transfers if row["stock_entry"] == fixture.inward.name)
        assert inward["outgoing_stock_entry"] == fixture.outward.name
        assert inward["source_detail"] == fixture.outward.items[0].name and inward["source_read_state"] == "已核对"
        assert len(_read(stock_entry_ledger, {**filters, "mode": "transfer", "warehouse": warehouses["B"].name})) == 2

        def balance(from_day, to_day, kind="A", include_zero=1):
            rows = _read(historical_stock_balance, {**filters, "from_date": dates[from_day],
                         "to_date": dates[to_day], "warehouse": warehouses[kind].name,
                         "include_zero": include_zero})
            if not rows:
                return None
            assert len(rows) == 1
            row = rows[0]
            assert row["item_code"] == item.name and row["warehouse"] == warehouses[kind].name
            assert row["stock_uom"] == stock_uom
            native = _latest(item.name, warehouses[kind].name, dates[to_day])
            assert flt(row["bal_qty"], 6) == flt(native.qty_after_transaction, 6)
            assert flt(row["bal_val"], 6) == flt(native.stock_value, 6)
            return row

        baseline = balance(9, 6)
        assert tuple(flt(baseline[key]) for key in ("opening_qty", "opening_val", "in_qty", "in_val",
                     "out_qty", "out_val", "bal_qty", "bal_val")) == (30, 300, 7, 70, 10, 100, 27, 270)
        for kind, expected in (("B", (0, 7, 0, 7)), ("REJECTED", (0, 2, 1, 1)), ("TRANSIT", (0, 4, 4, 0))):
            row = balance(9, 6, kind)
            assert tuple(flt(row[key]) for key in ("opening_qty", "in_qty", "out_qty", "bal_qty")) == expected
        assert balance(7, 7, "TRANSIT")["bal_qty"] == 4
        assert balance(6, 6, "TRANSIT")["bal_qty"] == 0
        for day, expected in ((5, (3, 0, 30, 0)), (4, (0, 5, 0, 50)),
                              (3, (0, 0, 50, 0)), (2, (0, 25, 0, 300)), (1, (8, 0, 96, 0))):
            row = balance(day, day)
            assert tuple(flt(row[key]) for key in ("in_qty", "out_qty", "in_val", "out_val")) == expected
        ending = balance(5, 0)
        assert tuple(flt(ending[key]) for key in ("opening_qty", "opening_val", "in_qty", "in_val",
                     "out_qty", "out_val", "bal_qty", "bal_val", "val_rate")) == (27, 270, 11, 176, 30, 350, 8, 96, 12)
        canceled_day = balance(0, 0)
        assert tuple(flt(canceled_day[key]) for key in ("opening_qty", "in_qty", "out_qty", "bal_qty")) == (8, 0, 0, 8)
        assert balance(0, 0, "TRANSIT", include_zero=0) is None
        assert balance(0, 0, "TRANSIT", include_zero=1)["bal_qty"] == 0

        rows = _read(stock_reconciliation_ledger, filters)
        expected_reconciliations = {
            fixture.gain.name: (27, 30, 3, 270, 300, 30),
            fixture.loss.name: (30, 25, -5, 300, 250, -50),
            fixture.revalue.name: (25, 25, 0, 250, 300, 50),
            fixture.zero.name: (25, 0, -25, 300, 0, -300),
            fixture.restored.name: (0, 8, 8, 0, 96, 96),
        }
        assert {row["reconciliation"] for row in rows} == set(expected_reconciliations)
        for row in rows:
            assert tuple(flt(row[key]) for key in ("before_qty", "after_qty", "quantity_difference",
                         "before_value", "after_value", "value_difference")) == expected_reconciliations[row["reconciliation"]]
            assert flt(row["ledger_value_difference"]) == flt(row["value_difference"])
            assert row["stock_uom"] == stock_uom and row["warehouse"] == warehouses["A"].name
            document = frappe.get_doc("Stock Reconciliation", row["reconciliation"])
            assert row["reconciliation_item"] == document.items[0].name
        assert len(_read(stock_reconciliation_ledger, {**filters, "only_difference": 1})) == 5
        assert not _read(stock_reconciliation_ledger, {**filters, "from_date": today(), "to_date": today()})
        assert not _read(stock_reconciliation_ledger, {**filters, "warehouse": warehouses["B"].name})
        _invalid(lambda: _read(historical_stock_balance, {**filters, "from_date": today(), "to_date": dates[1]}))

        user = frappe.get_doc({"doctype": "User", "email": f"jf-inventory-{fixture.suffix}@example.invalid",
                               "first_name": "库存台账验收", "send_welcome_email": 0,
                               "roles": [{"role": "Stock User"}, {"role": "Purchase User"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                        "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Item",
                        "for_value": item.name, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(user.name)
        assert balance(9, 6)["bal_qty"] == 27
        assert balance(5, 0)["bal_qty"] == 8
        _denied(lambda: _read(stock_reconciliation_ledger, filters))
        _denied(lambda: _read(historical_stock_balance, {**filters, "item_code": fixture.hidden_item.name}))
        frappe.set_user(original_user)
        user.append("roles", {"role": "Stock Manager"})
        user.save()
        frappe.set_user(user.name)
        assert balance(5, 0)["bal_qty"] == 8
        assert len(_read(stock_reconciliation_ledger, filters)) == 5
        if report_api:
            for report in ("Jifeng Historical Stock Balance", "Jifeng Stock Reconciliation Ledger"):
                before = _ledger_snapshot()
                result = run(report, filters=filters)
                assert result["result"] and _ledger_snapshot() == before

        frappe.set_user(original_user)
        source_permission = frappe.get_doc({
            "doctype": "User Permission", "user": user.name, "allow": "Stock Entry",
            "for_value": fixture.initial.name, "apply_to_all_doctypes": 0, "applicable_for": "Stock Entry",
        }).insert()
        frappe.set_user(user.name)
        assert frappe.has_permission("Stock Ledger Entry", "read")
        assert not frappe.get_doc("Stock Entry", fixture.transfer.name).has_permission("read")
        assert balance(5, 0)["bal_qty"] == 8, "Balance access is defined by complete readable ledger evidence"
        frappe.set_user(original_user)
        source_permission.delete()
        visible_sle = frappe.db.get_value("Stock Ledger Entry", {"voucher_no": fixture.initial.name,
                                         "is_cancelled": 0}, "name")
        ledger_permission = frappe.get_doc({
            "doctype": "User Permission", "user": user.name, "allow": "Stock Ledger Entry",
            "for_value": visible_sle, "apply_to_all_doctypes": 0, "applicable_for": "Stock Ledger Entry",
        }).insert()
        frappe.set_user(user.name)
        _denied(lambda: _read(historical_stock_balance, filters))
        frappe.set_user(original_user)
        ledger_permission.delete()
        warehouse_permission = frappe.get_doc({
            "doctype": "User Permission", "user": user.name, "allow": "Warehouse",
            "for_value": warehouses["A"].name, "apply_to_all_doctypes": 1,
        }).insert()
        frappe.set_user(user.name)
        _denied(lambda: _read(historical_stock_balance, {**filters, "warehouse": warehouses["B"].name}))
        assert balance(5, 0)["bal_qty"] == 8
        assert len(_read(stock_reconciliation_ledger, {**filters, "warehouse": warehouses["A"].name})) == 5
        frappe.set_user(original_user)
        warehouse_permission.delete()
        item.disabled = 1
        item.save()
        warehouses["A"].disabled = 1
        warehouses["A"].save()
        assert balance(5, 0)["bal_qty"] == 8, "Disabled masters must retain historical stock evidence"
        assert len(_read(stock_reconciliation_ledger, filters)) == 5
        frappe.set_user("Guest")
        _denied(lambda: _read(historical_stock_balance, filters))
        _denied(lambda: _read(stock_reconciliation_ledger, filters))
        checks = [
            "native_purchase_request_order_receipts", "stock_unit_conversion", "accepted_rejected_returns",
            "canceled_returns_restore_stock", "material_receipt_issue", "one_row_two_endpoint_transfer",
            "two_stage_transit_out_and_in", "historical_opening_in_out_balance", "physical_ledger_qty_and_value",
            "gain_loss_and_zero_reconciliations", "value_only_revaluation", "reconciliation_row_identity",
            "draft_and_cancel_excluded", "historical_before_and_after_reconciliation", "end_date_boundary",
            "zero_balance_option", "disabled_historical_masters", "date_validation", "stock_user_ledger_scope",
            "stock_manager_native_roles", "item_permission_isolation",
            "balance_does_not_expose_hidden_source", "partial_ledger_denies_balance", "warehouse_permission_isolation", "guest_denied",
            "reports_leave_all_ledger_contents_unchanged",
            "request_ordered_stock_qty", "signed_purchase_receipt_rejected_warehouse_returns",
            "purchase_receipt_matches_native_stock_ledger", "alternate_uom_receipt_business_stock_qty",
            "transfer_one_business_row_per_native_detail", "transfer_warehouse_endpoint_filter",
        ]
        if report_api:
            checks.append("report_api_role_access")
        return {"passed": True, "checks": checks, "report_api_verified": bool(report_api),
                "native_fixture_reposts_completed": fixture.native_reposts,
                "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
