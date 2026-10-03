"""Quote integrity, approved snapshots and native allocation propagation."""

import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1] / "jifeng_erp"


class Doc(types.SimpleNamespace):
    pass


class TestWageRateReferences(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Doc(user="Administrator")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda msg, *args: (_ for _ in ()).throw(ValueError(msg))
        self.resolve = Mock()
        modules = {"frappe": self.frappe, "jifeng_erp.wage_rates": Doc(resolve_reference=self.resolve)}
        scope = patch.dict(sys.modules, modules)
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("wage_rate_references_test", ROOT / "wage_rate_references.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.wage = Doc(company="C1", employee="E1", operation="O1", work_date="2026-10-01",
                        stock_uom="Nos", job_card="J1", quantity=2, piece_rate=3,
                        piece_rate_reference="H1", piece_rate_reference_version="2026-09-01 09:00:00.000001",
                        docstatus=0, work_order="WO1", flow_order="F1",
                        wage_allocation=None, wage_allocation_employee=None)

    def test_draft_and_submit_recheck_locked_history(self):
        for status in (0, 1):
            self.wage.docstatus = status
            self.module.validate_wage_reference(self.wage)
            self.resolve.assert_called_with(self.wage, "H1", "2026-09-01 09:00:00.000001", 3, for_update=True)
        self.assertEqual(self.resolve.call_count, 2)

    def test_blank_reference_retains_manual_price_without_reading_history(self):
        self.wage.piece_rate_reference = ""
        self.wage.piece_rate_reference_version = None
        self.module.validate_wage_reference(self.wage)
        self.assertEqual(self.wage.piece_rate, 3)
        self.assertIsNone(self.wage.piece_rate_reference)
        self.resolve.assert_not_called()

    def test_missing_invalid_or_oversized_reference_rejected(self):
        for field, values in (("piece_rate_reference", (None, "", " H1", "H1 ", True, "x" * 141)),
                              ("piece_rate_reference_version", (None, "", True, "2026-09-01", "2026-09-01 09:00:00Z"))):
            for value in values:
                with self.subTest(field=field, value=value):
                    wage = copy.deepcopy(self.wage)
                    setattr(wage, field, value)
                    with self.assertRaisesRegex(ValueError, "完整"):
                        self.module.validate_wage_reference(wage)
        self.resolve.assert_not_called()

    def test_quote_requires_positive_quantity_but_allows_zero_price(self):
        self.wage.quantity = 0
        with self.assertRaisesRegex(ValueError, "计件数量"):
            self.module.validate_wage_reference(self.wage)
        self.wage.quantity, self.wage.piece_rate = 2, 0
        self.module.validate_wage_reference(self.wage)
        self.resolve.assert_called_once()

    def test_rejects_nonfinite_and_negative_amounts(self):
        for field in ("quantity", "piece_rate"):
            for value in ("NaN", "Infinity", -1, True):
                wage = copy.deepcopy(self.wage)
                setattr(wage, field, value)
                with self.assertRaises(ValueError):
                    self.module.validate_wage_reference(wage)
        self.resolve.assert_not_called()

    def approved(self):
        before = copy.deepcopy(self.wage)
        before.docstatus = self.wage.docstatus = 1
        self.wage.get_doc_before_save = lambda: before
        return before

    def test_approved_quote_survives_original_cancel_or_permission_change(self):
        self.approved()
        self.resolve.side_effect = PermissionError("original hidden or cancelled")
        for status in (1, 2):
            self.wage.docstatus = status
            self.module.validate_wage_reference(self.wage)
        self.resolve.assert_not_called()

    def test_approved_quote_cannot_be_added_removed_or_scope_changed(self):
        before = self.approved()
        for field in self.module.QUOTE_FIELDS:
            old = getattr(self.wage, field)
            setattr(self.wage, field, "OTHER" if field not in ("quantity", "piece_rate") else 7)
            with self.assertRaisesRegex(ValueError, "不能修改"):
                self.module.validate_wage_reference(self.wage)
            setattr(self.wage, field, old)
        self.wage.piece_rate_reference = ""
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.validate_wage_reference(self.wage)
        before.piece_rate_reference = None
        before.piece_rate_reference_version = None
        self.wage.piece_rate_reference = "H1"
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.validate_wage_reference(self.wage)
        self.resolve.assert_not_called()

    def test_approved_allocation_is_authority_for_exact_generated_quote(self):
        # Called only after validate_wage_source has matched all wage fields.
        self.wage.wage_allocation = "A1"
        self.module.validate_wage_reference(self.wage)
        self.resolve.assert_not_called()

    def allocation(self):
        parent = Doc(name="A1", company="C1", operation="O1", work_date="2026-10-01",
                     stock_uom="Nos", job_card="J1", work_order="WO1", flow_order="F1", docstatus=0)
        row = copy.deepcopy(self.wage)
        row.name = "R1"
        parent.employees = [row]
        return parent, row

    def test_new_allocation_row_checks_history_with_actual_wage_context(self):
        parent, row = self.allocation()
        values = Mock(return_value={key: getattr(self.wage, key) for key in self.module.QUOTE_FIELDS})
        self.frappe.get_doc = Mock(side_effect=lambda value: Doc(**value))
        with patch.dict(sys.modules, {"jifeng_erp.wage_allocation": Doc(wage_values=values)}):
            self.module.validate_allocation_reference(parent, row)
        target = self.resolve.call_args.args[0]
        self.assertEqual(target.doctype, "Jifeng Wage Entry")
        self.assertEqual(target.employee, row.employee)
        self.assertEqual(target.job_card, parent.job_card)
        self.assertEqual(self.resolve.call_args.kwargs, {"for_update": True})

    def test_approved_allocation_preserves_only_exact_employee_row_and_parent_context(self):
        parent, row = self.allocation()
        parent.docstatus = 1
        before = copy.deepcopy(parent)
        self.module.validate_allocation_reference(parent, row, before)
        row.piece_rate_reference_version = "2026-09-02 09:00:00"
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.validate_allocation_reference(parent, row, before)
        row.piece_rate_reference_version = before.employees[0].piece_rate_reference_version
        parent.stock_uom = "Kg"
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.validate_allocation_reference(parent, row, before)
        self.resolve.assert_not_called()

    def test_cancel_guard_does_not_depend_on_validate_or_original_source(self):
        self.approved()
        self.wage.docstatus = 2
        self.assertTrue(self.module.guard_approved_wage_quote(self.wage))
        self.wage.piece_rate = 4
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.guard_approved_wage_quote(self.wage)
        self.resolve.assert_not_called()

    def test_allocation_cancel_rejects_removed_replaced_or_new_cited_rows(self):
        parent, row = self.allocation()
        parent.docstatus = 1
        before = copy.deepcopy(parent)
        parent.docstatus = 2
        parent.get_doc_before_save = lambda: before
        self.module.guard_approved_allocation_quotes(parent)
        parent.employees = []
        with self.assertRaisesRegex(ValueError, "删除或替换"):
            self.module.guard_approved_allocation_quotes(parent)
        row.name = "R2"
        parent.employees = [row]
        with self.assertRaisesRegex(ValueError, "删除或替换"):
            self.module.guard_approved_allocation_quotes(parent)
        parent.employees = [copy.deepcopy(before.employees[0]), row]
        with self.assertRaisesRegex(ValueError, "增加"):
            self.module.guard_approved_allocation_quotes(parent)
        self.resolve.assert_not_called()

    def test_cancelled_wage_financial_snapshot_protects_manual_and_quoted_records(self):
        for cited in (True, False):
            current = copy.deepcopy(self.wage)
            if not cited:
                current.piece_rate_reference = current.piece_rate_reference_version = None
            current.hours, current.hourly_rate, current.extra_amount = 1, 4, 2
            current.piece_amount, current.time_amount, current.total_amount = 6, 4, 12
            current.payroll_date, current.salary_component = "2026-10-01", "PAY"
            current.docstatus = 1
            before = copy.deepcopy(current)
            current.docstatus = 2
            current.get_doc_before_save = lambda: before
            self.module.guard_cancelled_wage_snapshot(current)
            for field in self.module.CANCEL_WAGE_FIELDS:
                old = getattr(current, field, None)
                setattr(current, field, 99 if field in self.module.CANCEL_NUMBERS | self.module.CANCEL_CHECKS else "ALTERED")
                with self.subTest(cited=cited, field=field), self.assertRaisesRegex(ValueError, "取消单据不能修改"):
                    self.module.guard_cancelled_wage_snapshot(current)
                setattr(current, field, old)
        self.resolve.assert_not_called()

    def test_cancelled_allocation_preserves_uncited_rows_financial_values_and_identity(self):
        parent, row = self.allocation()
        row.piece_rate_reference = row.piece_rate_reference_version = None
        parent.allocated_qty, parent.total_amount = 2, 6
        parent.payroll_date, parent.salary_component = "2026-10-01", "PAY"
        parent.docstatus = 1
        before = copy.deepcopy(parent)
        parent.docstatus = 2
        parent.get_doc_before_save = lambda: before
        source = ("company", "job_card", "work_order", "flow_order", "operation", "stock_uom", "work_date")
        with patch.dict(sys.modules, {"jifeng_erp.wage_allocation": Doc(SOURCE_FIELDS=source)}):
            self.module.guard_cancelled_allocation_snapshot(parent)
            for field in ("total_amount", "allocated_qty", "payroll_date", "salary_component", "flow_order", "amended_from"):
                old = getattr(parent, field, None)
                setattr(parent, field, 99 if field in self.module.CANCEL_NUMBERS else "ALTERED")
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "取消单据不能修改"):
                    self.module.guard_cancelled_allocation_snapshot(parent)
                setattr(parent, field, old)
            for field in self.module.CANCEL_ROW_FIELDS:
                if field == "name":
                    continue
                old = getattr(row, field, None)
                setattr(row, field, 99 if field in self.module.CANCEL_NUMBERS | self.module.CANCEL_CHECKS else "ALTERED")
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "取消单据不能修改"):
                    self.module.guard_cancelled_allocation_snapshot(parent)
                setattr(row, field, old)
            parent.employees = []
            with self.assertRaisesRegex(ValueError, "员工行"):
                self.module.guard_cancelled_allocation_snapshot(parent)
            parent.employees = [row, copy.deepcopy(row)]
            with self.assertRaisesRegex(ValueError, "员工行"):
                self.module.guard_cancelled_allocation_snapshot(parent)
        self.resolve.assert_not_called()

    def test_cancelled_check_snapshots_accept_equivalent_native_bool(self):
        self.approved()
        before = self.wage.get_doc_before_save()
        before.piece_worker_qualified = 1
        self.wage.piece_worker_qualified = True
        self.wage.docstatus = 2
        self.module.guard_cancelled_wage_snapshot(self.wage)

    def test_hidden_target_price_or_reference_fields_deny_draft(self):
        self.frappe.session.user = "HR"
        fields = ["piece_rate", *self.module.REFERENCE_FIELDS]
        meta = Doc(get_permitted_fieldnames=Mock(return_value=fields),
                   get_permlevel_access=Mock(return_value=[0]), get_field=Mock(return_value=None))
        self.wage.meta = meta
        meta.get_permitted_fieldnames.return_value = fields[:-1]
        with self.assertRaisesRegex(ValueError, "权限"):
            self.module.validate_wage_reference(self.wage)
        meta.get_permitted_fieldnames.return_value = fields
        masked = {"mask": 1, "permlevel": 1}
        meta.get_field = lambda _: Doc(get=masked.get, permlevel=1)
        with self.assertRaisesRegex(ValueError, "权限"):
            self.module.validate_wage_reference(self.wage)
        self.resolve.assert_not_called()

    def test_child_mask_uses_parent_native_permissions(self):
        self.frappe.session.user = "HR"
        parent, row = self.allocation()
        fields = ["piece_rate", *self.module.REFERENCE_FIELDS]
        meta = Doc(get_permitted_fieldnames=Mock(return_value=fields),
                   get_permlevel_access=Mock(return_value=[0]), get_field=Mock(return_value=None))
        table = Doc(fieldtype="Table", options="Jifeng Wage Allocation Employee", permlevel=1)
        parent.meta = Doc(get_field=Mock(return_value=table), get_permlevel_access=Mock(return_value=[0]))
        self.frappe.get_meta = Mock(return_value=meta)
        with self.assertRaisesRegex(ValueError, "员工分配"):
            self.module.validate_allocation_reference(parent, row)
        meta.get_permlevel_access.assert_called_with(permission_type="mask", parenttype="Jifeng Wage Allocation", user="HR")
        self.resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
