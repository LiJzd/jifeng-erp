"""One-time wage consumption through native payroll's calculated child rows."""

from copy import deepcopy
from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class TestSalarySlipWageSources(unittest.TestCase):
    def setUp(self):
        self.sources = {"AS-1": Record(
            name="AS-1", ref_doctype="Jifeng Wage Entry", ref_docname="W-1",
            is_recurring=0, docstatus=1, employee="E-1", company="C-1",
            payroll_date=date(2026, 9, 20), salary_component="Piece", type="Earning",
            disabled=0, overwrite_salary_structure_amount=0,
        )}
        self.saved_rows, self.lock_order = [], []
        self.frappe = types.ModuleType("frappe")
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        self.frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        self.frappe.utils = types.SimpleNamespace(
            getdate=lambda value: datetime.fromisoformat(str(value)).date(),
        )
        self.frappe.get_doc = Mock(side_effect=AssertionError("payroll must not read private wage documents"))
        self.frappe.db = types.SimpleNamespace(
            get_value=Mock(side_effect=self.get_source), sql=Mock(side_effect=self.used_source),
        )
        with patch.dict(sys.modules, {"frappe": self.frappe}):
            spec = importlib.util.spec_from_file_location("slip_source_payroll", ROOT / "jifeng_erp/payroll.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def get_source(self, doctype, filters, fields, *, as_dict, for_update):
        self.assertEqual(doctype, "Additional Salary")
        self.assertTrue(as_dict and for_update)
        self.lock_order.append(filters["name"])
        source = self.sources.get(filters["name"])
        if source and all(source.get(key) == value for key, value in filters.items()):
            return deepcopy(source)

    def used_source(self, query, values):
        self.assertIn("FOR UPDATE NOWAIT", query)
        self.assertIn("FORCE INDEX (`jf_salary_source_consumption`)", query)
        self.assertNotIn("JOIN", query)
        return [(1,)] if any(
            row.additional_salary in values[:-1] and row.parent != values[-1]
            and row.parenttype == "Salary Slip" and row.parentfield in ("earnings", "deductions")
            and row.docstatus < 2 for row in self.saved_rows
        ) else []

    def slip(self, name="SLIP-1", start="2026-09-01", end="2026-09-30", names=("AS-1",)):
        return Record(name=name, employee="E-1", company="C-1", start_date=start, end_date=end,
                      earnings=[Record(additional_salary=source, salary_component="Piece",
                                       is_recurring_additional_salary=0) for source in names], deductions=[])

    def save(self, doc):
        self.module.validate_salary_slip_wage_sources(doc)
        self.saved_rows[:] = [row for row in self.saved_rows if row.parent != doc.name]
        self.saved_rows.extend(Record(
            parent=doc.name, parenttype="Salary Slip", parentfield=field,
            additional_salary=row.additional_salary, docstatus=0,
        ) for field in ("earnings", "deductions") for row in doc.get(field))

    def test_overlapping_different_periods_cannot_consume_the_same_wage_twice(self):
        self.save(self.slip())
        with self.assertRaisesRegex(ValueError, "其他有效工资单"):
            self.save(self.slip("SLIP-2", "2026-09-15", "2026-10-14"))
        self.assertEqual({row.parent for row in self.saved_rows}, {"SLIP-1"})

    def test_own_draft_edit_and_submit_do_not_compete_with_self(self):
        self.save(self.slip())
        self.save(self.slip())
        self.saved_rows[0].docstatus = 1
        self.module.validate_salary_slip_wage_sources(self.slip())
        self.assertEqual(len(self.saved_rows), 1)

    def test_cancel_and_native_delete_release_the_source(self):
        for release in ("cancel", "delete"):
            with self.subTest(release=release):
                self.saved_rows.clear()
                self.save(self.slip())
                if release == "cancel":
                    self.saved_rows[0].docstatus = 2
                else:
                    self.saved_rows.clear()
                self.save(self.slip("SLIP-2"))
                self.assertTrue(any(row.parent == "SLIP-2" for row in self.saved_rows))

    def test_other_parent_types_do_not_reserve_a_wage(self):
        self.saved_rows.append(Record(parent="STRUCTURE-1", parenttype="Salary Structure",
                                     parentfield="earnings", additional_salary="AS-1", docstatus=1))
        self.save(self.slip())

    def test_multiple_wages_for_the_same_component_keep_distinct_sources(self):
        self.sources["AS-2"] = Record(self.sources["AS-1"], name="AS-2", ref_docname="W-2")
        self.save(self.slip(names=("AS-2", "AS-1")))
        self.assertEqual(self.lock_order, ["AS-1", "AS-2"])
        self.assertEqual(self.frappe.db.sql.call_args.args[1], ("AS-1", "AS-2", "SLIP-1"))
        self.assertEqual(len(self.saved_rows), 2)

    def test_same_source_twice_within_one_slip_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复引用"):
            self.save(self.slip(names=("AS-1", "AS-1")))
        self.assertFalse(self.saved_rows)

    def test_native_unrelated_and_recurring_additional_salaries_are_unchanged(self):
        for change in ({"ref_doctype": "Gratuity"}, {"is_recurring": 1}):
            with self.subTest(change=change):
                self.sources["AS-1"].update(change)
                self.module.validate_salary_slip_wage_sources(self.slip())
                self.frappe.db.sql.assert_not_called()
                self.sources["AS-1"].update(ref_doctype="Jifeng Wage Entry", is_recurring=0)

    def test_source_status_identity_and_earning_kind_are_checked(self):
        for field, bad in (("docstatus", 0), ("docstatus", 2), ("disabled", 1),
                           ("employee", "E-OTHER"), ("company", "C-OTHER"),
                           ("ref_docname", ""), ("type", "Deduction"),
                           ("overwrite_salary_structure_amount", 1)):
            with self.subTest(field=field, bad=bad):
                original = self.sources["AS-1"][field]
                self.sources["AS-1"][field] = bad
                with self.assertRaisesRegex(ValueError, "来源已变化"):
                    self.save(self.slip())
                self.sources["AS-1"][field] = original
        self.assertFalse(self.saved_rows)
        self.frappe.get_doc.assert_not_called()

    def test_source_cannot_be_moved_to_deductions_or_another_component(self):
        for change in ("deductions", "component", "recurring"):
            doc = self.slip()
            if change == "deductions":
                doc.deductions, doc.earnings = doc.earnings, []
            elif change == "component":
                doc.earnings[0].salary_component = "Other"
            else:
                doc.earnings[0].is_recurring_additional_salary = 1
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "来源已变化"):
                self.save(doc)

    def test_source_date_is_inclusive_and_must_belong_to_the_period(self):
        self.module.validate_salary_slip_wage_sources(self.slip(start="2026-09-20", end="2026-09-20"))
        self.module.validate_salary_slip_wage_sources(
            self.slip(start="2026-09-20 00:00:00", end="2026-09-20 23:59:59"),
        )
        for start, end in (("2026-09-21", "2026-09-30"), ("2026-09-01", "2026-09-19")):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, "期间内"):
                self.save(self.slip(start=start, end=end))

    def test_missing_dates_do_not_default_to_today(self):
        for field in ("start_date", "end_date", "payroll_date"):
            with self.subTest(field=field):
                doc = self.slip()
                target = self.sources["AS-1"] if field == "payroll_date" else doc
                original = target[field]
                target[field] = None
                with self.assertRaisesRegex(ValueError, "明确的计薪日期"):
                    self.save(doc)
                target[field] = original

    def test_busy_and_deadlocked_sources_fail_without_a_nonlocking_fallback(self):
        for error in (self.frappe.QueryTimeoutError, self.frappe.QueryDeadlockError):
            self.frappe.db.sql.side_effect = error("private competing slip")
            with self.subTest(error=error), self.assertRaisesRegex(error, "本次未保存") as caught:
                self.save(self.slip())
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIsInstance(caught.exception, ValueError)
            self.assertFalse(self.saved_rows)
        self.assertEqual(self.frappe.db.sql.call_count, 2)

    def test_no_wage_sources_does_not_query_or_lock(self):
        self.module.validate_salary_slip_wage_sources(self.slip(names=()))
        self.frappe.db.get_value.assert_not_called()
        self.frappe.db.sql.assert_not_called()

    def test_native_validate_hook_is_installed_for_save_and_submit(self):
        spec = importlib.util.spec_from_file_location("payroll_hooks", ROOT / "jifeng_erp/hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        self.assertEqual(hooks.doc_events["Salary Slip"]["validate"],
                         "jifeng_erp.payroll.validate_salary_slip_wage_sources")


if __name__ == "__main__":
    unittest.main()
