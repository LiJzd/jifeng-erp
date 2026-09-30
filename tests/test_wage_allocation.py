"""Employee shares and wage provenance; native database validation lives in the smoke helper."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1] / "jifeng_erp"


class Doc(types.SimpleNamespace):
    def check_permission(self, permission):
        if getattr(self, "hidden", False):
            raise PermissionError("hidden source")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWageAllocation(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        frappe.whitelist = lambda: (lambda function: function)
        frappe.only_for = Mock()
        frappe.db = types.SimpleNamespace(sql=Mock(return_value=[]), get_value=Mock(return_value="CNY"))
        self.db = frappe.db
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        scope = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils})
        scope.start()
        self.addCleanup(scope.stop)
        payroll = load_module("allocation_payroll", ROOT / "payroll.py")
        payroll_scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        payroll_scope.start()
        self.addCleanup(payroll_scope.stop)
        self.module = load_module("allocation_test_module", ROOT / "wage_allocation.py")
        self.card = Doc(name="JC-1", company="C-1", work_order="WO-1", operation="OP-1",
                        operation_id="WO-OP-1", production_item="ITEM-1", stock_uom="Nos",
                        posting_date="2026-09-29", total_completed_qty=5, docstatus=1,
                        is_corrective_job_card=0, sub_operations=[], time_logs=[
                            Doc(name="L-1", employee="E-1", from_time="2026-09-29 09:00:00",
                                to_time="2026-09-29 10:00:00", time_in_mins=60, completed_qty=0),
                            Doc(name="L-2", employee="E-2", from_time="2026-09-29 09:00:00",
                                to_time="2026-09-29 11:00:00", time_in_mins=120, completed_qty=5),
                        ])
        self.work = Doc(name="WO-1", company="C-1", docstatus=1, jf_flow_order="FLOW-1",
                        operations=[Doc(name="WO-OP-1", operation="OP-1")])
        self.docs = {("Job Card", "JC-1"): self.card, ("Work Order", "WO-1"): self.work,
                     ("Jifeng Flow Order", "FLOW-1"): Doc(company="C-1"),
                     ("Item", "ITEM-1"): Doc(stock_uom="Nos"),
                     ("Salary Component", "PIECE"): Doc(type="Earning", disabled=0),
                     ("Employee", "E-1"): Doc(company="C-1", status="Active", employee_name="甲"),
                     ("Employee", "E-2"): Doc(company="C-1", status="Active", employee_name="乙")}
        self.get_doc = Mock(side_effect=lambda doctype, name, **kwargs: self.docs[(doctype, name)])
        frappe.get_doc = self.get_doc
        self.frappe = frappe
        self.allocation = Doc(name="A-1", job_card="JC-1", docstatus=0,
                              payroll_date="2026-09-30", salary_component="PIECE", employees=[
                                  Doc(name="R-1", employee="E-1", quantity=2, piece_rate=3,
                                      include_time_pay=0, hourly_rate=10, extra_amount=0, db_update=Mock()),
                                  Doc(name="R-2", employee="E-2", quantity=3, piece_rate=4,
                                      include_time_pay=0, hourly_rate=20, extra_amount=0, db_update=Mock()),
                              ])
        self.docs[("Jifeng Wage Allocation", "A-1")] = self.allocation

    def approve(self):
        self.allocation.docstatus = 1
        self.module.validate_allocation(self.allocation)

    def wage(self, index=0):
        self.approve()
        return Doc(name="W-1", **self.module.wage_values(self.allocation, self.allocation.employees[index]))

    def test_team_output_on_last_native_employee_does_not_set_personal_shares(self):
        self.approve()
        self.assertEqual([row.quantity for row in self.allocation.employees], [Decimal(2), Decimal(3)])
        self.assertEqual([row.hours for row in self.allocation.employees], [Decimal(1), Decimal(2)])
        self.assertEqual(self.allocation.allocated_qty, 5)
        self.assertEqual(self.allocation.total_amount, Decimal("18.00"))
        self.assertEqual([row.wage_hours for row in self.allocation.employees], [0, 0])

    def test_time_pay_requires_explicit_choice_and_uses_own_closed_hours(self):
        self.allocation.employees[0].include_time_pay = 1
        wage = self.wage()
        self.assertEqual(wage.hours, Decimal(1))
        self.assertEqual(wage.time_amount, Decimal("10.00"))
        self.assertEqual(self.allocation.employees[1].time_amount, Decimal("0.00"))

    def test_approval_rejects_under_and_over_shares_but_draft_can_be_incomplete(self):
        self.allocation.employees[0].quantity = 1
        self.module.validate_allocation(self.allocation)
        with self.assertRaisesRegex(ValueError, "合计必须等于"):
            self.approve()
        self.allocation.employees[0].quantity = 3
        with self.assertRaisesRegex(ValueError, "合计必须等于"):
            self.approve()

    def test_duplicate_employee_is_rejected(self):
        self.allocation.employees[1].employee = "E-1"
        with self.assertRaisesRegex(ValueError, "只能填写一行"):
            self.approve()

    def test_unreported_employee_is_rejected(self):
        self.allocation.employees[1].employee = "E-3"
        with self.assertRaisesRegex(ValueError, "时间记录"):
            self.approve()

    def test_wrong_company_or_inactive_employee_is_rejected(self):
        self.docs[("Employee", "E-1")].company = "C-2"
        with self.assertRaisesRegex(ValueError, "在职员工"):
            self.approve()
        self.docs[("Employee", "E-1")].company = "C-1"
        self.docs[("Employee", "E-1")].status = "Left"
        with self.assertRaisesRegex(ValueError, "在职员工"):
            self.approve()

    def test_allocation_uses_full_document_read_for_each_source(self):
        for key in self.docs:
            if key[0] == "Jifeng Wage Allocation":
                continue
            with self.subTest(source=key):
                self.docs[key].hidden = True
                with self.assertRaises(PermissionError):
                    self.approve()
                self.docs[key].hidden = False

    def test_corrective_and_draft_cards_cannot_be_allocated(self):
        self.card.is_corrective_job_card = 1
        with self.assertRaisesRegex(ValueError, "纠正报工"):
            self.approve()
        self.card.is_corrective_job_card = 0
        self.card.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.approve()

    def test_source_operation_and_stock_unit_must_match(self):
        self.card.operation_id = "WRONG-ROW"
        with self.assertRaisesRegex(ValueError, "工序行"):
            self.approve()
        self.card.operation_id = "WO-OP-1"
        self.card.stock_uom = "Kg"
        with self.assertRaisesRegex(ValueError, "库存单位"):
            self.approve()

    def test_forged_source_snapshot_is_rejected(self):
        self.allocation.source_completed_qty = 999
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.approve()

    def test_existing_active_allocation_blocks_draft_and_is_a_current_read(self):
        self.db.sql.return_value = [Doc(name="A-OTHER", docstatus=0)]
        with self.assertRaisesRegex(ValueError, "重复分配"):
            self.module.validate_allocation(self.allocation)
        query, params = self.db.sql.call_args.args
        self.assertIn("FOR UPDATE", query)
        self.assertEqual(params, ("JC-1", "A-1"))
        self.assertIn(unittest.mock.call("Job Card", "JC-1", for_update=True), self.get_doc.mock_calls)

    def test_independent_piece_wage_blocks_allocation(self):
        self.db.sql.side_effect = [[], [Doc(name="W-OLD", quantity=1, wage_allocation=None)]]
        with self.assertRaisesRegex(ValueError, "独立计件记录"):
            self.approve()

    def test_missing_actual_hours_cannot_be_paid_as_timing(self):
        self.card.time_logs[0].to_time = None
        self.module.validate_allocation(self.allocation)
        self.assertEqual(self.allocation.employees[0].source_hours_complete, 0)
        self.allocation.employees[0].include_time_pay = 1
        with self.assertRaisesRegex(ValueError, "完整且不重叠"):
            self.approve()

    def test_overlapping_personal_intervals_cannot_be_paid_twice(self):
        self.card.time_logs.append(Doc(name="L-3", employee="E-1",
                                      from_time="2026-09-29 09:30:00", to_time="2026-09-29 10:30:00"))
        self.allocation.employees[0].include_time_pay = 1
        with self.assertRaisesRegex(ValueError, "完整且不重叠"):
            self.approve()

    def test_negative_nonfinite_or_excess_precision_shares_are_rejected(self):
        for value in (-1, "NaN", "Infinity", "0.0000001"):
            self.allocation.employees[0].quantity = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.approve()

    def test_rates_are_explicit_and_not_loaded_from_manufacturing_costs(self):
        self.allocation.employees[0].piece_rate = None
        with self.assertRaisesRegex(ValueError, "主管填写计件工价"):
            self.approve()
        self.allocation.employees[0].piece_rate = "0.017"
        self.approve()
        self.assertEqual(self.allocation.employees[0].piece_amount, Decimal("0.03"))

    def test_zero_share_and_zero_amount_are_valid_without_a_wage(self):
        self.allocation.employees[0].quantity = 0
        self.allocation.employees[0].piece_rate = None
        self.allocation.employees[1].quantity = 5
        self.approve()
        with self.assertRaisesRegex(ValueError, "零金额"):
            self.module.validate_allocated_wage(Doc(name="W-ZERO", **self.module.wage_values(
                self.allocation, self.allocation.employees[0])))

    def test_wage_must_match_every_allocated_amount_and_source_field(self):
        wage = self.wage()
        self.module.validate_allocated_wage(wage)
        for field in self.module.WAGE_FIELDS:
            if field in ("wage_allocation", "wage_allocation_employee"):
                continue
            original = getattr(wage, field)
            setattr(wage, field, Decimal(99) if field in self.module.NUMERIC_WAGE_FIELDS else "WRONG")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "必须与已审核工资分配"):
                self.module.validate_allocated_wage(wage)
            setattr(wage, field, original)

    def test_wage_needs_exact_submitted_allocation_employee_row(self):
        wage = self.wage()
        wage.wage_allocation_employee = "OTHER-ROW"
        with self.assertRaisesRegex(ValueError, "准确的工资分配员工行"):
            self.module.validate_allocated_wage(wage)
        wage.wage_allocation_employee = "R-1"
        self.allocation.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已审核的工资分配"):
            self.module.validate_allocated_wage(wage)

    def test_duplicate_wage_for_same_allocation_employee_is_rejected(self):
        wage = self.wage()
        self.db.sql.return_value = [("W-EXISTING",)]
        with self.assertRaisesRegex(ValueError, "重复生成"):
            self.module.validate_allocated_wage(wage)
        self.assertIn("FOR UPDATE", self.db.sql.call_args.args[0])

    def test_allocated_wage_cannot_unlink_parent_to_become_manual(self):
        wage = self.wage()
        wage.get_doc_before_save = lambda: Doc(wage_allocation="A-1", wage_allocation_employee="R-1")
        wage.wage_allocation = None
        wage.job_card = None
        with self.assertRaisesRegex(ValueError, "不能移除或替换"):
            self.module.validate_wage_source(wage)

    def test_single_employee_path_counts_existing_drafts_and_submitted_piece_shares(self):
        self.card.time_logs = self.card.time_logs[:1]
        wage = Doc(name="W-NEW", job_card="JC-1", quantity=3)
        self.db.sql.side_effect = [[], [Doc(quantity=2)]]
        self.module.validate_wage_source(wage)
        self.db.sql.side_effect = [[], [Doc(quantity=Decimal("2.000001"))]]
        with self.assertRaisesRegex(ValueError, "不能超过"):
            self.module.validate_wage_source(wage)

    def test_multiple_employee_piece_wage_requires_allocation(self):
        with self.assertRaisesRegex(ValueError, "多人报工"):
            self.module.validate_wage_source(Doc(job_card="JC-1", quantity=1))

    def test_source_edit_and_cancel_are_blocked_while_approved_allocation_exists(self):
        self.db.sql.return_value = [Doc(name="A-1", docstatus=1)]
        self.module.guard_job_card_allocation(self.card, "before_update_after_submit")
        with self.assertRaisesRegex(ValueError, "请先取消工资及分配"):
            self.module.guard_job_card_allocation(self.card, "before_cancel")
        modified = Doc(**vars(self.card))
        modified.total_completed_qty = 6
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            self.module.guard_job_card_allocation(modified, "before_update_after_submit")

    def test_allocation_cancel_blocks_any_active_wage_and_preserves_history(self):
        self.db.sql.return_value = [("W-DRAFT",)]
        with self.assertRaisesRegex(ValueError, "取消计薪记录"):
            self.module.validate_allocation_cancel(self.allocation)
        self.db.sql.return_value = []
        self.module.validate_allocation_cancel(self.allocation)

    def test_cancelled_source_cannot_support_later_wage_generation(self):
        wage = self.wage()
        self.card.docstatus = 2
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.module.validate_allocated_wage(wage)

    def test_currency_is_fixed_to_the_authorized_source_company(self):
        self.db.get_value.return_value = "USD"
        self.approve()
        self.assertEqual(self.allocation.currency, "USD")
        self.allocation.currency = "CNY"
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.approve()

    def test_approved_legacy_wage_also_holds_quantity_and_time_source(self):
        self.db.sql.side_effect = [[], [Doc(name="W-LEGACY", docstatus=1)]]
        modified = Doc(**vars(self.card))
        modified.total_completed_qty = 4
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            self.module.guard_job_card_allocation(modified, "before_update_after_submit")

    def test_allocated_wage_lock_order_is_source_then_parent(self):
        wage = self.wage()
        self.get_doc.reset_mock()
        self.module.validate_allocated_wage(wage)
        locked = [call.args[0] for call in self.get_doc.mock_calls if call.kwargs.get("for_update")]
        self.assertEqual(locked, ["Job Card", "Jifeng Wage Allocation"])

    def test_generation_reuses_exact_existing_employee_wages(self):
        self.approve()
        existing = [Doc(name=f"W-{idx}", **self.module.wage_values(self.allocation, row))
                    for idx, row in enumerate(self.allocation.employees)]
        for wage in existing:
            self.docs[("Jifeng Wage Entry", wage.name)] = wage
        def results(query, params, **kwargs):
            if "SELECT name FROM `tabJifeng Wage Entry`" in query:
                index = [row.name for row in self.allocation.employees].index(params[1])
                return [Doc(name=existing[index].name)]
            return []
        self.db.sql.side_effect = results
        self.assertEqual(self.module.generate_wages(self.allocation), [wage.name for wage in existing])
        self.assertEqual(self.module.generate_wages(self.allocation), [wage.name for wage in existing])
        self.assertEqual([row.wage_entry for row in self.allocation.employees], [wage.name for wage in existing])


if __name__ == "__main__":
    unittest.main()
