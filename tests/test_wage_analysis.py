"""Wage totals preserve units, individual legacy sources and document access."""

import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestWageAnalysis(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict = Record
        self.frappe.PermissionError = PermissionError
        self.frappe.has_permission = Mock(return_value=True)

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))
        self.documents = {}
        self.frappe.get_list = Mock(side_effect=lambda doctype, **kwargs: (
            ["JF"] if doctype == "Company" else list(self.documents)))
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[name])
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        path = Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/report/jifeng_wage_analysis/jifeng_wage_analysis.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            spec = importlib.util.spec_from_file_location("wage_analysis_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.filters = {"company": "JF", "from_date": "2026-09-01", "to_date": "2026-09-30"}

    def wage(self, name, unit=None, quantity=1, amount=10, readable=True):
        doc = Record(name=name, company="JF", docstatus=1, work_date="2026-09-20",
                     employee="E-1", employee_name="员工甲", operation="抛光",
                     salary_component="Piece", stock_uom=unit, quantity=quantity,
                     piece_amount=amount, hours=0, time_amount=0, extra_amount=0, total_amount=amount)
        doc["has_permission"] = lambda permission: readable
        self.documents[name] = doc
        return doc

    def rows(self):
        return self.module.execute(self.filters)[1]

    def test_different_units_never_share_a_quantity_total(self):
        self.wage("W1", "Nos", 3, 6)
        self.wage("W2", "Kg", 2, 8)
        self.wage("W3", "Nos", 4, 12)
        rows = {row["stock_uom"]: row for row in self.rows()}
        self.assertEqual(rows["Nos"]["quantity"], 7)
        self.assertEqual(rows["Nos"]["total_amount"], 18)
        self.assertEqual(rows["Nos"]["record_count"], 2)
        self.assertIsNone(rows["Nos"]["wage_entry"])
        self.assertEqual(rows["Kg"]["quantity"], 2)
        self.assertEqual(rows["Kg"]["wage_entry"], "W2")

    def test_unknown_legacy_units_keep_each_source_separate(self):
        self.wage("OLD1", quantity=5)
        self.wage("OLD2", quantity=20)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["wage_entry"] for row in rows}, {"OLD1", "OLD2"})
        self.assertEqual({row["quantity"] for row in rows}, {5, 20})
        self.assertTrue(all(row["record_count"] == 1 and row["unit_note"] for row in rows))

    def test_time_only_wages_can_group_without_piece_unit(self):
        for name, hours in (("TIME1", 1.5), ("TIME2", 2)):
            doc = self.wage(name, quantity=0, amount=0)
            doc.update(hours=hours, time_amount=hours * 20, total_amount=hours * 20)
        row, = self.rows()
        self.assertEqual((row["quantity"], row["hours"], row["time_amount"]), (0, 3.5, 70))
        self.assertEqual(row["record_count"], 2)

    def test_full_document_denial_excludes_salary_and_identity(self):
        self.wage("PRIVATE", "Nos", 99, 99999, readable=False)
        self.assertEqual(self.rows(), [])

    def test_source_changed_after_list_is_rejected(self):
        doc = self.wage("W1", "Nos")
        doc["docstatus"] = 2
        with self.assertRaisesRegex(ValueError, "审核状态"):
            self.rows()

    def test_invalid_or_negative_stored_amount_does_not_poison_totals(self):
        doc = self.wage("W1", "Nos")
        for value in ("NaN", "Infinity", "bad", -1):
            with self.subTest(value=value):
                doc["total_amount"] = value
                with self.assertRaises(ValueError):
                    self.rows()

    def test_guest_role_or_hidden_company_is_denied(self):
        self.frappe.has_permission.return_value = False
        with self.assertRaises(PermissionError):
            self.rows()
        self.frappe.has_permission.return_value = True
        self.frappe.get_list.return_value = []
        self.frappe.get_list.side_effect = None
        with self.assertRaises(PermissionError):
            self.rows()

    def test_reverse_dates_are_rejected(self):
        self.filters["from_date"] = "2026-10-01"
        with self.assertRaisesRegex(ValueError, "开始日期"):
            self.rows()


if __name__ == "__main__":
    unittest.main()
