"""One rollback-only native experiment for net sales item ranking.

After migration, invoke ``smoke`` exactly once from an initialized direct
bench-env Python process, in an exclusive business-write window. Do not use
``bench execute``: its exception fallback can invoke a failing function twice.
Use an existing synthetic company with leaf accounts/cost centre, item group,
and two different existing stock UOMs. No commit, schema/DocPerm changes,
permission bypass, external files or persistent fixture is requested.
Expected native Report PermissionError logs are retained only after exact ID,
title, exception type, content hash and actual MyISAM engine verification;
the native logger is neither intercepted nor its audit rows deleted.

The experiment uses actual submitted invoices, their stored discounted child
values, a native signed credit, and an explicit small native POS closing. It
does not claim native creation of one Item with conflicting stock UOMs, or
coverage of the background POS closing queue for ten or more invoices.
"""

from collections import Counter
from decimal import Decimal
import hashlib
import json
import re

import frappe
from frappe.utils import add_days, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.sales_validation import _closing, _invoice, _masters, _mode, _opening, _return


REPORT = "Jifeng Sales Item Ranking"
SI = "Sales Invoice"
TRACKED_TYPES = (
    "Item", "Item Default", "Item Price", "UOM Conversion Detail", "Warehouse", "Bin", "Customer",
    "Customer Credit Limit", "Contact", "Dynamic Link", "Mode of Payment", "Mode of Payment Account",
    "Price List", "User", "Has Role", "User Permission", "DocShare", "POS Profile", "POS Payment Method",
    "POS Profile User", "Stock Entry", "Stock Entry Detail", SI, "Sales Invoice Item",
    "Sales Invoice Payment", "Sales Taxes and Charges", "POS Invoice", "POS Invoice Item",
    "POS Invoice Payment", "POS Opening Entry", "POS Opening Entry Detail", "POS Closing Entry",
    "POS Closing Entry Detail", "POS Invoice Reference", "Sales Invoice Reference",
    "POS Invoice Merge Log", "POS Invoice Merge Log Item", "GL Entry", "Payment Ledger Entry",
    "Stock Ledger Entry", "Repost Item Valuation", "Repost Item Valuation Item",
    "Version", "File", "Communication", "Notification Log", "Email Queue", "Error Log", "Activity Log",
    "Access Log", "User Permission Log",
)


def _table_counts():
    # Include auxiliary/child tables unknown to this ERPNext version. Counts are
    # read only; table identifiers come directly from this database, not filters.
    tables = sorted(row[0] for row in frappe.db.sql("SHOW TABLES") if row[0].startswith("tab"))
    return {table: frappe.db.sql("SELECT COUNT(*) FROM `" + table.replace("`", "``") + "`")[0][0]
            for table in tables}


def _identities():
    return {doctype: set(frappe.get_all(doctype, pluck="name", limit_page_length=0))
            for doctype in TRACKED_TYPES if frappe.db.table_exists(doctype)}


