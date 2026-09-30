"""Journaled native sales fixtures for an isolated browser acceptance site.

Prepare and cleanup commit explicitly. Verify reads only. Cancellation preserves
native immutable accounting history; QA masters are disabled, never deleted.
"""

import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import frappe
from frappe.utils import add_days, cint, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot, _read
from jifeng_erp.sales_validation import _cancel_closing, _closing, _invoice, _mode, _money, _opening, _return


KINDS = {
    "items": "Item", "customers": "Customer", "payments": "Mode of Payment",
    "price_lists": "Price List", "users": "User", "profiles": "POS Profile",
    "contacts": "Contact", "user_shares": "DocShare",
    "invoices": "Sales Invoice", "pos_invoices": "POS Invoice",
    "openings": "POS Opening Entry", "merge_logs": "POS Invoice Merge Log",
    "closings": "POS Closing Entry",
}
BUSINESS_TYPES = {"Sales Invoice", "POS Invoice", "POS Opening Entry", "POS Closing Entry", "POS Invoice Merge Log"}
DISABLE_FIELDS = {
    "Item": ("disabled", 1), "Customer": ("disabled", 1), "POS Profile": ("disabled", 1),
    "User": ("enabled", 0), "Price List": ("enabled", 0), "Mode of Payment": ("enabled", 0),
}
SETTINGS_FIELDS = {"POS Settings": ("invoice_type", "post_change_gl_entries"), "Selling Settings": ("so_required", "dn_required")}
INVOICE_FIELDS = (
    "company", "customer", "debit_to", "currency", "conversion_rate", "selling_price_list",
    "price_list_currency", "plc_conversion_rate", "posting_date", "posting_time", "update_stock",
    "disable_rounded_total", "is_pos", "is_created_using_pos", "is_consolidated", "pos_profile",
    "is_return", "return_against", "net_total", "base_net_total", "grand_total", "base_grand_total",
    "paid_amount", "base_paid_amount", "change_amount", "base_change_amount", "write_off_amount",
    "base_write_off_amount", "account_for_change_amount", "ignore_pricing_rule",
)
PARENT_FIELDS = {
    "Item": ("item_code", "item_name", "item_group", "stock_uom", "is_stock_item", "is_sales_item"),
    "Customer": ("customer_name", "customer_type", "customer_group", "territory"),
    "Mode of Payment": ("mode_of_payment", "type"),
    "Price List": ("price_list_name", "currency", "selling", "buying"),
    # A User identity never includes passwords, API keys, tokens or sessions.
    "User": ("email", "first_name", "user_type", "send_welcome_email", "role_profile_name"),
    "Contact": ("first_name", "last_name", "user", "email_id", "company_name"),
    "DocShare": ("user", "share_doctype", "share_name", "read", "write", "share", "submit", "everyone", "notify_by_email"),
    "POS Profile": ("company", "customer", "customer_group", "territory", "currency", "selling_price_list",
        "warehouse", "income_account", "expense_account", "cost_center", "write_off_account",
        "write_off_cost_center", "account_for_change_amount", "update_stock", "ignore_pricing_rule",
        "disable_rounded_total", "taxes_and_charges", "tax_category"),
    "Sales Invoice": INVOICE_FIELDS,
    "POS Invoice": INVOICE_FIELDS,
    "POS Opening Entry": ("company", "pos_profile", "user", "period_start_date", "posting_date"),
    "POS Closing Entry": ("company", "pos_profile", "user", "pos_opening_entry", "invoice_type",
        "period_start_date", "period_end_date", "posting_date", "posting_time", "grand_total", "net_total", "total_quantity"),
    "POS Invoice Merge Log": ("company", "customer", "customer_group", "merge_invoices_based_on",
        "pos_closing_entry", "posting_date", "posting_time", "consolidated_invoice", "consolidated_credit_note"),
}
INVOICE_TABLES = {
    "items": ("item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty", "rate",
        "amount", "base_amount", "net_rate", "net_amount", "base_net_amount", "warehouse",
        "income_account", "expense_account", "cost_center", "sales_invoice_item", "pos_invoice", "pos_invoice_item"),
    "payments": ("mode_of_payment", "account", "amount", "base_amount"),
    "taxes": ("charge_type", "account_head", "rate", "tax_amount", "base_tax_amount", "total", "base_total"),
}
CHILD_FIELDS = {
    "Item": {"uoms": ("uom", "conversion_factor"), "item_defaults": ("company", "default_warehouse",
        "income_account", "expense_account", "selling_cost_center")},
    "Mode of Payment": {"accounts": ("company", "default_account")},
    "User": {"roles": ("role",)},
    "Contact": {"email_ids": ("email_id", "is_primary"), "phone_nos": ("phone", "is_primary_phone", "is_primary_mobile_no"),
                "links": ("link_doctype", "link_name")},
    "POS Profile": {"payments": ("mode_of_payment", "default"), "applicable_for_users": ("user", "default")},
    "Sales Invoice": INVOICE_TABLES,
    "POS Invoice": INVOICE_TABLES,
    "POS Opening Entry": {"balance_details": ("mode_of_payment", "opening_amount")},
    "POS Closing Entry": {
        "pos_invoices": ("pos_invoice", "posting_date", "customer", "is_return", "return_against", "grand_total"),
        "sales_invoices": ("sales_invoice", "posting_date", "customer", "is_return", "return_against", "grand_total"),
        "payment_reconciliation": ("mode_of_payment", "opening_amount", "expected_amount", "closing_amount", "difference"),
    },
    "POS Invoice Merge Log": {"pos_invoices": ("pos_invoice", "posting_date", "customer", "is_return", "return_against", "grand_total")},
}


