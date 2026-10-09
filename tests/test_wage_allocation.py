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
    def get(self, name, default=None):
        return getattr(self, name, default)

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
        frappe.throw = lambda message, exc=ValueError, *args: (_ for _ in ()).throw(exc(message))
        frappe.whitelist = lambda: (lambda function: function)
        frappe.only_for = Mock()
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.has_permission = Mock(return_value=True)
        frappe.PermissionError = PermissionError
        frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.db = types.SimpleNamespace(sql=Mock(return_value=[]), get_value=Mock(return_value="CNY"))
        frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: (
            Doc(name="SUP-1", wage_method="计件", modified="2026-10-01 08:00:00")
            if doctype == "Jifeng Employee Supplement" else self.db.get_value.return_value)
        self.db = frappe.db
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        source_fields = {
            "Job Card": ("company", "work_order", "operation", "operation_id", "production_item", "finished_good",
                         "stock_uom", "posting_date", "total_completed_qty", "is_corrective_job_card", "time_logs", "sub_operations",
                         "for_quantity", "process_loss_qty", "pending_qty"),
            "Job Card Time Log": ("employee", "from_time", "to_time", "time_in_mins", "completed_qty", "operation"),
            "Job Card Operation": ("sub_operation", "completed_qty", "completed_time", "status"),
            "Work Order": ("company", "jf_flow_order", "operations"),
            "Work Order Operation": ("operation",), "Jifeng Flow Order": ("company",),
            "Item": ("stock_uom",), "Company": ("default_currency",), "Employee": ("employee_name",),
        }
        self.source_metas = {}
        for doctype, names in source_fields.items():
            fields = [Doc(fieldname=name, fieldtype="Data", permlevel=0, mask=0) for name in names]
            meta = Doc(fields=fields, read_levels=[0], mask_levels=[0])
            meta.get_field = lambda name, fields=fields: next((f for f in fields if f.fieldname == name), None)
            meta.get_permlevel_access = Mock(side_effect=lambda permission_type="read", meta=meta, **kwargs:
                                            meta.mask_levels if permission_type == "mask" else meta.read_levels)
            self.source_metas[doctype] = meta
        for table, childtype in (("time_logs", "Job Card Time Log"), ("sub_operations", "Job Card Operation")):
            field = self.source_metas["Job Card"].get_field(table)
            field.fieldtype, field.options = "Table", childtype
        field = self.source_metas["Work Order"].get_field("operations")
        field.fieldtype, field.options = "Table", "Work Order Operation"
        frappe.get_meta = lambda doctype: self.source_metas[doctype]
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=lambda doctype, **kwargs:
                                         [f.fieldname for f in self.source_metas[doctype].fields
                                          if f.fieldtype != "Table" and f.permlevel in self.source_metas[doctype].read_levels])
        self.permitted_fields = model.get_permitted_fields
        scope = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils, "frappe.model": model})
        scope.start()
        self.addCleanup(scope.stop)
        payroll = load_module("allocation_payroll", ROOT / "payroll.py")
        payroll_scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        payroll_scope.start()
        self.addCleanup(payroll_scope.stop)
        pieces = load_module("allocation_piece_workers", ROOT / "piece_workers.py")
        pieces_scope = patch.dict(sys.modules, {"jifeng_erp.piece_workers": pieces})
        pieces_scope.start()
        self.addCleanup(pieces_scope.stop)
        self.module = load_module("allocation_test_module", ROOT / "wage_allocation.py")
        self.card = Doc(name="JC-1", company="C-1", work_order="WO-1", operation="OP-1",
                        operation_id="WO-OP-1", production_item="ITEM-1", stock_uom="Nos",
                        posting_date="2026-09-29", total_completed_qty=5, docstatus=1,
                        for_quantity=5, pending_qty=0, process_loss_qty=0,
                        is_corrective_job_card=0, sub_operations=[], time_logs=[
                            Doc(name="L-1", employee="E-1", from_time="2026-09-29 09:00:00",
                                to_time="2026-09-29 10:00:00", time_in_mins=60, completed_qty=0),
                            Doc(name="L-2", employee="E-2", from_time="2026-09-29 09:00:00",
                                to_time="2026-09-29 11:00:00", time_in_mins=120, completed_qty=5),
                        ])
        self.work = Doc(name="WO-1", company="C-1", docstatus=1, jf_flow_order="FLOW-1",
                        operations=[Doc(name="WO-OP-1", operation="OP-1")])
        self.docs = {("Job Card", "JC-1"): self.card, ("Work Order", "WO-1"): self.work,
                     ("Company", "C-1"): Doc(),
                     ("Jifeng Flow Order", "FLOW-1"): Doc(company="C-1"),
                     ("Item", "ITEM-1"): Doc(stock_uom="Nos"),
                     ("Salary Component", "PIECE"): Doc(type="Earning", disabled=0),
                     ("Employee", "E-1"): Doc(company="C-1", status="Active", employee_name="甲"),
                     ("Employee", "E-2"): Doc(company="C-1", status="Active", employee_name="乙")}
        self.get_doc = Mock(side_effect=lambda doctype, name=None, **kwargs:
                            Doc(**doctype) if isinstance(doctype, dict) else self.docs[(doctype, name)])
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
        self.approve()
        self.db.sql.return_value = [Doc(name="A-1", docstatus=1, source_signature=self.allocation.source_signature)]
        self.module.guard_job_card_allocation(self.card, "before_update_after_submit")
        with self.assertRaisesRegex(ValueError, "请先取消工资及分配"):
            self.module.guard_job_card_allocation(self.card, "before_cancel")
        modified = Doc(**vars(self.card))
        modified.total_completed_qty = 6
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            self.module.guard_job_card_allocation(modified, "before_update_after_submit")

    def test_approved_snapshot_holds_source_even_after_native_db_prewrite(self):
        self.approve()
        self.db.sql.return_value = [Doc(name="A-1", docstatus=1, source_signature=self.allocation.source_signature)]
        # Native JobCard.add_time_logs updates rows and completion before save.
        # Both the caller and get_doc now see the same changed DB state.
        self.card.total_completed_qty = 6
        self.card.time_logs[1].completed_qty = 6
        self.assertEqual(self.module.source_snapshot(self.card, self.work),
                         self.module.source_snapshot(self.docs[("Job Card", "JC-1")], self.work))
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            self.module.guard_job_card_allocation(self.card, "before_update_after_submit")

    def test_loss_is_not_piece_quantity_and_does_not_reprice_an_approved_share(self):
        self.card.for_quantity, self.card.pending_qty, self.card.process_loss_qty = 6, 0, 1
        self.approve()
        signature = self.allocation.source_signature
        self.assertEqual(self.allocation.source_completed_qty, "5.000000")
        self.assertEqual(self.allocation.allocated_qty, 5)
        self.assertEqual(self.allocation.total_amount, Decimal("18.00"))
        self.db.sql.return_value = [Doc(name="A-1", docstatus=1, source_signature=signature)]
        self.card.for_quantity, self.card.process_loss_qty = 7, 2
        self.module.guard_job_card_allocation(self.card, "before_update_after_submit")
        self.assertEqual(self.allocation.total_amount, Decimal("18.00"))

    def test_source_summary_reads_all_four_quantities_without_changing_share_signature(self):
        self.card.for_quantity, self.card.pending_qty, self.card.process_loss_qty = 8, 2, 1
        before = self.module.source_snapshot(self.card, self.work)
        source = self.module.get_allocation_source("JC-1")
        self.assertEqual(source["production_quantities"], {"for_quantity": "8",
            "total_completed_qty": "5", "process_loss_qty": "1", "pending_qty": "2", "stock_uom": "Nos"})
        self.assertEqual({field: source[field] for field in self.module.SOURCE_FIELDS}, before)
        self.assertNotIn("production_quantities", self.module.SOURCE_FIELDS)
        self.assertNotIn("process_loss_qty", self.module.SOURCE_FIELDS)
        self.card.for_quantity = "8.000000001"
        self.assertEqual(self.module.production_quantities(self.card)["for_quantity"], "8.000000001")

    def test_source_summary_never_turns_missing_values_into_zero(self):
        for field in self.module.PRODUCTION_QUANTITY_FIELDS:
            original = getattr(self.card, field)
            for missing in (None, ""):
                with self.subTest(field=field, missing=missing):
                    setattr(self.card, field, missing)
                    with self.assertRaisesRegex(ValueError, "缺失数量不能按零计"):
                        self.module.production_quantities(self.card)
            setattr(self.card, field, original)

    def test_quantity_or_time_fields_and_tables_must_not_be_hidden_or_masked(self):
        self.frappe.session.user = "qa@jifeng.test"
        for doctype, name in (("Job Card", "process_loss_qty"), ("Job Card", "pending_qty"),
                              ("Job Card", "stock_uom"), ("Job Card", "time_logs"),
                              ("Job Card", "sub_operations"), ("Job Card Time Log", "employee"),
                              ("Job Card Time Log", "from_time"), ("Job Card Operation", "completed_qty"),
                              ("Work Order", "jf_flow_order"), ("Work Order", "operations"),
                              ("Work Order Operation", "operation"), ("Company", "default_currency"),
                              ("Employee", "employee_name")):
            meta = self.source_metas[doctype]
            field = meta.get_field(name)
            for mask in (False, True):
                with self.subTest(doctype=doctype, field=name, mask=mask):
                    field.permlevel = 1 if not mask else 0
                    field.mask = int(mask)
                    meta.mask_levels = [] if mask else [0]
                    with self.assertRaises(PermissionError):
                        self.module.get_allocation_source("JC-1")
                field.permlevel, field.mask, meta.mask_levels = 0, 0, [0]
        self.source_metas["Job Card Time Log"].get_permlevel_access.assert_called_with(
            permission_type="mask", parenttype="Job Card", user="qa@jifeng.test")

    def test_wrong_native_source_child_table_cannot_supply_wage_hours(self):
        self.source_metas["Job Card"].get_field("time_logs").options = "Other Logs"
        with self.assertRaisesRegex(ValueError, "明细表定义不一致"):
            self.module.get_allocation_source("JC-1")

    def test_reverse_salary_locks_fail_without_wait_and_preserve_db_error_types(self):
        for lookup in (self.module._active_allocations, self.module._active_wages):
            for error_type in (self.frappe.QueryTimeoutError, self.frappe.QueryDeadlockError):
                with self.subTest(lookup=lookup.__name__, error=error_type.__name__):
                    self.db.sql.side_effect = error_type("private native database detail")
                    with self.assertRaisesRegex(error_type, "本次未保存") as raised:
                        lookup("JC-1")
                    self.assertNotIn("private", str(raised.exception))
                    self.assertIn("ORDER BY name FOR UPDATE NOWAIT", self.db.sql.call_args.args[0])
                    self.assertEqual(self.db.sql.call_count, 1)
                    self.db.sql.reset_mock()
        self.db.sql.side_effect = None

    def native_callback_card(self, change=lambda doc: None):
        events = []
        original = self.card

        class NativeCard(Doc):
            def add_time_log(self, args):
                events.append("native_old_timer_prewrite")
                change(self)
                original.time_logs = self.time_logs
                return "old-native-result"

            def add_time_logs(self, **kwargs):
                events.append("native_child_prewrite")
                change(self)
                original.time_logs = self.time_logs
                return self.validate_time_logs(save=True)

            def validate_time_logs(self, save=False):
                events.append(("native_totals_prewrite", save))
                if save:
                    original.total_completed_qty = self.total_completed_qty
                return "native-result"

        class Card(self.module.WageSourceJobCardMixin, NativeCard):
            pass

        card = Card(**vars(self.card))
        card.time_logs = [Doc(**vars(row)) for row in self.card.time_logs]
        return card, events

    def approved_native_rows(self, *, allocated=False, hours=0):
        if allocated:
            self.approve()
        allocation_rows = [Doc(name="A-1", docstatus=1, source_signature=self.allocation.source_signature)] if allocated else []
        wage_rows = [] if allocated else [Doc(name="W-1", docstatus=1, quantity=5 if not hours else 0, hours=hours)]
        self.db.sql.side_effect = lambda query, *args, **kwargs: (
            allocation_rows if "tabJifeng Wage Allocation" in query else wage_rows)

    def test_native_callbacks_hold_approved_legacy_and_allocated_completion(self):
        for allocated in (False, True):
            with self.subTest(allocated=allocated):
                self.card.total_completed_qty = 5
                self.card.time_logs[1].completed_qty = 5
                self.db.sql.side_effect = None
                self.db.sql.return_value = []
                self.approved_native_rows(allocated=allocated)
                card, events = self.native_callback_card(
                    lambda doc: (setattr(doc, "total_completed_qty", 4), setattr(doc.time_logs[1], "completed_qty", 4)))
                with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
                    card.add_time_logs(completed_qty=4)
                self.assertIn("native_child_prewrite", events)
                self.assertIn(unittest.mock.call("Job Card", "JC-1", for_update=True), self.get_doc.mock_calls)

    def test_native_callback_holds_legacy_hourly_time_not_only_piece_quantity(self):
        self.approved_native_rows(hours=1)
        card, _ = self.native_callback_card(lambda doc: setattr(doc.time_logs[0], "to_time", "2026-09-29 11:00:00"))
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            card.add_time_logs(to_time="2026-09-29 11:00:00")

    def test_old_timer_entry_holds_approved_hours_before_parent_save(self):
        self.approved_native_rows(hours=1)
        card, events = self.native_callback_card(lambda doc: setattr(doc.time_logs[0], "to_time", "2026-09-29 11:00:00"))
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            card.add_time_log(Doc(complete_time="2026-09-29 11:00:00"))
        self.assertIn("native_old_timer_prewrite", events)
        self.db.sql.side_effect = None
        self.db.sql.return_value = []
        card, _ = self.native_callback_card()
        self.assertEqual(card.add_time_log(Doc(start_time="2026-09-29 11:00:00")), "old-native-result")

    def test_direct_native_total_save_is_guarded_and_pure_validation_is_not_blocked(self):
        self.approved_native_rows()
        card, events = self.native_callback_card()
        card.total_completed_qty = 4
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            card.validate_time_logs(save=True)
        self.get_doc.reset_mock()
        self.assertEqual(card.validate_time_logs(save=False), "native-result")
        self.get_doc.assert_not_called()
        self.assertIn(("native_totals_prewrite", False), events)

    def test_unchanged_callbacks_and_unpaid_normal_reporting_keep_native_result(self):
        self.approved_native_rows()
        card, events = self.native_callback_card()
        self.assertEqual(card.add_time_logs(completed_qty=5), "native-result")
        self.db.sql.side_effect = None
        self.db.sql.return_value = []
        card, _ = self.native_callback_card(lambda doc: setattr(doc, "total_completed_qty", 4))
        self.assertEqual(card.add_time_logs(completed_qty=4), "native-result")
        self.assertEqual(self.card.total_completed_qty, 4)

    def test_native_numeric_and_datetime_serialization_is_not_a_source_edit(self):
        self.approved_native_rows(allocated=True)
        original_signature = self.allocation.source_signature
        current = Doc(**vars(self.card))
        current.time_logs = [Doc(**vars(row)) for row in self.card.time_logs]
        current.time_logs[0].time_in_mins = 60.0
        current.time_logs[0].from_time = datetime.datetime(2026, 9, 29, 9)
        current.time_logs[1].completed_qty = "5.0"
        self.assertNotEqual(self.module.source_snapshot(current, self.work)["source_signature"], original_signature)
        self.module.guard_job_card_allocation(current, "before_update_after_submit")
        self.assertEqual(self.allocation.source_signature, original_signature)
        current.time_logs[1].completed_qty = "4.999999"
        with self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
            self.module.guard_job_card_allocation(current, "before_update_after_submit")

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
