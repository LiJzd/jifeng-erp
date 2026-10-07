"""Source integrity/permission models; these are not native database evidence."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, name, value):
        self[name] = value

    def as_dict(self):
        return dict(self)

    def precision(self, field):
        return 6


class Meta:
    def __init__(self, environment, name):
        self.environment, self.name = environment, name

    @property
    def fields(self):
        return list(self.environment.schemas[self.name].values())

    def get_field(self, name):
        return self.environment.schemas[self.name].get(name)

    def get_valid_columns(self):
        return self.environment.schemas[self.name]

    def get_permlevel_access(self, permission_type=None, **kwargs):
        return [0]


class IntakeSourceTests(unittest.TestCase):
    def setUp(self):
        self.docs, self.schemas = {}, {}
        self.denied, self.list_denied, self.hidden, self.masked = set(), set(), {}, {}
        self.on_doc = None
        self.fr = fr = types.ModuleType("frappe")
        fr._dict, fr.session, fr.conf = Row, Row(user="ordinary-stock-user"), Row()
        fr.PermissionError = PermissionError
        fr.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        fr.whitelist = lambda: lambda function: function
        fr.get_doc, fr.get_all, fr.get_list = self.get_doc, self.get_all, self.get_list
        fr.get_meta = lambda doctype: Meta(self, doctype)
        fr.share = Row(get_shared=lambda *args, **kwargs: [])
        fr.db = Row(sql=Mock(side_effect=AssertionError("this reader must not write SQL")))
        self.model = types.ModuleType("frappe.model")
        self.model.get_permitted_fields = self.columns
        query_utils = types.ModuleType("frappe.query_builder.utils")
        query_utils.mask_fields = self.mask_fields
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: value
        package = types.ModuleType("jifeng_erp")
        package.__path__ = [str(ROOT / "jifeng_erp")]
        scoped = types.ModuleType("jifeng_erp.sales_fulfillment_scoped_query")
        scoped.record_list = lambda doctype, **kwargs: self.get_list(doctype, filters=kwargs["filters"],
            fields=kwargs["fields"], order_by="name asc", limit_page_length=kwargs["limit"])
        patches = patch.dict(sys.modules, {"frappe": fr, "frappe.model": self.model,
            "frappe.query_builder.utils": query_utils, "frappe.utils": utils,
            "jifeng_erp": package, scoped.__name__: scoped})
        patches.start()
        self.addCleanup(patches.stop)
        # Import real shared guards and stage logic, not look-alike substitutes.
        for slug in ("ledger_source_guard", "subcontract_stage", "inventory_scope", "sales_intake_sources"):
            self.load(slug)
        self.module = sys.modules["jifeng_erp.sales_intake_sources"]
        for doctype, value in self.module._specs().items():
            self.schema(doctype, value["fields"] | value["optional"])
            for field, (childtype, required, optional) in value["tables"].items():
                self.schema(childtype, required | optional)
                self.schemas[doctype][field] = Row(fieldname=field, fieldtype="Table", options=childtype, permlevel=0)
        self.schema("Has Role", {"name", "parent", "parenttype", "role"})
        self.add("Company", "JF")
        self.add("Item", "FG", stock_uom="Nos", is_stock_item=1, secret_rate=999)
        self.add("Item", "RM", stock_uom="Kg", is_stock_item=1)
        self.add("Item", "SERVICE", stock_uom="Nos", is_stock_item=0)
        self.add("Warehouse", "FG-WH", company="JF", is_group=0)
        self.add("Warehouse", "REJECT-WH", company="JF", is_group=0)
        self.add("UOM", "Nos")
        self.add("Jifeng Flow Order", "FLOW", company="JF", item_code="FG")
        self.add("Work Order", "WO", company="JF", docstatus=1, production_item="FG", jf_flow_order="FLOW",
                 track_semi_finished_goods=0, operations=[])
        self.manufacture("SE-1", "SE-ROW-1", "1", "1")

    def load(self, slug):
        name = "jifeng_erp." + slug
        specification = importlib.util.spec_from_file_location(name, ROOT / "jifeng_erp" / (slug + ".py"))
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        specification.loader.exec_module(module)
        setattr(sys.modules["jifeng_erp"], slug, module)
        return module

    def schema(self, doctype, fields):
        existing = self.schemas.setdefault(doctype, {})
        existing.update({field: Row(fieldname=field, fieldtype="Data", permlevel=0) for field in fields})

    def add(self, doctype, name, **values):
        row = Row(doctype=doctype, name=name, modified=datetime(2026, 10, 8, 1), docstatus=0, **{})
        row.update(values)
        self.docs[doctype, name] = row
        return row

    def child(self, parenttype, parent, name, **values):
        childtype = self.module._specs()[parenttype]["tables"]["items"][0]
        self.add(childtype, name, parent=parent, parenttype=parenttype, parentfield="items", idx=1, **values)

    def manufacture(self, name, row, qty, factor, *, item="FG", finished=1, status=1, purpose="Manufacture"):
        self.add("Stock Entry", name, company="JF", docstatus=status, purpose=purpose,
                 posting_date=date(2026, 10, 8), work_order="WO", job_card=None, bom_no=None)
        self.child("Stock Entry", name, row, item_code=item, stock_uom="Nos", uom="Box", qty=Decimal(qty),
                   conversion_factor=Decimal(factor), transfer_qty=Decimal(qty) * Decimal(factor),
                   jf_flow_order="FLOW", s_warehouse=None, t_warehouse="FG-WH", is_finished_item=finished)
        self.ledger(name + "-SLE", "Stock Entry", name, row, Decimal(qty) * Decimal(factor), item=item)

    def ledger(self, name, doctype, voucher, detail, qty, *, item="FG", warehouse="FG-WH"):
        return self.add("Stock Ledger Entry", name, company="JF", docstatus=1, is_cancelled=0,
                        item_code=item, stock_uom="Nos", warehouse=warehouse, actual_qty=qty,
                        voucher_type=doctype, voucher_no=voucher, voucher_detail_no=detail,
                        posting_date=date(2026, 10, 8), stock_value_difference=999)

    def subcontract(self, name="SCR-1", row="SCR-ROW-1", qty="1", *, rejected="0", original=None):
        if ("Purchase Order", "PO") not in self.docs:
            self.add("Purchase Order", "PO", company="JF", supplier="SUP", docstatus=1,
                     is_subcontracted=1, is_old_subcontracting_flow=0)
            self.child("Purchase Order", "PO", "PO-ROW", item_code="SERVICE", fg_item="FG",
                       job_card=None, bom=None, jf_flow_order="FLOW")
            self.add("Subcontracting Order", "SCO", company="JF", supplier="SUP", docstatus=1, purchase_order="PO")
            self.child("Subcontracting Order", "SCO", "SCO-ROW", item_code="FG", job_card=None,
                       bom=None, purchase_order_item="PO-ROW", jf_flow_order="FLOW")
        self.add("Subcontracting Receipt", name, company="JF", supplier="SUP", docstatus=1,
                 posting_date=date(2026, 10, 8), is_return=int(bool(original)), return_against=original)
        self.child("Subcontracting Receipt", name, row, item_code="FG", stock_uom="Nos", qty=Decimal(qty),
                   conversion_factor=1, jf_flow_order="FLOW", rejected_qty=Decimal(rejected),
                   warehouse="FG-WH", rejected_warehouse="REJECT-WH" if Decimal(rejected) else None,
                   secondary_item_type=None, valuation_type=None, reference_name=None,
                   subcontracting_order="SCO", subcontracting_order_item="SCO-ROW", purchase_order="PO",
                   purchase_order_item="PO-ROW", subcontracting_receipt_item="SCR-ROW-1" if original else None,
                   job_card=None, bom=None)
        self.ledger(name + "-SLE", "Subcontracting Receipt", name, row, Decimal(qty))
        if Decimal(rejected):
            self.ledger(name + "-REJECT", "Subcontracting Receipt", name, row, Decimal(rejected), warehouse="REJECT-WH")

    def stage(self, card, item, *, outsourced):
        work = self.docs["Work Order", "WO"]
        work.track_semi_finished_goods = 1
        self.add("Item", item, stock_uom="Nos", is_stock_item=1)
        bom = self.add("BOM", card + "-BOM", company="JF", docstatus=1, item=item)
        operation = self.add("Work Order Operation", card + "-OP", parent="WO", parenttype="Work Order",
            parentfield="operations", idx=1, operation=card + "-PROCESS", finished_good=item,
            is_subcontracted=int(outsourced))
        self.add("BOM Item", card + "-MATERIAL", parent=bom.name, parenttype="BOM", parentfield="items", idx=1,
                 item_code="RM")
        self.add("Job Card", card, company="JF", docstatus=1, work_order="WO", production_item="FG",
                 operation_id=operation.name, operation=operation.operation, finished_good=item,
                 semi_fg_bom=bom.name, track_semi_finished_goods=1, is_subcontracted=int(outsourced),
                 jf_flow_order="FLOW")
        return bom.name

    def columns(self, doctype, **kwargs):
        return set(self.schemas[doctype]) - self.hidden.get(doctype, set())

    def mask_fields(self, doctype, fields, rows, **kwargs):
        for row in rows:
            for field in self.masked.get(doctype, set()):
                if field in row:
                    row[field] = "MASKED"
        return rows

    @staticmethod
    def matches(row, filters):
        for key, value in (filters or {}).items():
            if isinstance(value, (tuple, list)) and value[0] == "in":
                if row.get(key) not in value[1]:
                    return False
            elif row.get(key) != value:
                return False
        return True

    def get_all(self, doctype, *, filters=None, fields=None, limit_page_length=0, **kwargs):
        rows = [deepcopy(row) for (kind, _), row in sorted(self.docs.items())
                if kind == doctype and self.matches(row, filters)]
        if limit_page_length:
            rows = rows[:limit_page_length]
        return [Row({key: row.get(key) for key in fields}) for row in rows] if fields and fields != ["*"] else rows

    def get_list(self, doctype, **kwargs):
        rows = self.get_all(doctype, **kwargs)
        return [row for row in rows if (doctype, row.name) not in self.list_denied]

    def get_doc(self, doctype, name):
        if self.on_doc:
            self.on_doc(doctype, name)
        environment = self

        class Document(Row):
            def has_permission(document, permission="read"):
                return (doctype, name) not in environment.denied

            def check_permission(document, permission="read"):
                if not document.has_permission(permission):
                    raise PermissionError("native document denied")

        doc = Document(deepcopy(self.docs[doctype, name]))
        if doctype in self.module._specs():
            for field, (childtype, _required, _optional) in self.module._specs()[doctype]["tables"].items():
                doc[field] = [deepcopy(row) for (kind, _), row in sorted(self.docs.items())
                              if kind == childtype and row.parent == name and row.parenttype == doctype]
        return doc

    def read(self):
        return self.module.read_sources("JF", "FLOW")

    def test_manufacturing_units_and_two_scr_batches_keep_precise_identities_without_price(self):
        source = self.docs["Stock Entry Detail", "SE-ROW-1"]
        source.update(qty=Decimal("2"), conversion_factor=Decimal("3"), transfer_qty=Decimal("6"))
        self.docs["Stock Ledger Entry", "SE-1-SLE"].actual_qty = Decimal("6")
        self.subcontract()
        self.subcontract("SCR-2", "SCR-ROW-2")
        before = deepcopy(self.docs)
        result = self.read()
        self.assertEqual(self.docs, before)
        self.assertFalse(result["stock_posting_required"])
        self.assertEqual(len(result["sources"]), 3)
        source = next(row for row in result["sources"] if row["source_name"] == "SE-1")
        self.assertEqual((source["document_qty"], source["document_uom"], source["stock_qty"], source["stock_uom"]),
                         ("2", "Box", "6", "Nos"))
        self.assertEqual({row["source_detail"] for row in result["sources"]}, {"SE-ROW-1", "SCR-ROW-1", "SCR-ROW-2"})
        self.assertNotIn("999", str(result))
        self.assertFalse(any("rate" in row or "amount" in row or "list_price" in row for row in result["sources"]))
        self.fr.db.sql.assert_not_called()

    def test_returns_are_exact_signed_movements_without_available_stock_total(self):
        self.subcontract()
        self.subcontract("SCR-RET", "RET-ROW", "-0.25", original="SCR-1")
        result = self.read()
        returned = next(row for row in result["sources"] if row["direction"] == "return")
        self.assertEqual((returned["stock_qty"], returned["return_against"], returned["return_source_detail"]),
                         ("-0.25", "SCR-1", "SCR-ROW-1"))
        self.assertNotIn("available_qty", result)
        self.assertNotIn("total_qty", result)

    def test_cancelled_draft_nonmanufacture_and_material_rows_are_not_receipt_sources(self):
        self.manufacture("DRAFT", "DR", "1", "1", status=0)
        self.manufacture("CANCELLED", "CR", "1", "1", status=2)
        self.manufacture("TRANSFER", "TR", "1", "1", purpose="Material Transfer")
        self.child("Stock Entry", "SE-1", "RAW", item_code="RM", stock_uom="Kg", uom="Kg", qty=1,
                   conversion_factor=1, transfer_qty=1, jf_flow_order="FLOW", s_warehouse="RM-WH",
                   t_warehouse=None, is_finished_item=0)
        self.assertEqual([row["source_name"] for row in self.read()["sources"]], ["SE-1"])

    def test_primary_scr_rejection_is_validated_but_not_added_to_accepted_quantity(self):
        self.subcontract(rejected="0.5")
        result = self.read()
        source = next(row for row in result["sources"] if row["source_name"] == "SCR-1")
        self.assertEqual(source["stock_qty"], "1")
        self.docs["Stock Ledger Entry", "SCR-1-REJECT"].actual_qty = Decimal("0.75")
        with self.assertRaises(ValueError):
            self.read()

    def test_exact_intermediate_stage_is_excluded_and_final_outsourced_stage_keeps_both_batches(self):
        intermediate_bom = self.stage("JC-INTERNAL", "SFG", outsourced=False)
        self.docs["Stock Entry", "SE-1"].update(job_card="JC-INTERNAL", bom_no=intermediate_bom)
        self.docs["Stock Entry Detail", "SE-ROW-1"].item_code = "SFG"
        self.docs["Stock Ledger Entry", "SE-1-SLE"].item_code = "SFG"
        final_bom = self.stage("JC-OUTSOURCE", "FG", outsourced=True)
        self.subcontract()
        self.subcontract("SCR-2", "SCR-ROW-2")
        for kind, name in (("Purchase Order Item", "PO-ROW"), ("Subcontracting Order Item", "SCO-ROW"),
                           ("Subcontracting Receipt Item", "SCR-ROW-1"), ("Subcontracting Receipt Item", "SCR-ROW-2")):
            self.docs[kind, name].update(job_card="JC-OUTSOURCE", bom=final_bom)
        result = self.read()
        self.assertEqual({row["source_name"] for row in result["sources"]}, {"SCR-1", "SCR-2"})
        self.assertEqual([row["stock_qty"] for row in result["sources"]], ["1", "1"])
        self.docs["Job Card", "JC-OUTSOURCE"].operation_id = "JC-INTERNAL-OP"
        with self.assertRaises(ValueError):
            self.read()

    def test_ordinary_finished_flag_cannot_turn_an_unverified_semi_finished_item_into_source(self):
        self.docs["Stock Entry Detail", "SE-ROW-1"].item_code = "RM"
        with self.assertRaises(ValueError):
            self.read()

    def test_an_unreadable_posted_second_batch_refuses_all_instead_of_silently_undercounting(self):
        self.subcontract()
        self.subcontract("SCR-2", "SCR-ROW-2")
        self.list_denied.add(("Subcontracting Receipt", "SCR-2"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_native_document_denial_refuses_even_if_the_list_can_see_it(self):
        self.denied.add(("Stock Entry", "SE-1"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_hidden_quantity_or_table_or_masked_quantity_refuses_whole_projection(self):
        for kind in ("quantity", "table", "mask"):
            with self.subTest(kind=kind):
                self.hidden, self.masked = {}, {}
                if kind == "quantity":
                    self.hidden["Stock Entry Detail"] = {"transfer_qty"}
                elif kind == "table":
                    self.schemas["Stock Entry"]["items"].permlevel = 1
                else:
                    self.masked["Stock Ledger Entry"] = {"actual_qty"}
                with self.assertRaises(PermissionError):
                    self.read()
                self.schemas["Stock Entry"]["items"].permlevel = 0

    def test_hidden_ledger_membership_and_hidden_warehouse_refuse(self):
        for source in (("Stock Ledger Entry", "SE-1-SLE"), ("Warehouse", "FG-WH")):
            with self.subTest(source=source):
                self.list_denied = {source}
                with self.assertRaises(PermissionError):
                    self.read()

    def test_missing_ledger_and_wrong_ledger_quantity_warehouse_company_item_or_unit_refuse(self):
        pristine = deepcopy(self.docs["Stock Ledger Entry", "SE-1-SLE"])
        for field, value in (("actual_qty", Decimal("2")), ("warehouse", "OTHER"), ("company", "OTHER"),
                             ("item_code", "RM"), ("stock_uom", "Kg"), ("is_cancelled", 1)):
            with self.subTest(field=field):
                self.docs["Stock Ledger Entry", "SE-1-SLE"] = deepcopy(pristine)
                self.docs["Stock Ledger Entry", "SE-1-SLE"][field] = value
                with self.assertRaises(ValueError):
                    self.read()

    def test_sales_order_work_flow_and_scr_exact_parent_rows_are_never_guessed_by_item(self):
        self.subcontract()
        self.docs["Subcontracting Receipt Item", "SCR-ROW-1"].subcontracting_order_item = "PO-ROW"
        with self.assertRaises(ValueError):
            self.read()
        self.docs["Subcontracting Receipt Item", "SCR-ROW-1"].subcontracting_order_item = "SCO-ROW"
        self.docs["Stock Entry Detail", "SE-ROW-1"].jf_flow_order = None
        with self.assertRaises(ValueError):
            self.read()

    def test_wrong_return_source_or_omitted_flow_refuses(self):
        self.subcontract()
        self.subcontract("SCR-RET", "RET-ROW", "-0.25", original="SCR-1")
        row = self.docs["Subcontracting Receipt Item", "RET-ROW"]
        row.jf_flow_order = None
        with self.assertRaises(ValueError):
            self.read()
        row.jf_flow_order, row.subcontracting_receipt_item = "FLOW", "SCO-ROW"
        with self.assertRaises(ValueError):
            self.read()

    def test_guest_cross_company_and_replica_refuse(self):
        self.fr.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.read()
        self.fr.session.user = "ordinary-stock-user"
        self.docs["Jifeng Flow Order", "FLOW"].company = "OTHER"
        with self.assertRaises(ValueError):
            self.read()
        self.fr.conf.read_from_replica = True
        with self.assertRaises(ValueError):
            self.read()

    def test_source_or_permission_change_during_output_refuses_after_native_callbacks(self):
        calls = 0

        def changed(doctype, name):
            nonlocal calls
            if doctype == "Stock Entry" and name == "SE-1":
                calls += 1
                if calls == 2:
                    self.docs["Stock Ledger Entry", "SE-1-SLE"].actual_qty = Decimal("2")

        self.on_doc = changed
        with self.assertRaises(PermissionError):
            self.read()
        self.on_doc = None
        self.docs["Stock Ledger Entry", "SE-1-SLE"].actual_qty = Decimal("1")
        calls = 0

        def revoked(doctype, name):
            nonlocal calls
            if doctype == "Stock Entry" and name == "SE-1":
                calls += 1
                if calls == 2:
                    self.add("Has Role", "LATE", parent="ordinary-stock-user", parenttype="User", role="Changed")

        self.on_doc = revoked
        with self.assertRaises(PermissionError):
            self.read()


if __name__ == "__main__":
    unittest.main()
