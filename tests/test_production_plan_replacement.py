"""Original Plan factory -> booked full loss -> draft reuse -> inverse cancellation.

Pinned native factory bodies are the installed 16.36 source (SHA below).
Adapters provide documents/SQL rows only. No GUI or business DB writes.
"""

from collections import defaultdict
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import textwrap
import types
import unittest
from unittest.mock import patch

import test_production_plan_loss as loss_fixture

# Captured installed ERPNext source, not replacement factory math.
PINNED_NATIVE = json.loads(r'''{"methods":{"get_production_items":"\tdef get_production_items(self):\n\t\titem_dict = {}\n\t\tpending = ProductionPlanWorkOrderQuantities(self.name).get_pending_quantities(self)\n\n\t\tfor d in self.po_items:\n\t\t\titem_details = {\n\t\t\t\t\"production_item\": d.item_code,\n\t\t\t\t\"use_multi_level_bom\": d.include_exploded_items,\n\t\t\t\t\"sales_order\": d.sales_order,\n\t\t\t\t\"sales_order_item\": d.sales_order_item,\n\t\t\t\t\"material_request\": d.material_request,\n\t\t\t\t\"material_request_item\": d.material_request_item,\n\t\t\t\t\"bom_no\": d.bom_no,\n\t\t\t\t\"description\": d.description,\n\t\t\t\t\"stock_uom\": d.stock_uom,\n\t\t\t\t\"company\": self.company,\n\t\t\t\t\"source_warehouse\": frappe.get_value(\"BOM\", d.bom_no, \"default_source_warehouse\"),\n\t\t\t\t\"fg_warehouse\": d.warehouse,\n\t\t\t\t\"production_plan\": self.name,\n\t\t\t\t\"production_plan_item\": d.name,\n\t\t\t\t\"product_bundle_item\": d.product_bundle_item,\n\t\t\t\t\"planned_start_date\": d.planned_start_date,\n\t\t\t\t\"project\": self.project,\n\t\t\t}\n\n\t\t\tif not item_details[\"project\"] and d.sales_order:\n\t\t\t\titem_details[\"project\"] = frappe.get_cached_value(\"Sales Order\", d.sales_order, \"project\")\n\n\t\t\titem_details[\"qty\"] = pending[\"production_plan_item\"][d.name]\n\t\t\t# A Work Order can reference only one Production Plan row.\n\t\t\titem_dict[d.name] = item_details\n\n\t\treturn item_dict\n","make_work_order":"\t@frappe.whitelist()\n\tdef make_work_order(self):\n\t\tfrom erpnext.manufacturing.doctype.work_order.work_order import get_default_warehouse\n\n\t\tself.reload()\n\t\two_list, po_list = [], []\n\t\tsubcontracted_po = {}\n\t\tdefault_warehouses = get_default_warehouse(self.company)\n\n\t\tself.make_work_order_for_finished_goods(wo_list, default_warehouses)\n\t\tself.make_work_order_for_subassembly_items(wo_list, subcontracted_po, default_warehouses)\n\t\tself.make_subcontracted_purchase_order(subcontracted_po, po_list)\n\t\tself.show_list_created_message(\"Work Order\", wo_list)\n\t\tself.show_list_created_message(\"Purchase Order\", po_list)\n\n\t\tif not wo_list:\n\t\t\tfrappe.msgprint(_(\"No Work Orders were created\"))\n\n\t\tif not po_list:\n\t\t\tfrappe.msgprint(_(\"No Purchase Orders were created\"))\n","make_work_order_for_finished_goods":"\tdef make_work_order_for_finished_goods(self, wo_list, default_warehouses):\n\t\titems_data = self.get_production_items()\n\n\t\tfor _key, item in items_data.items():\n\t\t\tif self.sub_assembly_items:\n\t\t\t\titem[\"use_multi_level_bom\"] = 0\n\n\t\t\tset_default_warehouses(item, default_warehouses)\n\t\t\twork_order = self.create_work_order(item)\n\t\t\tif work_order:\n\t\t\t\two_list.append(work_order)\n","create_work_order":"\tdef create_work_order(self, item):\n\t\tfrom erpnext.manufacturing.doctype.work_order.work_order import OverProductionError\n\n\t\tif flt(item.get(\"qty\")) <= 0:\n\t\t\treturn\n\n\t\two = frappe.new_doc(\"Work Order\")\n\t\two.update(item)\n\n\t\two.reserve_stock = self.reserve_stock\n\t\two.planned_start_date = item.get(\"planned_start_date\") or item.get(\"schedule_date\")\n\n\t\tif item.get(\"warehouse\"):\n\t\t\two.fg_warehouse = item.get(\"warehouse\")\n\n\t\two.set_work_order_operations()\n\t\two.set_required_items()\n\n\t\ttry:\n\t\t\two.flags.ignore_mandatory = True\n\t\t\two.flags.ignore_validate = True\n\t\t\two.company = self.company\n\t\t\two.insert()\n\t\t\treturn wo.name\n\t\texcept OverProductionError:\n\t\t\tpass\n","make_work_order_for_subassembly_items":"\tdef make_work_order_for_subassembly_items(self, wo_list, subcontracted_po, default_warehouses):\n\t\tpending = ProductionPlanWorkOrderQuantities(self.name).get_pending_quantities(self)\n\t\tfor row in self.sub_assembly_items:\n\t\t\tif row.type_of_manufacturing == \"Subcontract\":\n\t\t\t\tsubcontracted_po.setdefault(row.supplier, []).append(row)\n\t\t\t\tcontinue\n\n\t\t\tif row.type_of_manufacturing == \"Material Request\":\n\t\t\t\tcontinue\n\n\t\t\twork_order_data = {\n\t\t\t\t\"source_warehouse\": frappe.get_value(\"BOM\", row.bom_no, \"default_source_warehouse\"),\n\t\t\t\t\"wip_warehouse\": default_warehouses.get(\"wip_warehouse\"),\n\t\t\t\t\"fg_warehouse\": default_warehouses.get(\"fg_warehouse\"),\n\t\t\t\t\"scrap_warehouse\": default_warehouses.get(\"scrap_warehouse\"),\n\t\t\t\t\"company\": self.get(\"company\"),\n\t\t\t}\n\n\t\t\tself.prepare_data_for_sub_assembly_items(\n\t\t\t\trow, work_order_data, pending[\"production_plan_sub_assembly_item\"][row.name]\n\t\t\t)\n\n\t\t\tif work_order_data.get(\"qty\") <= 0:\n\t\t\t\tcontinue\n\n\t\t\twork_order = self.create_work_order(work_order_data)\n\t\t\tif work_order:\n\t\t\t\two_list.append(work_order)\n","prepare_data_for_sub_assembly_items":"\tdef prepare_data_for_sub_assembly_items(self, row, wo_data, pending_qty=None):\n\t\tfor field in [\n\t\t\t\"production_item\",\n\t\t\t\"item_name\",\n\t\t\t\"fg_warehouse\",\n\t\t\t\"description\",\n\t\t\t\"bom_no\",\n\t\t\t\"stock_uom\",\n\t\t\t\"bom_level\",\n\t\t\t\"schedule_date\",\n\t\t\t\"sales_order\",\n\t\t\t\"sales_order_item\",\n\t\t]:\n\t\t\tif row.get(field):\n\t\t\t\two_data[field] = row.get(field)\n\n\t\tif pending_qty is None:\n\t\t\tpending_qty = ProductionPlanWorkOrderQuantities(self.name).get_pending_quantities(self)[\n\t\t\t\t\"production_plan_sub_assembly_item\"\n\t\t\t][row.name]\n\t\two_data[\"qty\"] = pending_qty\n\n\t\two_data.update(\n\t\t\t{\n\t\t\t\t\"use_multi_level_bom\": 0,\n\t\t\t\t\"production_plan\": self.name,\n\t\t\t\t\"production_plan_sub_assembly_item\": row.name,\n\t\t\t}\n\t\t)\n"},"helpers":{"set_default_warehouses":"def set_default_warehouses(row, default_warehouses):\n\tfor field in [\"wip_warehouse\", \"fg_warehouse\", \"scrap_warehouse\"]:\n\t\tif not row.get(field):\n\t\t\trow[field] = default_warehouses.get(field)\n"},"quantity_methods":{"get_pending_quantities":"def get_pending_quantities(self, plan):\n\t\tcommitted = self.get_committed_quantities()\n\t\tprecision = frappe.get_precision(\"Work Order\", \"qty\")\n\t\tpending = {}\n\t\tfor table, reference_field, qty_field in (\n\t\t\t(\"po_items\", \"production_plan_item\", \"planned_qty\"),\n\t\t\t(\"sub_assembly_items\", \"production_plan_sub_assembly_item\", \"qty\"),\n\t\t):\n\t\t\tpending[reference_field] = {\n\t\t\t\trow.name: max(\n\t\t\t\t\t0, flt(flt(row.get(qty_field)) - committed[reference_field].get(row.name, 0), precision)\n\t\t\t\t)\n\t\t\t\tfor row in plan.get(table)\n\t\t\t\tif table == \"po_items\" or row.type_of_manufacturing == \"In House\"\n\t\t\t}\n\t\treturn pending","_get_committed_qty":"def _get_committed_qty(self, work_order, process_loss_qty):\n\t\t# Excess loss in existing records must not erase finished goods already produced.\n\t\treturn max(0, flt(work_order.produced_qty), flt(work_order.qty) - flt(process_loss_qty))"},"sha256":"f37c5ed3268c6f97e12463bc49af3b54cfe9d4652f70caab89df69a3b514b21c"}''')
Doc = loss_fixture.Doc


class ProductionPlanReplacementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = p = loss_fixture.ProductionPlanLossTests()
        p.setUp()
        self.addCleanup(p.doCleanups)
        self.h, self.frappe = p.h, p.h.modules["frappe"]
        self.flt = p.h.modules["frappe.utils"].flt
        self.h.flow.planned_qty = 5
        self.h.flow.bom = "BOM"
        p.row.update(include_exploded_items=1, material_request=None, material_request_item=None,
                     description="成品", warehouse="FG-WH", product_bundle_item=None, planned_start_date="2026-10-09")
        p.plan.update(project=None, reserve_stock=1)
        self.precision = 6
        self.frappe.get_precision = lambda *unused: self.precision
        self.frappe.has_permission = lambda *args, **kwargs: "read" not in kwargs["doc"].get("denied", ())
        self.frappe.get_cached_value = lambda kind, name, field: "Nos" if kind == "Item" else None
        self.h.modules["frappe.utils"].now_datetime = lambda: None
        self.created, self.messages, self.native_calls = [], [], []
        self.frappe.msgprint = lambda message, **kwargs: self.messages.append(("native-message", message))
        old_sql, old_value = self.frappe.db.sql, self.frappe.db.get_value
        owner = self

        def sql(query, args, **kwargs):
            if "AS flow" in query:
                self.h.queries.append(query)
                return [Doc(flow=row.get("jf_flow_order")) for row in p.plan.po_items]
            return old_sql(query, args, **kwargs)

        def get_value(kind, name, fields, **kwargs):
            if isinstance(fields, list):
                source = self.h.store.get((kind, name))
                return Doc({field: source.get(field) for field in fields}) if source else None
            return old_value(kind, name, fields, **kwargs)

        self.frappe.db.sql, self.frappe.db.get_value = sql, get_value
        self.frappe.get_value = get_value

        def work_query(kind, filters, fields, **kwargs):
            assert kind == "Work Order" and kwargs["for_update"] and kwargs["ignore_permissions"]
            self.h.queries.append(("current-WO", deepcopy(filters)))
            rows = []
            for (doctype, _), work in self.h.store.items():
                if doctype != kind:
                    continue
                matched = True
                for field, value in filters.items():
                    if isinstance(value, list):
                        matched = matched and (work.get(field) < value[1] if value[0] == "<"
                                               else work.get(field) != value[1])
                    else:
                        matched = matched and work.get(field) == value
                if matched:
                    rows.append(Doc({field: work.get(field) for field in fields}))
            rows.sort(key=lambda row: row.get("name") or "")
            return types.SimpleNamespace(run=lambda **unused: rows)

        self.frappe.qb = types.SimpleNamespace(get_query=work_query)
        source = Path(__file__).parents[1] / "jifeng_erp/production_instruction.py"
        spec = importlib.util.spec_from_file_location("plan_replacement_controller_under_test", source)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

        class Quantities:
            def __init__(self, name):
                self.production_plan = name

            def get_committed_quantities(self):
                quantities = {"production_plan_item": defaultdict(float),
                              "production_plan_sub_assembly_item": defaultdict(float)}
                # Exact installed get_committed_quantities SQL scope: submitted
                # only. The real mixin below must account for draft reservations.
                for (kind, _), work in owner.h.store.items():
                    if kind == "Work Order" and work.get("production_plan") == self.production_plan and work.docstatus == 1:
                        field = ("production_plan_sub_assembly_item" if work.get("production_plan_sub_assembly_item")
                                 else "production_plan_item")
                        quantities[field][work.get(field)] += self._get_committed_qty(work, work.process_loss_qty)
                return quantities

        namespace = {"frappe": self.frappe, "flt": self.flt, "ProductionPlanWorkOrderQuantities": Quantities, "_": lambda value: value}
        for name, method in PINNED_NATIVE["quantity_methods"].items():
            exec(compile(textwrap.dedent(method), "actual-installed-plan-quantity", "exec"), namespace)
            setattr(Quantities, name, namespace[name])
        exec(compile(PINNED_NATIVE["helpers"]["set_default_warehouses"], "actual-installed-plan-defaults", "exec"), namespace)

        class NativePlan(Doc):
            def reload(self):
                owner.native_calls.append("reload")
                self.update(deepcopy(p.plan))
                self.flags = self.get("flags") or Doc()
                return self

            def show_list_created_message(self, kind, names):
                owner.messages.append((kind, tuple(names)))

            def make_subcontracted_purchase_order(self, data, names):
                owner.native_calls.append("purchase-factory")

            def check_if_latest(self):
                owner.h.queries.append("native-plan-parent-current")
                self._doc_before_save = deepcopy(p.plan)
                return "native-latest"

        for name, method in PINNED_NATIVE["methods"].items():
            exec(compile(textwrap.dedent(method), "actual-installed-production-plan", "exec"), namespace)
            setattr(NativePlan, name, namespace[name])
        self.plan_type = type("Plan", (self.module.ProductionInstructionMixin, NativePlan), {})
        self.plan = self.plan_type(deepcopy(p.plan))
        self.plan.flags = Doc()
        self.quantities = Quantities("PLAN")

        class NativeWork(Doc):
            def set_work_order_operations(self):
                owner.native_calls.append("operations")
                self.operations = [Doc(operation="OPERATION")]

            def set_required_items(self):
                owner.native_calls.append("required-items")
                self.required_items = [Doc(item_code="RM", required_qty=float(self.qty) * 2)]

            def insert(self):
                self.name = "NEW-WO-" + str(len(owner.created) + 1)
                self.new = True
                owner.module.validate_work_order(self)
                self.new = False
                self.check_permission("create")
                owner.created.append(self.name)
                owner.h.store["Work Order", self.name] = self
                return self

        self.frappe.new_doc = lambda kind: NativeWork(doctype=kind, flags=Doc(), docstatus=0, qty=0,
            produced_qty=0, process_loss_qty=0, production_plan_sub_assembly_item=None)
        work_module = types.ModuleType("erpnext.manufacturing.doctype.work_order.work_order")
        work_module.OverProductionError = type("OverProductionError", (ValueError,), {})
        work_module.get_default_warehouse = lambda company: {"wip_warehouse": "WIP", "fg_warehouse": "DEFAULT-FG", "scrap_warehouse": "SCRAP"}
        stub = patch.dict(sys.modules, {work_module.__name__: work_module})
        stub.start()
        self.addCleanup(stub.stop)

    def booked_loss(self):
        doc = self.h.post()
        # Actual source proof and native pending formula both use the same WO.
        self.assertEqual(self.h.source(doc)["lost_qty"], 5)
        self.h.work.process_loss_qty = 5
        return doc

    def work_messages(self):
        return [names for kind, names in self.messages if kind == "Work Order"]

    def test_native_plan_full_loss_factory_reuses_draft_and_inverse_cancel_closes(self):
        self.assertEqual(self.quantities.get_pending_quantities(self.fixture.plan)["production_plan_item"]["PLAN-ROW"], 0)
        self.plan.make_work_order()
        self.assertEqual(self.created, [])

    def test_unlinked_native_plan_factory_and_manual_missing_uom_stay_usable(self):
        self.plan.make_work_order()
        self.assertEqual(self.created, [])
        self.booked_loss()
        self.fixture.row.jf_flow_order = None
        self.plan.make_work_order()
        created = self.h.store["Work Order", self.created[0]]
        self.assertFalse(created.get("jf_flow_order"))
        self.assertEqual((created.qty, created.stock_uom, created.fg_warehouse), (5, "Nos", "FG-WH"))
        created.docstatus = 2
        self.fixture.row.jf_flow_order = "FLOW"
        manual = Doc(created)
        manual.update(name="MANUAL", docstatus=0, stock_uom=None)
        self.module.validate_work_order(manual)
        self.assertEqual(manual.jf_flow_order, "FLOW")

    def test_saved_plan_source_change_cannot_take_new_flow_after_native_parent_lock(self):
        self.plan._doc_before_save = None
        native_save = self.plan_type.__mro__[2].check_if_latest
        def save(current):
            value = native_save(current)
            current._doc_before_save.po_items[0].jf_flow_order = "LATE-FLOW"
            return value
        with patch.object(self.plan_type.__mro__[2], "check_if_latest", save), self.assertRaisesRegex(ValueError, "来源已变化"):
            self.plan.check_if_latest()
        self.assertFalse(any(isinstance(query, str) and "LATE-FLOW" in query for query in self.h.queries))
        loss = self.booked_loss()
        self.assertEqual(self.quantities.get_pending_quantities(self.fixture.plan)["production_plan_item"]["PLAN-ROW"], 5)
        self.plan.make_work_order()
        self.assertEqual(len(self.created), 1)
        replacement = self.h.store["Work Order", self.created[0]]
        self.assertEqual((replacement.qty, replacement.production_plan, replacement.production_plan_item,
                          replacement.jf_flow_order, replacement.stock_uom), (5, "PLAN", "PLAN-ROW", "FLOW", "Nos"))
        self.assertEqual((replacement.wip_warehouse, replacement.fg_warehouse, replacement.reserve_stock), ("WIP", "FG-WH", 1))
        self.assertEqual((len(replacement.operations), replacement.required_items[0].required_qty), (1, 10))
        self.plan.make_work_order()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.work_messages()[-1], (replacement.name,))
        self.assertEqual(self.h.card.total_completed_qty, 0)
        with self.assertRaisesRegex(ValueError, "原生产计划补产预留"):
            self.h.module.before_cancel_loss_entry(loss)
        replacement.docstatus = 2
        self.h.module.before_cancel_loss_entry(loss)
        loss.docstatus = 2
        self.h.work.process_loss_qty = 0
        self.assertEqual(self.plan.get_production_items()["PLAN-ROW"]["qty"], 0)
        self.plan.make_work_order()
        self.assertEqual(len(self.created), 1)

    def test_partial_existing_draft_is_preserved_and_only_remaining_quantity_is_created(self):
        self.booked_loss()
        draft = self.fixture.replacement(quantity=2)
        self.plan.make_work_order()
        self.assertEqual(draft.qty, 2)
        self.assertEqual(self.h.store["Work Order", self.created[0]].qty, 3)
        self.assertEqual(set(self.work_messages()[-1]), {draft.name, self.created[0]})
        self.plan.make_work_order()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(draft.qty, 2)

    def test_hidden_draft_reserves_quantity_without_exposing_identity(self):
        self.booked_loss()
        draft = self.fixture.replacement()
        draft.denied = ("read",)
        self.plan.make_work_order()
        self.assertEqual(self.created, [])
        self.assertTrue(all(draft.name not in names for names in self.work_messages()))
        self.assertEqual(self.plan.get_production_items()["PLAN-ROW"]["qty"], 0)

    def test_row_cap_and_other_plan_do_not_borrow_remaining_quantity(self):
        self.booked_loss()
        self.fixture.replacement(quantity=6)
        with self.assertRaisesRegex(ValueError, "超过对应生产计划"):
            self.plan.make_work_order()
        self.assertEqual(self.created, [])
        self.h.store["Work Order", "REPLACEMENT"].production_plan = "OTHER"
        # The same row UUID on another plan cannot be reused. It still consumes
        # the shared Flow cap, so the native insertion guard prevents creation.
        with self.assertRaisesRegex(ValueError, "流程单计划数量"):
            self.plan.make_work_order()
        self.assertEqual(self.created, [])

    def test_wrong_draft_source_is_not_returned_as_reusable(self):
        self.booked_loss()
        draft = self.fixture.replacement()
        for field, value in (("production_plan_sub_assembly_item", "SUB"), ("bom_no", "OTHER"), ("company", "OTHER"),
                             ("sales_order_item", "OTHER"), ("jf_flow_order", "OTHER")):
            old = draft.get(field)
            draft[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "草稿工单"):
                self.plan.make_work_order()
            draft[field] = old
        self.assertEqual(self.created, [])

    def test_factory_and_native_plan_save_hold_flow_before_plan_and_work_reads(self):
        self.booked_loss()
        self.h.queries.clear()
        self.plan.make_work_order()
        queries = self.h.queries
        flow_index = next(i for i, query in enumerate(queries) if isinstance(query, str) and "tabJifeng Flow Order" in query)
        plan_index = next(i for i, query in enumerate(queries) if isinstance(query, str) and "tabProduction Plan`" in query)
        work_index = next(i for i, query in enumerate(queries) if isinstance(query, tuple) and query[0] == "current-WO")
        self.assertLess(flow_index, plan_index)
        self.assertLess(plan_index, work_index)
        self.h.queries.clear()
        self.assertEqual(self.plan.check_if_latest(), "native-latest")
        first_flow = next(i for i, query in enumerate(self.h.queries) if "tabJifeng Flow Order" in query)
        self.assertLess(first_flow, self.h.queries.index("native-plan-parent-current"))

    def test_changed_current_plan_flow_aborts_without_reversed_new_lock(self):
        self.booked_loss()
        old_sql = self.frappe.db.sql
        def change(query, args, **kwargs):
            result = old_sql(query, args, **kwargs)
            if "tabProduction Plan`" in query:
                self.fixture.row.jf_flow_order = "NEW-FLOW"
            return result
        with patch.dict(self.frappe.db, {"sql": change}), self.assertRaisesRegex(ValueError, "来源已变化"):
            self.plan.make_work_order()
        self.assertFalse(any(isinstance(query, str) and "tabJifeng Flow Order" in query and "NEW-FLOW" in query
                             for query in self.h.queries))
        self.assertEqual(self.created, [])

    def test_closed_plan_permission_and_nowait_failure_stop_before_native_insert(self):
        self.booked_loss()
        self.fixture.plan.status = "Closed"
        with self.assertRaisesRegex(ValueError, "未关闭"):
            self.plan.make_work_order()
        self.fixture.plan.status = "Submitted"
        self.fixture.plan.denied = ("read",)
        with self.assertRaises(PermissionError):
            self.plan.make_work_order()
        self.fixture.plan.denied = ()
        self.h.lock_error = TimeoutError
        with self.assertRaises(TimeoutError):
            self.plan.make_work_order()
        self.assertEqual(self.created, [])

    def test_native_precision_keeps_tiny_pending_and_rejects_real_excess(self):
        self.booked_loss()
        self.precision = 9
        self.fixture.replacement(quantity=4.999999999)
        self.assertEqual(self.plan.get_production_items()["PLAN-ROW"]["qty"], 0.000000001)
        self.h.store["Work Order", "REPLACEMENT"].qty = 5.000000002
        with self.assertRaisesRegex(ValueError, "超过对应生产计划"):
            self.plan.get_production_items()
        self.precision = 3
        self.assertTrue(self.module._over_quantity(1.0006, 1.0004))


if __name__ == "__main__":
    unittest.main()
