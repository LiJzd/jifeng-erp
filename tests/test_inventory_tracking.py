"""Exact archive/native-balance projection and whole-source guards; no live DB."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog_tests = load("tracking_catalog_fixture", "tests/test_inventory_catalog.py")
Record, NativeDocument = catalog_tests.Record, catalog_tests.NativeDocument
PROFILE = catalog_tests.PROFILE


class Meta(catalog_tests.Meta):
    def get_table_fields(self):
        return []

    def has_field(self, field):
        return field in self.environment.metas[self.doctype]


class TestInventoryTracking(unittest.TestCase):
    def setUp(self):
        # Reuse the real archive/Item/File permission fixture, not a DTO-only spy.
        self.env = catalog_tests.TestInventoryCatalog(methodName="runTest")
        self.env.setUp()
        self.addCleanup(self.env.doCleanups)
        self.frappe = self.env.frappe
        self.frappe._dict = Record
        self.frappe.get_meta = lambda doctype: Meta(self.env, doctype)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.utils = types.ModuleType("frappe.utils")
        self.utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(value)
        self.utils.today = lambda: "2026-10-04"
        self.utils.cint = lambda value: int(value or 0)
        dimensions = types.ModuleType("erpnext.stock.doctype.inventory_dimension.inventory_dimension")
        self.dimensions = []
        dimensions.get_inventory_dimensions = lambda: self.dimensions
        self.module_patch = patch.dict(sys.modules, {
            "frappe.utils": self.utils,
            "jifeng_erp.inventory_catalog": self.env.module,
            "erpnext.stock.doctype.inventory_dimension.inventory_dimension": dimensions,
        })
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.reports = load("tracking_real_inventory_reports", "jifeng_erp/inventory_reports.py")
        self.report_patch = patch.dict(sys.modules, {"jifeng_erp.inventory_reports": self.reports})
        self.report_patch.start()
        self.addCleanup(self.report_patch.stop)
        self.module = load("inventory_tracking_subject", "jifeng_erp/inventory_tracking.py")
        self.env.item.update(is_stock_item=1)
        self.env.profile.update(weight=Decimal("2.300000"))
        self.env.documents["Company", "JF"].update(default_currency="CNY")
        self.env.permitted["Item"].add("is_stock_item")
        self.env.metas["Item"]["is_stock_item"] = Record(fieldname="is_stock_item", fieldtype="Check")
        for doctype, fields in (
            ("Warehouse", self.module.WAREHOUSE_FIELDS),
            ("Stock Ledger Entry", self.module.LEDGER_FIELDS),
            ("Stock Entry", {"name", "docstatus", "company", "is_opening"}),
        ):
            self.env.permitted[doctype] = set(fields)
            self.env.metas[doctype] = {field: Record(fieldname=field, fieldtype="Data") for field in fields}
        self.env.metas["Company"]["default_currency"] = Record(fieldname="default_currency", fieldtype="Link")
        self.warehouse = self.env.add("Warehouse", "A", company="JF", is_group=0)
        self.voucher = self.env.add("Stock Entry", "VOUCHER-A", company="JF", docstatus=1, is_opening="Yes")
        self.entry = self.env.add("Stock Ledger Entry", "SLE-A", creation="2026-10-01 10:00:00", company="JF",
                                 item_code="ITEM-A", warehouse="A", stock_uom="Nos", posting_date=date(2026, 10, 1),
                                 posting_datetime="2026-10-01 10:00:00", docstatus=1, is_cancelled=0,
                                 actual_qty=Decimal("3.00"), qty_after_transaction=Decimal("3.00"),
                                 valuation_rate=Decimal("2.00"), stock_value=Decimal("6.00"),
                                 stock_value_difference=Decimal("6.00"), voucher_type="Stock Entry",
                                 voucher_no="VOUCHER-A", voucher_detail_no="DETAIL-A", batch_no=None,
                                 serial_no=None, serial_and_batch_bundle=None, has_serial_no=0, is_adjustment_entry=0)
        self.native_rows = [Record(item_code="ITEM-A", warehouse="A", company="JF", stock_uom="Nos",
                                   bal_qty=Decimal("3.00"), val_rate=Decimal("2.00"), bal_val=Decimal("6.00"))]
        self.reports._completed_valuation = Mock()
        self.native_hook = None
        self.reports._native_balance = Mock(side_effect=self.native)

    @staticmethod
    def matches(doc, filters):
        for field, expected in filters.items():
            actual = doc.get(field)
            if isinstance(expected, list):
                operator, value = expected
                if operator == "in" and actual not in value:
                    return False
                if operator == "<=" and actual > value:
                    return False
                if operator == "<" and actual >= value:
                    return False
            elif actual != expected:
                return False
        return True

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            return NativeDocument(deepcopy(doctype))
        return self.env.get_doc(doctype, name)

    def query_rows(self, doctype, filters, *, ignore_permissions=False):
        return sorted((doc for (kind, name), doc in self.env.documents.items()
                       if kind == doctype and self.matches(doc, filters)
                       and (ignore_permissions or (doctype, name) not in self.env.list_denials)),
                      key=lambda row: row.name)

    def get_list(self, doctype, filters, fields=None, limit_page_length=0, **kwargs):
        if doctype in (PROFILE, "Item"):
            return self.env.get_list(doctype, filters, fields, limit_page_length, **kwargs)
        rows = self.query_rows(doctype, filters)
        if (doctype, "read") in self.env.role_denials:
            raise PermissionError("native role denied")
        if limit_page_length:
            rows = rows[:limit_page_length]
        if kwargs.get("pluck"):
            return [row[kwargs["pluck"]] for row in rows]
        return [Record({field: row.get(field) for field in fields}) for row in rows]

    def get_all(self, doctype, filters, fields=None, limit_page_length=0, **kwargs):
        if doctype == "File":
            return self.env.get_all(doctype, filters, limit_page_length=limit_page_length, **kwargs)
        rows = self.query_rows(doctype, filters, ignore_permissions=True)
        if fields == ["*"]:
            return [Record(deepcopy(row)) for row in rows]
        return [Record({field: row.get(field) for field in fields}) for row in rows]

    def native(self, filters, entries, opening):
        # The native algorithm itself is verified elsewhere. This suite executes
        # real archive/SLE/source-scope helpers and checks its exact call inputs.
        if self.native_hook:
            self.native_hook(self.reports._native_balance.call_count)
        return deepcopy(self.native_rows)

    def query(self, **arguments):
        return self.module.query_tracking("物料", "JF", **arguments)

    def add_pair(self, index, kind="物料", company="JF", warehouse="A"):
        suffix = f"{index:04d}"
        item_name = "ITEM-" + suffix
        self.env.add("Item", item_name, item_code=item_name, item_name="分批商品", stock_uom="Kg",
                     is_stock_item=1, image=None)
        self.env.add(PROFILE, "PROFILE-" + suffix, company=company, archive_kind=kind, item_code=item_name,
                     legacy_number="号" + suffix, display_name="分批商品", inactive=1)
        self.env.add("Stock Ledger Entry", "SLE-" + suffix, **{
            key: value for key, value in self.entry.items() if key not in {"doctype", "name", "modified"}
        }).update(item_code=item_name, company=company, warehouse=warehouse, stock_uom="Kg")
        self.native_rows.append(Record(item_code=item_name, warehouse=warehouse, company=company, stock_uom="Kg",
                                       bal_qty=Decimal("0"), val_rate=Decimal("0"), bal_val=Decimal("0")))

    def test_exact_native_values_actual_units_and_unknown_weight_are_not_recomputed(self):
        self.env.file()
        before = deepcopy(self.env.documents)
        result = self.query()
        row = result["rows"][0]
        self.assertEqual((result["as_of"], result["total"], result["total_pages"], result["currency"]),
                         ("2026-10-04", 1, 1, "CNY"))
        self.assertEqual((row["stock_qty"], row["valuation_rate"], row["stock_value"], row["stock_uom"]),
                         ("3.00", "2.00", "6.00", "Nos"))
        self.assertEqual((row["profile"]["legacy_number"], row["profile"]["weight"], row["profile"]["display_uom"]),
                         (" 0007 ", "2.300000", "对（旧原文）"))
        self.assertIsNone(row["total_weight"])
        self.assertNotIn("list_price", row["profile"])
        self.assertEqual(row["image"]["url"], "/private/files/item.png?fid=EXACT")
        self.assertTrue(result["readonly"])
        self.assertFalse(result["can_write"])
        self.assertIn("不是档案参考价", result["basis"]["valuation_rate"])
        self.assertEqual(self.env.documents, before)
        for forbidden in (self.frappe.db.commit, self.frappe.db.sql, self.frappe.db.set_value,
                          self.frappe.new_doc, self.frappe.delete_doc):
            forbidden.assert_not_called()
        for call in self.reports._native_balance.call_args_list:
            filters, entries, opening = call.args
            self.assertEqual(filters, Record(company="JF", from_date=date(2026, 10, 4), to_date=date(2026, 10, 4),
                                              warehouse=["A"], item_code=["ITEM-A"], ignore_closing_balance=1,
                                              include_zero_stock_items=1))
            self.assertEqual([entry.name for entry in entries], ["SLE-A"])
            self.assertEqual(opening, {"Stock Entry": ["VOUCHER-A"]})
        self.assertNotIn("VOUCHER-A", repr(result))

    def test_true_total_and_pages_above_1000_include_inactive_historical_zero_pairs(self):
        for index in range(1100):
            self.add_pair(index)
        result = self.query(page="12", page_size="100")
        self.assertEqual((result["total"], result["total_pages"], len(result["rows"])), (1101, 12, 1))
        self.assertEqual(result["rows"][0]["profile_name"], "PROFILE-A")
        self.assertEqual(len(self.reports._native_balance.call_args.args[1]), 1101)
        batch_calls = [call for call in self.frappe.get_list.call_args_list if call.args[0] == PROFILE]
        self.assertTrue(all(call.kwargs["limit_page_length"] == 500 for call in batch_calls))
        self.assertTrue(all("inactive" not in call.kwargs["filters"] for call in batch_calls))

    def test_company_kind_search_and_single_leaf_warehouse_are_exact(self):
        self.add_pair(1, kind="材料")
        self.add_pair(2, company="OTHER")
        self.native_rows = self.native_rows[:1]
        self.env.add("Warehouse", "B", company="JF", is_group=0)
        self.env.list_denials.add(("Warehouse", "B"))
        result = self.query(search="0007", warehouse="A", as_of="2026-10-02")
        self.assertEqual((result["total"], result["search"], result["warehouse"], result["as_of"]),
                         (1, "0007", "A", "2026-10-02"))
        self.assertEqual(self.reports._native_balance.call_args.args[0]["item_code"], ["ITEM-A"])

    def test_initially_hidden_archive_is_outside_scope_but_hidden_linked_item_refuses_all(self):
        self.add_pair(1)
        self.env.list_denials.add((PROFILE, "PROFILE-0001"))
        self.native_rows = self.native_rows[:1]
        self.assertEqual(self.query()["total"], 1)
        self.env.list_denials.add(("Item", "ITEM-A"))
        with self.assertRaises(PermissionError):
            self.query()

    def test_all_company_warehouses_cannot_silently_drop_hidden_source(self):
        self.env.add("Warehouse", "HIDDEN", company="JF", is_group=0)
        self.env.list_denials.add(("Warehouse", "HIDDEN"))
        with self.assertRaises(PermissionError):
            self.query()
        self.reports._native_balance.assert_not_called()
        self.assertEqual(self.query(warehouse="A")["total"], 1)

    def test_group_wrong_company_missing_or_unreadable_warehouse_refuses_all(self):
        for name, values in (("GROUP", {"company": "JF", "is_group": 1}),
                             ("OTHER", {"company": "OTHER", "is_group": 0})):
            self.env.add("Warehouse", name, **values)
        for name in ("GROUP", "OTHER", "MISSING"):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                self.query(warehouse=name)
        self.env.doc_denials.add(("Warehouse", "A", "read"))
        with self.assertRaises(PermissionError):
            self.query()

    def test_hidden_ledger_and_full_document_denial_refuse_whole_balance(self):
        self.env.list_denials.add(("Stock Ledger Entry", "SLE-A"))
        with self.assertRaises(PermissionError):
            self.query()
        self.env.list_denials.clear()
        self.env.doc_denials.add(("Stock Ledger Entry", "SLE-A", "read"))
        with self.assertRaises(PermissionError):
            self.query()
        self.reports._native_balance.assert_not_called()

    def test_role_and_company_select_user_permission_denials(self):
        for doctype in (PROFILE, "Item", "Warehouse", "Stock Ledger Entry"):
            with self.subTest(doctype=doctype):
                self.env.role_denials.add((doctype, "read"))
                with self.assertRaises(PermissionError):
                    self.query()
                self.env.role_denials.clear()
        self.env.doc_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.query()
        self.env.doc_denials.clear()
        self.env.doc_denials.add(("Company", "JF", "read"))
        self.assertEqual(self.query()["total"], 1)
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.query()

    def test_optional_archive_mask_and_list_omission_are_not_filled_from_document(self):
        self.env.metas[PROFILE]["weight"].update(mask=1, permlevel=0)
        self.env.projection_omissions[PROFILE] = {"display_name"}
        result = self.query()
        self.assertNotIn("weight", result["rows"][0]["profile"])
        self.assertNotIn("display_name", result["rows"][0]["profile"])
        self.env.mask_levels[PROFILE] = {0}
        self.assertEqual(self.query()["rows"][0]["profile"]["weight"], "2.300000")

    def test_material_shape_color_old_number_and_owner_keep_saved_archive_values(self):
        self.env.profile.update(archive_kind="材料", shape="形状原文", color="颜色原文",
                                old_number=" 旧0007 ", owner_name="档案负责人", region="区域原文",
                                supplier="供应商档案原值", material="不适用的材质")
        result = self.module.query_tracking("材料", "JF")
        profile = result["rows"][0]["profile"]
        self.assertEqual([profile[field] for field in ("shape", "color", "old_number", "owner_name", "region", "supplier")],
                         ["形状原文", "颜色原文", " 旧0007 ", "档案负责人", "区域原文", "供应商档案原值"])
        self.assertNotIn("material", profile)
        self.env.metas[PROFILE]["shape"].update(mask=1, permlevel=0)
        self.env.projection_omissions[PROFILE] = {"owner_name"}
        profile = self.module.query_tracking("材料", "JF")["rows"][0]["profile"]
        self.assertNotIn("shape", profile)
        self.assertNotIn("owner_name", profile)

    def test_raw_material_only_fields_do_not_appear_in_supply_tracking(self):
        self.env.profile.update(shape="材料形状", color="材料颜色")
        profile = self.query()["rows"][0]["profile"]
        self.assertNotIn("shape", profile)
        self.assertNotIn("color", profile)

    def test_required_quantity_unit_warehouse_identity_and_company_currency_masks_refuse(self):
        for doctype, field in (("Stock Ledger Entry", "stock_value_difference"),
                               ("Item", "stock_uom"), ("Warehouse", "company")):
            with self.subTest(doctype=doctype, field=field):
                self.env.metas[doctype][field].update(mask=1)
                with self.assertRaises(PermissionError):
                    self.query()
                self.env.metas[doctype][field].pop("mask")
        self.env.metas["Company"]["default_currency"].update(mask=1)
        with self.assertRaises(PermissionError):
            self.query()

    def test_unreadable_inventory_dimension_is_not_used_in_derived_balance(self):
        self.dimensions.append(Record(fieldname="inventory_location"))
        with self.assertRaises(PermissionError):
            self.query()
        self.env.permitted["Stock Ledger Entry"].add("inventory_location")
        self.env.metas["Stock Ledger Entry"]["inventory_location"] = Record(fieldname="inventory_location")
        self.entry["inventory_location"] = "LOCATION-A"
        self.assertEqual(self.query()["total"], 1)

    def test_ledger_unit_conflict_and_non_stock_item_refuse(self):
        self.entry["stock_uom"] = "Kg"
        with self.assertRaises(ValueError):
            self.query()
        self.entry["stock_uom"] = "Nos"
        self.env.item["is_stock_item"] = 0
        with self.assertRaises(PermissionError):
            self.query()

    def test_missing_and_unsubmitted_native_source_refuse_all(self):
        self.voucher["docstatus"] = 0
        with self.assertRaisesRegex(ValueError, "提交状态"):
            self.query()
        del self.env.documents["Stock Entry", "VOUCHER-A"]
        with self.assertRaisesRegex(ValueError, "来源凭证不完整"):
            self.query()

    def test_pending_reposting_refuses_before_calculation(self):
        self.reports._completed_valuation.side_effect = ValueError("估价重算")
        with self.assertRaisesRegex(ValueError, "估价重算"):
            self.query()
        self.reports._native_balance.assert_not_called()

    def test_no_history_does_not_invent_zero_or_a_warehouse_for_archive(self):
        del self.env.documents["Stock Ledger Entry", "SLE-A"]
        result = self.query()
        self.assertEqual((result["total"], result["total_pages"], result["rows"]), (0, 0, []))
        self.reports._native_balance.assert_not_called()

    def test_native_missing_duplicate_extra_wrong_company_or_wrong_unit_row_refuses(self):
        original = deepcopy(self.native_rows)
        cases = [[], original * 2, [Record(original[0], item_code="UNSELECTED")],
                 [Record(original[0], warehouse="OTHER")], [Record(original[0], company="OTHER")],
                 [Record(original[0], stock_uom="Kg")], [Record(original[0], currency="USD")]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.native_rows = rows
                self.query()

    def test_non_finite_missing_bool_and_invalid_quantities_or_money_are_rejected(self):
        for field in ("bal_qty", "val_rate", "bal_val"):
            original = self.native_rows[0][field]
            for value in (None, True, "NaN", "Infinity", "-Infinity", "x"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.native_rows[0][field] = value
                    self.query()
            self.native_rows[0][field] = original
        self.entry["actual_qty"] = True
        with self.assertRaises(ValueError):
            self.query()

    def test_legal_zero_negative_exponent_and_high_precision_remain_decimal_text(self):
        self.native_rows[0].update(bal_qty=Decimal("-0.00000000000000001"), val_rate=Decimal("0E-12"),
                                   bal_val=Decimal("-12345678901234567890.00000000001"))
        row = self.query()["rows"][0]
        self.assertEqual((row["stock_qty"], row["valuation_rate"], row["stock_value"]),
                         ("-1E-17", "0E-12", "-12345678901234567890.00000000001"))

    def test_permission_revoked_during_native_balance_refuses_all(self):
        self.native_hook = lambda count: self.env.doc_denials.add(("Item", "ITEM-A", "read")) if count == 1 else None
        with self.assertRaises(PermissionError):
            self.query()

    def test_ledger_change_during_native_balance_refuses_all(self):
        self.native_hook = lambda count: self.entry.update(actual_qty=Decimal("4")) if count == 1 else None
        with self.assertRaises(PermissionError):
            self.query()

    def test_native_reconciliation_result_change_without_ledger_change_refuses_all(self):
        self.native_hook = lambda count: self.native_rows[0].update(bal_qty=Decimal("4")) if count == 2 else None
        with self.assertRaises(PermissionError):
            self.query()

    def test_late_native_pass_cannot_revoke_source_permission_after_the_last_guard(self):
        for doctype, name in ((PROFILE, "PROFILE-A"), ("Item", "ITEM-A"),
                              ("Warehouse", "A"), ("Stock Ledger Entry", "SLE-A")):
            with self.subTest(doctype=doctype):
                self.reports._native_balance.reset_mock()
                self.env.doc_denials.clear()
                self.native_hook = lambda count: self.env.doc_denials.add((doctype, name, "read")) if count == 2 else None
                with self.assertRaises(PermissionError):
                    self.query()

    def test_late_private_image_download_revocation_refuses_previously_projected_url(self):
        self.env.file()
        self.native_hook = lambda count: self.env.download_denials.add("EXACT") if count == 2 else None
        with self.assertRaises(PermissionError):
            self.query()

    def test_invalid_date_page_size_and_search_or_warehouse_are_not_coerced(self):
        for arguments in ({"as_of": "2026-2-03"}, {"as_of": "2026-02-30"}, {"as_of": " 2026-10-04"},
                          {"page": True}, {"page": "1e1"}, {"page": 0}, {"page_size": 101},
                          {"warehouse": ""}, {"warehouse": " A"}, {"search": None}, {"search": "x" * 141}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.query(**arguments)
        with self.assertRaisesRegex(ValueError, "页码超过"):
            self.query(page=2)


if __name__ == "__main__":
    unittest.main()
