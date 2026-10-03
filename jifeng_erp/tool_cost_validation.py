"""Prepared serial native tool-cost experiment; invoke once only in a clone.

The caller installs the two reviewed owned DocTypes and four deny-only hooks
first, then independently audits a fresh whole database after this returns.
This helper never connects, reloads, migrates, commits, uploads or grants native
accounting rights. Fixtures use real Expense/JE/PE insert/submit/cancel and real
allocation controllers. SQL is solely a complete-row/schema rollback oracle.
No saved docstatus/db_set fixtures, fake journals or posted reference prices.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import frappe
from frappe.utils import today

from jifeng_erp import tool_cost_sources as costs
from jifeng_erp.accounting import payment_context
from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller, _profile_rollback, _version_has
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.order_customer_selection_validation import _permission_response_state, _rows
from jifeng_erp.order_operations_validation import _doctypes, _physical_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _sql_row_hash


AUDIT = set(costs.PARENTS) | {costs.CHILD, "User", "User Permission", "Version", "DocShare", "Property Setter",
    "Custom DocPerm", "Custom Field", "DocType", "DocField", "DocPerm", "Role", "Has Role", "Comment",
    "Deleted Document", "Activity Log", "Email Queue", "Notification Log", "Communication", "Error Log",
    "Payment Reconciliation Log", "Unreconcile Payment", "Repost Payment Ledger", "Repost Accounting Ledger"}


def _contents():
    selected = _doctypes({doctype for doctype in AUDIT if frappe.db.exists("DocType", doctype)})
    values = {doctype: _rows(doctype) for doctype in sorted(selected) if not frappe.get_meta(doctype).issingle}
    values["tabSingles"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    for table in ("__Auth", "tabSeries", "__global_search"):
        values[table] = _sql_row_hash(frappe.db.sql("SELECT * FROM `" + table + "` ORDER BY 1", as_dict=True))
    return values


def _pins():
    paths = [frappe.get_app_path("jifeng_erp", "tool_cost_sources.py"),
             frappe.get_app_path("jifeng_erp", "tool_cost_validation.py"),
             frappe.get_app_path("jifeng_erp", "accounting.py"), frappe.get_app_path("jifeng_erp", "hooks.py"),
             frappe.get_app_path("jifeng_erp", "personnel_separation.py")]
    for folder in ("jifeng_tool_cost_allocation", "jifeng_tool_cost_allocation_item"):
        for suffix in ("py", "json"):
            paths.append(frappe.get_app_path("jifeng_erp", "jifeng_erp", "doctype", folder, folder + "." + suffix))
    paths.append(frappe.get_app_path("jifeng_erp", "jifeng_erp", "doctype", "jifeng_tool_cost_allocation",
                                   "jifeng_tool_cost_allocation.js"))
    return {str(Path(path).relative_to(Path(frappe.get_app_path("jifeng_erp")))):
            hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}


def _restore_messages(state):
    for name, entry in state["local"].items():
        if entry["present"]:
            setattr(frappe.local, name, deepcopy(entry["value"]))
        elif hasattr(frappe.local, name):
            delattr(frappe.local, name)
    if state["error_message_present"]:
        frappe.flags.error_message = deepcopy(state["error_message"])
    else:
        frappe.flags.pop("error_message", None)


def smoke(company, other_company):
    """Native serial contracts, synthetic records and strict complete rollback."""
    frappe.only_for("System Manager")
    assert frappe.local.site.endswith(".localhost") and frappe.db.db_type == "mariadb"
    assert company != other_company and all(frappe.db.exists("Company", value) for value in (company, other_company))
    costs._require_hooks()
    meta = frappe.get_meta(costs.DOCTYPE)
    assert meta.is_submittable and meta.track_changes and not meta.allow_import and not meta.allow_rename
    assert meta.get_field("active_source_key").unique and meta.get_field("request_id").unique
    assert frappe.get_meta(costs.CHILD).istable
    rights = {"delete", "amend", "report", "export", "import", "share", "print", "email"}
    assert {row.role for row in meta.permissions} == {"Accounts User", "Accounts Manager", "System Manager"}
    assert all(all(row.get(right) == 0 for right in rights) for row in meta.permissions)
    from erpnext.accounts.utils import get_fiscal_year
    get_fiscal_year(today(), company=company, verbose=0)
    currency = frappe.get_doc("Company", company).default_currency
    assert currency
    def choose(doctype, filters):
        names = frappe.get_list(doctype, filters=filters, pluck="name", order_by="name asc", limit_page_length=1)
        assert names, "验收需要既有本币费用/应付/现金银行明细科目及成本中心、供应商分组"
        return names[0]
    base = {"company": company, "is_group": 0, "disabled": 0, "account_currency": currency}
    expense_account = choose("Account", {**base, "root_type": "Expense"})
    payable = choose("Account", {**base, "root_type": "Liability", "account_type": "Payable"})
    bank = choose("Account", {**base, "root_type": "Asset", "account_type": ["in", ["Cash", "Bank"]]})
    center, group = choose("Cost Center", {"company": company, "is_group": 0}), choose("Supplier Group", {})
    original_user, original_test = frappe.session.user, frappe.in_test
    frappe.cache.hdel("roles", original_user)
    original_roles = sorted(frappe.get_roles(original_user))
    transaction_control = frappe.db._disable_transaction_control
    callbacks, context, messages = _callbacks(), _local_context(), _permission_response_state()
    physical_before, contents_before, ledger_before, pins = _physical_snapshot(), _contents(), _ledger_snapshot(), _pins()
    point, prefix = "jf_tool_cost_" + frappe.generate_hash(length=8), "JF-QA-TC-" + frappe.generate_hash(length=10)
    records, owned, users, objects, checks, evidence = set(), set(), [], [], [], {}
    primary = None
    _begin_rollback_only(point)

    def native(action):
        prior = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = prior

    def track(document, created=True):
        objects.append((document, created))
        for item in [document, *document.get_all_children()]:
            if item.name:
                records.add((item.doctype, item.name))
                if created:
                    owned.add((item.doctype, item.name))
        return document

    def insert(doctype, **values):
        document = track(frappe.get_doc({"doctype": doctype, **values}))
        native(document.insert)
        return track(document)

    def actor(label, roles, scope):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
            send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=scope, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def clear_meta():
        for doctype in (costs.DOCTYPE, costs.CHILD, costs.EXPENSE, costs.JE, "Journal Entry Account", costs.GL):
            frappe.clear_cache(doctype=doctype)
            parent = {costs.CHILD: costs.DOCTYPE, "Journal Entry Account": costs.JE}.get(doctype, "")
            for user in {*users, original_user, "Administrator"}:
                frappe.cache.delete_value(f"masked_fields::{doctype}::{parent}::{user}")

    def experiment(action):
        previous, queues, local, response = _contents(), _callbacks(), _local_context(), _permission_response_state()
        user, testing = frappe.session.user, frappe.in_test
        nested, failure = "jf_tc_case_" + frappe.generate_hash(length=8), None
        _begin_rollback_only(nested)
        try:
            return action()
        except Exception as error:
            failure = error
            raise
        finally:
            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, queues)
            def restore():
                clear_meta()
                _profile_restore_caller(user, testing, users, records, queues, local)
                _restore_messages(response)
            _profile_audit([("nested_rollback", rollback), ("nested_restore", restore),
                            ("nested_contents_exact", lambda: _contents() == previous)], primary=failure)

    def rejected(action, errors=frappe.ValidationError):
        def check():
            try:
                native(action)
            except errors:
                return
            raise AssertionError("原生接受了必须拒绝的工具费用动作")
        experiment(check)

    def pure(action):
        before = _contents()
        result = native(action)
        assert _contents() == before, "只读工具费用查询改动持久数据"
        return result

    def passed(label):
        assert label not in checks and _pins() == pins
        checks.append(label)

    def expense(method="赊账"):
        document = insert(costs.EXPENSE, company=company, posting_date=today(), payment_method=method,
            expense_account=expense_account, cost_center=center, amount=120, summary=prefix + "-120",
            **({"supplier": supplier.name, "payable_account": payable} if method == "赊账" else {"payment_account": bank}))
        native(document.submit)
        document.reload()
        track(frappe.get_doc(costs.JE, document.journal_entry))
        return document

    def create(source, amounts=("70", "50"), request_id=None):
        result = native(lambda: costs.create_tool_cost_allocation({"company": company, "expense_voucher": source.name,
            "expense_version": str(source.modified), "reason": "明确模具70、版种50，不按参考价分配"}, [
                {"record_type": kind, "name": tool.name, "version": str(tool.modified), "amount": amount}
                for kind, tool, amount in zip(("mold", "plate"), (mold, plate), amounts)], request_id or str(uuid4())))
        return track(frappe.get_doc(costs.DOCTYPE, result["name"]))

    def payment(source, amount):
        values = payment_context(source.name)
        document = insert(costs.PE, company=company, posting_date=today(), payment_type="Pay", party_type="Supplier",
            party=supplier.name, paid_from=bank, paid_to=payable, paid_amount=amount, received_amount=amount,
            source_exchange_rate=1, target_exchange_rate=1, reference_no=prefix, reference_date=today(),
            references=[{"reference_doctype": costs.JE, "reference_name": values["reference_name"],
                         "total_amount": values["total_amount"], "outstanding_amount": values["outstanding_amount"], "allocated_amount": amount}])
        native(document.submit)
        return track(document)

    try:
        frappe.set_user("Administrator")
        manager = actor("manager", ["Accounts Manager", "Stock Manager"], company)
        clerk = actor("clerk", ["Accounts User", "Stock Manager"], company)
        outside = actor("outside", ["Accounts Manager", "Stock Manager"], other_company)
        stock = actor("stock", ["Stock Manager"], company)
        supplier = insert("Supplier", supplier_name=prefix, supplier_group=group, supplier_type="Company",
                          default_currency=currency, accounts=[{"company": company, "account": payable}])
        frappe.set_user(manager)
        mold = insert("Jifeng Mold", company=company, mold_no=prefix + "-M", product_code=prefix,
                      quantity=1, mold_date=today(), amount=9999)
        plate = insert("Jifeng Plate", company=company, plate_no=prefix + "-P", product_code=prefix,
                       plate_date=today(), reference_cost=8888)
        source = expense()
        assignment = create(source)
        native(assignment.submit)
        assignment.reload()
        assert _version_has(assignment, "docstatus")
        versions = frappe.get_all("Version", filters={"ref_doctype": costs.DOCTYPE, "docname": assignment.name}, pluck="data")
        assert any(["docstatus", 0, 1] in json.loads(data).get("changed", []) for data in versions)
        view = pure(lambda: costs.get_tool_cost_allocation(assignment.name, str(assignment.modified)))
        assert [row["historical_amount"] for row in view["items"]] == ["70", "50"]
        assert all(row["paid_amount"] == "0" for row in view["items"])
        passed("native_expense_je_gl_ple_and_exact70_50_submitted_assignment")
        def forbid_sharing():
            frappe.set_user("Administrator")
            rejected(lambda: frappe.share.add(costs.DOCTYPE, assignment.name, user=outside, read=1), frappe.PermissionError)
            rejected(lambda: frappe.share.add_docshare(costs.DOCTYPE, assignment.name, user=outside,
                flags={"ignore_share_permission": True}), frappe.PermissionError)
            rejected(lambda: frappe.share.add_docshare(costs.DOCTYPE, assignment.name, user=outside,
                flags={"ignore_share_permission": True, "ignore_validate": True}), frappe.PermissionError)
            rejected(lambda: frappe.share.add_docshare(costs.CHILD, assignment.items[0].name, user=outside,
                flags={"ignore_share_permission": True}), frappe.PermissionError)
            assert not frappe.db.exists("DocShare", {"share_doctype": ["in", [costs.DOCTYPE, costs.CHILD]]})
            frappe.set_user(outside)
            from frappe.client import get as native_get
            rejected(lambda: native_get(costs.DOCTYPE, assignment.name), frappe.PermissionError)
            assert not frappe.get_list(costs.DOCTYPE, filters={"name": assignment.name}, fields=["name", "source_amount"])
            frappe.set_user(manager)
        experiment(forbid_sharing)
        passed("native_admin_parent_child_shares_forbidden_and_source_up_client_list_guarded")
        # The submitted allocation has native Expense/JE Links. Ordinary
        # back-link checks must block cancelling even an unpaid source.
        rejected(source.cancel)
        source.reload()
        passed("native_submitted_allocation_links_block_unpaid_source_cancel")
        before_financial = {doctype: _rows(doctype) for doctype in (costs.EXPENSE, costs.JE, costs.PE, costs.GL, costs.PLE)}
        mold.amount = 10000
        native(mold.save)
        assert pure(lambda: costs.get_tool_costs(company, "mold", mold.name))["incurred_amount"] == "70"
        assert before_financial == {doctype: _rows(doctype) for doctype in before_financial}
        passed("reference_price_change_creates_no_accounting_and_does_not_reprice70")
        partial = payment(source, 40)
        view = pure(lambda: costs.get_tool_cost_allocation(assignment.name))
        assert (view["source"]["paid_amount"], view["source"]["unpaid_amount"]) == ("40", "80")
        assert all(row["paid_amount"] is None and row["unpaid_amount"] is None for row in view["items"])
        passed("actual_partial40_unpaid80_no_invented_tool_payment_split")
        def hidden_payment_response():
            frappe.set_user("Administrator")
            insert("User Permission", user=manager, allow=costs.PE, for_value=partial.name,
                apply_to_all_doctypes=0, applicable_for=costs.PE)
            # Restrict the reader to a different saved native payment to hide
            # this source. Both real Payment Entries remain untouched.
            other = payment(source, 1)
            permission = frappe.get_doc("User Permission", {"user": manager, "allow": costs.PE, "for_value": partial.name})
            permission.for_value = other.name
            native(permission.save)
            frappe.clear_cache(user=manager)
            frappe.set_user(manager)
            assert not frappe.has_permission(costs.PE, "read", doc=partial, throw=False)
            prior = _permission_response_state()
            try:
                costs.get_tool_cost_allocation(assignment.name)
            except frappe.PermissionError as error:
                channels = json.dumps({"state": _permission_response_state(), "exception": str(error)}, default=str)
                assert partial.name not in channels and other.name not in channels
            else:
                raise AssertionError("工具费用查询返回了不可读取付款的金额")
            finally:
                _restore_messages(prior)
        experiment(hidden_payment_response)
        passed("native_hidden_payment_denial_contains_no_payment_identity_in_response_channels")
        remaining = payment(source, 80)
        view = pure(lambda: costs.get_tool_cost_allocation(assignment.name))
        assert (view["source"]["paid_amount"], view["source"]["unpaid_amount"]) == ("120", "0")
        assert [row["paid_amount"] for row in view["items"]] == ["70", "50"]
        passed("actual40_plus80_native_payments_close120_and_tools70_50")
        for amounts in (("70", "49"), ("70", "51"), ("70.001", "49.999")):
            rejected(lambda amounts=amounts: create(source, amounts))
        duplicate = create(source)
        rejected(duplicate.submit)
        passed("native_partial_excess_precision_and_duplicate_whole_source_rejected")
        frappe.set_user(clerk)
        rejected(lambda: frappe.get_doc(costs.DOCTYPE, duplicate.name).submit(), frappe.PermissionError)
        frappe.set_user(outside)
        rejected(lambda: costs.get_tool_cost_allocation(assignment.name), frappe.PermissionError)
        frappe.set_user(stock)
        rejected(lambda: costs.get_tool_cost_source({"company": company, "expense_voucher": source.name}), frappe.PermissionError)
        frappe.set_user(manager)
        passed("existing_clerk_stock_and_company_up_permissions_no_native_grants")
        def masked(doctype, field, property_name="mask"):
            frappe.set_user("Administrator")
            insert("Property Setter", doc_type=doctype, doctype_or_field="DocField", field_name=field,
                   property=property_name, property_type="Int", value="1")
            clear_meta()
            frappe.set_user(manager)
            try:
                costs.get_tool_cost_allocation(assignment.name)
            except frappe.PermissionError:
                assert not frappe.get_doc(costs.DOCTYPE, assignment.name).has_permission("read")
                visible = frappe.get_list(costs.DOCTYPE, filters={"name": assignment.name}, fields=["name", "source_amount"])
                assert not visible
            else:
                raise AssertionError("源mask或Table权限被工具分摊绕过")
        for doctype, field, property_name in ((costs.EXPENSE, "amount", "mask"), (costs.JE, "accounts", "mask"),
            (costs.JE, "accounts", "permlevel"), ("Journal Entry Account", "credit_in_account_currency", "mask"),
            (costs.CHILD, "amount", "mask")):
            experiment(lambda: masked(doctype, field, property_name))
        passed("native_parent_child_table_field_mask_and_list_guards")
        rejected(source.cancel)
        # Native failed cancel mutates the Python Document before the nested
        # savepoint rolls back. Restore its saved state before real cancellation.
        source.reload()
        passed("native_paid_source_cancellation_still_blocked")
        before_financial = {doctype: _rows(doctype) for doctype in (costs.EXPENSE, costs.JE, costs.PE, costs.GL, costs.PLE)}
        cleared = native(lambda: costs.cancel_tool_cost_allocation(assignment.name, str(assignment.modified), "明确撤销工具费用归属，后续另行逆序取消付款和费用"))
        assert cleared["docstatus"] == 2
        assert before_financial == {doctype: _rows(doctype) for doctype in before_financial}
        assert pure(lambda: costs.resolve_tool_cost_creation(company, assignment.request_id))["name"] == assignment.name
        inactive = pure(lambda: costs.get_tool_cost_allocation(assignment.name))
        assert all(row["paid_amount"] is None and row["payment_state"] == "not_active" for row in inactive["items"])
        passed("allocation_cancel_preserves_receipt_and_does_not_cancel_financial_sources")
        native(remaining.cancel)
        native(partial.cancel)
        native(source.cancel)
        view = pure(lambda: costs.get_tool_cost_allocation(assignment.name))
        assert view["source"]["source_state"] == "cancelled" and view["source"]["incurred_amount"] == "0"
        assert [row["historical_amount"] for row in view["items"]] == ["70", "50"]
        assert all(row["paid_amount"] == "0" and row["unpaid_amount"] == "0" for row in view["items"])
        passed("native_reverse_cancel_preserves_history_and_zero_actual_ledger")
        assert view["docstatus"] == 2 and not view["can_cancel"]
        assert pure(lambda: costs.resolve_tool_cost_creation(company, assignment.request_id))["name"] == assignment.name
        cash_source = expense("其他")
        cash_assignment = create(cash_source)
        native(cash_assignment.submit)
        cash_assignment.reload()
        cash_view = pure(lambda: costs.get_tool_cost_allocation(cash_assignment.name))
        assert cash_view["source"]["payment_basis"] == "native_cash_bank_gl"
        assert cash_view["source"]["paid_amount"] == "120" and cash_view["source"]["unpaid_amount"] == "0"
        passed("native_cash_bank_expense_gl_proves_paid_no_reference_price")
        public = json.dumps(view, ensure_ascii=False)
        assert not any(key in public for key in ("bank_account_no", "bank_account", "source_snapshot", "source_digest", "active_source_key"))
        evidence["semantics"] = {"fee": 120, "shares": [70, 50], "partial_paid": 40, "partial_unpaid": 80,
                                  "partial_tool_paid": None, "partial_tool_unpaid": None, "fully_paid": 120,
                                  "native_serial_only": True, "no_accounting_permission_grants": True}
    except Exception as error:
        primary = error
        raise
    finally:
        def collect():
            for document, created in objects:
                for item in [document, *document.get_all_children()]:
                    if item.name:
                        records.add((item.doctype, item.name))
                        if created:
                            owned.add((item.doctype, item.name))
        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks)
        def restore():
            clear_meta()
            for user in {original_user, "Administrator"}:
                frappe.cache.hdel("roles", user)
            _profile_restore_caller(original_user, original_test, users, records, callbacks, context)
            _restore_messages(messages)
        def caller():
            return (_callbacks() == callbacks and _local_context() == context and _permission_response_state() == messages
                and frappe.session.user == original_user and frappe.in_test == original_test
                and sorted(frappe.get_roles(original_user)) == original_roles
                and frappe.db._disable_transaction_control == transaction_control)
        _profile_audit([("collect_native_fixture_rows", collect), ("native_rollback", rollback), ("caller_restore", restore),
            ("owned_rows_absent", lambda: all(not frappe.db.exists(doctype, name) for doctype, name in owned)),
            ("complete_business_auth_metadata_versions_unchanged", lambda: _contents() == contents_before),
            ("whole_stock_gl_ledger_unchanged", lambda: _ledger_snapshot() == ledger_before),
            ("full_physical_schema_indexes_engines_rows_unchanged", lambda: _physical_snapshot() == physical_before),
            ("source_pins_unchanged", lambda: _pins() == pins), ("caller_roles_context_callbacks_messages_restored", caller)], primary=primary)
    checks.append("complete_clone_rollback_and_fresh_physical_oracle")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "evidence": evidence,
            "source_sha256": pins, "business_data": "rolled_back", "schema_changed": False,
            "gui_verified": False, "http_verified": False, "multi_connection_lock_verified": False,
            "prepared_only_requires_root_execution_and_independent_fresh_snapshot": True}
