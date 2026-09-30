"""Native rollback acceptance of supervisor shares and the HRMS payroll chain."""

from decimal import Decimal

import frappe
from frappe.utils import get_first_day, get_last_day, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.wage_allocation import get_allocation_source


def _amount(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def smoke(company, item_group, stock_uom, payroll=True):
    """Requires migrated hooks and synthetic company accounts; never commits fixtures."""
    frappe.only_for("System Manager")
    required = ("Jifeng Wage Allocation", "Jifeng Wage Allocation Employee", "Jifeng Wage Entry")
    if not all(frappe.db.exists("DocType", name) for name in required):
        frappe.throw("请先迁移工资分配字段与单据，再运行原生验收")
    if "jifeng_erp.wage_allocation.guard_job_card_allocation" not in frappe.get_hooks(
        "doc_events").get("Job Card", {}).get("before_update_after_submit", []):
        frappe.throw("请先安装报工单工资来源修改守卫")
    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    gender = frappe.db.get_value("Gender", {}, "name")
    if not warehouse or not gender:
        frappe.throw("验收公司需要启用的明细仓库和原生性别字典")
    point = "jf_wage_allocation_smoke"
    original_user, original_in_test = frappe.session.user, frappe.in_test
    original_enqueue = frappe.flags.enqueue_payroll_entry
    original_via_payroll = frappe.flags.via_payroll_entry
    users, checks = [], []
    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.flags.enqueue_payroll_entry = False
        suffix = frappe.generate_hash(length=9)
        ledger_before = _ledger_snapshot()
        currency = frappe.db.get_value("Company", company, "default_currency")
        first, last = str(get_first_day(today())), str(get_last_day(today()))
        employees = [frappe.get_doc({"doctype": "Employee", "first_name": f"JF-WAGE-{idx}-{suffix}",
                                     "company": company, "gender": gender,
                                     "date_of_birth": "1990-01-01", "date_of_joining": first,
                                     "status": "Active"}).insert() for idx in range(3)]
        component = frappe.get_doc({"doctype": "Salary Component", "salary_component": "JF-PIECE-" + suffix,
                                    "salary_component_abbr": "JFP" + suffix, "type": "Earning",
                                    "depends_on_payment_days": 0, "is_tax_applicable": 0}).insert()
        source_company = frappe.get_doc("Company", company)
        other_company = frappe.get_doc({"doctype": "Company", "company_name": "JF-WAGE-OTHER-" + suffix,
                                        "abbr": "W" + suffix[:4], "default_currency": currency,
                                        "country": source_company.country,
                                        "create_chart_of_accounts_based_on": "Existing Company",
                                        "existing_company": company}).insert()
        raw, finished = [frappe.get_doc({"doctype": "Item", "item_code": f"JF-WAGE-{kind}-{suffix}",
                                        "item_group": item_group, "stock_uom": stock_uom}).insert()
                         for kind in ("RAW", "FG")]
        workstation = frappe.get_doc({"doctype": "Workstation", "workstation_name": "JF-WAGE-WS-" + suffix,
                                       "production_capacity": 1}).insert()
        operation = frappe.get_doc({"doctype": "Operation", "name": "JF-WAGE-OP-" + suffix,
                                    "workstation": workstation.name}).insert()
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        settings.save()
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                             "quantity": 1, "with_operations": 1, "transfer_material_against": "Work Order",
                             "items": [{"item_code": raw.name, "qty": 1, "rate": 1}],
                             "operations": [{"operation": operation.name, "workstation": workstation.name,
                                             "time_in_mins": 1}]}).insert()
        bom.submit()

        def native_card(hour, people, qty=5, completed=None, pending=0, company_name=company,
                        company_warehouse=warehouse, company_bom=None):
            flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company_name,
                                  "item_code": finished.name, "planned_qty": qty,
                                  "bom": (company_bom or bom).name,
                                  "materials": [{"item_code": raw.name, "uom": stock_uom,
                                                 "qty_per_unit": 1, "unit_price": 1}]}).insert()
            work = frappe.get_doc("Work Order", flow.make_work_order())
            work.source_warehouse = company_warehouse
            work.fg_warehouse = company_warehouse
            work.skip_transfer = 1
            work.planned_start_date = today() + f" {hour:02d}:00:00"
            work.save()
            work.submit()
            card_name = frappe.get_all("Job Card", filters={"work_order": work.name}, pluck="name")[0]
            card = frappe.get_doc("Job Card", card_name)
            card.start_timer(start_time=today() + f" {hour:02d}:00:00",
                             employees=[{"employee": employee.name} for employee in people])
            card.reload()
            card.complete_job_card(qty=qty if completed is None else completed, for_quantity=qty,
                                   pending_qty=pending, process_loss_qty=0,
                                   end_time=today() + f" {hour + 1:02d}:00:00")
            card.reload()
            card.submit()
            return card

        foreign_bom = frappe.copy_doc(bom)
        foreign_bom.company = other_company.name
        foreign_bom.is_default = 0
        foreign_bom.insert()
        foreign_bom.submit()
        foreign_employee = frappe.get_doc({"doctype": "Employee", "first_name": "JF-WAGE-FOREIGN-" + suffix,
                                           "company": other_company.name, "gender": gender,
                                           "date_of_birth": "1990-01-01", "date_of_joining": first,
                                           "status": "Active"}).insert()
        foreign_warehouse = frappe.db.get_value("Warehouse", {"company": other_company.name,
                                                              "is_group": 0, "disabled": 0}, "name")
        foreign_card = native_card(7, [foreign_employee], qty=1, company_name=other_company.name,
                                   company_warehouse=foreign_warehouse, company_bom=foreign_bom)
        card = native_card(9, employees[:2])
        single_card = native_card(11, employees[:1])
        spare_card = native_card(13, employees[:1])
        from erpnext.manufacturing.doctype.job_card.job_card import make_corrective_job_card
        from erpnext.manufacturing.doctype.work_order.work_order import make_job_card

        correction_operation = frappe.get_doc({"doctype": "Operation", "name": "JF-WAGE-COR-" + suffix,
                                               "is_corrective_operation": 1,
                                               "workstation": workstation.name}).insert()
        correction = make_corrective_job_card(spare_card.name, operation=correction_operation.name,
                                               for_operation=spare_card.operation)
        correction.for_quantity = 2
        correction.append("time_logs", {"employee": employees[0].name,
                                          "from_time": today() + " 15:00:00",
                                          "to_time": today() + " 16:00:00", "completed_qty": 2})
        correction.insert()
        correction.submit()
        split_card = native_card(17, employees[:2], completed=2, pending=3)
        make_job_card(split_card.work_order, [{"name": split_card.operation_id, "operation": operation.name,
                                               "qty": 3, "pending_qty": 3, "time_in_mins": 3,
                                               "sequence_id": split_card.sequence_id}])
        continuation = frappe.get_doc("Job Card", frappe.get_all(
            "Job Card", filters={"work_order": split_card.work_order, "docstatus": 0}, pluck="name")[0])
        continuation.start_timer(start_time=today() + " 18:00:00",
                                  employees=[{"employee": employee.name} for employee in employees[:2]])
        continuation.reload()
        continuation.complete_job_card(qty=3, for_quantity=3, pending_qty=0, process_loss_qty=0,
                                       end_time=today() + " 19:00:00")
        continuation.reload()
        continuation.submit()
        assert (split_card.total_completed_qty, continuation.total_completed_qty) == (2, 3)
        assert [row.completed_qty for row in card.time_logs] == [0, 5]
        assert [row.time_in_mins for row in card.time_logs] == [60, 60]
        assert card.total_time_in_mins == 120 and card.total_completed_qty == 5
        checks.append("native_team_output_is_not_personal_share")

        def role_user(kind, roles):
            user = frappe.get_doc({"doctype": "User", "email": f"jf-walloc-{kind}-{suffix}@example.invalid",
                                   "first_name": "工资原生验收", "send_welcome_email": 0,
                                   "roles": [{"role": role} for role in roles]}).insert()
            users.append(user.name)
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                            "for_value": company, "apply_to_all_doctypes": 1}).insert()
            return user

        manager = role_user("manager", ["HR Manager"])
        hr_user = role_user("clerk", ["HR User"])
        accountant = role_user("accounts", ["Accounts User"])

        def blocked(action, expected=frappe.ValidationError):
            save_point = "jf_walloc_expected_failure"
            frappe.db.savepoint(save_point)
            before = _ledger_snapshot()
            try:
                action()
            except expected:
                pass
            else:
                raise AssertionError("Invalid source/share/action was accepted")
            finally:
                frappe.db.rollback(save_point=save_point)
            assert _ledger_snapshot() == before

        def allocation(source=card, shares=(2, 3), rates=(3, 4), include_time=False):
            rows = [{"employee": employees[idx].name, "quantity": qty, "piece_rate": rates[idx],
                     "include_time_pay": int(include_time and idx == 0), "hourly_rate": 10 if idx == 0 else 0,
                     "extra_amount": 0} for idx, qty in enumerate(shares)]
            return frappe.get_doc({"doctype": "Jifeng Wage Allocation", "job_card": source.name,
                                   "payroll_date": today(), "salary_component": component.name,
                                   "employees": rows}).insert()

        frappe.set_user(manager.name)
        assert not frappe.has_permission("Job Card", "write", doc=card)
        assert not frappe.has_permission("Work Order", "submit")
        assert frappe.has_permission("Additional Salary", "cancel")
        assert frappe.has_permission("Additional Salary", "delete")
        source = get_allocation_source(card.name)
        assert source["source_completed_qty"] == "5.000000"
        assert [row["hours"] for row in source["employees"]] == [1, 1]
        checks.append("hr_manager_company_scoped_read_without_manufacturing_write")
        blocked(lambda: get_allocation_source(foreign_card.name), frappe.PermissionError)
        checks.append("company_user_permission_denies_foreign_job_card")
        blocked(lambda: allocation(correction, shares=(2,), rates=(2,)))
        checks.append("corrective_piece_allocation_explicitly_rejected")
        split_allocations = [allocation(split_card, shares=(1, 1), rates=(2, 2)),
                             allocation(continuation, shares=(1, 2), rates=(2, 2))]
        for split_allocation in split_allocations:
            split_allocation.submit()
            for name in split_allocation.make_wages():
                frappe.delete_doc("Jifeng Wage Entry", name)
            split_allocation.cancel()
        assert [Decimal(str(row.source_completed_qty)) for row in split_allocations] == [Decimal(2), Decimal(3)]
        checks.append("continuation_cards_allocate_their_own_completion_once")
        draft = allocation(include_time=True)
        under = frappe.get_doc("Jifeng Wage Allocation", draft.name)
        under.employees[0].quantity = 1
        blocked(under.submit)
        over = frappe.get_doc("Jifeng Wage Allocation", draft.name)
        over.employees[0].quantity = 3
        blocked(over.submit)
        checks.append("under_and_over_total_shares_rejected")
        duplicate_person = frappe.get_doc("Jifeng Wage Allocation", draft.name)
        duplicate_person.employees[1].employee = employees[0].name
        blocked(duplicate_person.save)
        outsider = frappe.get_doc("Jifeng Wage Allocation", draft.name)
        outsider.employees[1].employee = employees[2].name
        blocked(outsider.save)
        blocked(lambda: allocation())
        checks.extend(["employee_unique_and_source_participant", "active_allocation_unique"])
        frappe.set_user(hr_user.name)
        blocked(lambda: frappe.get_doc("Jifeng Wage Allocation", draft.name).submit(), frappe.PermissionError)
        frappe.set_user(manager.name)
        draft.reload()
        draft.submit()
        wages = [frappe.get_doc("Jifeng Wage Entry", name) for name in draft.make_wages()]
        assert len(wages) == 2 and all(wage.docstatus == 0 for wage in wages)
        by_employee = {wage.employee: wage for wage in wages}
        assert [(by_employee[employee.name].quantity, by_employee[employee.name].hours)
                for employee in employees[:2]] == [(2, 1), (3, 0)]
        assert sorted(_amount(wage.total_amount) for wage in wages) == [Decimal(12), Decimal(16)]
        assert draft.make_wages() == [wage.name for wage in wages]
        assert all(wage.stock_uom == stock_uom and wage.wage_allocation == draft.name for wage in wages)
        assert len(frappe.get_all("Jifeng Wage Entry", filters={"wage_allocation": draft.name})) == 2
        checks.extend(["explicit_unequal_shares", "explicit_personal_time_pay", "draft_wage_generation_idempotent"])
        for field, value in (("quantity", 4), ("piece_rate", 99), ("hours", 2), ("stock_uom", "Kg")):
            changed = frappe.get_doc("Jifeng Wage Entry", wages[0].name)
            setattr(changed, field, value)
            blocked(changed.save)
        unlinked = frappe.get_doc("Jifeng Wage Entry", wages[0].name)
        unlinked.wage_allocation = None
        unlinked.wage_allocation_employee = None
        unlinked.job_card = None
        blocked(unlinked.save)
        blocked(lambda: frappe.copy_doc(wages[0]).insert())
        blocked(lambda: frappe.get_doc("Jifeng Wage Allocation", draft.name).cancel())
        checks.extend(["allocated_wage_source_amount_and_duplicate_guard", "draft_wages_block_allocation_cancel"])
        frappe.set_user(original_user)
        blocked(lambda: frappe.get_doc("Job Card", card.name).cancel())
        modified_source = frappe.get_doc("Job Card", card.name)
        modified_source.time_logs[0].to_time = today() + " 10:30:00"
        blocked(modified_source.save)
        checks.append("approved_allocation_holds_source_before_wage_approval")
        for allow, other in (("Job Card", spare_card.name), ("Work Order", spare_card.work_order),
                             ("Item", raw.name), ("Jifeng Flow Order", spare_card.jf_flow_order)):
            save_point = "jf_walloc_hidden_source"
            frappe.db.savepoint(save_point)
            try:
                frappe.get_doc({"doctype": "User Permission", "user": manager.name, "allow": allow,
                                "for_value": other, "apply_to_all_doctypes": 1}).insert()
                frappe.clear_cache(user=manager.name)
                frappe.set_user(manager.name)
                blocked(lambda: get_allocation_source(card.name), frappe.PermissionError)
            finally:
                frappe.set_user(original_user)
                frappe.db.rollback(save_point=save_point)
                frappe.clear_cache(user=manager.name)
        checks.append("hidden_source_documents_do_not_yield_partial_shares")
        frappe.set_user("Guest")
        blocked(lambda: get_allocation_source(card.name), frappe.PermissionError)
        frappe.set_user(manager.name)

        def standalone(source, qty):
            return frappe.get_doc({"doctype": "Jifeng Wage Entry", "company": company,
                                   "employee": employees[0].name, "operation": source.operation,
                                   "job_card": source.name, "work_date": today(), "payroll_date": today(),
                                   "salary_component": component.name, "quantity": qty,
                                   "piece_rate": 2, "hours": 0, "hourly_rate": 0, "extra_amount": 0}).insert()

        blocked(lambda: standalone(card, 1))
        legacy = standalone(single_card, 2)
        legacy.submit()
        second = standalone(single_card, 3)
        blocked(lambda: standalone(single_card, "0.000001"))
        blocked(lambda: allocation(single_card, shares=(5,), rates=(2,)))
        legacy.cancel()
        released = standalone(single_card, 2)
        frappe.delete_doc("Jifeng Wage Entry", released.name)
        frappe.delete_doc("Jifeng Wage Entry", second.name)
        checks.extend(["single_employee_legacy_path_preserved", "single_employee_total_cap_and_cancel_release",
                       "allocation_excludes_independent_piece_wages", "manager_deletes_wage_drafts"])
        # E-2 did not participate on the spare card: a zero share still needs source participation.
        blocked(lambda: allocation(spare_card, shares=(5, 0), rates=(2, 0)))
        spare = allocation(spare_card, shares=(5,), rates=(2,))
        frappe.delete_doc("Jifeng Wage Allocation", spare.name)
        assert _ledger_snapshot() == ledger_before
        checks.append("allocation_and_wages_do_not_post_stock_or_gl")

        if payroll:
            _payroll_chain(company, currency, employees[:2], wages, component, manager,
                           accountant, suffix, first, last, checks, original_user)
        else:
            for wage in wages:
                wage.submit()
                wage.cancel()
        frappe.set_user(manager.name)
        draft.reload()
        draft.cancel()
        assert all(frappe.get_doc("Jifeng Wage Entry", wage.name).docstatus == 2 for wage in wages)
        replacement = allocation(shares=(5, 0), rates=(2, 0))
        replacement.submit()
        assert len(replacement.make_wages()) == 1
        for name in replacement.make_wages():
            frappe.delete_doc("Jifeng Wage Entry", name)
        replacement.cancel()
        checks.extend(["cancelled_allocation_preserves_wage_history_and_releases_source",
                       "zero_share_zero_amount_kept_without_wage", "manager_can_reverse_drafts_and_allocation"])
        frappe.set_user(original_user)
        frappe.get_doc("Job Card", card.name).cancel()
        checks.extend(["source_can_cancel_after_payroll_reversal", "guest_denied"])
        return {"passed": True, "checks": checks, "payroll_chain": bool(payroll),
                "business_data": "rolled_back", "concurrency": "source locks and current reads; not raced"}
    finally:
        frappe.set_user(original_user)
        _end_rollback_only(point)
        frappe.in_test = original_in_test
        frappe.flags.enqueue_payroll_entry = original_enqueue
        frappe.flags.via_payroll_entry = original_via_payroll
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        frappe.clear_document_cache("Payroll Settings", "Payroll Settings")
        for user in users:
            frappe.clear_cache(user=user)


