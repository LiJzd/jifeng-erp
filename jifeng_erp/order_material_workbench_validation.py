"""Serial rollback-only acceptance of saved analysis and partial native stock issues."""

from decimal import Decimal

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.order_material_workbench import create_issue, detail, query
from jifeng_erp.subcontract_validation import _denied, _invalid


def smoke(company, item_group, stock_uom, foreign_company):
    frappe.only_for("System Manager")
    original_user, original_test = frappe.session.user, frappe.in_test
    point, suffix = "jf_order_material_native", frappe.generate_hash(length=10)
    checks = []
    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        alt = frappe.get_doc({"doctype": "UOM", "uom_name": "JF-OM-PAIR-" + suffix,
                              "must_be_whole_number": 0}).insert()
        raw = frappe.get_doc({"doctype": "Item", "item_code": "JF-OM-RAW-" + suffix,
            "item_group": item_group, "stock_uom": stock_uom, "valuation_method": "Moving Average",
            "uoms": [{"uom": stock_uom, "conversion_factor": 1},
                     {"uom": alt.name, "conversion_factor": 2}]}).insert()
        finished = frappe.get_doc({"doctype": "Item", "item_code": "JF-OM-FG-" + suffix,
                                  "item_group": item_group, "stock_uom": stock_uom}).insert()
        warehouses = [frappe.get_doc({"doctype": "Warehouse", "warehouse_name": f"JF-OM-{i}-{suffix}",
                                     "company": company}).insert() for i in range(2)]
        foreign_warehouse = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "JF-OM-OTHER-" + suffix,
                                            "company": foreign_company}).insert()
        receipt = frappe.get_doc({"doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
            "items": [{"item_code": raw.name, "qty": 30, "basic_rate": 10, "t_warehouse": warehouses[0].name}]}).insert()
        receipt.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company, "item_code": finished.name,
            "planned_qty": 5, "materials": [
                {"item_code": raw.name, "uom": alt.name, "qty_per_unit": 1, "unit_price": 3},
                {"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 2}]}).insert()
        filters = {"company": company, "from_date": today(), "to_date": today(), "specified_date": 1,
                   "keyword": "", "flow_order": flow.name}
        before = _ledger_snapshot()
        data = query(filters)
        assert _ledger_snapshot() == before
        assert len(data["materials"]) == 2 and len(data["summary"]) == 2
        assert {row["uom"] for row in data["summary"]} == {stock_uom, alt.name}
        assert all(row["quantity"] == Decimal(5) for row in data["summary"])
        assert sum(row["budget_amount"] for row in data["summary"]) == 25
        assert all(row["sources"][0]["material_row"] in {r.name for r in flow.materials} for row in data["summary"])
        checks.append("read_only_exact_analysis_sources_units_and_budget")
        assert query({**filters, "keyword": raw.name})["materials"]
        assert not query({**filters, "keyword": "JF-OM-NO-MATCH-" + suffix})["materials"]
        assert not query({**filters, "from_date": "2000-01-01", "to_date": "2000-01-01"})["materials"]
        assert query({**filters, "from_date": "2000-01-01", "to_date": "2000-01-01", "specified_date": 0})["materials"]
        _invalid(lambda: query({**filters, "from_date": "2100-01-01"}))
        checks.append("keyword_date_specified_date_and_invalid_range")
        initial = detail(flow.name, company)
        assert initial["materials"][0]["required"] == 15
        assert initial["materials"][0]["issued"] == 0
        checks.append("real_item_uom_conversion_and_remaining")
        _invalid(lambda: create_issue(flow.name, company, "stale", warehouses[0].name))
        _invalid(lambda: create_issue(flow.name, company, str(flow.modified), foreign_warehouse.name))
        checks.append("stale_snapshot_and_foreign_warehouse_denied")
        before = _ledger_snapshot()
        saved = create_issue(flow.name, company, str(flow.modified), warehouses[0].name)
        draft = frappe.get_doc("Stock Entry", saved["name"])
        assert draft.docstatus == 0 and draft.items[0].transfer_qty == 15
        assert _ledger_snapshot() == before
        assert create_issue(flow.name, company, str(flow.modified), warehouses[0].name)["name"] == draft.name
        _invalid(lambda: create_issue(flow.name, company, str(flow.modified), warehouses[1].name))
        checks.append("native_draft_no_posting_reuse_and_warehouse_identity")
        draft.items[0].qty = 6
        draft.save()
        draft.submit()
        assert detail(flow.name, company)["materials"][0]["issued"] == 6
        second = frappe.get_doc("Stock Entry", create_issue(flow.name, company, str(flow.modified), warehouses[0].name)["name"])
        assert second.items[0].transfer_qty == 9
        second.items[0].qty = 10
        _invalid(second.save)
        second.reload()
        second.submit()
        assert detail(flow.name, company)["materials"][0]["remaining"] == 0
        _invalid(lambda: create_issue(flow.name, company, str(flow.modified), warehouses[0].name))
        checks.append("two_partial_native_submits_cap_and_no_extra_issue")
        second.cancel()
        assert detail(flow.name, company)["materials"][0]["remaining"] == 9
        third = frappe.get_doc("Stock Entry", create_issue(flow.name, company, str(flow.modified), warehouses[0].name)["name"])
        third.items[0].jf_flow_order = ""
        _invalid(third.save)
        third.reload()
        checks.append("native_cancel_restores_remaining_and_foreign_detail_denied")
        third.jf_material_issue_flow = ""
        _invalid(third.save)
        third.reload()
        third.jf_material_issue_flow = flow.name + "-changed"
        _invalid(third.save)
        third.reload()
        manual = frappe.get_doc({"doctype": "Stock Entry", "company": company,
            "stock_entry_type": "Material Issue", "items": [{"item_code": raw.name,
                "qty": 1, "s_warehouse": warehouses[0].name, "jf_flow_order": flow.name}]}).insert()
        manual.jf_material_issue_flow = flow.name
        _invalid(manual.save)
        checks.append("persisted_issue_header_cannot_be_cleared_replaced_or_rebound")
        flow.source_changed = 1
        flow.save()
        _invalid(lambda: create_issue(flow.name, company, str(flow.modified), warehouses[0].name))
        flow.source_changed = 0
        flow.save()
        checks.append("source_change_blocks_new_issue")
        # Real stock clerk roles and Company restrictions; no ignore_permissions.
        stock_user = frappe.get_doc({"doctype": "User", "email": f"jf-om-{suffix}@example.invalid",
            "first_name": "JF Order Materials", "enabled": 1, "send_welcome_email": 0,
            "roles": [{"role": "Stock Manager"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": stock_user.name,
                       "allow": "Company", "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(stock_user.name)
        assert len(query(filters)["materials"]) == 2
        _denied(lambda: detail(flow.name, company))
        checks.append("stock_manager_without_native_warehouse_read_is_denied")
        frappe.set_user("Administrator")
        stock_user.append("roles", {"role": "Stock User"})
        stock_user.save()
        frappe.set_user(stock_user.name)
        current = detail(flow.name, company)
        assert current["materials"][0]["remaining"] == 9
        assert create_issue(flow.name, company, str(flow.modified), warehouses[0].name)["name"] == third.name
        _denied(lambda: query({**filters, "company": foreign_company, "flow_order": ""}))
        checks.append("stock_manager_native_query_detail_draft_and_company_scope")
        frappe.set_user("Administrator")
        # A readable flow must not expose a hidden linked voucher as zero issued.
        frappe.get_doc({"doctype": "User Permission", "user": stock_user.name, "allow": "Stock Entry",
                       "for_value": receipt.name, "apply_to_all_doctypes": 0, "applicable_for": "Stock Entry"}).insert()
        frappe.set_user(stock_user.name)
        assert query(filters)["materials"]  # Analysis does not depend on stock voucher visibility.
        for denied_call in (
            lambda: detail(flow.name, company),
            lambda: create_issue(flow.name, company, str(flow.modified), warehouses[0].name),
        ):
            try:
                denied_call()
            except frappe.PermissionError as error:
                assert all(name not in str(error) for name in (draft.name, second.name, third.name))
            else:
                raise AssertionError("Hidden linked vouchers must deny the complete balance")
        checks.append("hidden_voucher_blocks_complete_balance_and_creation")
        frappe.set_user("Administrator")
        manufacturing_user = frappe.get_doc({"doctype": "User", "email": f"jf-om-read-{suffix}@example.invalid",
            "first_name": "JF Read Materials", "enabled": 1, "send_welcome_email": 0,
            "roles": [{"role": "Manufacturing User"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": manufacturing_user.name,
                       "allow": "Company", "for_value": company, "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(manufacturing_user.name)
        _denied(lambda: create_issue(flow.name, company, str(flow.modified), warehouses[0].name))
        checks.append("manufacturing_read_only_cannot_create_stock_draft")
        frappe.set_user("Administrator")
        # These rows are physically deleted by the empty-analysis save. Rebuild
        # native new children; stale deleted names would silently DB-update
        # absent rows instead of reinserting them.
        original_materials = [{key: row.get(key) for key in (
            "item_code", "item_name", "uom", "qty_per_unit", "unit_price", "supplier", "note",
        )} for row in flow.materials]
        flow.set("materials", [])
        flow.save()
        historical = detail(flow.name, company)["materials"][0]
        assert historical["item_code"] == raw.name and historical["required"] == 0
        assert historical["issued"] == 6 and historical["remaining"] == -6
        _invalid(lambda: create_issue(flow.name, company, str(flow.modified), warehouses[0].name))
        flow.set("materials", original_materials)
        flow.save()
        checks.append("removed_analysis_material_preserves_posted_issue_visibility")
        draft.reload()
        draft.cancel()
        # Cancelled vouchers stay visible, and their quantities are excluded.
        state = detail(flow.name, company)
        assert state["materials"][0]["issued"] == 0 and state["materials"][0]["remaining"] == 15
        quantity = sum(Decimal(str(row.actual_qty)) for row in frappe.get_all("Stock Ledger Entry",
                       filters={"item_code": raw.name, "warehouse": warehouses[0].name}, fields=["actual_qty"]))
        assert quantity == 30
        checks.append("native_cancel_ledger_net_matches_receipt")
        return {"ok": True, "checks": checks, "browser_verified": False, "business_data": "rolled_back"}
    finally:
        frappe.set_user(original_user)
        frappe.in_test = original_test
        _end_rollback_only(point)
