"""Pure Python source/output models; never native SQL, files, queue or UI proof."""

import ast
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_ledger_field_mask_candidate as source_models
from test_ledger_field_mask_candidate import Row


ROOT = Path(__file__).parents[1]


class Report(Row):
    def __setattr__(self, key, value): self[key] = value
    def as_dict(self): return dict(self)
    def get_module_method(self, method):
        slug = self.name.lower().replace(" ", "_")
        return getattr(importlib.import_module(f"jifeng_erp.jifeng_erp.report.{slug}.{slug}"), method)
    def build_data_dict(self, rows, columns):
        return [Row(row) if isinstance(row, dict) else Row(zip([col.fieldname for col in columns], row)) for row in rows]


class FreshModels(unittest.TestCase):
    def setUp(self):
        source_models.CandidateModels.setUp(self)
        self.fr = self.env.frappe
        self.fr.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
        self.fr.unscrub = lambda value: value.title().replace("_", " ")
        self.cache = {}
        self.fr.cache = types.SimpleNamespace(hset=lambda key, name, value: self.cache.__setitem__((key, name), value),
                                             hget=lambda key, name: self.cache.get((key, name), 0))
        scripts = types.ModuleType("jifeng_erp.sample_source_report_scripts")
        scripts._native_module = self.pinned_model_module
        self.script_patch = patch.dict(sys.modules, {scripts.__name__: scripts})
        self.script_patch.start(); self.addCleanup(self.script_patch.stop)
        sys.modules.pop("jifeng_erp.ledger_report_output", None)
        self.output = importlib.import_module("jifeng_erp.ledger_report_output")
        for report_name in self.output.LEDGER_REPORTS:
            slug = report_name.lower().replace(" ", "_")
            sys.modules.pop(f"jifeng_erp.jifeng_erp.report.{slug}.{slug}", None)
        self.reports = {}
        original_doc, original_all, original_list = self.fr.get_doc, self.fr.get_all, self.fr.get_list
        self.fr.get_doc = lambda dt, name: deepcopy(self.reports[name]) if dt == "Report" else self.child_or_doc(original_doc, dt, name)
        self.fr.get_all = lambda dt, **kw: self.report_rows(kw) if dt == "Report" else original_all(dt, **kw)
        for meta in self.env.meta.values(): meta.istable = "parent" in meta.fields

        def get_list(dt, **kw):
            rows = original_list(dt, **kw)
            if kw.get("as_list"):
                return [tuple(row.get(field) for field in kw["fields"]) for row in rows]
            return rows
        self.fr.get_list = get_list
        self.script_models = []
        source = ast.parse((ROOT / "jifeng_erp/sample_source_reports.py").read_text(encoding="utf-8"))
        body = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "SampleSourceReportMixin")
        namespace = {"KNOWN_REPORTS": frozenset({"Open Work Orders"}),
                     "_execute": lambda report, filters, kind, native: native(filters)}
        exec(compile(ast.Module(body=[body], type_ignores=[]), "actual-owned-ReportMixin-model", "exec"), namespace)
        mixin = namespace["SampleSourceReportMixin"]

        class Native(Report):
            def execute_script_report(self, filters): raise AssertionError("Core Timer path used")
            def get_data(self, *args): return ("ordinary native", args)
            def run_query_report(self, *args): return ("ordinary query", args)
        class BoundReport(mixin, Native): pass
        self.report_class = BoundReport
        for name, (_, dt, _, _) in self.output.LEDGER_REPORTS.items():
            self.reports[name] = BoundReport(doctype="Report", name=name, report_name=name, report_type="Script Report",
                                             ref_doctype=dt, module="Jifeng ERP", is_standard="Yes", disabled=0,
                                             prepared_report=0, disable_prepared_report_automation=0, add_total_row=0,
                                             snapshot_report=0, roles=[{"role": "Purchase User"}], columns=[], filters=[])
        self.fr.has_permission = lambda dt, permission: (dt, "*", permission) not in self.env.denied
        self.role_denied = False
        self.chart, self.summary = {"chart": 1}, [{"summary": 1}]
        self.native = types.SimpleNamespace(
            get_report_doc=self.native_get_report,
            validate_filters_permissions=Mock(), sbool=lambda value: value not in (None, False, 0, "0", "false"),
            cint=lambda value: int(value or 0),
            get_report_result=lambda report, filters: [*report.execute_script_report(filters), "message", self.chart, self.summary, False],
            get_column_as_dict=lambda column: deepcopy(column),
            normalize_result=lambda rows, columns: deepcopy(rows),
            get_filtered_data=Mock(side_effect=lambda dt, columns, rows, user: rows),
            add_total_row=Mock(side_effect=lambda rows, columns, **kw: [*rows, {"Total": True}]),
            translate_report_data=Mock(side_effect=lambda rows, total: rows),
            ljust_list=lambda values, length: [*values, *([None] * (length - len(values)))],
        )
        self.output._native = lambda: self.native
        self.name = "Jifeng Purchase Receipt Ledger"

    def pinned_model_module(self, name, digest, methods=()):
        if name == "frappe.model.utils.mask":
            return types.SimpleNamespace(mask_field_value=lambda field, value: "XXXXXXXX" if value else value)
        module = importlib.import_module(name)
        self.assertEqual(hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(), digest)
        for method in methods: self.assertTrue(callable(getattr(module, method)))
        return module

    def report_rows(self, kwargs):
        names = kwargs.get("filters", {}).get("name")
        return [deepcopy(doc) for name, doc in self.reports.items() if not names or name == names]

    def child_or_doc(self, original, dt, name):
        if (dt, name) in self.env.data: return original(dt, name)
        candidates = [row for row in self.env.records(dt) if row.get("name") == name]
        if len(candidates) == 1: return deepcopy(candidates[0])
        raise KeyError((dt, name))

    def native_get_report(self, name):
        target = self.output.resolve_target(name)
        if self.role_denied: raise PermissionError("native Report role gate")
        report = deepcopy(target.chain[-1])
        if not self.fr.has_permission(report.ref_doctype, "report"): raise PermissionError("native refdoctype report gate")
        if report.disabled: raise ValueError("native disabled")
        report.custom_columns, report.custom_filters = [], []
        if len(target.chain) > 1:
            outer = target.chain[0]
            data = json.loads(outer.get("json") or "{}")
            report.custom_columns = data.get("columns", [])
            report.custom_filters = data.get("filters", [])
            report.custom_report = outer.name
            report.prepared_report = outer.prepared_report
        return report

    def alias(self, name="CUSTOM", reference=None, **kw):
        doc = self.report_class(doctype="Report", name=name, report_name=name, report_type="Custom Report",
                                reference_report=reference or self.name, prepared_report=1, json="{}", **kw)
        self.reports[name] = doc
        return doc

    def run_report(self, **kw): return self.output.generate_fresh(self.name, self.filters, **kw)

    def test_all_five_modules_execute_without_core_timer(self):
        for name in self.output.LEDGER_REPORTS:
            with self.subTest(name=name):
                result = self.output.generate_fresh(name, self.filters)
                self.assertTrue(result["columns"])
                self.assertEqual(result["message"], "message")
                self.assertIs(result["chart"], self.chart)
                self.assertIs(result["report_summary"], self.summary)
        self.assertEqual(self.reports[self.name].prepared_report, 0)

    def test_native_get_data_and_run_query_report_use_fresh_even_prepared_flag(self):
        report = self.alias()
        columns, rows = report.get_data(self.filters, as_dict=True)
        self.assertEqual(rows[0]["purchase_receipt"], "PR")
        self.assertEqual(columns[0].fieldname, "purchase_receipt")
        self.assertEqual(report.run_query_report(self.filters)[1][0]["accepted_qty"], 6)

    def test_nested_alias_native_defaults_and_columns_translate_totals(self):
        self.reports[self.name].add_total_row = 1
        self.alias("INNER")
        outer = self.alias("OUTER", "INNER")
        outer.json = json.dumps({"filters": {**self.filters, "translate_data": True},
                                 "columns": [{"fieldname": "accepted_qty", "fieldtype": "Float"},
                                             {"fieldname": "stale_old_column", "fieldtype": "Data"}]})
        payload = self.output.generate_fresh("OUTER", {"company": "wrong"})
        self.assertEqual([col["fieldname"] for col in payload["columns"]], ["accepted_qty"])
        self.assertTrue(payload["add_total_row"])
        self.native.translate_report_data.assert_called_once()
        self.assertEqual(payload["custom_filters"]["company"], "CO")

    def test_canonical_tamper_custom_cycle_missing_chain_never_ordinary(self):
        self.reports[self.name].report_type = "Report Builder"
        self.assertEqual(self.output.resolve_target(self.name).canonical_name, self.name)
        with self.assertRaises(PermissionError): self.run_report()
        self.alias("LOOP", "LOOP")
        with self.assertRaises(PermissionError): self.output.resolve_target("LOOP")
        self.alias("MISSING", "absent")
        with self.assertRaises(KeyError): self.output.resolve_target("MISSING")

    def test_role_gate_and_explicit_snapshot_deny_before_source(self):
        self.role_denied = True
        with self.assertRaises(PermissionError): self.run_report()
        self.role_denied = False
        with self.assertRaises(PermissionError): self.output.generate_fresh(self.name, {**self.filters, "prepared_report_name": "OLD"})
        self.assertFalse(self.env.permission_calls)

    def test_native_memory_definition_or_saved_columns_mismatch_rejects(self):
        original = self.native.get_report_doc
        def changed(name):
            report = original(name)
            report.ref_doctype = "Supplier"
            return report
        self.native.get_report_doc = changed
        with self.assertRaises(PermissionError): self.run_report()
        alias = self.alias()
        def wrong_columns(name):
            report = original(name)
            report.custom_columns = [{"fieldname": "unexpected"}]
            return report
        self.native.get_report_doc = wrong_columns
        with self.assertRaises(PermissionError): self.output.generate_fresh(alias.name, self.filters)
        self.assertFalse(self.env.permission_calls)

    def test_guard_reused_through_native_filter_and_formatter(self):
        seen = []
        original_finish = self.guard.LedgerSourceGuard.finish
        def finish(guard):
            seen.append(id(guard)); return original_finish(guard)
        self.native.get_filtered_data.side_effect = lambda dt, columns, rows, user: rows
        with patch.object(self.guard.LedgerSourceGuard, "finish", finish):
            value = self.run_report(transform=lambda payload: ("formatted", payload["result"][0]["accepted_qty"]))
        self.assertEqual(value, ("formatted", 6))
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 1)

    def test_final_filter_or_transform_revocation_rejects(self):
        def revoke(payload):
            self.env.unreadable.add(("Purchase Receipt Item", "amount"))
            return "private formatted bytes"
        with self.assertRaises(PermissionError): self.run_report(transform=revoke)
        self.env.unreadable.clear()
        self.native.get_filtered_data.side_effect = lambda dt, columns, rows, user: (self.env.denied.add(("Purchase Receipt", "PR", "read")) or rows)
        with self.assertRaises(PermissionError): self.run_report()

    def test_actual_session_identity_no_internal_impersonation_or_unframed_execution(self):
        with self.assertRaises(PermissionError): self.run_report(user="other")
        with self.assertRaises(PermissionError): self.reports[self.name].execute_script_report(self.filters)
        self.assertEqual(self.output.run(self.name, self.filters, user="ignored-old-http-user")["result"][0]["accepted_qty"], 6)

    def test_extra_mask_and_omitted_native_names_preserved(self):
        self.env.meta["Supplier"].fields["phone"] = Row(fieldname="phone", fieldtype="Data", permlevel=0, mask=1)
        self.env.data["Supplier", "SUP"]["phone"] = "13812345678"
        native_get_list = self.fr.get_list
        def masked(dt, **kw):
            result = native_get_list(dt, **kw)
            return [(name, "XXXXXXXX") for name, value in result] if dt == "Supplier" and kw.get("as_list") else result
        self.fr.get_list = masked
        columns = [{"fieldname": "phone", "doctype": "Supplier", "link_field": "supplier", "insert_after_index": 0}]
        self.assertEqual(self.run_report(custom_columns=columns)["result"][0]["phone"], "XXXXXXXX")
        self.env.hidden.add(("Supplier", "SUP"))
        # Supplier also contributes to the source report, so use an ordinary
        # extra-only identity to verify the native omitted-name contract.
        extra = self.output._ExtraRead(self.guard.LedgerSourceGuard("purchase_receipt"), "Supplier", "phone", ["SUP"])
        self.assertEqual(extra.before[0], {})

    def test_child_custom_column_parent_table_binding_and_final_change(self):
        columns = [{"fieldname": "amount", "doctype": "Purchase Receipt Item", "link_field": "receipt_item", "insert_after_index": 0}]
        # Original amount is already a source column; use a distinct optional
        # native child field so the custom-field traversal actually runs.
        self.env.meta["Purchase Receipt Item"].fields["note"] = Row(fieldname="note", fieldtype="Data", permlevel=0, mask=0)
        self.env.data["Purchase Receipt", "PR"]["items"][0]["note"] = "child note"
        columns[0]["fieldname"] = "note"
        self.assertEqual(self.run_report(custom_columns=columns)["result"][0]["note"], "child note")
        def alter(payload):
            self.env.data["Purchase Receipt", "PR"]["items"][0]["note"] = "changed"
            return payload
        with self.assertRaises(PermissionError): self.run_report(custom_columns=columns, transform=alter)

    def test_custom_column_conflict_and_chain_kept(self):
        self.env.meta["Supplier"].fields["contact"] = Row(fieldname="contact", fieldtype="Data", permlevel=0, mask=0)
        self.env.data["Supplier", "SUP"]["contact"] = "RAW"
        columns = [{"fieldname": "contact-supplier", "id": "custom_contact", "doctype": "Supplier", "link_field": "supplier", "insert_after_index": 0},
                   {"fieldname": "stock_uom", "doctype": "Item", "link_field": "custom_contact", "insert_after_index": 1}]
        # stock_uom is an original report column, so native logic leaves that
        # existing output alone. Chained extras use an additional Item field.
        self.env.meta["Item"].fields["description"] = Row(fieldname="description", fieldtype="Data", permlevel=0, mask=0)
        self.env.data["Item", "RAW"]["description"] = "chained extra"
        columns[1]["fieldname"] = "description"
        payload = self.run_report(custom_columns=columns)
        self.assertEqual(payload["result"][0]["custom_contact"], "RAW")
        self.assertEqual(payload["result"][0]["description"], "chained extra")

    def test_ordinary_controller_forwarding_and_source_guard_outside_frame(self):
        self.reports["Ordinary"] = self.report_class(doctype="Report", name="Ordinary", report_name="Ordinary", report_type="Report Builder")
        report = self.reports["Ordinary"]
        self.assertEqual(report.get_data()[0], "ordinary native")
        self.assertEqual(report.run_query_report()[0], "ordinary query")
        self.assertEqual(self.proc.purchase_receipt_ledger(self.filters)[1][0]["accepted_qty"], 6)


if __name__ == "__main__": unittest.main()
