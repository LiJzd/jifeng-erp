"""The two real Jinja templates under saved-document and native permission models."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from jinja2.sandbox import SandboxedEnvironment


ROOT = Path(__file__).resolve().parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    environment = None

    def has_permission(self, permission):
        return (self.doctype, self.name, permission) not in self.environment.denied_documents

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError(permission)

    def is_downloadable(self):
        return self.name not in self.environment.denied_downloads


class Meta:
    def __init__(self, fields):
        self.fields = [Record(field) for field in fields]
        self.read_levels, self.mask_levels = {0}, set()

    def get_field(self, name):
        return next((df for df in self.fields if df.fieldname == name), None)

    def get_permlevel_access(self, permission_type, parenttype=None, user=None):
        return self.mask_levels if permission_type == "mask" else self.read_levels


class FlowPrintingTests(unittest.TestCase):
    def setUp(self):
        self.denied_documents, self.denied_downloads, self.hidden = set(), set(), {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="ordinary-production-user@example.invalid")
        self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model})
        scope.start(); self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("flow_printing_under_test", ROOT / "jifeng_erp/flow_printing.py")
        self.P = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.P)
        self.metas = {}
        for dt in (self.P.FLOW, "Jifeng Material Line", "Jifeng Operation Line"):
            folder = dt.lower().replace(" ", "_")
            schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")).read_text("utf-8"))
            self.metas[dt] = Meta(schema["fields"])
        self.metas[self.P.PLAN] = Meta([{"fieldname": f, "fieldtype": "Data"} for f in self.P.PLAN_FIELDS]
            + [{"fieldname": "po_items", "fieldtype": "Table", "options": "Production Plan Item"}])
        self.metas["Production Plan Item"] = Meta([{"fieldname": f, "fieldtype": "Data"} for f in self.P.PLAN_ITEM_FIELDS])
        self.metas["Company"] = Meta([{"fieldname": "default_currency", "fieldtype": "Link"}])
        self.metas["File"] = Meta([{"fieldname": "file_url", "fieldtype": "Data"}])
        Document.environment = self
        self.flow = Document(doctype=self.P.FLOW, name="SAVED-FLOW", company="JF", item_code="FG",
            planned_qty=288, stock_uom="Nos", quantity_basis="stock_v1", external_order=None,
            source_qty=2, source_uom="Box", source_stock_uom="Nos", source_conversion_factor=144,
            source_stock_qty=288, source_stock_precision=9, requirements="本单镀色<金>", packaging="本单单独包装",
            supplier_product_code="VENDOR-CODE", specification="SNAPSHOT-SPEC", sales_order="SO-SAVED",
            materials=[self.row("Jifeng Material Line", "materials", item_code="RAW", qty_per_unit=Decimal("0.25"), uom="Kg", note="<材料>")],
            operations=[self.row("Jifeng Operation Line", "operations", operation="SNAPSHOT-OP", supplier="SNAPSHOT-SUP",
                                 unit_price=Decimal("2.50"), note="本单工艺<要求>")])
        self.plan = Document(doctype=self.P.PLAN, name="SAVED-PLAN", company="JF", docstatus=1,
            posting_date=date(2026, 10, 9), jf_instruction_no="原号", jf_instruction_weight=3,
            jf_instruction_weight_uom="Kg", jf_instruction_amount=Decimal("15.20"), jf_instruction_remark="<独立备注>",
            po_items=[self.row("Production Plan Item", "po_items", parent="SAVED-PLAN", parenttype=self.P.PLAN,
                item_code="FG", bom_no="BOM-SNAPSHOT", jf_flow_order="SAVED-FLOW", planned_qty=5, stock_uom="Nos", ordered_qty=5, produced_qty=2),
                self.row("Production Plan Item", "po_items", parent="SAVED-PLAN", parenttype=self.P.PLAN,
                item_code="KG-ITEM", bom_no="BOM-KG", planned_qty=2, stock_uom="Kg", ordered_qty=0, produced_qty=0)])
        self.company = Document(doctype="Company", name="JF", default_currency="CNY")
        self.documents = {(d.doctype, d.name): d for d in (self.flow, self.plan, self.company)}
        self.frappe.get_doc = Mock(side_effect=lambda dt, name: deepcopy(self.documents[(dt, name)]))
        self.frappe.get_meta = lambda dt: self.metas[dt]
        self.frappe.get_all = Mock(side_effect=self.files)
        self.frappe.call = Mock(side_effect=AssertionError("print helpers must not need an HTTP whitelist"))
        self.jenv = SandboxedEnvironment(autoescape=False)

    def row(self, doctype, field, **values):
        return Document(doctype=doctype, name="ROW-" + field, parent=values.pop("parent", "SAVED-FLOW"),
            parenttype=values.pop("parenttype", self.P.FLOW), parentfield=field, idx=1, **values)

    def columns(self, doctype, parenttype=None, **options):
        meta = self.metas[doctype]
        levels = self.metas[parenttype].read_levels if parenttype else meta.read_levels
        return ({df.fieldname for df in meta.fields if df.fieldtype != "Table" and (df.permlevel or 0) in levels}
                | {"name", "idx", "parent", "parenttype", "parentfield", "docstatus"}) - self.hidden.get(doctype, set())

    def files(self, doctype, filters, **options):
        self.assertEqual(doctype, "File")
        return [name for (dt, name), row in self.documents.items()
                if dt == "File" and all(row.get(field) == value for field, value in filters.items())]

    def render(self, name, doc):
        path = ROOT / "jifeng_erp/jifeng_erp/print_format" / name / (name + ".json")
        template = json.loads(path.read_text("utf-8"))["html"]
        self.assertNotIn("{{ doc.", template)
        return self.jenv.from_string(template).render(frappe=self.frappe, doc=doc,
            jifeng_flow_sheet=self.P.jifeng_flow_sheet, jifeng_production_instruction=self.P.jifeng_production_instruction)

    def test_flow_saved_snapshot_explicit_conversion_units_fee_and_escaping(self):
        before = deepcopy(self.documents)
        forged = deepcopy(self.flow); forged.update(planned_qty=999, requirements="UNSAVED-SECRET")
        view = self.P.jifeng_flow_sheet(forged)
        self.assertEqual(view["flow"]["planned_qty"], 288)
        self.assertEqual(view["materials"][0]["planned_requirement"], 72)
        self.assertTrue(view["source_conversion"] and view["plan_confirmed"])
        html = self.render("jifeng_flow_sheet", forged)
        for value in ("288 Nos", "2 Box", "144", "预计每库存单位工价（CNY）", "2.50 / Nos", "SNAPSHOT-SUP", "本单单独包装"):
            self.assertIn(value, html)
        self.assertIn("本单镀色&lt;金&gt;", html)
        self.assertNotIn("UNSAVED-SECRET", html)
        self.assertNotIn("加工方式", html)
        self.assertEqual(self.documents, before)
        self.assertFalse(any(call.args[0] == "Item" for call in self.frappe.get_doc.call_args_list))

    def test_unknown_upstream_units_never_infer_144_or_material_total_from_pending_plan(self):
        self.flow.update(quantity_basis="pending", source_uom=None, source_stock_uom=None,
                         source_conversion_factor=None, source_stock_qty=None)
        view = self.P.jifeng_flow_sheet(self.flow)
        self.assertFalse(view["source_conversion"] or view["plan_confirmed"])
        self.assertNotIn("planned_requirement", view["materials"][0])
        html = self.render("jifeng_flow_sheet", self.flow)
        self.assertIn("源单位未核实", html)
        self.assertIn("计划库存单位待核对", html)
        self.assertNotIn("源订单库存换算系数", html)
        self.assertNotIn("预计总需求", html)

    def test_hidden_and_masked_parent_child_fields_never_render_or_feed_derived_values(self):
        for dt, field, secret in ((self.P.FLOW, "requirements", "本单镀色"),
                                 ("Jifeng Operation Line", "unit_price", "2.50"),
                                 ("Jifeng Material Line", "qty_per_unit", "0.25")):
            self.hidden[dt] = {field}
            html = self.render("jifeng_flow_sheet", self.flow)
            self.assertNotIn(secret, html)
            self.hidden.clear()
            df = self.metas[dt].get_field(field); df.mask, df.permlevel = 1, 0
            html = self.render("jifeng_flow_sheet", self.flow)
            self.assertNotIn(secret, html)
            df.mask = 0
        self.hidden[self.P.FLOW] = {"planned_qty"}
        view = self.P.jifeng_flow_sheet(self.flow)
        self.assertNotIn("planned_requirement", view["materials"][0])

    def test_table_parent_permissions_and_exact_child_parent_binding_are_required(self):
        for field, rows in (("materials", "materials"), ("operations", "operations")):
            df = self.metas[self.P.FLOW].get_field(field); df.permlevel = 1
            self.assertEqual(self.P.jifeng_flow_sheet(self.flow)[rows], [])
            df.permlevel = 0; df.mask = 1
            self.assertEqual(self.P.jifeng_flow_sheet(self.flow)[rows], [])
            df.mask = 0
        for field, wrong in (("parent", "OTHER"), ("parenttype", "OTHER"), ("parentfield", "materials"), ("doctype", "OTHER")):
            row = self.flow.operations[0]; old = row[field]; row[field] = wrong
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "不属于"):
                self.P.jifeng_flow_sheet(self.flow)
            row[field] = old

    def test_guest_wrong_source_and_read_or_print_denial_reject_without_writes(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.P.jifeng_flow_sheet(self.flow)
        self.frappe.session.user = "ordinary-production-user@example.invalid"
        for request in (None, Record(doctype=self.P.PLAN, name="SAVED-FLOW"), Record(doctype=self.P.FLOW, name=None)):
            with self.assertRaises(ValueError):
                self.P.jifeng_flow_sheet(request)
        for permission in ("read", "print"):
            self.denied_documents.add((self.P.FLOW, self.flow.name, permission))
            with self.assertRaises(PermissionError):
                self.P.jifeng_flow_sheet(self.flow)
            self.denied_documents.clear()

    def test_photo_is_authorized_local_flow_snapshot_not_current_item_or_external_source(self):
        self.flow.image = "/files/historical.png"
        attachment = Document(doctype="File", name="PHOTO", file_url=self.flow.image, is_private=0,
            attached_to_doctype="Item", attached_to_name="FG", attached_to_field=None,
            unique_url=self.flow.image)
        self.documents[("File", "PHOTO")] = attachment
        self.assertEqual(self.P.jifeng_flow_sheet(self.flow)["image_url"], self.flow.image)
        for field, wrong in (("attached_to_name", "OTHER"), ("attached_to_field", "other_image"), ("is_private", 1)):
            old = attachment[field]; attachment[field] = wrong
            self.assertIsNone(self.P.jifeng_flow_sheet(self.flow)["image_url"])
            attachment[field] = old
        self.denied_downloads.add("PHOTO")
        self.assertIsNone(self.P.jifeng_flow_sheet(self.flow)["image_url"])
        self.denied_downloads.clear()
        self.denied_documents.add(("File", "PHOTO", "read"))
        self.assertIsNone(self.P.jifeng_flow_sheet(self.flow)["image_url"])
        self.denied_documents.clear()
        self.hidden[self.P.FLOW] = {"image"}
        self.assertIsNone(self.P.jifeng_flow_sheet(self.flow)["image_url"])
        self.hidden.clear()
        for value in ("https://source.invalid/photo.jpg", "/files/../secret.png", "/files/%2fsecret.png"):
            self.flow.image = value
            self.assertIsNone(self.P.jifeng_flow_sheet(self.flow)["image_url"])
        self.flow.image = "/private/files/historical.png"
        attachment.update(file_url=self.flow.image, is_private=1, unique_url=self.flow.image + "?fid=PHOTO")
        self.assertEqual(self.P.jifeng_flow_sheet(self.flow)["image_url"], attachment.unique_url)

    def test_production_instruction_saved_rows_units_totals_and_reference_amount(self):
        before = deepcopy(self.documents)
        view = self.P.jifeng_production_instruction(self.plan)
        self.assertEqual(view["quantity_totals"], [("Kg", Decimal(2)), ("Nos", Decimal(5))])
        html = self.render("jifeng_production_instruction", self.plan)
        for value in ("5 Nos", "2 Kg", "15.20 CNY", "BOM-SNAPSHOT", "&lt;独立备注&gt;"):
            self.assertIn(value, html)
        self.assertNotIn("7 Nos", html)
        self.assertEqual(self.documents, before)

    def test_production_instruction_hidden_quantity_or_money_never_leaks_or_sums(self):
        self.hidden["Production Plan Item"] = {"stock_uom"}
        self.assertEqual(self.P.jifeng_production_instruction(self.plan)["quantity_totals"], [])
        self.assertNotIn("计划数量合计", self.render("jifeng_production_instruction", self.plan))
        self.hidden.clear(); self.hidden[self.P.PLAN] = {"jf_instruction_amount"}
        self.assertNotIn("15.20", self.render("jifeng_production_instruction", self.plan))
        self.hidden.clear(); self.metas[self.P.PLAN].get_field("jf_instruction_amount").mask = 1
        self.assertNotIn("15.20", self.render("jifeng_production_instruction", self.plan))


if __name__ == "__main__":
    unittest.main()