def _error_logs():
    # SQL reads avoid ErrorLog.onload, which marks an audit row seen. Raw traces
    # stay inside this verifier and are never included in returned evidence.
    return {row["name"]: {"method": row["method"], "trace": row["error"],
                         "hash": hashlib.sha256(json.dumps(row, default=str, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()}
            for row in frappe.db.sql("SELECT * FROM `tabError Log` ORDER BY name", as_dict=True)}


def _native_error_engine():
    declared = frappe.get_meta("Error Log").engine
    actual = frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES "
                          "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", ("tabError Log",))
    assert declared == "MyISAM" and actual and actual[0][0] == "MyISAM", "原生 Error Log 引擎已变更，需重新核对审计规则"
    return actual[0][0]


def _number(value):
    assert value not in (None, ""), "原生凭证明细缺少已持久化数值"
    value = Decimal(str(value))
    assert value.is_finite()
    return value


def _oracle(result, documents, item_code=None):
    """Independent source oracle: only persisted invoice children are numeric inputs."""
    expected, sources = {}, {}
    for document in documents:
        assert document.docstatus == 1
        for child in document.items:
            if item_code and child.item_code != item_code:
                continue
            assert (child.parent, child.parenttype, child.parentfield) == (document.name, SI, "items")
            identity = (document.name, child.name)
            assert identity not in sources
            sources[identity] = (document, child)
            key = (child.item_code, child.stock_uom)
            summary = expected.setdefault(key, {"quantity": Decimal(0), "amount": Decimal(0), "count": 0})
            summary["quantity"] += _number(child.stock_qty)
            summary["amount"] += _number(child.base_net_amount)
            summary["count"] += 1
    ranked = sorted(expected, key=lambda key: (-expected[key]["amount"], *key))
    headers, actual_sources, current = {}, {}, None
    for row in result["result"]:
        assert isinstance(row, dict)
        key = (row["item_code"], row["stock_uom"])
        if row["is_group"]:
            assert row["indent"] == 0 and key not in headers and key in expected
            assert row["rank"] == ranked.index(key) + 1
            summary = expected[key]
            assert (_number(row["stock_qty"]), _number(row["base_net_amount"]), row["source_count"]) == (
                summary["quantity"], summary["amount"], summary["count"])
            headers[key], current = row, key
        else:
            identity = (row["document"], row["invoice_item"])
            assert row["indent"] == 1 and key == current and identity not in actual_sources
            document, child = sources[identity]
            assert row["item_idx"] == child.idx
            assert str(row["posting_date"]) == str(document.posting_date)
            assert row["source_item_name"] == child.item_name
            assert (_number(row["source_stock_qty"]), _number(row["source_base_net_amount"])) == (
                _number(child.stock_qty), _number(child.base_net_amount))
            assert row["updates_stock"] == int(document.update_stock)
            assert row["entry_type"] == ("退货 / 贷项" if document.is_return else "销售")
            assert not {"stock_qty", "base_net_amount", "source_count", "rank"}.intersection(row)
            actual_sources[identity] = row
    assert list(headers) == ranked and set(actual_sources) == set(sources)
    assert sum(row["source_count"] for row in headers.values()) == len(sources)
    for key, header in headers.items():
        children = [row for row in actual_sources.values() if (row["item_code"], row["stock_uom"]) == key]
        assert sum((_number(row["source_stock_qty"]) for row in children), Decimal(0)) == _number(header["stock_qty"])
        assert sum((_number(row["source_base_net_amount"]) for row in children), Decimal(0)) == _number(header["base_net_amount"])
    assert not {"gross_payment_amount", "paid_amount", "profit", "rate"}.intersection(
        column["fieldname"] for column in result["columns"])
    return headers, actual_sources


def smoke(company, item_group, stock_uom, second_stock_uom="Kg"):
    """Default is always rollback; failures propagate after object/ledger audits."""
    from frappe.desk.query_report import run

    frappe.only_for("System Manager")
    for doctype, name in (("Company", company), ("Item Group", item_group),
                          ("UOM", stock_uom), ("UOM", second_stock_uom)):
        if not frappe.db.exists(doctype, name):
            frappe.throw("请提供既有验收公司、明细商品分组和两种库存单位")
    other_company = frappe.db.get_value("Company", {"name": ["!=", company]}, "name")
    if not other_company or stock_uom == second_stock_uom or frappe.db.get_value("Item Group", item_group, "is_group"):
        frappe.throw("验收需要另一既有公司、明细商品分组和两个不同库存单位")
    definition = frappe.get_doc("Report", REPORT)
    assert not definition.disabled and not definition.prepared_report and definition.ref_doctype == SI
    assert {row.role for row in definition.roles} == {"Accounts User", "Accounts Manager"}
    before, counts, identities = _ledger_snapshot(), _table_counts(), _identities()
    error_engine, initial_logs = _native_error_engine(), _error_logs()
    settings = {doctype: frappe.get_single(doctype).as_dict()
                for doctype in ("Selling Settings", "POS Settings")}
    original_user, original_test = frappe.session.user, frappe.in_test
    point = "jf_item_ranking_" + frappe.generate_hash(length=10)
    objects, records, users, checks, evidence = [], set(), [], [], {}
    retained_native_audit = {}
    current_gate = "begin"
    _begin_rollback_only(point)

    def gate(label):
        nonlocal current_gate
        current_gate = label

    def passed():
        checks.append(current_gate)

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **values):
        return remember(frappe.get_doc({"doctype": doctype, **values}).insert())

    def report(**filters):
        ledger = _ledger_snapshot()
        try:
            result = run(REPORT, filters={"company": company, "from_date": today(), "to_date": today(), **filters},
                         ignore_prepared_report=True, is_tree=True)
            assert not result.get("prepared_report")
            return result
        finally:
            assert _ledger_snapshot() == ledger, "原生 Report API 改变 whole GL/SLE 内容"

    def denied(operation):
        nested = "jf_ranking_deny_" + frappe.generate_hash(length=8)
        ledger = _ledger_snapshot()
        prior_logs = _error_logs()
        frappe.db.savepoint(nested)
        try:
            try:
                operation()
            except frappe.PermissionError as exception:
                current_logs = _error_logs()
                assert set(prior_logs) <= set(current_logs), "原生权限拒绝删除了既有 Error Log"
                assert all(current_logs[name]["hash"] == row["hash"] for name, row in prior_logs.items()), "原生权限拒绝修改了既有 Error Log"
                added = set(current_logs) - set(prior_logs)
                assert len(added) <= 1, "一次预期报告权限拒绝产生了多个原生日志，需核对实际来源"
                exception_type = type(exception).__module__ + "." + type(exception).__name__
                assert exception_type == "frappe.exceptions.PermissionError"
                for name in added:
                    row = current_logs[name]
                    assert row["method"] == "Report execution failed for: " + REPORT, "预期权限拒绝产生了其他标题的日志"
                    assert re.search(r"(?m)^" + re.escape(exception_type) + r"(?::|$)", row["trace"] or ""), "预期权限拒绝日志缺少原生 PermissionError"
                    assert name not in retained_native_audit
                    retained_native_audit[name] = {"name": name, "method": row["method"],
                        "exception_type": exception_type, "engine": error_engine, "content_hash": row["hash"]}
                evidence.setdefault("native_denial_audits", []).append({"gate": current_gate,
                    "new_error_log_ids": sorted(added), "exception_type": exception_type})
            else:
                raise AssertionError("验收预期原生权限拒绝，但查询成功")
        finally:
            frappe.db.rollback(save_point=nested)
        assert _ledger_snapshot() == ledger

    def allow(user, doctype, names):
        frappe.set_user("Administrator")
        permissions = [insert("User Permission", user=user, allow=doctype, for_value=name,
                              apply_to_all_doctypes=1) for name in names]
        frappe.clear_cache(user=user)
        return permissions

    def invoice(lines, *, discount=0, submit=True, posting_date=None):
        document = remember(frappe.get_doc({"doctype": SI, "company": company,
            "customer": fixture.customer.name, "debit_to": fixture.accounts.receivable,
            "currency": fixture.currency, "conversion_rate": 1,
            "selling_price_list": fixture.price_list.name, "price_list_currency": fixture.currency,
            "plc_conversion_rate": 1, "posting_date": posting_date or today(),
            "posting_time": "08:30:00", "set_posting_time": 1, "update_stock": 0,
            "ignore_pricing_rule": 1, "disable_rounded_total": 1, "apply_discount_on": "Net Total",
            "additional_discount_percentage": discount,
            "items": [{"item_code": item.name, "qty": qty, "price_list_rate": rate, "rate": rate,
                "discount_percentage": line_discount, "warehouse": fixture.warehouse.name,
                "income_account": fixture.accounts.income, "expense_account": fixture.accounts.expense,
                "cost_center": fixture.cost_center} for item, qty, rate, line_discount in lines]}))
        document.insert()
        if submit:
            document.submit()
        document.reload()
        return document

    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        gate("native_two_items_distinct_stock_uoms_no_stock_invoice_discounted_persisted_sources")
        fixture = _masters(company, item_group, stock_uom)
        for value in fixture.values():
            if hasattr(value, "doctype") and value.doctype:
                remember(value)
            elif isinstance(value, list):
                for member in value:
                    if hasattr(member, "doctype") and member.doctype:
                        remember(member)
        second_item = insert("Item", item_code="JF-RANKING-KG-" + fixture.suffix,
            item_group=item_group, stock_uom=second_stock_uom, is_stock_item=1, valuation_method="Moving Average",
            item_defaults=[{"company": company, "default_warehouse": fixture.warehouse.name,
                "income_account": fixture.accounts.income, "expense_account": fixture.accounts.expense,
                "selling_cost_center": fixture.cost_center}])
        quantity_b = 1 if frappe.db.get_value("UOM", second_stock_uom, "must_be_whole_number") else 1.5
        ordinary = invoice([(fixture.item, 2, 12.34, 5), (fixture.item, 1, 7.25, 20),
                            (second_item, quantity_b, 20, 0)], discount=10)
        additional = invoice([(fixture.item, 3, 9.5, 0)], discount=5)
        assert ordinary.additional_discount_percentage and ordinary.discount_amount > 0
        assert ordinary.items[0].discount_percentage and ordinary.items[1].discount_percentage
        assert any(_number(row.base_net_amount) != _number(row.qty) * _number(row.price_list_rate)
                   for row in ordinary.items)
        assert {row.stock_uom for row in ordinary.items} == {stock_uom, second_stock_uom}
        assert not frappe.get_all("Stock Ledger Entry", filters={"voucher_type": SI,
            "voucher_no": ["in", [ordinary.name, additional.name]]})
        active = [ordinary, additional]
        _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
        passed()

        gate("native_credit_signed_stored_quantity_and_discounted_amount_exact_child")
        returned = remember(_return(ordinary, qty=1, index=0))
        assert returned.is_return and returned.return_against == ordinary.name and not returned.update_stock
        assert returned.items[0].sales_invoice_item == ordinary.items[0].name
        assert returned.items[0].stock_qty < 0 and returned.items[0].base_net_amount < 0
        active.append(returned)
        _, source_rows = _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
        assert (returned.name, returned.items[0].name) in source_rows
        passed()

        gate("native_draft_cancelled_and_outside_posting_date_excluded")
        draft = invoice([(fixture.item, 1, 99, 0)], submit=False)
        cancelled = invoice([(fixture.item, 1, 88, 0)])
        cancelled.cancel()
        outside = invoice([(fixture.item, 1, 77, 0)], posting_date=add_days(today(), -2))
        _, source_rows = _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
        assert not {draft.name, cancelled.name, outside.name}.intersection(row[0] for row in source_rows)
        passed()

        gate("native_unmerged_pos_excluded_then_consolidated_sales_invoice_counted_once")
        _mode("POS Invoice")
        frappe.set_user(fixture.cashier.name)
        opening = remember(_opening(fixture))
        pos_sale = remember(_invoice(fixture, "POS Invoice", qty=1, paid=20))
        assert not pos_sale.consolidated_invoice
        frappe.set_user("Administrator")
        _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
        frappe.set_user(fixture.cashier.name)
        closing = remember(_closing(opening))
        pos_sale.reload()
        merged = remember(frappe.get_doc(SI, pos_sale.consolidated_invoice))
        assert merged.docstatus == 1 and merged.is_consolidated and merged.update_stock
        assert {row.pos_invoice_item for row in merged.items} == {row.name for row in pos_sale.items}
        active.append(merged)
        second_opening = remember(_opening(fixture))
        pending = remember(_invoice(fixture, "POS Invoice", qty=2, paid=30, posting_time="16:00:00"))
        assert second_opening.docstatus == 1 and not pending.consolidated_invoice
        frappe.set_user("Administrator")
        _, source_rows = _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
        assert sum(key[0] == merged.name for key in source_rows) == len(merged.items)
        assert not {pos_sale.name, pending.name}.intersection(key[0] for key in source_rows)
        evidence["pos"] = {"source": pos_sale.name, "consolidated": merged.name,
                           "unmerged_excluded": pending.name, "closing": closing.name}
        passed()

        gate("native_accounts_user_and_manager_with_actual_item_read_query_report_tree")
        actors, permissions = {}, {}
        for role in ("Accounts User", "Accounts Manager"):
            user = insert("User", email=f"jf-ranking-{role.lower().replace(' ', '-')}-{fixture.suffix}@example.invalid",
                first_name="商品排行原生验收", send_welcome_email=0,
                roles=[{"role": role}, {"role": "Purchase User"}])
            users.append(user.name)
            actors[role] = user.name
            allow(user.name, "Company", [company])
            allow(user.name, SI, [doc.name for doc in active])
            permissions[role] = allow(user.name, "Item", [fixture.item.name, second_item.name])
            frappe.set_user(user.name)
            assert frappe.has_permission(SI, "read") and frappe.has_permission(SI, "report")
            assert frappe.has_permission("Item", "read")
            visible = set(frappe.get_list(SI, filters={"company": company, "docstatus": 1,
                "posting_date": today()}, pluck="name", limit_page_length=0))
            assert visible == {doc.name for doc in active}, "原生凭证范围未收敛至验收来源"
            headers, source_rows = _oracle(report(), active)
            assert set(headers) == {(fixture.item.name, stock_uom), (second_item.name, second_stock_uom)}
            assert all(row["company_currency"] == fixture.currency for row in headers.values())
            _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
            _oracle(report(item_code=second_item.name), active, second_item.name)
            evidence.setdefault("native_roles", {})[role] = {"roles": sorted(frappe.get_roles()),
                "invoice_count": len({key[0] for key in source_rows}), "exact_child_count": len(source_rows)}
            frappe.set_user("Administrator")
        passed()

        gate("native_company_user_permission_denies_other_company_report")
        for user in actors.values():
            frappe.set_user(user)
            assert not frappe.get_list("Company", filters={"name": other_company}, pluck="name", limit_page_length=1)
            denied(lambda: report(company=other_company))
        passed()

        gate("native_shared_readable_invoice_hidden_item_rejects_partial_ranking")
        for role, user in actors.items():
            frappe.set_user("Administrator")
            for document in active:
                share = frappe.share.add(SI, document.name, user=user, read=1, notify=0)
                if share:
                    remember(share)
            hidden = next(doc for doc in permissions[role] if doc.for_value == second_item.name)
            hidden.delete()
            frappe.clear_cache(user=user)
            frappe.set_user(user)
            assert frappe.get_doc(SI, ordinary.name).has_permission("read")
            assert ordinary.name in frappe.get_list(SI, filters={"name": ordinary.name}, pluck="name")
            assert not frappe.get_doc("Item", second_item.name).has_permission("read")
            denied(lambda: report())
            denied(lambda: report(item_code=second_item.name))
            _oracle(report(item_code=fixture.item.name), active, fixture.item.name)
            frappe.set_user("Administrator")
            allow(user, "Item", [second_item.name])
        passed()

        gate("native_accounts_manager_without_item_read_and_guest_rejected")
        user = insert("User", email=f"jf-ranking-noitem-{fixture.suffix}@example.invalid",
            first_name="商品引用读取权限验收", send_welcome_email=0, roles=[{"role": "Accounts Manager"}])
        users.append(user.name)
        allow(user.name, "Company", [company])
        allow(user.name, SI, [ordinary.name])
        frappe.set_user(user.name)
        assert frappe.has_permission(SI, "read") and frappe.has_permission(SI, "report")
        assert frappe.get_doc(SI, ordinary.name).has_permission("read")
        assert not frappe.has_permission("Item", "read"), "原生角色权限变更：需重新界定此门禁"
        denied(lambda: report())
        frappe.set_user("Guest")
        denied(lambda: report())
        passed()
    except Exception as exception:
        exception.add_note("sales item ranking smoke failed at gate: " + current_gate)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((child.doctype, child.name) for child in document.get_all_children() if child.name)
            for doctype, current in _identities().items():
                added = current - identities.get(doctype, set())
                if doctype == "Error Log":
                    assert added == set(retained_native_audit), "验收产生了未验证或非预期失败的原生 Error Log"
                else:
                    records.update((doctype, name) for name in added)
        finally:
            try:
                _end_rollback_only(point)
            finally:
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
                for doctype in settings:
                    frappe.clear_document_cache(doctype, doctype)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records), "回滚后残留验收主档、凭证或辅助对象"
            current_logs = _error_logs()
            assert set(current_logs) == set(initial_logs) | set(retained_native_audit), "回滚后 Error Log 存在未验证差异"
            assert all(current_logs[name]["hash"] == row["hash"] for name, row in initial_logs.items()), "回滚后既有 Error Log 内容有差异"
            assert all(current_logs[name]["hash"] == row["content_hash"] for name, row in retained_native_audit.items()), "回滚后已验证原生权限日志内容有差异"
            expected_counts = {**counts, "tabError Log": counts["tabError Log"] + len(retained_native_audit)}
            assert _table_counts() == expected_counts, "回滚后全站业务及辅助表记录数存在未验证差异"
            assert _ledger_snapshot() == before, "回滚后 whole GL/SLE 内容有差异"
            assert all(frappe.get_single(doctype).as_dict() == original for doctype, original in settings.items()), "回滚后收银或销售设置有差异"
    checks.extend(("every_report_and_permission_denial_preserves_whole_gl_sle_hashes",
                   "rollback_all_tracked_masters_vouchers_children_permissions_and_auxiliary_objects",
                   "rollback_all_native_table_counts_except_exact_verified_myisam_permission_logs",
                   "original_settings_whole_ledger_and_preexisting_error_log_hashes_unchanged"))
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "record_counts": dict(Counter(doctype for doctype, _ in records)), "evidence": evidence,
            "retained_native_audit": list(retained_native_audit.values()),
            "whole_ledger_hashes_before": before, "whole_ledger_hashes_after": _ledger_snapshot(),
            "table_counts_before": counts, "table_counts_after": _table_counts(),
            "native_limits": ["two distinct Items retain their own stock UOMs; conflicting UOMs of one Item not constructed",
                              "explicit small POS closing; background closing queue for ten or more invoices not tested"],
            "execution": "initialized direct Python, one invocation, serial rollback experiment"}
