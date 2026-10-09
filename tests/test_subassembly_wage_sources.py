"""Closed child payroll origins and immutable approvals; no database writes."""

from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_flow_wage_cost_sources as accounting_fixture
import test_subassembly_wage_entry as entry_fixture


class SubassemblyWageSourceTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing native-shaped company payroll accounting graph,
        # then compose it with the shared real Plan/WO/BOM/source-reader graph.
        finance = accounting_fixture.TestFlowWageCostSources()
        finance.setUp()
        financial_rows = deepcopy(finance.data)
        finance.doCleanups()
        self.t = entry_fixture.SubassemblyWageEntryTests()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.wage = self.t.allocation
        self.frappe, self.Doc = self.t.frappe, entry_fixture.Doc
        self.reader = __import__("sys").modules["jifeng_erp.flow_wage_cost_sources"]
        self.work, self.card, self.plan = self.t.h.work, self.t.h.card, self.t.graph.plan_h.plan
        self.store = self.t.h.store
        self.t.approved.employees[1].piece_rate = 2
        for kind, fields in self.reader.FIELDS.items(): self.t.schema(kind, fields)
        combined = {}
        for kinds in (self.t.tables, self.reader.TABLES):
            for kind, tables in kinds.items():
                target = combined.setdefault(kind, {})
                for field, child, fields in tables:
                    previous = target.get(field, (child, set()))
                    self.assertEqual(previous[0], child)
                    target[field] = child, set(fields) | previous[1]
        self.t.tables = {kind: tuple((field, child, fields) for field, (child, fields) in tables.items())
                         for kind, tables in combined.items()}
        for kind, tables in self.t.tables.items():
            for field, child, fields in tables:
                self.t.schema(child, fields)
                self.t.metas[kind][field] = self.Doc(fieldname=field, fieldtype="Table", options=child, permlevel=0)
        skip = {"Company", "Work Order", "Work Order Operation", "Job Card", "Jifeng Flow Order", "Employee"}
        for (kind, name), values in financial_rows.items():
            if kind in skip: continue
            values = deepcopy(values)
            if values.get("company") == "C": values["company"] = "JF"
            if values.get("employee"): values["employee"] = values["employee"].replace("EMP-", "E-")
            if kind == self.reader.WAGE:
                index = int(name[-1])
                amount, quantity = (12, 6) if index == 1 else (8, 4)
                values.update(operation="OPERATION", stock_uom="Kg", quantity=quantity,
                              piece_amount=amount, total_amount=amount)
            elif kind == self.reader.AS: values["amount"] = 12 if name.endswith("1") else 8
            elif kind == "Salary Detail" and values.get("additional_salary"):
                values["amount"] = 12 if name.endswith("1") else 8
            self.store[kind, name] = self.Doc(values)
        # Native children are loaded from their parent; no query adds rows or
        # fills missing business facts as a side effect of a proof.
        for kind, tables in self.t.tables.items():
            for (parent_kind, name), document in list(self.store.items()):
                if parent_kind != kind: continue
                for field, child, _ in tables:
                    if field in document: continue
                    document[field] = [row for (row_kind, _), row in self.store.items()
                        if row_kind == child and row.get("parent") == name and row.get("parentfield") == field]
        self.t.normalize_children()
        self.hidden = {}
        model = __import__("sys").modules["frappe.model"]
        model.get_permitted_fields = lambda kind, **kwargs: set(self.t.metas[kind]) - self.hidden.get(kind, set())
        self.reader.get_permitted_fields = model.get_permitted_fields
        self.active = set()
        original_sql = self.frappe.db.sql.side_effect
        def sql(query, values, **kwargs):
            if "tabJifeng Wage" in query:
                if "work_order=%s" in query:
                    kind = self.wage.ALLOCATION if "tabJifeng Wage Allocation" in query else "Jifeng Wage Entry"
                    return [self.Doc(name="hidden-approved", docstatus=1)] if kind in self.active else []
                if "tabJifeng Wage Allocation" in query and self.wage.ALLOCATION in self.active:
                    return [self.Doc(name="A-1", docstatus=1, source_signature=self.t.approved.source_signature)]
                return []
            if "production_plan_sub_assembly_item IN" in query:
                return [self.Doc(name=self.work.name)]
            return original_sql(query, values, **kwargs)
        self.frappe.db.sql.side_effect = sql

    def cost(self):
        return self.reader.posted_flow_wage_costs("FLOW")

    def test_six_four_manual_child_rates_accrue_exact_twenty_not_company_payroll_2020(self):
        self.t.approve()
        self.assertEqual([row.quantity for row in self.t.approved.employees], [Decimal(6), Decimal(4)])
        self.assertEqual([row.total_amount for row in self.t.approved.employees], [Decimal("12.00"), Decimal("8.00")])
        self.assertEqual(self.t.approved.total_amount, 20)
        self.assertEqual((self.t.approved.flow_order, self.t.approved.stock_uom), ("FLOW", "Kg"))
        self.assertFalse(self.work.jf_flow_order)
        result = self.cost()
        self.assertEqual(result["total"], 20)
        self.assertEqual([row["amount"] for row in result["rows"]], [12, 8])
        self.assertEqual({row["journal_entry"] for row in result["rows"]}, {"ACCRUAL"})
        self.assertEqual(self.store[self.reader.JE, "ACCRUAL"].total_debit, 2020)
        result["finish_guard"]()

    def test_approval_freezes_child_quantities_operation_unit_and_plan_source(self):
        self.t.approve()
        signature = self.t.approved.source_signature
        self.active.add(self.wage.ALLOCATION)
        for field, value in (("for_quantity", 11), ("total_completed_qty", 11), ("process_loss_qty", 1),
                             ("pending_qty", 1), ("stock_uom", "Nos"), ("operation", "ParentOp")):
            candidate = deepcopy(self.card)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "不能修改计薪来源"):
                self.wage.guard_job_card_allocation(candidate)
        self.assertEqual(self.t.approved.source_signature, signature)

    def test_work_source_guard_blocks_business_edits_cancel_but_allows_native_progress(self):
        self.active.add(self.wage.ALLOCATION)
        for field, value in (("qty", 11), ("stock_uom", "Nos"), ("production_plan_sub_assembly_item", "OTHER"),
                             ("production_item", "FG"), ("wip_warehouse", "OTHER-WIP")):
            candidate = deepcopy(self.work)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "已有审核工资或分配"):
                self.wage.guard_subassembly_wage_sources(candidate)
        candidate = deepcopy(self.work)
        candidate.required_items[0].source_warehouse = "OTHER-RAW"
        with self.assertRaisesRegex(ValueError, "已有审核工资或分配"):
            self.wage.guard_subassembly_wage_sources(candidate)
        with self.assertRaisesRegex(ValueError, "已有审核工资或分配"):
            self.wage.guard_subassembly_wage_sources(deepcopy(self.work), "before_cancel")
        candidate = deepcopy(self.work)
        candidate.update(produced_qty=10, material_transferred_for_manufacturing=10, status="Completed", modified="later")
        candidate.operations[0].update(completed_qty=10, actual_operation_time=60, status="Completed")
        self.wage.guard_subassembly_wage_sources(candidate)
        self.active.clear()
        self.wage.guard_subassembly_wage_sources(deepcopy(self.work), "before_cancel")

    def test_plan_guard_blocks_exact_child_or_parent_changes_without_freezing_progress(self):
        self.active.add("Jifeng Wage Entry")
        for target, field, value in (("child", "qty", 11), ("child", "bom_no", "OTHER"),
                ("parent", "planned_qty", 6), ("parent", "jf_flow_order", "OTHER"),
                ("plan", "combine_sub_items", 1)):
            candidate = deepcopy(self.plan)
            row = candidate.sub_assembly_items[0] if target == "child" else candidate.po_items[0] if target == "parent" else candidate
            row[field] = value
            with self.subTest(target=target, field=field), self.assertRaisesRegex(ValueError, "已有审核工资或分配"):
                self.wage.guard_subassembly_wage_sources(candidate)
        with self.assertRaisesRegex(ValueError, "已有审核工资或分配"):
            self.wage.guard_subassembly_wage_sources(deepcopy(self.plan), "before_cancel")
        candidate = deepcopy(self.plan)
        candidate.update(status="Completed", modified="later")
        candidate.po_items[0].update(produced_qty=5, pending_qty=0, ordered_qty=5)
        candidate.sub_assembly_items[0].update(produced_qty=10, ordered_qty=10, received_qty=10)
        self.wage.guard_subassembly_wage_sources(candidate)
        self.active.clear()
        self.wage.guard_subassembly_wage_sources(deepcopy(self.plan), "before_cancel")

    def test_unsubmitted_child_replacement_draft_keeps_normal_quantity_and_warehouse_edits(self):
        self.work.docstatus = 0
        candidate = deepcopy(self.work)
        candidate.update(qty=8, wip_warehouse="OTHER-WIP", fg_warehouse="OTHER-FG")
        self.wage.guard_subassembly_wage_sources(candidate)
        self.assertFalse(self.work.jf_flow_order)

    def test_native_progress_keeps_signature_but_business_bom_or_raw_source_does_not(self):
        proof = self.wage.resolve_wage_work_source(self.work, card=self.card)
        snapshot = self.wage.source_snapshot(self.card, self.work, _work_source=proof)
        self.work.update(status="Completed", produced_qty=10, modified="later")
        self.plan.update(status="Closed", modified="later")
        again = self.wage.source_snapshot(self.card, self.work)
        self.assertEqual(snapshot["source_signature"], again["source_signature"])
        self.work.required_items[0].source_warehouse = "OTHER-RAW"
        self.assertNotEqual(snapshot["source_signature"], self.wage.source_snapshot(self.card, self.work)["source_signature"])

    def test_late_card_business_change_cannot_sign_an_initial_stale_object(self):
        old = deepcopy(self.card)
        for field, value in (("for_quantity", 11), ("stock_uom", "Nos"), ("operation_id", "OTHER")):
            self.card[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "来源已变化"):
                self.wage.resolve_wage_work_source(self.work, card=old)
            self.card[field] = old[field]
        self.card.time_logs[0].employee = "E-2"
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.wage.resolve_wage_work_source(self.work, card=old)

    def test_hidden_raw_warehouse_or_plan_field_and_masked_table_cannot_disclose_cost(self):
        for kind, field in (("Work Order Item", "source_warehouse"), ("Production Plan Sub Assembly Item", "qty"),
                            ("Job Card", "pending_qty")):
            self.hidden[kind] = {field}
            with self.subTest(kind=kind, field=field), self.assertRaises(PermissionError): self.cost()
            self.hidden.clear()
        self.frappe.session.user = "ordinary-payroll-reader"
        self.t.metas["Production Plan"]["sub_assembly_items"].mask = 1
        old_meta = self.t.meta
        def meta(kind):
            result = old_meta(kind)
            result.get_permlevel_access = lambda permission_type, **kwargs: [0] if permission_type == "read" else []
            return result
        self.frappe.get_meta = meta
        with self.assertRaises(PermissionError): self.cost()

    def test_cost_finish_guard_covers_child_raw_membership_source_and_permission_changes(self):
        for change in ("raw", "card", "plan", "permission"):
            result = self.cost()
            original_work, original_card, original_plan = deepcopy(self.work), deepcopy(self.card), deepcopy(self.plan)
            if change == "raw": self.work.required_items.append(self.Doc(name="HIDDEN-RM", item_code="RM", required_qty=1, stock_uom="Kg"))
            elif change == "card": self.card.pending_qty = 1
            elif change == "plan": self.plan.sub_assembly_items[0].production_plan_item = "OTHER"
            else: self.hidden["Work Order Item"] = {"source_warehouse"}
            with self.subTest(change=change), self.assertRaises(PermissionError): result["finish_guard"]()
            self.work.clear(); self.work.update(original_work)
            self.card.clear(); self.card.update(original_card)
            self.plan.clear(); self.plan.update(original_plan)
            self.hidden.clear()

    def test_cost_tracks_real_accrual_cancellation_and_ignores_payment_state(self):
        self.store[self.reader.JE, "PAYMENT"] = self.Doc(doctype=self.reader.JE, name="PAYMENT", company="JF",
            docstatus=1, voucher_type="Bank Entry", total_debit=2020, total_credit=2020)
        self.assertEqual(self.cost()["total"], 20)
        self.store[self.reader.JE, "PAYMENT"].docstatus = 2
        self.assertEqual(self.cost()["total"], 20)
        self.store[self.reader.JE, "ACCRUAL"].docstatus = 2
        self.assertEqual(self.cost()["total"], 0)

    def test_hooks_protect_child_sources_before_validate_update_and_cancel(self):
        spec = importlib.util.spec_from_file_location("child_wage_hooks", Path(__file__).parents[1] / "jifeng_erp/hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        for kind in ("Work Order", "Production Plan"):
            for event in ("before_validate", "before_update_after_submit", "before_cancel"):
                names = hooks.doc_events[kind][event]
                names = names if isinstance(names, list) else [names]
                self.assertIn("jifeng_erp.wage_allocation.guard_subassembly_wage_sources", names)


if __name__ == "__main__":
    unittest.main()
