"""Rollback-only native acceptance of independent purchase-return stock pools."""

import frappe
from frappe.utils import flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _finish_fixture_reposts, _ledger_snapshot
from jifeng_erp.purchase_returns import PurchaseReceiptMixin, install_quantity_dispatcher


def smoke(company, item_group, stock_uom, preview=False):
    """Run native submit/cancel; preview extends objects only in the CLI process."""
    frappe.only_for("System Manager")
    from erpnext.controllers import sales_and_purchase_return
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
        make_purchase_return, make_purchase_return_against_rejected_warehouse,
    )
    from frappe.model.base_document import get_controller

    hook_verified = issubclass(get_controller("Purchase Receipt"), PurchaseReceiptMixin)
    if not hook_verified and not preview:
        frappe.throw("原生采购收货控制器尚未加载退货额度扩展，请先安装并重启后验收")
    point, original_user = "jf_purchase_return_pools", frappe.session.user
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        alt_uom = frappe.get_doc({"doctype": "UOM", "uom_name": "JF-RETURN-PAIR-" + suffix,
                                 "must_be_whole_number": 0}).insert()
        item = frappe.get_doc({"doctype": "Item", "item_code": "JF-RETURN-" + suffix,
                               "item_group": item_group, "stock_uom": stock_uom,
                               "uoms": [{"uom": stock_uom, "conversion_factor": 1},
                                        {"uom": alt_uom.name, "conversion_factor": 2}]}).insert()
        warehouses = {kind: frappe.get_doc({"doctype": "Warehouse", "warehouse_name": f"JF-RETURN-{kind}-{suffix}",
                                            "company": company}).insert() for kind in ("A", "R")}
        supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
        if not supplier_group:
            frappe.throw("采购退货验收需要明细供应商分组")
        supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "JF-RETURN-SUP-" + suffix,
                                   "supplier_type": "Company", "supplier_group": supplier_group}).insert()
        frappe.db.set_single_value("Buying Settings", "set_valuation_rate_for_rejected_materials", 1)
        stock = frappe.get_doc({"doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
                                "items": [{"item_code": item.name, "qty": 40, "basic_rate": 10,
                                           "t_warehouse": warehouses["A"].name}]}).insert()
        stock.submit()
        posted = [stock]
        preview_classes = {}

        def extended(document):
            if not isinstance(document, PurchaseReceiptMixin):
                assert preview, "The installed controller must use the actual app hook"
                native_class = type(document)
                if native_class not in preview_classes:
                    preview_classes[native_class] = type("PreviewPurchaseReceipt", (PurchaseReceiptMixin, native_class), {})
                document.__class__ = preview_classes[native_class]
            return document

        def receive(two_rows=False):
            rows = 2 if two_rows else 1
            document = extended(frappe.get_doc({
                "doctype": "Purchase Receipt", "company": company, "supplier": supplier.name,
                "posting_date": today(), "items": [{
                    "item_code": item.name, "qty": 2 / rows, "received_qty": 3 / rows,
                    "rejected_qty": 1 / rows, "uom": alt_uom.name, "conversion_factor": 2, "rate": 20,
                    "warehouse": warehouses["A"].name, "rejected_warehouse": warehouses["R"].name,
                } for _ in range(rows)],
            }))
            document.insert()
            document.submit()
            assert all(row.stock_qty == 4 / rows for row in document.items)
            posted.append(document)
            return document

        def returning(source, accepted=0, rejected=0, rejected_only=False, source_idx=0,
                      submit=True, stale_stock=False):
            mapper = make_purchase_return_against_rejected_warehouse if rejected_only else make_purchase_return
            document = mapper(source.name)
            # Native mapping omits exhausted lines. Explicit attempts beyond an
            # exhausted source still require a genuine server-side bound check.
            source_row = source.items[source_idx]
            mapped = next((row for row in document.items if row.purchase_receipt_item == source_row.name), None)
            if not mapped:
                document = frappe.copy_doc(source)
                document.is_return, document.return_against = 1, source.name
                document.set("items", [frappe.copy_doc(source_row)])
                mapped = document.items[0]
                mapped.purchase_receipt_item = source_row.name
            document.set("items", [mapped])
            document = extended(document)
            mapped.qty = -(rejected if rejected_only else accepted) / 2
            mapped.rejected_qty = 0 if rejected_only else -rejected / 2
            mapped.received_qty = mapped.qty + mapped.rejected_qty
            mapped.stock_qty = 0 if stale_stock else mapped.qty * 2
            mapped.return_qty_from_rejected_warehouse = int(rejected_only)
            mapped.warehouse = warehouses["R"].name if rejected_only else warehouses["A"].name
            mapped.rejected_warehouse = None if rejected_only else warehouses["R"].name
            document.insert()
            if submit:
                document.submit()
                posted.append(document)
            return document

        def expect_over_return(call):
            before = _ledger_snapshot()
            try:
                call()
            except sales_and_purchase_return.StockOverReturnError:
                pass
            else:
                raise AssertionError("A true source-pool over-return must be rejected")
            assert _ledger_snapshot() == before, "Rejected over-return must not post or alter ledgers"

        def net(source, expected_accepted, expected_rejected):
            names = {source.name}
            names.update(frappe.get_all("Purchase Receipt", filters={"return_against": source.name,
                         "is_return": 1, "docstatus": 1}, pluck="name"))
            rows = frappe.get_all("Stock Ledger Entry", filters={"voucher_type": "Purchase Receipt",
                                 "voucher_no": ["in", list(names)], "is_cancelled": 0},
                                 fields=["warehouse", "actual_qty", "stock_uom"])
            actual = tuple(sum(flt(row.actual_qty) for row in rows if row.warehouse == warehouses[kind].name)
                           for kind in ("A", "R"))
            assert actual == (expected_accepted, expected_rejected), (source.name, actual)
            assert {row.stock_uom for row in rows} == {stock_uom}

        first = receive()
        returning(first, accepted=1)
        returning(first, rejected=1, rejected_only=True)
        returning(first, accepted=1, rejected=1)
        returning(first, accepted=2, stale_stock=True)
        net(first, 0, 0)
        expect_over_return(lambda: returning(first, accepted=1, stale_stock=True))
        expect_over_return(lambda: returning(first, rejected=1, rejected_only=True))

        accepted_first = receive()
        returning(accepted_first, accepted=4)
        returning(accepted_first, rejected=2)
        net(accepted_first, 0, 0)

        rejected_first = receive()
        returning(rejected_first, rejected=2, rejected_only=True)
        returning(rejected_first, accepted=1)
        returning(rejected_first, accepted=3)
        net(rejected_first, 0, 0)

        mixed = receive()
        returning(mixed, accepted=1, rejected=1)
        returning(mixed, rejected=1, rejected_only=True)
        expect_over_return(lambda: returning(mixed, rejected=1, rejected_only=True))
        expect_over_return(lambda: returning(mixed, accepted=4, stale_stock=True))
        returning(mixed, accepted=3)
        net(mixed, 0, 0)

        released = receive()
        draft = returning(released, accepted=4, submit=False)
        assert draft.docstatus == 0
        canceled = returning(released, accepted=2)
        canceled.cancel()
        returning(released, accepted=4)
        canceled_rejected = returning(released, rejected=2, rejected_only=True)
        canceled_rejected.cancel()
        returning(released, rejected=2)
        net(released, 0, 0)

        duplicates = receive()
        duplicate_return = extended(make_purchase_return(duplicates.name))
        for accepted in (3, 2):
            row = duplicate_return.items[0] if accepted == 3 else duplicate_return.append(
                "items", frappe.copy_doc(duplicate_return.items[0]))
            row.qty, row.received_qty, row.rejected_qty, row.stock_qty = -accepted / 2, -accepted / 2, 0, -accepted
        expect_over_return(duplicate_return.insert)
        net(duplicates, 4, 2)

        exact = receive(two_rows=True)
        returning(exact, accepted=2, source_idx=0)
        returning(exact, accepted=1, source_idx=1)
        expect_over_return(lambda: returning(exact, accepted=2, source_idx=1))
        returning(exact, accepted=1, source_idx=1)
        returning(exact, rejected=1, rejected_only=True, source_idx=0)
        returning(exact, rejected=1, rejected_only=True, source_idx=1)
        net(exact, 0, 0)

        dispatch = install_quantity_dispatcher()
        assert dispatch is install_quantity_dispatcher()
        before = _ledger_snapshot()
        other_doc = frappe._dict(doctype="Sales Invoice", company=company, update_stock=1, is_return=1)
        other_row = frappe._dict(item_code=item.name, stock_qty=-2, qty=-2, conversion_factor=1, idx=1)
        try:
            dispatch(other_doc, item.name, other_row, frappe._dict(stock_qty=1), {}, {})
        except sales_and_purchase_return.StockOverReturnError:
            pass
        else:
            raise AssertionError("Non-Purchase-Receipt must retain native quantity protection")
        assert _ledger_snapshot() == before
        completed = _finish_fixture_reposts(company, {item.name}, {warehouse.name for warehouse in warehouses.values()},
                                            {(document.doctype, document.name) for document in posted})
        for source in (first, accepted_first, rejected_first, mixed, released, exact):
            net(source, 0, 0)
        net(duplicates, 4, 2)
        return {"passed": True, "checks": [
            "native_purchase_receipt_extension", "native_alternative_uom_factor_two",
            "accepted_then_rejected_same_source", "rejected_then_accepted_same_source",
            "mixed_return_separate_pool_remainders", "full_accepted_then_plain_rejected_return",
            "full_rejected_then_accepted_return", "native_mapper_stale_stock_quantity",
            "true_accepted_over_return_denied", "true_rejected_over_return_denied",
            "cancel_releases_accepted_and_rejected_pools", "draft_does_not_consume_pool",
            "duplicate_current_source_rows_cannot_split_over_return", "same_item_exact_source_rows",
            "native_non_purchase_quantity_validator_preserved", "idempotent_permanent_dispatch",
            "physical_warehouse_ledgers_and_stock_units", "invalid_returns_leave_ledger_contents_unchanged",
        ], "extension_hook_verified": hook_verified, "native_fixture_reposts_completed": completed,
                "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
