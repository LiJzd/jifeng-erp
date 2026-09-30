"""Net sales rankings preserve native invoice amounts, signed units and read scope."""

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSalesItemRanking(unittest.TestCase):
    def setUp(self):
        self.documents = {"Sales Invoice": {}, "Item": {}, "Company": {"JF": Record(name="JF")}}
        self.hidden, self.denied = set(), set()
        self.permissions = {("Sales Invoice", "read"), ("Sales Invoice", "report"),
                            ("Item", "read"), ("Company", "select")}
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict, self.frappe.PermissionError = Record, PermissionError
        self.frappe.session = Record(user="accounts@example.invalid")
        self.frappe.has_permission = Mock(side_effect=lambda doctype, ptype="read", **kwargs:
                                          (doctype, ptype) in self.permissions)
        self.frappe.get_list = Mock(side_effect=lambda doctype, filters=None, **kwargs:
                                   self.query(doctype, filters, visible=True))
        self.frappe.get_all = Mock(side_effect=lambda doctype, filters=None, **kwargs:
                                  self.query(doctype, filters))
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[doctype][name])
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(str(value))
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            scope = load_module("ranking_scope_under_test", ROOT / "jifeng_erp/inventory_scope.py")
            with patch.dict(sys.modules, {"jifeng_erp.inventory_scope": scope}):
                sales = load_module("ranking_sales_under_test", ROOT / "jifeng_erp/sales_reports.py")
                with patch.dict(sys.modules, {"jifeng_erp.sales_reports": sales}):
                    self.module = load_module("sales_item_ranking_under_test", ROOT / "jifeng_erp/sales_item_ranking.py")
        self.filters = {"company": "JF", "from_date": "2026-09-01", "to_date": "2026-09-30"}

    def query(self, doctype, filters, visible=False):
        if visible:
            ptype = "select" if doctype == "Company" else "read"
            if (doctype, ptype) not in self.permissions:
                return []
        result = []
        for name, doc in self.documents.get(doctype, {}).items():
            if visible and (doctype, name) in self.hidden:
                continue
            matched = True
            for field, expected in (filters or {}).items():
                actual = doc.get(field)
                if isinstance(expected, list) and expected[0] == "between":
                    matched = matched and str(expected[1][0]) <= str(actual) <= str(expected[1][1])
                else:
                    matched = matched and actual == expected
            if matched:
                result.append(name)
        return result

    def permissions_for(self, doctype, doc):
        doc.has_permission = Mock(side_effect=lambda ptype="read":
                                  (doctype, ptype) in self.permissions and (doctype, doc.name) not in self.denied)

        def check_permission(ptype="read"):
            if not doc.has_permission(ptype):
                raise PermissionError("来源读取权限")

        doc.check_permission = Mock(side_effect=check_permission)
        return doc

    def item(self, code="FG", name="当前成品", stock_uom="Nos"):
        if code not in self.documents["Item"]:
            doc = self.permissions_for("Item", Record(name=code, item_name=name, stock_uom=stock_uom))
            self.documents["Item"][code] = doc
        return self.documents["Item"][code]

    def row(self, name="ROW-1", item_code="FG", stock_uom="Nos", amount="10.25", quantity="2", **fields):
        if item_code:
            self.item(item_code)
        return Record(name=name, item_code=item_code, stock_uom=stock_uom,
                      item_name="来源成品", base_net_amount=amount, stock_qty=quantity,
                      rate="NaN", qty="bad", conversion_factor="bad", paid_amount="bad", **fields)

    def invoice(self, name="SI-1", rows=None, **fields):
        rows = rows if rows is not None else [self.row()]
        for index, row in enumerate(rows, 1):
            row.update(parent=name, parenttype="Sales Invoice", parentfield="items", idx=index)
        doc = Record(doctype="Sales Invoice", name=name, company="JF", docstatus=1,
                     posting_date="2026-09-15", update_stock=0, is_return=0,
                     currency="USD", conversion_rate="NaN", base_net_total="ignored",
                     grand_total="ignored", paid_amount="ignored", items=rows)
        doc.update(fields)
        self.permissions_for("Sales Invoice", doc)
        self.documents["Sales Invoice"][name] = doc
        return doc

    def report(self, **filters):
        return self.module.sales_item_ranking({**self.filters, **filters})

    def groups(self, **filters):
        return [row for row in self.report(**filters)[1] if row["is_group"]]

    def test_stored_net_amount_and_stock_quantity_are_the_only_numeric_sources(self):
        self.invoice(rows=[self.row(amount="0.10", quantity="1.125"),
                           self.row(name="ROW-2", amount="0.20", quantity="2.875")])
        columns, rows = self.report()
        self.assertEqual((rows[0]["base_net_amount"], rows[0]["stock_qty"]), (Decimal("0.30"), Decimal(4)))
        self.assertEqual(rows[0]["company_currency"], "CNY")
        self.assertEqual(rows[0]["source_count"], 2)
        self.assertEqual(rows[0]["item_name"], "当前成品")
        self.assertEqual(rows[1]["source_item_name"], "来源成品")
        self.assertEqual(next(col for col in columns if col["fieldname"] == "base_net_amount")["options"],
                         "company_currency")
        self.assertFalse(any("paid" in col["fieldname"] or "profit" in col["fieldname"] for col in columns))

    def test_return_values_remain_negative_and_net_with_sales_once(self):
        self.invoice(rows=[self.row(amount="100.00", quantity=10)])
        self.invoice("SI-RETURN", [self.row("RETURN-ROW", amount="-25.00", quantity=-2)], is_return=1)
        rows = self.report()[1]
        self.assertEqual((rows[0]["base_net_amount"], rows[0]["stock_qty"]), (Decimal(75), Decimal(8)))
        returned = next(row for row in rows if row.get("document") == "SI-RETURN")
        self.assertEqual((returned["source_base_net_amount"], returned["source_stock_qty"]), (Decimal(-25), Decimal(-2)))
        self.assertEqual(returned["entry_type"], "退货 / 贷项")

    def test_amount_descending_includes_zero_and_negative_groups(self):
        for index, (code, amount) in enumerate((("D", "-20"), ("B", "0"), ("A", "10"), ("C", "-5")), 1):
            self.invoice(f"SI-{index}", [self.row(f"R-{index}", item_code=code, amount=amount)])
        groups = self.groups()
        self.assertEqual([row["item_code"] for row in groups], ["A", "B", "C", "D"])
        self.assertEqual([row["rank"] for row in groups], [1, 2, 3, 4])

    def test_equal_amounts_use_deterministic_item_and_unit_order(self):
        self.invoice(rows=[self.row("R1", item_code="B", stock_uom="Nos"),
                           self.row("R2", item_code="A", stock_uom="Nos"),
                           self.row("R3", item_code="A", stock_uom="Kg")])
        self.assertEqual([(row["item_code"], row["stock_uom"]) for row in self.groups()],
                         [("A", "Kg"), ("A", "Nos"), ("B", "Nos")])

    def test_same_item_with_different_source_stock_uoms_remains_separate(self):
        self.invoice(rows=[self.row("R1", stock_uom="Nos", quantity=2),
                           self.row("R2", stock_uom="Kg", quantity="0.5")])
        self.documents["Item"]["FG"].stock_uom = "Box"
        self.assertEqual({row["stock_uom"]: row["stock_qty"] for row in self.groups()},
                         {"Nos": Decimal(2), "Kg": Decimal("0.5")})

    def test_expansion_preserves_exact_child_identity_without_duplicate_summary_numbers(self):
        self.invoice(rows=[self.row("SAME-ITEM-1"), self.row("SAME-ITEM-2")])
        columns, rows = self.report()
        self.assertEqual(rows[0]["source_count"], 2)
        self.assertEqual({row["invoice_item"] for row in rows[1:]}, {"SAME-ITEM-1", "SAME-ITEM-2"})
        self.assertEqual([row["item_idx"] for row in rows[1:]], [1, 2])
        self.assertTrue(all(row["indent"] == 1 and "base_net_amount" not in row and "stock_qty" not in row
                            and "source_count" not in row for row in rows[1:]))
        self.assertEqual(sum(row["source_base_net_amount"] for row in rows[1:]), rows[0]["base_net_amount"])
        self.assertEqual(sum(row["source_stock_qty"] for row in rows[1:]), rows[0]["stock_qty"])
        self.assertEqual(next(col for col in columns if col["fieldname"] == "document")["options"], "Sales Invoice")
        self.assertEqual(next(col for col in columns if col["fieldname"] == "item_code")["options"], "Item")

    def test_pos_sources_are_never_read_or_added_to_consolidated_sales_invoice(self):
        self.invoice(is_consolidated=1, rows=[self.row(pos_invoice="POS-HIDDEN", pos_invoice_item="POS-ROW")])
        self.documents["POS Invoice"] = {"POS-HIDDEN": Record(name="POS-HIDDEN", base_net_amount=99999)}
        self.assertEqual(self.groups()[0]["base_net_amount"], Decimal("10.25"))
        calls = self.frappe.get_list.call_args_list + self.frappe.get_all.call_args_list + self.frappe.get_doc.call_args_list
        self.assertFalse(any(call.args[0] in ("POS Invoice", "POS Invoice Merge Log", "Stock Ledger Entry") for call in calls))

    def test_invoice_that_does_not_update_stock_is_included(self):
        self.invoice(update_stock=0)
        rows = self.report()[1]
        self.assertEqual(rows[0]["stock_qty"], Decimal(2))
        self.assertEqual(rows[1]["updates_stock"], 0)

    def test_drafts_cancelled_other_company_and_outside_dates_are_excluded(self):
        self.invoice()
        for name, fields in (("DRAFT", {"docstatus": 0}), ("CANCELLED", {"docstatus": 2}),
                             ("OTHER", {"company": "OTHER"}), ("BEFORE", {"posting_date": "2026-08-31"}),
                             ("AFTER", {"posting_date": "2026-10-01"})):
            self.invoice(name, [self.row(f"{name}-ROW")], **fields)
        self.assertEqual([row["document"] for row in self.report()[1] if not row["is_group"]], ["SI-1"])

    def test_company_and_date_changes_after_listing_do_not_escape_scope(self):
        doc = self.invoice()
        native = self.frappe.get_list.side_effect
        for changed in ({"company": "OTHER"}, {"docstatus": 2}, {"posting_date": "2026-10-01"}):
            doc.update(company="JF", docstatus=1, posting_date="2026-09-15")

            def change(doctype, **kwargs):
                names = native(doctype, **kwargs)
                if doctype == "Sales Invoice":
                    doc.update(changed)
                return names

            self.frappe.get_list.side_effect = change
            self.assertEqual(self.report()[1], [])

    def test_list_hidden_and_full_read_denied_invoices_do_not_contribute(self):
        self.invoice()
        self.invoice("HIDDEN", [self.row("HIDDEN-ROW", amount=9999)])
        self.invoice("DENIED", [self.row("DENIED-ROW", amount=9999)])
        self.hidden.add(("Sales Invoice", "HIDDEN"))
        self.denied.add(("Sales Invoice", "DENIED"))
        rows = self.report()[1]
        self.assertEqual(rows[0]["base_net_amount"], Decimal("10.25"))
        self.assertNotIn("HIDDEN-ROW", str(rows))
        self.assertNotIn("DENIED-ROW", str(rows))

    def test_hidden_or_full_read_denied_related_item_rejects_partial_ranking(self):
        self.invoice()
        for denied_set in (self.hidden, self.denied):
            denied_set.add(("Item", "FG"))
            with self.assertRaises(PermissionError):
                self.report()
            denied_set.clear()

    def test_item_filter_checks_native_item_access_and_selects_exact_invoice_rows(self):
        self.invoice(rows=[self.row("FG-ROW"), self.row("OTHER-ROW", item_code="OTHER", amount=999)])
        rows = self.report(item_code="FG")[1]
        self.assertEqual(rows[0]["base_net_amount"], Decimal("10.25"))
        self.assertEqual(rows[1]["invoice_item"], "FG-ROW")
        self.documents["Item"]["FG"].check_permission.assert_called_with("read")
        with self.assertRaises(PermissionError):
            self.report(item_code="MISSING")

    def test_company_select_access_suffices_without_company_profile_read(self):
        self.invoice()
        self.assertNotIn(("Company", "read"), self.permissions)
        self.assertEqual(len(self.groups()), 1)
        self.assertFalse(any(call.args[0] == "Company" for call in self.frappe.get_doc.call_args_list))
        self.permissions.remove(("Company", "select"))
        with self.assertRaises(PermissionError):
            self.report()

    def test_guest_missing_sales_read_or_report_are_rejected(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.session.user = "accounts@example.invalid"
        for permission in (("Sales Invoice", "read"), ("Sales Invoice", "report")):
            self.permissions.remove(permission)
            with self.assertRaises(PermissionError):
                self.report()
            self.permissions.add(permission)

    def test_missing_bounds_reversed_dates_and_unsupported_filters_reject(self):
        for field in ("company", "from_date", "to_date"):
            with self.assertRaises(ValueError):
                self.report(**{field: ""})
        with self.assertRaises(ValueError):
            self.report(from_date="2026-10-01")
        for field in ("customer", "warehouse", "pos_profile", "cashier", "source_type"):
            with self.assertRaisesRegex(ValueError, "仅支持"):
                self.report(**{field: "IGNORED"})

    def test_missing_and_non_finite_persisted_numbers_reject_without_recalculation(self):
        doc = self.invoice()
        row = doc.get("items")[0]
        for field in ("stock_qty", "base_net_amount"):
            previous = row[field]
            for value in (None, "", "NaN", "Infinity", "-Infinity", "invalid"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    row[field] = value
                    self.report()
            row[field] = previous

    def test_missing_or_wrong_parent_child_identity_rejects(self):
        doc = self.invoice()
        row = doc.get("items")[0]
        for field, value in (("name", None), ("item_code", None), ("stock_uom", None),
                             ("parent", "OTHER"), ("parenttype", "POS Invoice"), ("parentfield", "other")):
            previous = row[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                row[field] = value
                self.report()
            row[field] = previous
        doc.get("items").append(Record(row))
        with self.assertRaisesRegex(ValueError, "重复"):
            self.report()

    def test_special_names_remain_text_and_report_roles_do_not_expand_native_permissions(self):
        value = '<script>alert("x")</script>/引号\'；'
        doc = self.invoice(rows=[self.row(item_name_override="unused")])
        doc.get("items")[0].item_name = value
        self.documents["Item"]["FG"].item_name = value
        rows = self.report()[1]
        self.assertEqual((rows[0]["item_name"], rows[1]["source_item_name"]), (value, value))
        folder = ROOT / "jifeng_erp/jifeng_erp/report/jifeng_sales_item_ranking"
        definition = json.loads((folder / "jifeng_sales_item_ranking.json").read_text(encoding="utf-8"))
        self.assertEqual([role["role"] for role in definition["roles"]], ["Accounts User", "Accounts Manager"])
        self.assertEqual(definition["ref_doctype"], "Sales Invoice")
        self.assertEqual((definition["prepared_report"], definition["add_total_row"]), (0, 0))

    def test_missing_company_currency_is_rejected(self):
        self.frappe.db.get_value.return_value = None
        with self.assertRaisesRegex(ValueError, "本币"):
            self.report()


if __name__ == "__main__":
    unittest.main()
