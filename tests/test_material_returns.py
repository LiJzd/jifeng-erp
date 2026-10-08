"""Source-bound material return arithmetic and valuation guards."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch


class Record(dict):
    def __getattribute__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return dict.__getattribute__(self, key)

    def __getattr__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class ValidationError(ValueError):
    pass


class PermissionError(ValueError):
    pass


class Meta:
    def __init__(self):
        self.masked = set()

    def get_permlevel_access(self, **kwargs):
        return set() if kwargs.get("permission_type") == "mask" else {0}

    def get_field(self, name):
        return Record(mask=int(name in self.masked), permlevel=0)


class TestMaterialReturns(unittest.TestCase):
    def setUp(self):
        self.ledger = []
        self.reposts = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.ValidationError = ValidationError
        self.frappe.PermissionError = PermissionError
        self.frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        self.frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        self.frappe.session = Record(user="qa@example.test")
        self.meta = Meta()
        self.frappe.get_meta = lambda doctype: self.meta

        def throw(message, exc=ValidationError):
            raise exc(message)

        self.frappe.throw = throw
        self.frappe.whitelist = lambda **kwargs: (lambda function: function)
        self.frappe.get_all = lambda doctype, **kwargs: (
            list(self.ledger) if doctype == "Stock Ledger Entry" else
            list(self.reposts) if doctype == "Repost Item Valuation" else [])
        self.frappe.get_list = self.frappe.get_all
        locking = types.ModuleType("jifeng_erp.material_issue_locking")
        locking._lock_flows = lambda names: None
        locking._persisted_flow_ids = lambda name: set()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, **kwargs: {
            "name", "company", "docstatus", "voucher_type", "voucher_no", "voucher_detail_no",
            "item_code", "warehouse", "stock_uom", "actual_qty", "stock_value_difference",
            "is_cancelled", "is_adjustment_entry",
        }
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "material_returns.py"
        self.module = None
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                      "jifeng_erp.material_issue_locking": locking}):
            spec = importlib.util.spec_from_file_location("material_returns_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def test_exact_source_ledger_rate_includes_later_valuation_adjustment(self):
        self.ledger = [
            Record(name="SLE-ISSUE", company="JF", docstatus=1, is_cancelled=0,
                   voucher_type="Stock Entry", voucher_no="MI-1", voucher_detail_no="ROW-1",
                   item_code="RAW", warehouse="Stores - JF", stock_uom="Nos",
                   posting_date="2026-10-01", actual_qty=-2, stock_value_difference=-20,
                   is_adjustment_entry=0),
            Record(name="SLE-REPOST", company="JF", docstatus=1, is_cancelled=0,
                   voucher_type="Stock Entry", voucher_no="MI-1", voucher_detail_no="ROW-1",
                   item_code="RAW", warehouse="Stores - JF", stock_uom="Nos",
                   posting_date="2026-10-04", actual_qty=0, stock_value_difference=-2,
                   is_adjustment_entry=1),
        ]
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF")
        source = Record(name="MI-1", company="JF", posting_date="2026-10-01", posting_time="09:00:00")
        rows = {"ROW-1": {"row": source_row, "uom": "Nos", "qty": Decimal("2")}}
        rates = self.module._source_rates(source, rows)
        self.assertEqual(rates["ROW-1"], Decimal("11"))

    def test_source_ledger_requires_exact_net_quantity_and_value(self):
        self.ledger = [Record(name="SLE-WRONG", company="JF", docstatus=1, is_cancelled=0,
                              voucher_type="Stock Entry", voucher_no="MI-OTHER", voucher_detail_no="ROW-1",
                              item_code="RAW", warehouse="Stores - JF", stock_uom="Nos",
                              posting_date="2026-10-01", actual_qty=-2, stock_value_difference=-20,
                              is_adjustment_entry=0)]
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF")
        source = Record(name="MI-1", company="JF", posting_date="2026-10-01", posting_time="09:00:00")
        with self.assertRaisesRegex(ValidationError, "库存价值来源与来源明细不匹配"):
            self.module._source_rates(source, {"ROW-1": {"row": source_row, "uom": "Nos", "qty": Decimal("2")}})

    def test_return_row_requires_exact_source_child_item_unit_warehouse_and_rate(self):
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF", expense_account="Stock Adj - JF",
                            cost_center="Main - JF", project=None)
        evidence = {"row": source_row, "uom": "Nos", "flow": "FLOW-1"}
        source, doc = Record(name="MI-1"), Record(name="MR-1")
        row = Record(jf_material_return_against_detail="ROW-1", item_code="RAW", stock_uom="Nos",
                     uom="Nos", conversion_factor=1, s_warehouse=None, t_warehouse="Stores - JF",
                     jf_flow_order="FLOW-1", expense_account="Stock Adj - JF",
                     cost_center="Main - JF", project=None, transfer_qty=1, qty=1,
                     set_basic_rate_manually=1, basic_rate=10)
        self.module._validate_return_row(source, doc, row, evidence, Decimal("10"))
        row.t_warehouse = "Other - JF"
        with self.assertRaisesRegex(ValidationError, "退回原发料仓"):
            self.module._validate_return_row(source, doc, row, evidence, Decimal("10"))

    def test_prior_submitted_and_current_return_cannot_exceed_source_row(self):
        source_rows = {"ROW-1": {"qty": Decimal("2")}}
        self.module._ensure_within_source_quantities(
            {"ROW-1": Decimal("0.5")}, {"ROW-1": Decimal("1.5")}, source_rows)
        with self.assertRaisesRegex(ValidationError, "累计超过"):
            self.module._ensure_within_source_quantities(
                {"ROW-1": Decimal("0.5001")}, {"ROW-1": Decimal("1.5")}, source_rows)

    def test_pending_repost_for_source_or_prior_item_warehouse_blocks_stale_value(self):
        source = Record(name="MI-1", company="JF", posting_date="2026-10-01")
        rows = {"ROW-1": {"row": Record(item_code="RAW", s_warehouse="Stores - JF")}}
        self.reposts = [Record(name="RIV-1", docstatus=1, status="Queued", based_on="Transaction",
                               voucher_type="Stock Entry", voucher_no="MI-1")]
        with self.assertRaisesRegex(ValidationError, "重估尚未完成"):
            self.module._ensure_no_pending_reposting(source, rows)
        self.reposts = [Record(name="RIV-2", docstatus=1, status="In Progress", based_on="Item and Warehouse",
                               item_code="RAW", warehouse="Stores - JF", posting_date="2026-09-30")]
        with self.assertRaisesRegex(ValidationError, "尚未完成的库存估价重算"):
            self.module._ensure_no_pending_reposting(source, rows)

    def test_unrelated_later_repost_does_not_block_source_value(self):
        source = Record(name="MI-1", company="JF", posting_date="2026-10-01")
        rows = {"ROW-1": {"row": Record(item_code="RAW", s_warehouse="Stores - JF")}}
        self.reposts = [Record(name="RIV-OTHER", docstatus=1, status="Queued", based_on="Item and Warehouse",
                               item_code="OTHER", warehouse="Stores - JF", posting_date="2026-09-30"),
                        Record(name="RIV-LATER", docstatus=1, status="Queued", based_on="Item and Warehouse",
                               item_code="RAW", warehouse="Stores - JF", posting_date="2026-10-02")]
        self.module._ensure_no_pending_reposting(source, rows)

    def test_masked_stock_valuation_cannot_be_used_for_return_pricing(self):
        self.meta.masked.add("stock_value_difference")
        with self.assertRaisesRegex(PermissionError, "没有读取原领料库存价值字段的权限"):
            self.module._permitted_query_fields("Stock Ledger Entry", ["stock_value_difference"])

    def test_submitted_return_cumulative_uses_exact_source_child_only(self):
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF", expense_account="Stock Adj - JF",
                            cost_center="Main - JF", project=None)
        evidence = {"row": source_row, "uom": "Nos", "flow": "FLOW-1", "qty": Decimal("2")}
        source = Record(name="MI-1")
        item = Record(jf_material_return_against_detail="ROW-1", item_code="RAW", stock_uom="Nos",
                      uom="Nos", conversion_factor=1, s_warehouse=None, t_warehouse="Stores - JF",
                      jf_flow_order="FLOW-1", expense_account="Stock Adj - JF",
                      cost_center="Main - JF", project=None, transfer_qty=1, qty=1)
        submitted = Record(name="MR-1", docstatus=1, items=[item])
        draft = Record(name="MR-2", docstatus=0, items=[Record(item)])
        totals = self.module._submitted_by_detail(source, {"ROW-1": evidence},
                                                  {"MR-1": submitted, "MR-2": draft})
        self.assertEqual(totals["ROW-1"], Decimal("1"))
        item.jf_material_return_against_detail = "OTHER-ROW"
        with self.assertRaisesRegex(ValidationError, "无法关联原领料明细"):
            self.module._submitted_by_detail(source, {"ROW-1": evidence}, {"MR-1": submitted})


if __name__ == "__main__":
    unittest.main()