def _payroll_chain(company, currency, employees, wages, component, manager, accountant,
                   suffix, first, last, checks, original_user):
    """Use synthetic non-taxable earnings, native accrual and an accountant's payment approval."""
    frappe.set_user(original_user)
    expense = frappe.db.get_value("Account", {"company": company, "is_group": 0, "disabled": 0,
                                              "root_type": "Expense", "account_currency": currency}, "name")
    payable = frappe.db.get_value("Account", {"company": company, "is_group": 0, "disabled": 0,
                                              "account_type": "Payable", "account_currency": currency}, "name")
    payment = frappe.db.get_value("Account", {"company": company, "is_group": 0, "disabled": 0,
                                              "account_type": ["in", ["Cash", "Bank"]],
                                              "account_currency": currency}, "name")
    cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
    if not all((expense, payable, payment, cost_center)):
        frappe.throw("完整工资验收需要公司本币的费用、应付、现金或银行明细科目及明细成本中心")
    payroll_settings = frappe.get_single("Payroll Settings")
    payroll_settings.email_salary_slip_to_employee = 0
    payroll_settings.process_payroll_accounting_entry_based_on_employee = 0
    payroll_settings.save()
    basic = frappe.get_doc({"doctype": "Salary Component", "salary_component": "JF-BASIC-" + suffix,
                             "salary_component_abbr": "JFB" + suffix, "type": "Earning",
                             "is_tax_applicable": 0, "depends_on_payment_days": 0,
                             "accounts": [{"company": company, "account": expense}]}).insert()
    component.append("accounts", {"company": company, "account": expense})
    component.save()
    structure = frappe.get_doc({"doctype": "Salary Structure", "name": "JF-WALLOC-SS-" + suffix,
                                 "company": company, "currency": currency, "is_active": "Yes",
                                 "payroll_frequency": "Monthly", "earnings": [{
                                     "salary_component": basic.name, "amount": 1000,
                                     "depends_on_payment_days": 0}]}).insert()
    structure.submit()
    holiday = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": "JF-WALLOC-HOL-" + suffix,
                               "from_date": first, "to_date": last, "holidays": []}).insert()
    for employee in employees:
        assignment = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": employee.name,
                                      "salary_structure": structure.name, "company": company,
                                      "currency": currency, "from_date": first,
                                      "payroll_payable_account": payable}).insert()
        assignment.submit()
        holiday_assignment = frappe.get_doc({"doctype": "Holiday List Assignment",
                                              "naming_series": "HR-HLA-.YYYY.-", "holiday_list": holiday.name,
                                              "applicable_for": "Employee", "assigned_to": employee.name,
                                              "from_date": first}).insert()
        holiday_assignment.submit()
    frappe.set_user(manager.name)
    salaries = []
    for wage in wages:
        wage.reload()
        wage.submit()
        salary = frappe.get_doc("Additional Salary", wage.make_additional_salary())
        assert salary.name == wage.make_additional_salary() and salary.docstatus == 0
        salary.submit()
        salaries.append(salary)
    checks.append("two_employee_wages_to_submitted_additional_salary")
    from jifeng_erp.jifeng_erp.report.jifeng_wage_analysis.jifeng_wage_analysis import execute

    before_report = _ledger_snapshot()
    wage_report = execute({"company": company, "from_date": today(), "to_date": today(),
                           "salary_component": component.name})[1]
    assert sum((_amount(row["total_amount"]) for row in wage_report), Decimal(0)) == Decimal("28.00")
    assert _ledger_snapshot() == before_report
    checks.append("hr_manager_wage_report_preserves_all_stock_and_gl_content")
    payroll_entry = frappe.get_doc({"doctype": "Payroll Entry", "company": company,
                                    "posting_date": today(), "start_date": first, "end_date": last,
                                    "payroll_frequency": "Monthly", "currency": currency, "exchange_rate": 1,
                                    "cost_center": cost_center, "payroll_payable_account": payable,
                                    "payment_account": payment, "validate_attendance": 0,
                                    "employees": [{"employee": employee.name} for employee in employees]}).insert()
    payroll_entry.submit()
    slips = [frappe.get_doc("Salary Slip", name) for name in frappe.get_all(
        "Salary Slip", filters={"payroll_entry": payroll_entry.name}, pluck="name")]
    assert len(slips) == 2
    salary_by_employee = {salary.employee: salary for salary in salaries}
    for slip in slips:
        salary = salary_by_employee[slip.employee]
        assert any(row.additional_salary == salary.name and _amount(row.amount) == _amount(salary.amount)
                   for row in slip.earnings)
        assert _amount(slip.net_pay) == Decimal(1000) + _amount(salary.amount)
    total = sum((_amount(slip.net_pay) for slip in slips), Decimal(0))
    assert total == Decimal("2028.00")
    payroll_entry.submit_salary_slips()
    for slip in slips:
        slip.reload()
        assert slip.docstatus == 1 and slip.journal_entry
    accrual_names = {slip.journal_entry for slip in slips}
    assert len(accrual_names) == 1
    accrual = frappe.get_doc("Journal Entry", next(iter(accrual_names)))
    assert accrual.docstatus == 1
    accrued = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry", "voucher_no": accrual.name,
                                                 "is_cancelled": 0}, fields=["account", "debit", "credit"])
    assert sum((_amount(row.debit) for row in accrued if row.account == expense), Decimal(0)) == total
    assert sum((_amount(row.credit) for row in accrued if row.account == payable), Decimal(0)) == total
    checks.extend(["salary_slips_keep_each_additional_salary_source", "native_payroll_accrual_posted_once"])
    payroll_entry.submit_salary_slips()
    assert {frappe.get_doc("Salary Slip", slip.name).journal_entry for slip in slips} == accrual_names
    bank = payroll_entry.make_bank_entry()
    assert bank.docstatus == 0
    frappe.set_user(accountant.name)
    bank = frappe.get_doc("Journal Entry", bank.name)
    bank.check_permission("submit")
    bank.submit()
    paid = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry", "voucher_no": bank.name,
                                              "is_cancelled": 0}, fields=["account", "debit", "credit"])
    assert sum((_amount(row.debit) for row in paid if row.account == payable), Decimal(0)) == total
    assert sum((_amount(row.credit) for row in paid if row.account == payment), Decimal(0)) == total
    checks.append("accountant_submits_native_cash_or_bank_payment")
    frappe.set_user(manager.name)
    for wage in wages:
        point = "jf_walloc_wage_cancel_blocked"
        frappe.db.savepoint(point)
        try:
            frappe.get_doc("Jifeng Wage Entry", wage.name).cancel()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Active Additional Salary must block cancelling its wage")
        finally:
            frappe.db.rollback(save_point=point)
    payroll_entry.cancel()
    assert not frappe.get_all("Salary Slip", filters={"payroll_entry": payroll_entry.name})
    assert all(frappe.get_doc("Journal Entry", name).docstatus == 2 for name in accrual_names | {bank.name})
    remaining_gl = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry",
                                 "voucher_no": ["in", list(accrual_names | {bank.name})], "is_cancelled": 0},
                                 fields=["debit", "credit"])
    assert sum((_amount(row.debit) for row in remaining_gl), Decimal(0)) == 0
    assert sum((_amount(row.credit) for row in remaining_gl), Decimal(0)) == 0
    for salary in salaries:
        salary.reload()
        salary.cancel()
    for wage in wages:
        wage.reload()
        wage.cancel()
    checks.extend(["payroll_reversal_cancels_linked_native_financial_entries",
                   "hr_manager_cancels_additional_salary_before_wage"])
