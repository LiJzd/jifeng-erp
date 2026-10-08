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

    def check_permission(self, permission):
        return None


class ValidationError(ValueError):
    pass


class PermissionError(ValueError):
    pass


class Meta:
    def __init__(self, name="Stock Entry Detail"):
        self.doctype = "DocType"
        self.name = name
        self.masked = set()
        self.last_parenttype = None
        self.allowed = {"item_code", "qty", "transfer_qty", "jf_material_return_against_detail",
                        "amount", "total_additional_costs", "additional_costs"}

    def get_permitted_fieldnames(self, parenttype=None, permission_type="read"):
        self.last_parenttype = parenttype
        self.last_permission_type = permission_type
        return self.allowed

    def get_permlevel_access(self, **kwargs):
        return set() if kwargs.get("permission_type") == "mask" else {0}

    def get_field(self, name):
        if name == "items":
            return Record(fieldtype="Table", options="Stock Entry Detail", permlevel=0,
                          mask=int(name in self.masked))
        if name == "additional_costs":
            return Record(fieldtype="Table", options="Landed Cost Taxes and Charges", permlevel=0, mask=0)
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
        self.meta = Meta("Stock Ledger Entry")
        self.metas = {}
        self.frappe.get_meta = lambda doctype: self.meta if doctype == "Stock Ledger Entry" else self.metas.setdefault(doctype, Meta(doctype))

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
        self.permission_calls = []

        def get_permitted_fields(doctype, **kwargs):
            self.permission_calls.append((doctype, kwargs.get("parenttype"),
                                          kwargs.get("permission_type"), kwargs.get("ignore_virtual")))
            if doctype == "Stock Ledger Entry":
                return {"name", "company", "docstatus", "voucher_type", "voucher_no", "voucher_detail_no",
                        "item_code", "warehouse", "stock_uom", "actual_qty", "stock_value_difference",
                        "is_cancelled", "is_adjustment_entry"}
            meta = self.frappe.get_meta(doctype)
            # Frappe's permission helper includes standard scalar fields omitted
            # by Meta.get_permitted_fieldnames().
            return set(meta.allowed) | {"name", "docstatus"}

        model.get_permitted_fields = get_permitted_fields
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

    def test_child_meta_uses_actual_meta_name_not_doc_type_label(self):
        child_meta = Meta("Stock Entry Detail")
        self.module._permitted_fields(child_meta, ("name", "docstatus", "item_code"), parenttype="Stock Entry")
        self.assertEqual(child_meta.name, "Stock Entry Detail")
        self.assertEqual(self.permission_calls[-1], ("Stock Entry Detail", "Stock Entry", "read", True))

    def test_scalar_standard_fields_use_native_permission_model_but_business_fields_stay_authorized(self):
        child_meta = Meta("Stock Entry Detail")
        meta_projection = set(child_meta.get_permitted_fieldnames(
            parenttype="Stock Entry", permission_type="read"))
        self.assertNotIn("name", meta_projection)
        self.assertNotIn("docstatus", meta_projection)
        self.module._permitted_fields(child_meta, ("name", "docstatus"), parenttype="Stock Entry")
        with patch.object(self.module, "get_permitted_fields", return_value={"name", "docstatus"}):
            with self.assertRaisesRegex(PermissionError, "没有读取或填写领料退库所需字段的权限"):
                self.module._permitted_fields(child_meta, ("name", "docstatus", "item_code"),
                                              parenttype="Stock Entry")

    def test_parent_items_table_mask_denies_source_rows_but_unmasked_normal_source_is_allowed(self):
        parent_meta = Meta("Stock Entry")
        parent = Record(doctype="Stock Entry", meta=parent_meta)
        child_meta = Meta("Stock Entry Detail")
        self.metas["Stock Entry Detail"] = child_meta

        self.module._permitted_table(parent, "items", "Stock Entry Detail", ("item_code",))
        parent_meta.masked.add("items")
        with self.assertRaisesRegex(PermissionError, "没有读取或填写领料退库明细的权限"):
            self.module._permitted_table(parent, "items", "Stock Entry Detail", ("item_code",))

    def test_return_cannot_add_header_or_row_additional_costs(self):
        header_meta = Meta("Stock Entry")
        row = Record(additional_cost=0, landed_cost_voucher_amount=0)
        doc = Record(doctype="Stock Entry", meta=header_meta, additional_costs=[], total_additional_costs=0,
                     items=[row])
        self.module._no_additional_costs(doc)
        doc.additional_costs = [Record(name="CHARGE", amount=1)]
        with self.assertRaisesRegex(ValidationError, "不得增加额外费用"):
            self.module._no_additional_costs(doc)
        doc.additional_costs = []
        row.additional_cost = 1
        with self.assertRaisesRegex(ValidationError, "明细不得增加额外费用"):
            self.module._no_additional_costs(doc)

    def test_flow_net_helper_reads_source_linked_submitted_returns_and_caps_cumulatively(self):
        rows = [Record(name="MR-1"), Record(name="MR-2")]
        docs = {row.name: Record(name=row.name, company="JF", docstatus=1, is_return=0,
                                 jf_material_return_against="MI-1", jf_material_issue_flow="FLOW-1",
                                 items=[Record(transfer_qty=Decimal("0.7"))]) for row in rows}
        self.frappe.db = types.SimpleNamespace(sql=lambda *args, **kwargs: rows)
        self.frappe.get_doc = lambda doctype, name, **kwargs: docs[name]
        source = Record(name="MI-1", company="JF")
        evidence = {"ROW-1": {"row": Record(item_code="RAW"), "qty": Decimal("1.5")}}
        with patch.object(self.module, "_permitted_fields"), \
             patch.object(self.module, "_permitted_table"), \
             patch.object(self.module, "_source", return_value=source), \
             patch.object(self.module, "_source_rows", return_value=evidence), \
             patch.object(self.module, "_submitted_by_detail", return_value={"ROW-1": Decimal("1.4")}):
            totals = self.module.submitted_return_quantities_for_flow("FLOW-1", {"MI-1"})
        self.assertEqual(totals["RAW"], Decimal("1.4"))
        with patch.object(self.module, "_permitted_fields"), \
             patch.object(self.module, "_permitted_table"), \
             patch.object(self.module, "_source", return_value=source), \
             patch.object(self.module, "_source_rows", return_value={
                 "ROW-1": {"row": Record(item_code="RAW"), "qty": Decimal("1.3")}}), \
             patch.object(self.module, "_submitted_by_detail", return_value={"ROW-1": Decimal("1.4")}):
            with self.assertRaisesRegex(ValidationError, "累计超过"):
                self.module.submitted_return_quantities_for_flow("FLOW-1", {"MI-1"})

    def test_submitted_return_cumulative_uses_exact_source_child_only(self):
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF", expense_account="Stock Adj - JF",
                            cost_center="Main - JF", project=None)
        evidence = {"row": source_row, "uom": "Nos", "flow": "FLOW-1", "qty": Decimal("2")}
        source = Record(name="MI-1")
        item = Record(jf_material_return_against_detail="ROW-1", item_code="RAW", stock_uom="Nos",
                      uom="Nos", conversion_factor=1, s_warehouse=None, t_warehouse="Stores - JF",
                      jf_flow_order="FLOW-1", expense_account="Stock Adj - JF",
                      cost_center="Main - JF", project=None, transfer_qty=1, qty=1)
        submitted = Record(name="MR-1", docstatus=1, items=[item], doctype="Stock Entry",
                           meta=Meta("Stock Entry"), additional_costs=[], total_additional_costs=0)
        draft = Record(name="MR-2", docstatus=0, items=[Record(item)], doctype="Stock Entry",
                       meta=Meta("Stock Entry"), additional_costs=[], total_additional_costs=0)
        totals = self.module._submitted_by_detail(source, {"ROW-1": evidence},
                                                  {"MR-1": submitted, "MR-2": draft})
        self.assertEqual(totals["ROW-1"], Decimal("1"))
        item.jf_material_return_against_detail = "OTHER-ROW"
        with self.assertRaisesRegex(ValidationError, "无法关联原领料明细"):
            self.module._submitted_by_detail(source, {"ROW-1": evidence}, {"MR-1": submitted})

    def test_submitted_document_is_excluded_from_prior_total_before_current_is_added(self):
        source_row = Record(name="ROW-1", item_code="RAW", s_warehouse="Stores - JF",
                            expense_account="Stock Adj - JF", cost_center="Main - JF", project=None)
        evidence = {"ROW-1": {"row": source_row, "uom": "Nos", "flow": "FLOW-1",
                              "qty": Decimal("2")}}
        def return_doc(name, quantity):
            row = Record(jf_material_return_against_detail="ROW-1", item_code="RAW", stock_uom="Nos",
                         uom="Nos", conversion_factor=1, s_warehouse=None, t_warehouse="Stores - JF",
                         jf_flow_order="FLOW-1", expense_account="Stock Adj - JF", cost_center="Main - JF",
                         project=None, transfer_qty=quantity, qty=quantity, additional_cost=0,
                         landed_cost_voucher_amount=0)
            return Record(name=name, docstatus=1, items=[row], doctype="Stock Entry",
                          meta=Meta("Stock Entry"), additional_costs=[], total_additional_costs=0)

        current = return_doc("MR-CURRENT", Decimal("1"))
        other = return_doc("MR-OTHER", Decimal("1"))
        prior = self.module._submitted_by_other_returns(
            Record(name="MI-1"), evidence,
            {current.name: current, other.name: other}, current.name)
        self.assertEqual(prior["ROW-1"], Decimal("1"))
        self.module._ensure_within_source_quantities(
            {"ROW-1": Decimal("1")}, prior, {"ROW-1": {"qty": Decimal("2")}})

    def test_return_validation_only_accepts_native_save_submit_update_and_cancel_states(self):
        self.assertEqual(self.module._return_validation_state(Record(docstatus=0), None), "active")
        self.assertEqual(self.module._return_validation_state(
            Record(docstatus=1, _action="submit"), None), "active")
        self.assertEqual(self.module._return_validation_state(
            Record(docstatus=1, _action="update_after_submit"), None), "active")
        previous = Record(docstatus=1)
        self.assertEqual(self.module._return_validation_state(
            Record(docstatus=2, _action="cancel"), previous), "cancel")
        for doc, prior in ((Record(docstatus=1), None),
                           (Record(docstatus=2, _action="cancel"), None),
                           (Record(docstatus=3), None)):
            with self.assertRaisesRegex(ValidationError, "只能保存草稿、提交或按原生流程取消"):
                self.module._return_validation_state(doc, prior)


if __name__ == "__main__":
    unittest.main()
