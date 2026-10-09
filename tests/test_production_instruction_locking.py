"""Factory locking and malformed downstream relationships without a business DB."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch

from jifeng_erp.flow_quantity import FlowQuantityError, assert_no_quantity_downstream


class Record(dict):
    __setattr__ = dict.__setitem__

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def check_permission(self, action):
        if action in self.get("denied", ()):
            raise PermissionError("native read denied")


class NativePlan:
    def __init__(self, owner, rows, *, snapshot=None, new=False):
        self.owner, self.name, self.company = owner, "PLAN", "JF"
        self.flags, self.new = Record(), new
        self.docstatus, self.status = 1, "Submitted"
        self.current_rows = deepcopy(rows)
        self.po_items = deepcopy(rows if snapshot is None else snapshot)
        self.sub_assembly_items = []
        owner.plan = self

    def get(self, key, default=None):
        return getattr(self, key, default)

    def is_new(self):
        return self.new

    def reload(self):
        self.owner.events.append(("plan-current-read", self.flags.get("for_update")))
        self.po_items = deepcopy(self.current_rows)

    def check_permission(self, action):
        self.owner.events.append(("plan-permission", action))

    def get_production_items(self):
        self.owner.events.append(("native-items",))
        return {row.name: {"qty": row.planned_qty} for row in self.po_items}


class ProductionFactoryLocking(unittest.TestCase):
    def setUp(self):
        self.events, self.allocations, self.denied_flows = [], {}, set()
        self.plan_exists = True
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda **unused: lambda function: function
        frappe.db = types.SimpleNamespace(get_value=self.get_value, sql=self.sql)
        frappe.get_doc = self.get_doc
        frappe.get_precision = lambda *unused: 9
        frappe.has_permission = lambda *unused, **kwargs: not kwargs["doc"].get("denied")
        frappe.qb = types.SimpleNamespace(get_query=self.get_query)
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda value=None, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
        utils.now_datetime = lambda: None
        self.modules = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        source = Path(__file__).resolve().parents[1] / "jifeng_erp" / "production_instruction.py"
        spec = importlib.util.spec_from_file_location("production_factory_locking_under_test", source)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.plan_type = type("Plan", (self.module.ProductionInstructionMixin, NativePlan), {})

    def get_value(self, doctype, name, field, **kwargs):
        self.events.append(("plan-lock", kwargs.get("for_update")))
        return name if self.plan_exists else None

    def sql(self, query, values, **kwargs):
        if "AS flow" in query:
            self.events.append(("plan-hint",))
            return [Record(flow=row.get("jf_flow_order")) for row in self.plan.current_rows]
        if "tabJifeng Flow Order" in query:
            self.events.append(("flow-scalar-lock", values[0]))
            return [(values[0],)]
        if "tabProduction Plan`" in query:
            self.events.append(("plan-lock", "FOR UPDATE NOWAIT" in query))
            return [("PLAN",)] if self.plan_exists else []
        self.events.append(("plan-child-lock", values[1], "FOR UPDATE NOWAIT" in query))
        return []

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Work Order":
            return self.drafts[name]
        self.events.append(("flow-lock", name, kwargs.get("for_update")))
        return Record(name=name, company="JF", denied=("read",) if name in self.denied_flows else ())

    def get_query(self, doctype, filters, **kwargs):
        self.events.append(("allocation-lock", filters["production_plan_item"], kwargs.get("for_update")))
        return types.SimpleNamespace(run=lambda **unused: self.allocations.get(filters["production_plan_item"], []))

    @staticmethod
    def row(name, flow, qty):
        return Record(name=name, jf_flow_order=flow, planned_qty=qty,
                      item_code="FG", bom_no="BOM", stock_uom="Nos", sales_order=None, sales_order_item=None)

    def test_opposite_row_orders_take_all_unique_flow_locks_before_native_items(self):
        for rows in ([self.row("B1", "B", 7), self.row("A1", "A", 9), self.row("A2", "A", 2)],
                     [self.row("A1", "A", 9), self.row("B1", "B", 7)]):
            with self.subTest(order=[row.jf_flow_order for row in rows]):
                self.events.clear()
                self.plan_type(self, rows).get_production_items()
                locks = [event for event in self.events if event[0] == "flow-lock"]
                self.assertEqual(locks, [("flow-lock", "A", True), ("flow-lock", "B", True)])
                self.assertLess(self.events.index(locks[-1]), self.events.index(("native-items",)))
                scalars = [event for event in self.events if event[0] == "flow-scalar-lock"]
                self.assertEqual(scalars, [("flow-scalar-lock", "A"), ("flow-scalar-lock", "B")])
                self.assertLess(self.events.index(scalars[-1]), self.events.index(("plan-lock", True)))
                self.assertTrue(all(event[2] for event in self.events if event[0] == "allocation-lock"))

    def test_current_locked_rows_replace_stale_rows_and_include_draft_reservations(self):
        self.allocations["CURRENT"] = [Record(name="DRAFT", docstatus=0, qty=4)]
        self.drafts = {"DRAFT": Record(name="DRAFT", docstatus=0, qty=4, production_plan="PLAN",
            production_plan_item="CURRENT", jf_flow_order="B", company="JF", production_item="FG", bom_no="BOM", stock_uom="Nos")}
        plan = self.plan_type(self, [self.row("CURRENT", "B", 10), self.row("NATIVE", None, 20)],
                              snapshot=[self.row("OLD", "A", 100)])
        result = plan.get_production_items()
        self.assertEqual(result, {"CURRENT": {"qty": 6}, "NATIVE": {"qty": 20}})
        self.assertEqual([event[1] for event in self.events if event[0] == "flow-lock"], ["B"])
        self.assertNotIn(("allocation-lock", "NATIVE", True), self.events)

    def test_saved_factory_rejects_missing_or_new_plan_before_allocations(self):
        self.plan_exists = False
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.plan_type(self, [self.row("A1", "A", 2)]).get_production_items()
        self.events.clear()
        with self.assertRaisesRegex(ValueError, "先保存"):
            self.plan_type(self, [self.row("A1", "A", 2)], new=True).get_production_items()
        self.assertEqual(self.events, [])

    def test_flow_read_permission_stops_before_native_factory_and_allocation(self):
        self.denied_flows.add("A")
        with self.assertRaises(PermissionError):
            self.plan_type(self, [self.row("A1", "A", 2)]).get_production_items()
        self.assertNotIn(("native-items",), self.events)
        self.assertFalse(any(event[0] == "allocation-lock" for event in self.events))


class DownstreamRelationshipLocking(unittest.TestCase):
    def setUp(self):
        self.childtype, self.parenttype, self.parentfield = "Material Request Item", "Material Request", "items"
        self.child_parenttype, self.child_parentfield = self.parenttype, self.parentfield
        self.parent_exists, self.parent_status = True, 2
        self.queries = []
        frappe = types.ModuleType("frappe")
        frappe.QueryTimeoutError, frappe.QueryDeadlockError = TimeoutError, RuntimeError
        frappe.db = types.SimpleNamespace(sql=self.query)
        self.modules = patch.dict(sys.modules, {"frappe": frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def query(self, query, values):
        self.queries.append(query)
        if f"FROM `tab{self.childtype}` detail " not in query:
            return []
        # Model the actual LEFT JOIN predicate. A wrong type or field cannot
        # borrow the cancelled status of an unrelated same-name parent.
        predicates = dict(re.findall(r"detail\.(parenttype|parentfield)='([^']+)'", query))
        joined = (self.parent_exists
                  and ("parenttype" not in predicates or predicates["parenttype"] == self.child_parenttype)
                  and ("parentfield" not in predicates or predicates["parentfield"] == self.child_parentfield))
        return [] if joined and self.parent_status == 2 else [("hidden-child",)]

    def test_cancelled_parent_only_excludes_exact_native_relationship(self):
        for childtype, parenttype, parentfield in (("Material Request Item", "Material Request", "items"),
                                                 ("Production Plan Item", "Production Plan", "po_items")):
            with self.subTest(childtype=childtype):
                self.childtype, self.parenttype, self.parentfield = childtype, parenttype, parentfield
                self.child_parenttype, self.child_parentfield = parenttype, parentfield
                assert_no_quantity_downstream("FLOW")
                for wrong_field, value in (("child_parenttype", "Other Type"), ("child_parentfield", "other")):
                    setattr(self, wrong_field, value)
                    with self.assertRaises(FlowQuantityError) as caught:
                        assert_no_quantity_downstream("FLOW")
                    self.assertNotIn("hidden-child", str(caught.exception))
                    self.child_parenttype, self.child_parentfield = parenttype, parentfield
        self.assertTrue(all("FOR UPDATE NOWAIT" in query for query in self.queries))


if __name__ == "__main__":
    unittest.main()
