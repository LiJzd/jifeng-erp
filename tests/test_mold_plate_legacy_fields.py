"""Legacy tool text stays independent of native identities, stock and HR data."""

from copy import deepcopy
import datetime
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get


class Document:
    def get(self, field, default=None):
        return getattr(self, field, default)


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema(kind):
    return json.loads((ROOT / f"jifeng_erp/jifeng_erp/doctype/jifeng_{kind}/jifeng_{kind}.json").read_text(encoding="utf-8"))


class TestToolLegacyFields(unittest.TestCase):
    def setUp(self):
        self.references = {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.references[doctype, name])
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(value)
        document = types.ModuleType("frappe.model.document")
        document.Document = Document
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "frappe.model.document": document}):
            access = load("legacy_catalog_access", "jifeng_erp/catalog_access.py")
            with patch.dict(sys.modules, {"jifeng_erp.catalog_access": access}):
                self.mold_module = load("legacy_mold", "jifeng_erp/jifeng_erp/doctype/jifeng_mold/jifeng_mold.py")
                self.plate_module = load("legacy_plate", "jifeng_erp/jifeng_erp/doctype/jifeng_plate/jifeng_plate.py")
        self.item = self.reference("Item", "ITEM")
        self.employee = self.reference("Employee", "EMP", company="JF", private_bank="must not be exposed")
        self.plate = self.plate_module.JifengPlate()
        self.plate.__dict__.update(name="PLATE", plate_no=" 业务身份 ", company="JF", item_code="ITEM",
            plate_date="2026-10-01", maker="EMP", modeler=None, reference_cost=3, retired=0,
            retired_date=None, retired_reason=None)
        self.mold = self.mold_module.JifengMold()
        self.mold.__dict__.update(name="MOLD", mold_no="M-1", product_code="P-1", company="JF", item_code="ITEM",
            mold_date="2026-10-01", maker="EMP", quantity=1, amount=2, scrapped=0, scrap_date=None,
            scrap_reason=None, copy_from=None)

    def reference(self, doctype, name, **fields):
        result = types.SimpleNamespace(name=name, check_permission=Mock(), **fields)
        self.references[doctype, name] = result
        return result

    def test_schema_original_text_is_optional_and_has_no_migration_defaults(self):
        plate = schema("plate")
        fields = {df["fieldname"]: df for df in plate["fields"]}
        for field in ("legacy_number", "plate_type", "product_code", "assessment_score", "quantity"):
            self.assertEqual(fields[field]["fieldtype"], "Data")
            self.assertFalse(fields[field].get("reqd"))
            self.assertFalse(fields[field].get("unique"))
            self.assertNotIn("default", fields[field])
        self.assertEqual(fields["plate_no"]["unique"], 1)
        self.assertEqual(fields["plate_no"]["reqd"], 1)
        self.assertEqual(plate["title_field"], "plate_no")
        self.assertEqual(fields["item_code"]["options"], "Item")
        self.assertEqual(fields["image"]["fieldtype"], "Attach Image")
        self.assertEqual(fields["reference_cost"]["fieldtype"], "Currency")
        self.assertEqual(fields["copy_from"]["options"], "Jifeng Plate")
        self.assertEqual(fields["legacy_copy"]["fieldtype"], "Check")
        order = plate["field_order"]
        self.assertEqual(order[1:11], ["legacy_number", "plate_type", "product_code", "assessment_score", "maker",
                                     "quantity", "notes", "location", "retired", "legacy_copy"])
        mold = schema("mold")
        mold_fields = {df["fieldname"]: df for df in mold["fields"]}
        self.assertEqual(mold_fields["legacy_number"]["fieldtype"], "Data")
        self.assertNotIn("default", mold_fields["legacy_number"])
        self.assertEqual(mold_fields["quantity"]["fieldtype"], "Float")
        self.assertEqual(mold_fields["quantity"]["default"], "1")
        self.assertEqual(mold_fields["legacy_copy"]["fieldtype"], "Check")
        for definition in (plate, mold):
            self.assertEqual({df["role"] for df in definition["permissions"]},
                             {"System Manager", "Stock Manager", "Manufacturing Manager"})

    def test_new_missing_score_default_does_not_fill_other_originals(self):
        self.plate.before_insert()
        self.assertEqual(self.plate.assessment_score, "1")
        for field in ("legacy_number", "plate_type", "product_code", "quantity", "copy_from"):
            self.assertIsNone(self.plate.get(field))

    def test_explicit_score_empty_zero_and_exact_numeric_text_are_retained(self):
        for value in ("", "0", "-0", "01.0000000000000000007", " 1e3 ", "待核原文"):
            with self.subTest(value=value):
                self.plate.assessment_score = value
                self.plate.before_insert()
                self.plate.validate()
                self.assertEqual(self.plate.assessment_score, value)

    def test_historic_absent_fields_remain_absent_on_validation(self):
        self.plate.validate()
        self.assertEqual(self.plate.plate_no, "业务身份")
        self.assertTrue(all(self.plate.get(field) is None for field in (
            "legacy_number", "plate_type", "product_code", "assessment_score", "quantity", "copy_from")))

    def test_legacy_text_and_copy_mark_never_copy_or_change_native_business_fields(self):
        originals = dict(legacy_number=" 000007 ", plate_type=" 类型原文 ", product_code=" 旧产品/编号 ",
                         assessment_score="-0.0100", quantity=" 001.00000000000000009 ", legacy_copy=1)
        self.plate.__dict__.update(originals)
        self.plate.before_insert()
        self.plate.validate()
        self.assertEqual({field: self.plate.get(field) for field in originals}, originals)
        self.assertEqual((self.plate.item_code, self.plate.reference_cost), ("ITEM", 3))
        self.frappe.get_doc.assert_has_calls([unittest.mock.call("Item", "ITEM"), unittest.mock.call("Employee", "EMP")])
        self.assertTrue(all(call.args[0] != "Jifeng Plate" for call in self.frappe.get_doc.call_args_list))
        self.item.check_permission.assert_called_with("read")
        self.employee.check_permission.assert_called_with("select")

    def test_copy_source_is_readable_same_company_and_only_records_a_link(self):
        source = self.reference("Jifeng Plate", "SOURCE", company="JF", legacy_number="not copied", plate_type="not copied")
        self.plate.copy_from = "SOURCE"
        self.plate.validate()
        source.check_permission.assert_called_once_with("read")
        self.assertIsNone(self.plate.get("legacy_number"))
        self.assertIsNone(self.plate.get("plate_type"))
        source.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "同一公司"):
            self.plate.validate()
        source.check_permission.side_effect = PermissionError("hidden source")
        with self.assertRaises(PermissionError):
            self.plate.validate()

    def test_copy_self_and_transitive_cycles_are_rejected(self):
        self.plate.copy_from = "PLATE"
        with self.assertRaisesRegex(ValueError, "循环"):
            self.plate.validate()
        self.reference("Jifeng Plate", "SOURCE", company="JF", copy_from="PLATE")
        self.plate.copy_from = "SOURCE"
        with self.assertRaisesRegex(ValueError, "循环"):
            self.plate.validate()

    def test_reference_cost_and_native_employee_guards_still_apply(self):
        self.plate.reference_cost = "NaN"
        with self.assertRaises(ValueError):
            self.plate.validate()
        self.plate.reference_cost = 3
        self.employee.check_permission.side_effect = PermissionError("employee cannot be selected")
        with self.assertRaises(PermissionError):
            self.plate.validate()
        self.employee.check_permission.assert_called_with("select")

    def test_mold_markers_do_not_change_quantity_amount_or_existing_source_rules(self):
        self.mold.legacy_number, self.mold.legacy_copy = " 00003 ", 1
        self.mold.validate()
        self.assertEqual((self.mold.legacy_number, self.mold.legacy_copy, self.mold.quantity, self.mold.amount),
                         (" 00003 ", 1, 1, 2))
        self.mold.quantity = 0
        with self.assertRaises(ValueError):
            self.mold.validate()


