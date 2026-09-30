"""Catalog reference boundaries, without a database or additional HR grants."""

import datetime
import importlib.util
import inspect
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, call, patch


ROOT = pathlib.Path(__file__).parents[1]


class Document:
    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCatalogProfiles(unittest.TestCase):
    def setUp(self):
        self.references = {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.references[doctype, name])
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.frappe.whitelist = lambda: lambda function: function
        self.frappe.db = types.SimpleNamespace(get_value=Mock())
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(value)
        self.frappe.utils = utils
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = Document
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.recipe_totals = Mock(return_value=(0, 0))
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                      "frappe.model": model, "frappe.model.document": document,
                                      "jifeng_erp.workflow": workflow}):
            access = load_module("jifeng_erp.catalog_access", "jifeng_erp/catalog_access.py")
            with patch.dict(sys.modules, {"jifeng_erp.catalog_access": access}):
                self.mold_module = load_module("mold_under_test", "jifeng_erp/jifeng_erp/doctype/jifeng_mold/jifeng_mold.py")
                self.plate_module = load_module("plate_under_test", "jifeng_erp/jifeng_erp/doctype/jifeng_plate/jifeng_plate.py")
                self.profile_module = load_module("profile_under_test", "jifeng_erp/jifeng_erp/doctype/jifeng_product_profile/jifeng_product_profile.py")
        self.access = access
        self.item = self.reference("Item", "ITEM", image="/files/item.png")
        self.employee = self.reference("Employee", "EMP", company="COMPANY", bank_ac_no="private")
        self.reference("Company", "COMPANY")
        self.mold = self.mold_module.JifengMold()
        self.mold.__dict__.update(name="MOLD", mold_no=" M-1 ", product_code=" P-1 ",
                                  company="COMPANY", mold_date="2026-09-01", quantity=1, amount=2,
                                  item_code="ITEM", maker="EMP", scrapped=0, scrap_date=None,
                                  scrap_reason=None, copy_from=None)
        self.plate = self.plate_module.JifengPlate()
        self.plate.__dict__.update(name="PLATE", plate_no=" P-1 ", company="COMPANY",
                                   plate_date="2026-09-01", item_code="ITEM", maker="EMP", modeler="EMP",
                                   reference_cost=3, retired=0, retired_date=None, retired_reason=None)
        self.profile = self.profile_module.JifengProductProfile()
        self.profile.__dict__.update(item_code="ITEM", company="COMPANY", supplier=None, bom=None,
                                     materials=[], operations=[], image="old-image", list_price=0,
                                     blank_price=0, weight=0, silver_weight=0, stone_weight=0)

    def reference(self, doctype, name, **values):
        reference = types.SimpleNamespace(name=name, check_permission=Mock(), **values)
        self.references[doctype, name] = reference
        return reference

    def test_employee_selection_returns_only_safe_fields(self):
        result = self.access.check_employee("EMP", "COMPANY", "制作人员")
        self.employee.check_permission.assert_called_once_with("select")
        self.assertEqual(result, {"name": "EMP", "company": "COMPANY"})

    def test_empty_employee_does_not_read_hr(self):
        self.assertIsNone(self.access.check_employee(None, "COMPANY", "制作人员"))
        self.frappe.get_doc.assert_not_called()

    def test_employee_permission_precedes_company_disclosure(self):
        self.employee.company = "FOREIGN"
        self.employee.check_permission.side_effect = PermissionError("hidden employee")
        with self.assertRaisesRegex(PermissionError, "hidden employee"):
            self.access.check_employee("EMP", "COMPANY", "制作人员")

    def test_employee_in_another_company_is_rejected(self):
        self.employee.company = "FOREIGN"
        with self.assertRaisesRegex(ValueError, "制作人员必须属于当前公司"):
            self.mold.validate()

    def test_mold_requires_full_item_read_and_only_employee_selection(self):
        self.mold.validate()
        self.item.check_permission.assert_called_once_with("read")
        self.employee.check_permission.assert_called_once_with("select")
        self.assertEqual((self.mold.mold_no, self.mold.product_code), ("M-1", "P-1"))

    def test_mold_hidden_item_is_rejected(self):
        self.item.check_permission.side_effect = PermissionError("hidden item")
        with self.assertRaisesRegex(PermissionError, "hidden item"):
            self.mold.validate()

    def test_mold_optional_links_still_allow_minimum_archive(self):
        self.mold.item_code = self.mold.maker = None
        self.mold.validate()
        self.frappe.get_doc.assert_not_called()

    def test_mold_unselectable_employee_is_rejected(self):
        self.employee.check_permission.side_effect = PermissionError("hidden employee")
        with self.assertRaises(PermissionError):
            self.mold.validate()

    def test_mold_invalid_quantity_or_reference_amount_is_rejected(self):
        for field, value in (("quantity", 0), ("quantity", -1), ("amount", "NaN"), ("amount", -1)):
            with self.subTest(field=field, value=value):
                old = getattr(self.mold, field)
                setattr(self.mold, field, value)
                with self.assertRaises(ValueError):
                    self.mold.validate()
                setattr(self.mold, field, old)

    def test_mold_scrap_dates_and_reactivation(self):
        self.mold.scrapped = 1
        with self.assertRaisesRegex(ValueError, "报废日期"):
            self.mold.validate()
        self.mold.scrap_date = "2026-08-31"
        with self.assertRaisesRegex(ValueError, "早于"):
            self.mold.validate()
        self.mold.scrap_date, self.mold.scrap_reason = "2026-09-01", "test"
        self.mold.validate()
        self.mold.scrapped = 0
        self.mold.validate()
        self.assertIsNone(self.mold.scrap_date)
        self.assertIsNone(self.mold.scrap_reason)

    def test_copy_source_requires_read_and_same_company(self):
        source = self.reference("Jifeng Mold", "SOURCE", company="COMPANY", copy_from=None)
        self.mold.copy_from = "SOURCE"
        self.mold.validate()
        source.check_permission.assert_called_once_with("read")
        source.company = "FOREIGN"
        with self.assertRaisesRegex(ValueError, "同一公司"):
            self.mold.validate()
        source.check_permission.side_effect = PermissionError("hidden source")
        with self.assertRaises(PermissionError):
            self.mold.validate()

    def test_copy_source_cycle_is_rejected(self):
        self.reference("Jifeng Mold", "SOURCE", company="COMPANY", copy_from="MOLD")
        self.mold.copy_from = "SOURCE"
        with self.assertRaisesRegex(ValueError, "循环"):
            self.mold.validate()

    def test_plate_requires_both_employee_select_permissions(self):
        modeler = self.reference("Employee", "MODELER", company="COMPANY")
        self.plate.modeler = "MODELER"
        self.plate.validate()
        self.item.check_permission.assert_called_once_with("read")
        self.employee.check_permission.assert_called_once_with("select")
        modeler.check_permission.assert_called_once_with("select")

    def test_plate_unselectable_maker_or_modeler_is_rejected(self):
        for field in ("maker", "modeler"):
            with self.subTest(field=field):
                self.reference("Employee", "HIDDEN", company="COMPANY").check_permission.side_effect = PermissionError("hidden")
                old = getattr(self.plate, field)
                setattr(self.plate, field, "HIDDEN")
                with self.assertRaises(PermissionError):
                    self.plate.validate()
                setattr(self.plate, field, old)

    def test_plate_foreign_maker_or_modeler_is_rejected(self):
        self.reference("Employee", "FOREIGN", company="FOREIGN")
        for field, label in (("maker", "制版人员"), ("modeler", "建模人员")):
            with self.subTest(field=field):
                old = getattr(self.plate, field)
                setattr(self.plate, field, "FOREIGN")
                with self.assertRaisesRegex(ValueError, label):
                    self.plate.validate()
                setattr(self.plate, field, old)

    def test_plate_hidden_item_is_rejected(self):
        self.item.check_permission.side_effect = PermissionError("hidden item")
        with self.assertRaises(PermissionError):
            self.plate.validate()

    def test_plate_retirement_dates_and_reactivation(self):
        self.plate.retired = 1
        self.plate.retired_date = "2026-08-31"
        with self.assertRaisesRegex(ValueError, "早于"):
            self.plate.validate()
        self.plate.retired_date, self.plate.retired_reason = "2026-09-01", "test"
        self.plate.validate()
        self.plate.retired = 0
        self.plate.validate()
        self.assertIsNone(self.plate.retired_date)
        self.assertIsNone(self.plate.retired_reason)

    def test_profile_reads_item_before_copying_image(self):
        self.profile.validate()
        self.item.check_permission.assert_called_once_with("read")
        self.references["Company", "COMPANY"].check_permission.assert_called_once_with("select")
        self.assertEqual(self.profile.image, "/files/item.png")
        self.frappe.db.get_value.assert_not_called()

    def test_profile_hidden_item_leaves_image_untouched(self):
        self.item.check_permission.side_effect = PermissionError("hidden item")
        with self.assertRaises(PermissionError):
            self.profile.validate()
        self.assertEqual(self.profile.image, "old-image")

    def test_profile_company_select_is_enforced(self):
        self.references["Company", "COMPANY"].check_permission.side_effect = PermissionError("hidden company")
        with self.assertRaises(PermissionError):
            self.profile.validate()

    def test_profile_suppliers_and_uom_keep_native_select(self):
        supplier = self.reference("Supplier", "SUPPLIER")
        uom = self.reference("UOM", "UOM")
        raw = self.reference("Item", "RAW")
        operation = self.reference("Operation", "OPERATION")
        self.profile.supplier = "SUPPLIER"
        self.profile.materials = [types.SimpleNamespace(item_code="RAW", uom="UOM", supplier="SUPPLIER")]
        self.profile.operations = [types.SimpleNamespace(operation="OPERATION", supplier="SUPPLIER")]
        self.profile.validate()
        self.assertEqual(supplier.check_permission.call_args_list, [call("select")] * 3)
        uom.check_permission.assert_called_once_with("select")
        raw.check_permission.assert_called_once_with("read")
        operation.check_permission.assert_called_once_with("select")

    def test_profile_hidden_material_item_or_uom_or_supplier_is_rejected(self):
        self.profile.materials = [types.SimpleNamespace(item_code="RAW", uom="UOM", supplier="SUPPLIER")]
        for doctype, name in (("Item", "RAW"), ("UOM", "UOM"), ("Supplier", "SUPPLIER")):
            self.reference(doctype, name)
        for key in (("Item", "RAW"), ("UOM", "UOM"), ("Supplier", "SUPPLIER")):
            with self.subTest(reference=key):
                self.references[key].check_permission.side_effect = PermissionError("hidden reference")
                with self.assertRaises(PermissionError):
                    self.profile.validate()
                self.references[key].check_permission.side_effect = None

    def test_profile_hidden_header_supplier_is_rejected(self):
        self.profile.supplier = "SUPPLIER"
        self.reference("Supplier", "SUPPLIER").check_permission.side_effect = PermissionError("hidden supplier")
        with self.assertRaises(PermissionError):
            self.profile.validate()

    def test_profile_hidden_operation_or_its_supplier_is_rejected(self):
        operation = self.reference("Operation", "OPERATION")
        supplier = self.reference("Supplier", "SUPPLIER")
        self.profile.operations = [types.SimpleNamespace(operation="OPERATION", supplier="SUPPLIER")]
        for reference in (operation, supplier):
            reference.check_permission.side_effect = PermissionError("hidden operation reference")
            with self.assertRaises(PermissionError):
                self.profile.validate()
            reference.check_permission.side_effect = None

    def test_profile_bom_requires_read_and_established_source_constraints(self):
        bom = self.reference("BOM", "BOM", item="ITEM", company="COMPANY", docstatus=1, is_active=1)
        self.profile.bom = "BOM"
        self.profile.validate()
        bom.check_permission.assert_called_once_with("read")
        for field, invalid in (("item", "OTHER"), ("company", "FOREIGN"), ("docstatus", 0), ("is_active", 0)):
            with self.subTest(field=field):
                old = getattr(bom, field)
                setattr(bom, field, invalid)
                with self.assertRaisesRegex(ValueError, "已提交且启用"):
                    self.profile.validate()
                setattr(bom, field, old)
        bom.check_permission.side_effect = PermissionError("hidden bom")
        with self.assertRaises(PermissionError):
            self.profile.validate()

    def test_profile_invalid_reference_price_is_rejected(self):
        self.profile.list_price = "Infinity"
        with self.assertRaises(ValueError):
            self.profile.validate()