def _path(data):
    match = re.fullmatch(r"JF-SALES-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "sales_browser" or data.get("fixture_version") != 1:
        raise ValueError("不是受支持的销售浏览器验收夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("sales-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("销售验收日志路径与精确夹具身份不一致")
    return path


def _journal(data):
    path = _path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["artifact_path"], data["updated_at"] = str(path), now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _load(data):
    if isinstance(data, str):
        data = json.loads(data)
    canonical = json.loads(_path(data).read_text(encoding="utf-8"))
    for field in ("prefix", "company", "fixture_type", "fixture_version", *KINDS):
        if data.get(field) != canonical.get(field):
            raise ValueError("调用参数与私有日志中的精确销售来源集合不一致")
    return canonical


def _snapshot(doc):
    result = {field: doc.get(field) for field in PARENT_FIELDS[doc.doctype]}
    for table, fields in CHILD_FIELDS.get(doc.doctype, {}).items():
        result[table] = [{"name": row.name, "parent": row.parent, "parenttype": row.parenttype,
                          **{field: row.get(field) for field in fields}} for row in doc.get(table) or []]
    return json.loads(json.dumps(result, default=str))


def _track(data, doc):
    doc.reload()
    kind = next(kind for kind, doctype in KINDS.items() if doctype == doc.doctype)
    assert doc.name not in data[kind], "销售验收来源重复"
    record = {"doctype": doc.doctype, "name": doc.name, "creation": str(doc.creation),
              "owner": doc.owner, "docstatus": cint(doc.docstatus), "identity": _snapshot(doc)}
    if doc.doctype in DISABLE_FIELDS:
        field, _ = DISABLE_FIELDS[doc.doctype]
        record["active_value"] = cint(doc.get(field))
    data[kind].append(doc.name)
    data["records"].append(record)
    _journal(data)
    return doc


def _begin(point):
    if getattr(frappe.db, "_disable_transaction_control", 0):
        frappe.throw("持久销售夹具不能嵌入禁止提交的诊断事务")
    _begin_rollback_only(point)


def _commit(data, phase):
    data["phase"] = phase + "_commit_pending"
    _journal(data)
    assert frappe.db._disable_transaction_control == 1, "销售验收提交屏障状态不一致"
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = phase + "_commit_unknown"
        _journal(data)
        exc.add_note("提交结果未知；先调用 verify 核对私有日志和原生状态，不要重复 prepare: " + data["artifact_path"])
        raise


def _settings():
    return {doctype: {field: frappe.db.get_single_value(doctype, field) for field in fields}
            for doctype, fields in SETTINGS_FIELDS.items()}


def _restore_settings(data):
    for doctype, fields in data["original_settings"].items():
        for field, value in fields.items():
            frappe.db.set_single_value(doctype, field, value)
        frappe.clear_document_cache(doctype, doctype)
    assert _settings() == data["original_settings"], "销售验收全局设置恢复未确认"
    data["settings_restored"] = True


def _masters(data):
    company = frappe.get_doc("Company", data["company"])
    accounts = {}
    for key, field, query in (
        ("income", "default_income_account", {"root_type": "Income"}),
        ("expense", "default_expense_account", {"account_type": "Cost of Goods Sold"}),
        ("cash", "default_cash_account", {"account_type": "Cash"}),
        ("receivable", "default_receivable_account", {"account_type": "Receivable"}),
    ):
        accounts[key] = company.get(field) or frappe.db.get_value(
            "Account", {"company": company.name, "is_group": 0, **query}, "name")
    cost_center = company.cost_center or frappe.db.get_value("Cost Center", {"company": company.name, "is_group": 0}, "name")
    warehouse = frappe.db.get_value("Warehouse", {"company": company.name, "is_group": 0, "disabled": 0}, "name")
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if not all([*accounts.values(), cost_center, warehouse, customer_group, territory, company.default_currency]):
        frappe.throw("销售验收需要公司本币、收入/成本/现金/应收明细科目、成本中心、叶仓及客户分组/地区")
    suffix = data["prefix"].rsplit("-", 1)[-1]

    def insert(doctype, **fields):
        return _track(data, frappe.get_doc({"doctype": doctype, **fields}).insert())

    item = insert("Item", item_code="JF-SALES-BROWSER-ITEM-" + suffix, item_group=data["item_group"],
        stock_uom=data["stock_uom"], is_stock_item=0, is_sales_item=1,
        item_defaults=[{"company": company.name, "default_warehouse": warehouse,
            "income_account": accounts["income"], "expense_account": accounts["expense"], "selling_cost_center": cost_center}])
    customer = insert("Customer", customer_name="JF-SALES-BROWSER-CUSTOMER-" + suffix,
        customer_type="Company", customer_group=customer_group, territory=territory)
    payment = insert("Mode of Payment", mode_of_payment="JF-SALES-BROWSER-CASH-" + suffix,
        type="Cash", enabled=1, accounts=[{"company": company.name, "default_account": accounts["cash"]}])
    price_list = insert("Price List", price_list_name="JF-SALES-BROWSER-PRICE-" + suffix,
        currency=company.default_currency, selling=1, buying=0, enabled=1)
    roles = ("Sales Manager", "Accounts Manager", "Stock User", "Item Manager")
    assert all(frappe.db.exists("Role", role) for role in roles), "销售夹具只使用既有原生角色"
    cashier = insert("User", email=f"jf-sales-browser-{suffix}@example.invalid", first_name="销售浏览器验收",
        user_type="System User", enabled=1, send_welcome_email=0, roles=[{"role": role} for role in roles])
    contacts = frappe.get_all("Contact", filters={"user": cashier.name}, pluck="name", limit_page_length=0)
    shares = frappe.get_all("DocShare", filters={"user": cashier.name, "share_doctype": "User", "share_name": cashier.name},
                            pluck="name", limit_page_length=0)
    assert len(contacts) == len(shares) == 1, "QA 用户原生联系人/自共享支持记录不完整"
    for name in contacts:
        _track(data, frappe.get_doc("Contact", name))
    for name in shares:
        _track(data, frappe.get_doc("DocShare", name))
    profile = insert("POS Profile", name="JF-SALES-BROWSER-PROFILE-" + suffix,
        company=company.name, customer=customer.name, customer_group=customer_group, territory=territory,
        currency=company.default_currency, selling_price_list=price_list.name, warehouse=warehouse,
        income_account=accounts["income"], expense_account=accounts["expense"], cost_center=cost_center,
        write_off_account=accounts["expense"], write_off_cost_center=cost_center,
        account_for_change_amount=accounts["cash"], update_stock=0, ignore_pricing_rule=1,
        disable_rounded_total=1, payments=[{"mode_of_payment": payment.name, "default": 1}],
        applicable_for_users=[{"user": cashier.name, "default": 1}])
    data["shared_warehouse"] = warehouse
    return frappe._dict(company=company.name, currency=company.default_currency, item=item, customer=customer,
        payment=payment, price_list=price_list, cashier=cashier, profile=profile,
        warehouse=frappe._dict(name=warehouse), cost_center=cost_center, accounts=frappe._dict(accounts))


def _ledger(data):
    owned = {(record["doctype"], record["name"]) for record in data["records"] if record["doctype"] in BUSINESS_TYPES}
    names = sorted(name for _, name in owned)
    stock = frappe.get_all("Stock Ledger Entry", filters={"item_code": ["in", data["items"]]},
                           pluck="name", limit_page_length=0)
    assert not stock, "非库存销售夹具出现库存流水，拒绝自动撤销"
    voucher_stock = frappe.get_all("Stock Ledger Entry", filters={"voucher_no": ["in", names]},
                                  pluck="name", limit_page_length=0) if names else []
    assert not voucher_stock, "销售验收来源凭证出现库存流水"
    gl = frappe.get_all("GL Entry", filters={"voucher_no": ["in", names]}, fields=["name", "company", "account",
        "voucher_type", "voucher_no", "debit", "credit", "is_cancelled", "party_type", "party"],
        order_by="name", limit_page_length=0) if names else []
    assert all((row.voucher_type, row.voucher_no) in owned and row.company == data["company"] for row in gl), "销售验收总账来源与精确日志不一致"
    customer_gl = frappe.get_all("GL Entry", filters={"party_type": "Customer", "party": ["in", data["customers"]]},
                                pluck="name", limit_page_length=0)
    assert set(customer_gl) <= {row.name for row in gl}, "验收客户出现日志外总账，拒绝自动撤销"
    originals = {row["name"]: row for row in data.get("ledger_records", {}).get("gl", [])}
    current = {row.name: json.loads(json.dumps(dict(row), default=str)) for row in gl}
    assert set(originals) <= set(current), "销售验收原生总账历史被删除"
    for name, original in originals.items():
        assert all(current[name][field] == value for field, value in original.items() if field != "is_cancelled"), "销售验收原生总账历史内容发生变化"
    for name in set(current) - set(originals) if originals else []:
        row = current[name]
        assert frappe.db.get_value(row["voucher_type"], row["voucher_no"], "docstatus") == 2, "销售已提交来源出现未经追踪的额外总账"
    return {"stock": [], "gl": list(current.values())}


def _references(data):
    """Reject native Link/Dynamic Link uses outside exact owned parent records."""
    owned = {(record["doctype"], record["name"]) for record in data["records"]}
    targets = {doctype: data[kind] for kind, doctype in KINDS.items() if data[kind]}
    definitions = frappe.get_all("DocField", filters={"fieldtype": ["in", ["Link", "Dynamic Link"]]},
        fields=["parent as doctype", "fieldname", "fieldtype", "options"], limit_page_length=0)
    definitions += frappe.get_all("Custom Field", filters={"fieldtype": ["in", ["Link", "Dynamic Link"]]},
        fields=["dt as doctype", "fieldname", "fieldtype", "options"], limit_page_length=0)
    seen = set()
    for definition in definitions:
        key = (definition.doctype, definition.fieldname)
        if key in seen or (definition.fieldtype == "Link" and definition.options not in targets):
            continue
        seen.add(key)
        meta = frappe.get_meta(definition.doctype)
        if meta.is_virtual:
            continue  # No local persistent rows exist for a virtual DocType.
        field = meta.get_field(definition.fieldname)
        if not field or field.fieldtype != definition.fieldtype:
            continue
        for target, names in targets.items():
            if definition.fieldtype == "Link" and definition.options != target:
                continue
            if meta.issingle:
                if definition.fieldtype == "Dynamic Link" and frappe.db.get_single_value(meta.name, definition.options) != target:
                    continue
                assert frappe.db.get_single_value(meta.name, field.fieldname) not in names, "验收来源被日志外全局设置引用"
                continue
            filters = {field.fieldname: ["in", names]}
            if definition.fieldtype == "Dynamic Link":
                filters[definition.options] = target
            native_ledger = meta.name in ("GL Entry", "Payment Ledger Entry")
            fields = ["name", "parent", "parenttype"] if meta.istable else ["name"]
            if native_ledger:
                fields += ["company", "voucher_type", "voucher_no"]
            references = frappe.get_all(meta.name, filters=filters, fields=fields, limit_page_length=0)
            for row in references:
                if meta.name == "Comment" and field.fieldname == "reference_name":
                    continue  # Native submit/cancel comments retain their exact source history.
                if native_ledger:
                    assert (row.voucher_type, row.voucher_no) in owned and row.company == data["company"], "销售来源被日志外原生账簿引用"
                    continue
                parent = (row.parenttype, row.parent) if meta.istable else (meta.name, row.name)
                assert parent in owned, "销售验收来源被日志外单据引用，拒绝撤销或停用: " + meta.name


def _validate(data):
    expected = {(record["doctype"], record["name"]): record for record in data["records"]}
    assert len(expected) == len(data["records"]), "销售验收日志来源重复"
    assert set(expected) == {(doctype, name) for kind, doctype in KINDS.items() for name in data[kind]}, "销售日志精确来源集合不完整"
    docs = {}
    for key, record in expected.items():
        assert frappe.db.exists(*key), "销售验收来源被删除，拒绝自动撤销: " + key[1]
        doc = frappe.get_doc(*key)
        assert str(doc.creation) == record["creation"] and doc.owner == record["owner"] and _snapshot(doc) == record["identity"], "销售验收来源身份或内容变化，拒绝自动撤销: " + key[1]
        assert cint(doc.docstatus) in ({1, 2} if doc.doctype in BUSINESS_TYPES else {0}), "销售验收来源审核状态不受支持"
        if doc.meta.has_field("company"):
            assert doc.company == data["company"], "销售验收来源公司不一致"
        if doc.doctype in ("Sales Invoice", "POS Invoice"):
            assert not cint(doc.update_stock), "销售夹具禁止更新库存"
        docs[key] = doc
    suffix = data["prefix"].rsplit("-", 1)[-1]
    assert data["items"] == ["JF-SALES-BROWSER-ITEM-" + suffix]
    assert len(data["customers"]) == len(data["payments"]) == len(data["price_lists"]) == len(data["users"]) == len(data["profiles"]) == 1
    assert docs[("Customer", data["customers"][0])].customer_name == "JF-SALES-BROWSER-CUSTOMER-" + suffix
    assert data["users"] == [f"jf-sales-browser-{suffix}@example.invalid"]
    assert data["profiles"] == ["JF-SALES-BROWSER-PROFILE-" + suffix]
    assert data["payments"] == ["JF-SALES-BROWSER-CASH-" + suffix]
    assert data["price_lists"] == ["JF-SALES-BROWSER-PRICE-" + suffix]
    assert not cint(docs[("Item", data["items"][0])].is_stock_item)
    assert docs[("POS Profile", data["profiles"][0])].warehouse == data["shared_warehouse"]
    _references(data)
    _ledger(data)
    _links(data, docs)
    return docs


def _links(data, docs):
    """Allow only native cancellation's known unlinking of tracked provenance."""
    for opening, closing in data["opening_closing_links"].items():
        open_doc, close_doc = docs[("POS Opening Entry", opening)], docs[("POS Closing Entry", closing)]
        assert (open_doc.get("pos_closing_entry") or "") == (closing if close_doc.docstatus == 1 else ""), "开结班回指与精确来源不一致"
        assert close_doc.status == ("Submitted" if close_doc.docstatus == 1 else "Cancelled"), "销售结班原生状态变化"
        expected_status = "Cancelled" if open_doc.docstatus == 2 else ("Closed" if close_doc.docstatus == 1 else "Open")
        assert open_doc.status == expected_status and not (open_doc.docstatus == 2 and close_doc.docstatus == 1), "销售开班原生状态与结班不一致"
    for invoice, closing in data["invoice_closing_links"].items():
        close_doc = docs[("POS Closing Entry", closing)]
        assert (docs[("Sales Invoice", invoice)].get("pos_closing_entry") or "") == (closing if close_doc.docstatus == 1 else ""), "直接零售 SI 结班回指变化"
    for pos, mirror in data["pos_mirror_links"].items():
        pos_doc, mirror_doc = docs[("POS Invoice", pos)], docs[("Sales Invoice", mirror)]
        assert (pos_doc.get("consolidated_invoice") or "") == (mirror if mirror_doc.docstatus == 1 else ""), "POS 合并回指与精确镜像不一致"
    for name in data["unmerged_pos"]:
        assert not docs[("POS Invoice", name)].get("consolidated_invoice"), "未合并 POS 出现未经追踪的镜像发票"
    for name in data["openings"]:
        if name not in data["opening_closing_links"]:
            doc = docs[("POS Opening Entry", name)]
            assert not doc.get("pos_closing_entry") and doc.status == ("Open" if doc.docstatus == 1 else "Cancelled"), "未结班班次出现未经追踪的结班或状态"
    for name in data["invoices"]:
        if name not in data["invoice_closing_links"]:
            assert not docs[("Sales Invoice", name)].get("pos_closing_entry"), "销售发票出现未经追踪的结班回指"


def _net_gl(rows, include_cancelled=False):
    balances = defaultdict(Decimal)
    for row in rows:
        if include_cancelled or not cint(row["is_cancelled"]):
            balances[(row["voucher_type"], row["voucher_no"], row["account"])] += Decimal(str(row["debit"] or 0)) - Decimal(str(row["credit"] or 0))
    return [{"voucher_type": key[0], "voucher_no": key[1], "account": key[2], "net_value": str(value)}
            for key, value in sorted(balances.items())]


def _state(data, docs):
    submitted = [name for (doctype, name), doc in docs.items() if doctype in BUSINESS_TYPES and doc.docstatus == 1]
    cancelled = [name for (doctype, name), doc in docs.items() if doctype in BUSINESS_TYPES and doc.docstatus == 2]
    active, disabled = [], []
    records = {(record["doctype"], record["name"]): record for record in data["records"]}
    for key, doc in docs.items():
        if doc.doctype not in DISABLE_FIELDS:
            continue
        field, disabled_value = DISABLE_FIELDS[doc.doctype]
        value = cint(doc.get(field))
        if value == records[key]["active_value"]:
            active.append(key)
        elif value == disabled_value:
            disabled.append(key)
        else:
            raise AssertionError("销售验收主档启停值不受支持")
    if submitted and cancelled:
        return "partial_business_state"
    if submitted:
        return "ready" if not disabled else "unexpected_disabled_masters"
    if active and disabled:
        return "partial_disabled_state"
    return "cancelled_history_retained" if disabled else "cancelled"


def _reports(data, docs):
    from jifeng_erp.sales_reports import posted_sales_ledger, sales_transaction_ledger

    filters = data["report_filters"]["Jifeng Sales Transaction Ledger"]
    transactions, posted = _read(sales_transaction_ledger, filters), _read(posted_sales_ledger, filters)
    submitted = {(doctype, name) for (doctype, name), doc in docs.items() if doctype in ("Sales Invoice", "POS Invoice") and doc.docstatus == 1}
    mirrors = {("Sales Invoice", name) for name in data["pos_mirror_links"].values()}
    assert {(row["doc_type"], row["document"]) for row in transactions} == submitted - mirrors, "销售业务台账重复或遗漏原始销售来源"
    assert {(row["doc_type"], row["document"]) for row in posted} == {key for key in submitted if key[0] == "Sales Invoice"}, "已过账销售台账与已提交 SI 集合不一致"
    for row in [*transactions, *posted]:
        _money(row, docs[(row["doc_type"], row["document"])])
    return {"transaction_rows": transactions, "posted_rows": posted,
        "transaction_base_net_total": str(sum((Decimal(str(row["base_net_total"])) for row in transactions), Decimal(0))),
        "posted_base_net_total": str(sum((Decimal(str(row["base_net_total"])) for row in posted), Decimal(0)))}


def _restored(data, docs):
    assert not any(doc.docstatus == 1 for (doctype, _), doc in docs.items() if doctype in BUSINESS_TYPES), "销售验收仍有已提交业务单据"
    rows = _ledger(data)
    checks = _net_gl(rows["gl"])
    retained_checks = _net_gl(rows["gl"], include_cancelled=True)
    assert all(abs(Decimal(row["net_value"])) <= Decimal("0.000001") for row in checks), "销售总账某凭证/科目有效净额未归零"
    assert all(abs(Decimal(row["net_value"])) <= Decimal("0.000001") for row in retained_checks), "销售总账某凭证/科目保留历史净额未归零"
    assert not any(doc.get("status") == "Open" for (doctype, _), doc in docs.items() if doctype == "POS Opening Entry"), "销售验收仍有开启班次"
    reports = _reports(data, docs)
    assert not reports["transaction_rows"] and not reports["posted_rows"], "销售验收取消后报表仍有业务行"
    return {"ledger": rows, "effective_gl_by_voucher_account": checks,
        "retained_gl_by_voucher_account": retained_checks, "reports_empty": True, "stock_entry_count": 0}


def prepare(company, item_group, stock_uom):
    """Persist six original transactions, four posted SI, and three native shifts."""
    frappe.only_for("System Manager")
    for doctype, name in (("Company", company), ("Item Group", item_group), ("UOM", stock_uom)):
        frappe.get_doc(doctype, name).check_permission("read")
    suffix = frappe.generate_hash(length=12)
    data = {"fixture_type": "sales_browser", "fixture_version": 1, "prefix": "JF-SALES-BROWSER-" + suffix,
        "company": company, "item_group": item_group, "stock_uom": stock_uom, "created_at": now(),
        "created_by": frappe.session.user, "phase": "preparing", "settings_restored": False,
        "business_cancelled": False, "masters_disabled": False, "records": [],
        "opening_closing_links": {}, "invoice_closing_links": {}, "pos_mirror_links": {}, "unmerged_pos": [],
        "original_settings": _settings(), **{kind: [] for kind in KINDS}}
    _journal(data)
    point = "jf_sales_browser_prepare_" + suffix
    original_user, original_test = frappe.session.user, frappe.in_test
    _begin(point)
    try:
        frappe.in_test = True  # Native QA User Contact is created synchronously.
        fixture = _masters(data)
        for field in ("so_required", "dn_required"):
            frappe.db.set_single_value("Selling Settings", field, "No")
        frappe.clear_document_cache("Selling Settings", "Selling Settings")
        _mode("Sales Invoice")
        ordinary = _track(data, _invoice(fixture, posting_date=add_days(today(), -1), update_stock=0))
        frappe.set_user(fixture.cashier.name)
        si_opening = _track(data, _opening(fixture))
        direct = _track(data, _invoice(fixture, pos=True, paid=30, update_stock=0))
        si_closing = _track(data, _closing(si_opening))
        data["opening_closing_links"][si_opening.name] = si_closing.name
        data["invoice_closing_links"][direct.name] = si_closing.name
        _mode("POS Invoice")
        pos_opening = _track(data, _opening(fixture))
        sale = _track(data, _invoice(fixture, "POS Invoice", qty=2, duplicate=True, paid=35, posting_time="10:00:00", update_stock=0))
        returned = _track(data, _return(sale, index=1))
        pos_closing = _closing(pos_opening)
        logs = frappe.get_all("POS Invoice Merge Log", filters={"pos_closing_entry": pos_closing.name}, pluck="name", limit_page_length=0)
        assert len(logs) == 1, "最小 POS 班次必须产生一个原生合并日志"
        log = _track(data, frappe.get_doc("POS Invoice Merge Log", logs[0]))
        assert {row.pos_invoice for row in log.pos_invoices} == {sale.name, returned.name}, "原生结班合并来源不完整"
        for name in (log.consolidated_invoice, log.consolidated_credit_note):
            assert name, "原生 POS 班次缺少销售或部分退货镜像 SI"
            _track(data, frappe.get_doc("Sales Invoice", name))
        sale.reload()
        returned.reload()
        data["pos_mirror_links"] = {sale.name: sale.consolidated_invoice, returned.name: returned.consolidated_invoice}
        _track(data, pos_closing)  # Cancel Closing before its native Merge Log/SI children.
        data["opening_closing_links"][pos_opening.name] = pos_closing.name
        unmerged_opening = _track(data, _opening(fixture))
        unmerged_sale = _track(data, _invoice(fixture, "POS Invoice", qty=2, duplicate=True, paid=35, posting_time="12:00:00", update_stock=0))
        unmerged_return = _track(data, _return(unmerged_sale, index=1))
        data["unmerged_pos"] = [unmerged_sale.name, unmerged_return.name]
        data["browser_documents"] = {"ordinary_si": ordinary.name, "direct_pos_si": direct.name,
            "si_opening": si_opening.name, "si_closing": si_closing.name, "pos_opening": pos_opening.name,
            "pos_closing": pos_closing.name, "merged_pos_sale": sale.name, "merged_pos_return": returned.name,
            "merge_log": log.name, "merged_si": log.consolidated_invoice, "merged_credit_note": log.consolidated_credit_note,
            "unmerged_opening": unmerged_opening.name, "unmerged_pos_sale": unmerged_sale.name,
            "unmerged_pos_return": unmerged_return.name, "item": fixture.item.name,
            "customer": fixture.customer.name, "pos_profile": fixture.profile.name, "cashier": fixture.cashier.name}
        filters = {"company": company, "from_date": add_days(today(), -1), "to_date": today(), "customer": fixture.customer.name}
        data["report_filters"] = {name: dict(filters) for name in ("Jifeng Sales Transaction Ledger", "Jifeng Posted Sales Ledger")}
        frappe.set_user(original_user)
        _restore_settings(data)
        docs = _validate(data)
        assert _state(data, docs) == "ready", "销售浏览器夹具创建状态不完整"
        data["expected"] = _reports(data, docs)
        assert len(data["expected"]["transaction_rows"]) == 6 and len(data["expected"]["posted_rows"]) == 4
        data["ledger_records"] = _ledger(data)
        assert {row["voucher_no"] for row in data["ledger_records"]["gl"]} == set(data["invoices"]), "四张已过账 SI 的原生总账来源不完整"
    except Exception as exc:
        _end_rollback_only(point)
        data["phase"], data["error"] = "prepare_failed_rolled_back", str(exc)
        _journal(data)
        raise
    finally:
        frappe.set_user(original_user)
        frappe.in_test = original_test
        for doctype in SETTINGS_FIELDS:
            frappe.clear_document_cache(doctype, doctype)
    _commit(data, "prepare")
    data["phase"] = "ready"
    _journal(data)
    return data


def verify(data):
    """Read exact source identities, references, settings, ledgers and reports."""
    frappe.only_for("System Manager")
    data = _load(data)
    before = _ledger_snapshot()
    try:
        docs = _validate(data)
        state = _state(data, docs)
        assert _settings() == data["original_settings"], "销售夹具原始全局设置与当前值不一致，请人工核对"
        result = {"passed": True, "journal_phase": data["phase"], "durable_state": state,
            "artifact_path": data["artifact_path"], "stock_entry_count": 0, "settings_match_original": True,
            "document_states": [{"doctype": key[0], "name": key[1], "docstatus": doc.docstatus, "status": doc.get("status")} for key, doc in docs.items()]}
        assert state in ("ready", "cancelled", "cancelled_history_retained"), "销售夹具处于不完整或未经追踪的状态"
        result["reports"] = _reports(data, docs)
        result["ledger"] = _ledger(data)
        if state != "ready":
            result["restored"] = _restored(data, docs)
        return result
    finally:
        assert _ledger_snapshot() == before, "只读销售 verify 改变了库存或总账"


def cleanup(data):
    """Reverse exact native dependencies, then disable owned QA masters."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert data["phase"] != "prepare_failed_rolled_back", "创建已回滚的销售夹具不能自动清理"
    try:
        checked = verify(data)  # Resolve commit uncertainty before any mutation.
        docs = _validate(data)
    except Exception as exc:
        data["cleanup_refused_at"], data["cleanup_validation_error"] = now(), str(exc)
        _journal(data)
        raise
    suffix = data["prefix"].rsplit("-", 1)[-1]
    if checked["durable_state"] == "ready":
        point = "jf_sales_browser_cancel_" + suffix
        _begin(point)
        try:
            changes = []
            for record in reversed(data["records"]):
                if record["doctype"] not in BUSINESS_TYPES:
                    continue
                doc = frappe.get_doc(record["doctype"], record["name"])
                if doc.docstatus != 1:
                    continue
                if doc.doctype == "POS Closing Entry":
                    _cancel_closing(doc)
                else:
                    doc.cancel()
                changes.append({"doctype": doc.doctype, "name": doc.name, "cancelled_at": now()})
            docs = _validate(data)
            data["restored_checks"] = _restored(data, docs)
        except Exception as exc:
            _end_rollback_only(point)
            data["phase"], data["cancel_error"] = "cancellation_failed_rolled_back", str(exc)
            _journal(data)
            raise
        data.setdefault("cancelled_document_log", []).extend(changes)
        _commit(data, "cancel")
        data["business_cancelled"], data["phase"] = True, "cancelled"
        _journal(data)
    if checked["durable_state"] != "cancelled_history_retained":
        point = "jf_sales_browser_disable_" + suffix
        original_test = frappe.in_test
        _begin(point)
        try:
            frappe.in_test = True  # Updating QA User must not enqueue an untracked Contact update.
            docs = _validate(data)
            before = _ledger(data)
            changes = []
            for record in reversed(data["records"]):
                if record["doctype"] not in DISABLE_FIELDS:
                    continue
                doc = docs[(record["doctype"], record["name"])]
                field, value = DISABLE_FIELDS[doc.doctype]
                definition = doc.meta.get_field(field)
                assert definition and definition.fieldtype == "Check", "销售验收主档缺少原生停用字段"
                previous = cint(doc.get(field))
                setattr(doc, field, value)
                doc.save()
                assert cint(frappe.db.get_value(doc.doctype, doc.name, field)) == value
                changes.append({"doctype": doc.doctype, "name": doc.name, "field": field,
                    "previous_value": previous, "value": value, "changed_at": now()})
            docs = _validate(data)
            assert _state(data, docs) == "cancelled_history_retained"
            assert before == _ledger(data), "停用主档不能改变原生总账或库存历史"
            data["restored_checks"] = _restored(data, docs)
        except Exception as exc:
            _end_rollback_only(point)
            data["phase"], data["disable_error"] = "cancelled_disable_failed_rolled_back", str(exc)
            _journal(data)
            raise
        finally:
            frappe.in_test = original_test
        data.setdefault("disabled_master_records", []).extend(changes)
        _commit(data, "disable_masters")
    data["business_cancelled"], data["masters_disabled"], data["cleanup_complete"] = True, True, True
    data["phase"] = "cancelled_history_retained"
    data["retained_history_records"] = [{"doctype": key[0], "name": key[1], "docstatus": doc.docstatus} for key, doc in docs.items()]
    _journal(data)
    return data
