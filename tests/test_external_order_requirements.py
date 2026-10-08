import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Row:
    def __init__(self, **values):
        self.__dict__.update(values)

    def get(self, fieldname):
        return getattr(self, fieldname, None)


class Flow:
    def __init__(self, name="FLOW-NEW", line="LINE-1"):
        from jifeng_erp.jushuitan import record_key

        self.name = name
        self.external_order = "JST-ORDER-1"
        self.external_line_id = line
        self.source_key = record_key(self.external_order, line)
        self.sales_order = None
        self.company = "QA"
        self.item_code = "ITEM"
        self.flags = types.SimpleNamespace()
        self.save = Mock()
        self.check_permission = Mock()


class TestExternalOrderRequirements(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.db = Mock()
        self.workflow = types.ModuleType("jifeng_erp.workflow")
        self.workflow.create_flows = Mock()
        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = type("Document", (), {})
        frappe_model = types.ModuleType("frappe.model")
        frappe_model.document = document_module
        modules = {
            "frappe": self.frappe,
            "frappe.model": frappe_model,
            "frappe.model.document": document_module,
            "jifeng_erp.workflow": self.workflow,
        }
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                "external_order_under_test",
                pathlib.Path(__file__).parents[1] / "jifeng_erp" / "jifeng_erp" / "doctype"
                / "jifeng_external_order" / "jifeng_external_order.py",
            )
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.module.check_quantity_fields = Mock()
        self.module.assert_no_quantity_downstream = Mock()

    def order(self, row):
        order = object.__new__(self.module.JifengExternalOrder)
        order.name = "JST-ORDER-1"
        order.doctype = "Jifeng External Order"
        order.flags = types.SimpleNamespace()
        order.company = "QA"
        order.source_status = "Confirmed"
        row.item_code = "ITEM"
        row.source_status = "Confirmed"
        order.items = [row]
        order.requirements = "订单通用定制"
        order.packaging = "订单通用包装"
        order.requirements_attachment = "/files/order-guide.pdf"
        order.check_permission = Mock()
        order.reload = Mock()
        return order

    def test_requirement_fields_are_editable_on_the_real_order_child_table(self):
        root = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "jifeng_erp" / "doctype"
        with (root / "jifeng_external_order_item" / "jifeng_external_order_item.json").open(encoding="utf-8") as f:
            child = json.load(f)
        with (root / "jifeng_external_order" / "jifeng_external_order.json").open(encoding="utf-8") as f:
            parent = json.load(f)
        fields = {field["fieldname"]: field for field in child["fields"]}
        self.assertEqual(parent["fields"][-1]["fieldname"], "items")
        self.assertEqual(parent["fields"][-1]["options"], "Jifeng External Order Item")
        for fieldname in ("jf_requirements", "jf_packaging"):
            self.assertIn(fieldname, fields)
            self.assertEqual(fields[fieldname]["fieldtype"], "Small Text")
            self.assertNotIn("read_only", fields[fieldname])
        write_roles = {perm["role"] for perm in parent["permissions"] if perm.get("write")}
        self.assertTrue({"Stock Manager", "Manufacturing Manager"}.issubset(write_roles))

    def test_new_flow_uses_line_specific_text_and_leaves_existing_flow_untouched(self):
        row = Row(external_line_id="LINE-1", flow_order=None,
                  jf_requirements="本行电镀哑光黑", jf_packaging="本行单件纸盒")
        order = self.order(row)
        flow = Flow()
        flow.requirements = "模板定制"
        flow.packaging = "模板包装"
        self.frappe.db.get_value.side_effect = [order.name, None]
        self.frappe.get_doc = Mock(return_value=flow)

        def create_flows(current):
            current.items[0].flow_order = "FLOW-NEW"
            return ["FLOW-NEW"]

        self.workflow.create_flows.side_effect = create_flows
        result = order.make_flows()

        self.assertEqual(result, ["FLOW-NEW"])
        self.assertEqual(flow.requirements, "本行电镀哑光黑")
        self.assertEqual(flow.packaging, "本行单件纸盒")
        self.assertEqual(flow.source_requirements, "本行电镀哑光黑")
        self.assertEqual(flow.source_packaging, "本行单件纸盒")
        self.assertEqual(flow.source_requirements_attachment, "/files/order-guide.pdf")
        self.assertTrue(flow.flags.from_sync)
        flow.save.assert_called_once_with()

        existing = Row(external_line_id="LINE-1", flow_order="FLOW-OLD",
                       jf_requirements="已修改定制", jf_packaging="已修改包装")
        existing_order = self.order(existing)
        self.frappe.db.get_value.side_effect = None
        self.frappe.db.get_value.return_value = "FLOW-OLD"
        self.workflow.create_flows.side_effect = lambda current: ["FLOW-OLD"]
        self.frappe.get_doc.reset_mock()
        self.assertEqual(existing_order.make_flows(), ["FLOW-OLD"])
        self.frappe.get_doc.assert_not_called()

    def test_explicit_apply_updates_each_flow_from_its_line_with_header_fallback(self):
        rows = [
            Row(flow_order="FLOW-1", external_line_id="LINE-1", item_code="ITEM", source_status="Confirmed",
                jf_requirements="行1定制", jf_packaging="行1包装"),
            Row(flow_order="FLOW-2", external_line_id="LINE-2", item_code="ITEM", source_status="Confirmed",
                jf_requirements="", jf_packaging="行2包装"),
        ]
        order = self.order(rows[0])
        order.items = rows
        flows = {name: Flow(name, line) for name, line in (("FLOW-1", "LINE-1"), ("FLOW-2", "LINE-2"))}
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name, **kwargs: flows[name])

        self.assertEqual(order.apply_requirements(), 2)

        self.assertEqual(flows["FLOW-1"].requirements, "行1定制")
        self.assertEqual(flows["FLOW-1"].packaging, "行1包装")
        self.assertEqual(flows["FLOW-2"].requirements, "订单通用定制")
        self.assertEqual(flows["FLOW-2"].packaging, "行2包装")
        for flow in flows.values():
            self.assertEqual(flow.source_requirements_attachment, "/files/order-guide.pdf")
            self.assertTrue(flow.flags.from_sync)
            self.assertEqual([call.args[0] for call in flow.check_permission.call_args_list], ["read", "write"])
            flow.save.assert_called_once_with()

    def test_explicit_apply_rejects_cross_company_or_unrelated_source_before_overwriting(self):
        row = Row(external_line_id="LINE-1", flow_order="FLOW-1", jf_requirements="新要求", jf_packaging="")
        order = self.order(row)
        for field, wrong in (("company", "OTHER"), ("external_order", "OTHER"),
                             ("external_line_id", "OTHER"), ("source_key", "OTHER"),
                             ("item_code", "OTHER"), ("sales_order", "SO-OTHER")):
            flow = Flow("FLOW-1")
            setattr(flow, field, wrong)
            self.frappe.get_doc = Mock(return_value=flow)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "准确来源行"):
                order.apply_requirements()
            flow.save.assert_not_called()

    def test_existing_downstream_blocks_all_requirement_writes(self):
        rows = [Row(external_line_id=f"LINE-{i}", flow_order=f"FLOW-{i}", jf_requirements="新要求",
                    item_code="ITEM", source_status="Confirmed") for i in (1, 2)]
        order = self.order(rows[0])
        order.items = rows
        flows = {f"FLOW-{i}": Flow(f"FLOW-{i}", f"LINE-{i}") for i in (1, 2)}
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name, **kwargs: flows[name])
        self.module.assert_no_quantity_downstream.side_effect = [None, self.module.FlowQuantityError("下游")]
        with self.assertRaisesRegex(ValueError, "有效下游"):
            order.apply_requirements()
        for flow in flows.values():
            flow.save.assert_not_called()

    def test_duplicate_or_missing_line_identity_cannot_guess_requirement_ownership(self):
        row = Row(external_line_id="LINE-1", flow_order=None)
        order = self.order(row)
        order.items = [row, row]
        with self.assertRaisesRegex(ValueError, "唯一行标识"):
            order.make_flows()
        self.workflow.create_flows.assert_not_called()