class TestCatalogRollbackGuard(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.db = types.SimpleNamespace(savepoint=Mock(), rollback=Mock())
        utils = types.ModuleType("frappe.utils")
        utils.add_days, utils.today = Mock(), Mock()
        diagnostics = types.ModuleType("jifeng_erp.diagnostics")
        diagnostics._begin_rollback_only, diagnostics._end_rollback_only = Mock(), Mock()
        inventory = types.ModuleType("jifeng_erp.inventory_validation")
        inventory._ledger_snapshot = Mock(return_value={"GL Entry": "unchanged", "Stock Ledger Entry": "unchanged"})
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                      "jifeng_erp.diagnostics": diagnostics,
                                      "jifeng_erp.inventory_validation": inventory}):
            self.module = load_module("catalog_validation_under_test", "jifeng_erp/catalog_validation.py")

    def test_expected_rejection_rolls_back_and_preserves_ledgers(self):
        action = Mock(side_effect=PermissionError("denied"))
        self.module._blocked(action, PermissionError, "test")
        self.frappe.db.rollback.assert_called_once_with(save_point="test")

    def test_unexpected_failure_also_rolls_back(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.module._blocked(Mock(side_effect=RuntimeError("unexpected")), PermissionError, "test")
        self.frappe.db.rollback.assert_called_once_with(save_point="test")

    def test_an_unexpectedly_accepted_action_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError, "应拒绝"):
            self.module._blocked(Mock(), PermissionError, "test")
        self.frappe.db.rollback.assert_called_once_with(save_point="test")

    def test_rejection_that_changes_a_ledger_fails_the_gate(self):
        self.module._ledger_snapshot.side_effect = [{"GL Entry": "before"}, {"GL Entry": "after"}]
        with self.assertRaisesRegex(AssertionError, "GL/SLE"):
            self.module._blocked(Mock(side_effect=PermissionError("denied")), PermissionError, "test")

    def test_smoke_has_no_commit_option(self):
        self.assertEqual(list(inspect.signature(self.module.smoke).parameters),
                         ["company", "item_group", "stock_uom", "other_company", "alternate_uom"])


if __name__ == "__main__":
    unittest.main()