class TestToolOriginalReport(unittest.TestCase):
    def setUp(self):
        self.docs, self.hidden = {}, {}
        frappe = types.ModuleType("frappe")
        frappe._dict, frappe.PermissionError = Record, PermissionError
        frappe.session = Record(user="stock-manager@example.invalid")
        frappe.has_permission = Mock(return_value=True)
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.company = types.SimpleNamespace(check_permission=Mock())
        frappe.get_doc = Mock(return_value=self.company)
        frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))
        fields = {definition["name"]: {"name", *(field["fieldname"] for field in definition["fields"])}
                  for definition in (schema("mold"), schema("plate"))}
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=lambda doctype, **kwargs: fields[doctype] - self.hidden.get(doctype, set()))
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, kind="Data", options=None, width=130: {
            "fieldname": field, "fieldtype": kind, "label": label, "options": options}
        scope.decimal_number = lambda value: Decimal(str(value or 0))
        scope.readable_documents = Mock(side_effect=lambda doctype, filters, **kwargs: {
            doc.name: doc for doc in self.docs.get(doctype, []) if not filters.get("name") or doc.name == filters["name"]})
        picture = types.ModuleType("jifeng_erp.production_picture")
        picture._readable_image = Mock(return_value=("", "无附件权限"))
        picture._normalize_image_url = Mock()
        access = types.ModuleType("jifeng_erp.catalog_access")
        access.check_employee = Mock()
        self.picture, self.access, self.scope = picture, access, scope
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model, "frappe.utils": utils,
                "jifeng_erp.inventory_scope": scope, "jifeng_erp.production_picture": picture}):
            self.module = load("tool_report_legacy", "jifeng_erp/catalog_reports.py")
        self.runtime = patch.dict(sys.modules, {"jifeng_erp.catalog_access": access})
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.plate = Record(name="PLATE", company="JF", item_code=None, plate_no="native-identity", plate_date="2026-10-01",
            maker="EMP", modeler=None, location="原库位", retired=0, retired_date=None, retired_reason=None,
            reference_cost=3, image="/private/files/old.png", legacy_number=" 000009 ", plate_type=" 原类型 ",
            product_code="旧产品号", assessment_score="01.00000000000009", quantity=" 0 ", legacy_copy=1,
            copy_from=None, notes="原文\n说明")
        self.docs["Jifeng Plate"] = [self.plate]

    def test_plate_report_preserves_originals_and_does_not_compute_from_quantity_or_score(self):
        before = deepcopy(self.plate)
        columns, rows = self.module.plate_catalog({"company": "JF"})
        for field in ("legacy_number", "plate_type", "product_code", "assessment_score", "quantity"):
            self.assertEqual(rows[0][field], self.plate[field])
            self.assertEqual(next(column["fieldtype"] for column in columns if column["fieldname"] == field), "Data")
        self.assertEqual(rows[0]["number"], "native-identity")
        self.assertEqual(rows[0]["reference_amount"], Decimal(3))
        self.assertEqual(rows[0]["catalog_notes"], "原文\n说明")
        self.assertEqual(rows[0]["legacy_copy"], 1)
        self.assertEqual(self.plate, before)
        self.picture._readable_image.assert_called_once_with("/private/files/old.png")

    def test_historic_none_and_explicit_empty_report_values_are_not_defaulted(self):
        for value in (None, "", "0", "-0"):
            self.plate["assessment_score"] = value
            self.plate["quantity"] = value
            row = self.module.plate_catalog({"company": "JF"})[1][0]
            self.assertEqual((row["assessment_score"], row["quantity"]), (value, value))

    def test_protected_original_field_refuses_report_before_loading_rows_or_images(self):
        for field in ("legacy_number", "plate_type", "assessment_score", "quantity", "legacy_copy", "copy_from"):
            self.hidden["Jifeng Plate"] = {field}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.plate_catalog({"company": "JF"})
        self.scope.readable_documents.assert_not_called()
        self.picture._readable_image.assert_not_called()

    def test_hidden_or_foreign_copy_source_is_not_exposed(self):
        self.plate["copy_from"] = "SOURCE"
        self.assertIsNone(self.module.plate_catalog({"company": "JF"})[1][0]["copy_from"])
        self.docs["Jifeng Plate"].append(Record(**{**self.plate, "name": "SOURCE", "company": "OTHER", "copy_from": None}))
        self.assertIsNone(self.module.plate_catalog({"company": "JF"})[1][0]["copy_from"])
        self.docs["Jifeng Plate"][-1]["company"] = "JF"
        self.assertEqual(self.module.plate_catalog({"company": "JF"})[1][0]["copy_from"], "SOURCE")

    def test_mold_keeps_numeric_reference_and_returns_separate_markers(self):
        self.docs["Jifeng Mold"] = [Record(name="MOLD", company="JF", item_code=None, mold_no="M-1", product_code="P-1",
            mold_date="2026-10-01", maker=None, location="库位", scrapped=0, scrap_date=None, scrap_reason=None,
            amount=2, quantity="1.5", image=None, legacy_number="000004", legacy_copy=0, copy_from=None, remark="说明")]
        columns, rows = self.module.mold_catalog({"company": "JF"})
        self.assertEqual((rows[0]["legacy_number"], rows[0]["legacy_copy"], rows[0]["quantity"]), ("000004", 0, Decimal("1.5")))
        self.assertEqual(next(column["fieldtype"] for column in columns if column["fieldname"] == "quantity"), "Float")
        self.assertEqual(rows[0]["catalog_notes"], "说明")


if __name__ == "__main__":
    unittest.main()
