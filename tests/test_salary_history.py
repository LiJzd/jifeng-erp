"""Saved payroll amounts, complete classifications and private source boundaries."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
STAMP = "2026-10-03T12:00:01.123456"
LATER = "2026-10-03T12:00:02.123456"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Meta:
    def __init__(self, names):
        self.fields = [Record(fieldname=name, fieldtype="Data", options="", permlevel=0, mask=0) for name in names]
        self.read_levels, self.mask_levels = [0], []

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return self.mask_levels if kwargs["permission_type"] == "mask" else self.read_levels


class TestSalaryHistory(unittest.TestCase):
    def setUp(self):
        self.records, self.metas, self.denials, self.hidden, self.hidden_fields = {}, {}, set(), set(), {}
        self.calls, self.whitelisted = [], []
        self.list_hook, self.policy = None, "initial"
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, KeyError
        self.frappe.session = Record(user="payroll@example.invalid")
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = self.whitelist
        self.frappe.has_permission, self.frappe.get_doc, self.frappe.get_list = self.permission, self.get_doc, self.get_list
        self.frappe.get_meta, self.frappe.get_all = lambda doctype: self.metas[doctype], self.get_all
        self.frappe.share = Record(get_shared=lambda *args, **kwargs: [])
        self.frappe.db = Record(commit=Mock(side_effect=AssertionError("no commit")),
            sql=Mock(side_effect=AssertionError("no SQL or lock")), set_value=Mock(side_effect=AssertionError("no write")))
        environment = self

        class Document(Record):
            @property
            def meta(self):
                return environment.metas[self.doctype]

            def precision(self, field):
                return 2

            def save(self):
                raise AssertionError("no save")

        self.Document = Document
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        self.rule_module = types.ModuleType("jifeng_erp.salary_history_classification")
        self.rule_module.EARNING_BUCKETS = {"basic", "time", "piece", "housing", "full_attendance", "other_earning"}
        self.rule_module.DEDUCTION_BUCKETS = {"utilities", "social_insurance", "fine", "loan", "other_deduction"}
        self.rule_rows = [self.rule("BASIC", "earnings", "basic"),
            self.rule("WAGES", "earnings", "other_earning", "wage_breakdown", "other_earning"),
            self.rule("UTIL", "deductions", "utilities")]
        self.rule_module.load_rules = Mock(side_effect=lambda company: deepcopy(self.rule_rows))
        self.rule_module.resolve_rule = self.resolve
        self.rule_module.guard_rule_snapshot = Mock()
        module_patch = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
            "jifeng_erp.salary_history_classification": self.rule_module})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        spec = importlib.util.spec_from_file_location("salary_history_model", ROOT / "jifeng_erp/salary_history.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        for doctype, fields in [("Salary Slip", self.module.SLIP_FIELDS | {"earnings", "deductions"}),
            ("Salary Detail", self.module.DETAIL_FIELDS), ("Additional Salary", self.module.AS_FIELDS),
            ("Jifeng Wage Entry", self.module.WAGE_FIELDS), ("Company", {"name", "default_currency"}),
            ("Employee", {"name"}), ("Currency", {"name"}), ("Salary Component", {"name", "modified", "type"})]:
            self.metas[doctype] = Meta(fields)
        for doctype, definitions in {
            "Salary Slip": {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
                "currency": ("Link", "Currency"), "earnings": ("Table", "Salary Detail"),
                "deductions": ("Table", "Salary Detail"), "gross_pay": ("Currency", "currency"),
                "total_deduction": ("Currency", "currency"), "net_pay": ("Currency", "currency"),
                "rounded_total": ("Currency", "currency")},
            "Salary Detail": {"salary_component": ("Link", "Salary Component"), "additional_salary": ("Link", "Additional Salary"),
                "amount": ("Currency", "currency"), "default_amount": ("Currency", "currency"), "additional_amount": ("Currency", "currency")},
            "Additional Salary": {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
                "currency": ("Link", "Currency"), "salary_component": ("Link", "Salary Component"),
                "amount": ("Currency", "currency"), "ref_doctype": ("Link", "DocType"), "ref_docname": ("Dynamic Link", "ref_doctype")},
            "Jifeng Wage Entry": {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
                "salary_component": ("Link", "Salary Component"), "piece_amount": ("Currency", None),
                "time_amount": ("Currency", None), "extra_amount": ("Currency", None), "total_amount": ("Currency", None)},
        }.items():
            for field, (kind, options) in definitions.items():
                self.metas[doctype].get_field(field).update(fieldtype=kind, options=options)
        self.add("Company", "COMPANY", default_currency="CNY")
        self.add("Company", "OTHER", default_currency="USD")
        self.add("Employee", "EMP", company="OTHER", employee_name="Current changed name", bank_account_no="PRIVATE-BANK")
        self.add("Currency", "CNY")
        for name, kind in (("BASIC", "Earning"), ("WAGES", "Earning"), ("UTIL", "Deduction")):
            self.add("Salary Component", name, type=kind)
        self.add("Additional Salary", "AS", docstatus=1, company="COMPANY", employee="EMP", currency="CNY",
            salary_component="WAGES", payroll_date="2026-10-20", ref_doctype="Jifeng Wage Entry", ref_docname="WAGE",
            amount=120, is_recurring=0, overwrite_salary_structure_amount=0)
        self.add("Jifeng Wage Entry", "WAGE", docstatus=1, company="COMPANY", employee="EMP", salary_component="WAGES",
            payroll_date="2026-10-20", piece_amount=80, time_amount=30, extra_amount=10, total_amount=120)
        self.slip()
        self.context = dict(company="COMPANY", from_date="2026-10-01", to_date="2026-10-03",
            use_date=False, date_basis="period_overlap", search="")

    def rule(self, component, parentfield, bucket, mode="component", extra=""):
        return dict(name="RULE-" + component, modified=STAMP, company="COMPANY", docstatus=1,
            salary_component=component, parentfield=parentfield, bucket=bucket, classification_mode=mode,
            extra_bucket=extra, effective_from="2026-01-01")

    def resolve(self, rows, component, parentfield, as_of):
        matches = [row for row in rows if row["salary_component"] == component and row["parentfield"] == parentfield
            and row["effective_from"] <= as_of]
        return max(matches, key=lambda row: row["effective_from"]) if matches else None

    def add(self, doctype, name, **fields):
        record = Record(doctype=doctype, name=name, modified=STAMP, **fields)
        self.records[doctype, name] = record
        return record

    def detail(self, slip, name, component, parentfield, amount, default=None, additional="", idx=1):
        return self.add("Salary Detail", name, parent=slip, parenttype="Salary Slip", parentfield=parentfield,
            idx=idx, salary_component=component, amount=amount, default_amount=default,
            additional_amount=amount if additional else 0, additional_salary=additional,
            do_not_include_in_total=0, statistical_component=0, depends_on_payment_days=0)

    def slip(self, name="SLIP", **changes):
        earning = [self.detail(name, name + "-B", "BASIC", "earnings", 1000, 1200),
            self.detail(name, name + "-W", "WAGES", "earnings", 120, 0, "AS", 2)]
        deduction = [self.detail(name, name + "-D", "UTIL", "deductions", 50, 50)]
        return self.add("Salary Slip", name, docstatus=1, company="COMPANY", employee="EMP",
            employee_name="Period employee name", department="Period department", currency="CNY", exchange_rate=1,
            start_date="2026-10-01", end_date="2026-10-31", posting_date="2026-11-01", total_working_days=22,
            payment_days=22, gross_pay=1120, total_deduction=50, net_pay=1070, rounded_total=1070,
            earnings=earning, deductions=deduction, bank_account_no="PRIVATE-BANK", account_name="PRIVATE-NAME", **changes)

    def whitelist(self, **kwargs):
        def decorate(function):
            self.whitelisted.append((function.__name__, kwargs))
            return function
        return decorate

    def permission(self, doctype, permission, doc=None, **kwargs):
        return (doctype, permission) not in self.denials and (doctype, doc.get("name") if doc else None, permission) not in self.denials

    def columns(self, doctype, permission_type="read", parenttype=None, **kwargs):
        # Fixed Frappe get_permitted_fields contains value fields, not Tables.
        return {field.fieldname for field in self.metas[doctype].fields
                if field.fieldtype not in {"Table", "Table MultiSelect", "Section Break", "Column Break"}
                } - self.hidden_fields.get((doctype, parenttype), set())

    def get_doc(self, doctype, name, **kwargs):
        self.calls.append(("doc", doctype, name, kwargs))
        return self.Document(deepcopy(self.records[doctype, name]))

    def get_list(self, doctype, *, filters, fields, limit_page_length, limit_start=0, **kwargs):
        self.calls.append(("list", doctype, deepcopy(filters), limit_page_length, limit_start, kwargs))
        if self.list_hook:
            self.list_hook(doctype, filters, limit_start)
        records = [row for (kind, name), row in self.records.items() if kind == doctype and (kind, name) not in self.hidden
            and self.permission(kind, "read", row) and all(row.get(field) == value for field, value in filters.items())]
        if doctype == "Salary Detail":
            records.sort(key=lambda row: (row.idx, row.name))
        elif doctype == "Salary Slip":
            records.sort(key=lambda row: row.name)
            records.sort(key=lambda row: row.end_date, reverse=True)
        records = records[limit_start:limit_start + limit_page_length]
        return [Record({field: row.get(field) for field in fields}) for row in records]

    def get_all(self, doctype, **kwargs):
        return [Record(name="policy", state=self.policy)] if doctype == "User Permission" else []

    def row(self):
        return self.module.query_salary_history(self.context)["rows"][0]

    def test_mixed_wage_uses_exact_applied_sources_and_never_employee_current_fields(self):
        row = self.row()
        self.assertEqual(row["employee_name"], "Period employee name")
        self.assertEqual(row["department"], "Period department")
        self.assertEqual(row["buckets"]["piece"], "80")
        self.assertEqual(row["buckets"]["time"], "30")
        self.assertEqual(row["buckets"]["other_earning"], "10")
        self.assertEqual(row["basic_default_amount"], "1200")
        detail = self.module.get_salary_history_detail(self.context, "SLIP", STAMP)
        self.assertEqual({source["name"] for source in detail["sources"]}, {"AS", "WAGE"})
        self.assertNotIn("PRIVATE", json.dumps(detail))
        self.assertEqual(self.rule_module.guard_rule_snapshot.call_args.args[1], "COMPANY")
        self.assertFalse(any(call[1] == "Salary Structure Assignment" for call in self.calls))

    def test_paid_leave_and_net_pay_never_become_attendance_or_payment(self):
        row = self.row()
        self.assertEqual(row["payment_days"], "22")
        self.assertEqual(row["net_pay"], "1070")
        for key in self.module.UNKNOWN_FIELDS:
            self.assertIsNone(row[key])

    def test_one_missing_classification_hides_the_whole_earning_side(self):
        self.rule_rows = [row for row in self.rule_rows if row["salary_component"] != "BASIC"]
        row = self.row()
        self.assertTrue(all(row["buckets"][key] is None for key in self.rule_module.EARNING_BUCKETS))
        self.assertEqual(row["buckets"]["utilities"], "50")
        self.assertEqual(row["gross_pay"], "1120")

    def test_native_total_mismatch_hides_complete_side(self):
        self.records["Salary Slip", "SLIP"].gross_pay = 1121
        row = self.row()
        self.assertEqual(row["classification_state"]["earnings"], "unreconciled")
        self.assertIsNone(row["buckets"]["basic"])

    def test_mixed_source_cannot_be_classified_as_one_piece_bucket(self):
        self.rule_rows[1].update(classification_mode="component", bucket="piece")
        self.assertIsNone(self.row()["buckets"]["piece"])

    def test_mixed_sources_reject_proration_cancellation_currency_and_identity(self):
        for doctype, name, field, value in [("Additional Salary", "AS", "amount", 119),
            ("Additional Salary", "AS", "docstatus", 2), ("Additional Salary", "AS", "currency", "USD"),
            ("Jifeng Wage Entry", "WAGE", "company", "OTHER"), ("Jifeng Wage Entry", "WAGE", "docstatus", 0),
            ("Jifeng Wage Entry", "WAGE", "total_amount", 121), ("Additional Salary", "AS", "is_recurring", 1)]:
            with self.subTest(field=field, value=value):
                original = self.records[doctype, name][field]
                self.records[doctype, name][field] = value
                self.assertIsNone(self.row()["buckets"]["piece"])
                self.records[doctype, name][field] = original

    def test_hidden_applied_wage_does_not_expose_source_or_partial_earnings(self):
        self.hidden.add(("Jifeng Wage Entry", "WAGE"))
        row = self.row()
        self.assertIsNone(row["buckets"]["time"])
        detail = self.module.get_salary_history_detail(self.context, "SLIP", STAMP)
        self.assertEqual(detail["sources"], [])
        self.assertTrue(all(item["sources"] == [] for item in detail["earnings"]))
        self.assertNotIn('"name": "WAGE"', json.dumps(detail["earnings"]))

    def test_repeated_additional_salary_cannot_classify_twice_even_if_native_total_reconciles(self):
        duplicate = self.detail("SLIP", "SLIP-W2", "WAGES", "earnings", 120, 0, "AS", 3)
        slip = self.records["Salary Slip", "SLIP"]
        slip.earnings.append(duplicate)
        slip.gross_pay, slip.net_pay, slip.rounded_total = 1240, 1190, 1190
        row = self.row()
        self.assertEqual(row["gross_pay"], "1240")
        self.assertEqual(row["classification_state"]["earnings"], "unclassified")
        self.assertTrue(all(row["buckets"][key] is None for key in self.rule_module.EARNING_BUCKETS))
        detail = self.module.get_salary_history_detail(self.context, "SLIP", STAMP)
        self.assertEqual(detail["sources"], [])
        self.assertIsNone(detail["earnings"][1]["classification"])
        self.assertIsNone(detail["earnings"][2]["classification"])

    def test_distinct_additional_salaries_cannot_consume_one_wage_source_twice(self):
        second = deepcopy(self.records["Additional Salary", "AS"])
        second.name = "AS2"
        self.records["Additional Salary", "AS2"] = second
        duplicate = self.detail("SLIP", "SLIP-W2", "WAGES", "earnings", 120, 0, "AS2", 3)
        slip = self.records["Salary Slip", "SLIP"]
        slip.earnings.append(duplicate)
        slip.gross_pay, slip.net_pay, slip.rounded_total = 1240, 1190, 1190
        self.assertIsNone(self.row()["buckets"]["piece"])
        self.assertEqual(self.module.get_salary_history_detail(self.context, "SLIP", STAMP)["sources"], [])

    def test_native_saved_statistical_flag_is_not_an_extra_exclusion(self):
        # Fixed HRMS get_component_totals skips do_not_include_in_total only.
        self.records["Salary Detail", "SLIP-B"].statistical_component = 1
        self.records["Salary Slip", "SLIP"].earnings[0].statistical_component = 1
        self.assertEqual(self.row()["buckets"]["basic"], "1000")
        self.records["Salary Detail", "SLIP-B"].do_not_include_in_total = 1
        slip = self.records["Salary Slip", "SLIP"]
        slip.earnings[0].do_not_include_in_total = 1
        slip.gross_pay, slip.net_pay, slip.rounded_total = 120, 70, 70
        self.assertEqual(self.row()["buckets"]["basic"], "0")
        self.assertEqual(self.row()["classification_state"]["earnings"], "verified")

    def test_guest_read_is_denied_even_if_a_fixture_grants_document_permissions(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.row()

    def test_child_field_permission_or_mask_denies_entire_read(self):
        self.hidden_fields["Salary Detail", "Salary Slip"] = {"amount"}
        with self.assertRaises(PermissionError):
            self.row()
        self.hidden_fields.clear()
        self.metas["Salary Detail"].get_field("default_amount").mask = 1
        with self.assertRaises(PermissionError):
            self.row()

    def test_masked_parent_table_and_wrong_table_metadata_denied(self):
        field = self.metas["Salary Slip"].get_field("earnings")
        field.mask = 1
        with self.assertRaises(PermissionError):
            self.row()
        field.mask, field.options = 0, "Other private child"
        with self.assertRaises(PermissionError):
            self.row()

    def test_native_table_value_field_omission_does_not_remove_readable_children(self):
        self.assertNotIn("earnings", self.columns("Salary Slip"))
        self.assertNotIn("deductions", self.columns("Salary Slip"))
        self.assertEqual(self.row()["classification_state"], {"earnings": "verified", "deductions": "verified"})
        self.frappe.session.user = "Administrator"
        self.assertEqual(self.row()["buckets"]["piece"], "80")

    def test_table_parent_permlevel_and_mask_are_independent_of_value_fields(self):
        field = self.metas["Salary Slip"].get_field("earnings")
        field.permlevel = 1
        with self.assertRaises(PermissionError):
            self.row()
        self.metas["Salary Slip"].read_levels = [0, 1]
        self.assertEqual(self.row()["buckets"]["piece"], "80")
        field.mask = 1
        with self.assertRaises(PermissionError):
            self.row()
        self.metas["Salary Slip"].mask_levels = [1]
        self.assertEqual(self.row()["buckets"]["piece"], "80")
        # A parent share grants native read fallback, never a mask grant.
        field.permlevel = 0
        self.metas["Salary Slip"].read_levels, self.metas["Salary Slip"].mask_levels = [], []
        self.frappe.share.get_shared = lambda *args, **kwargs: ["SLIP"]
        with self.assertRaises(PermissionError):
            self.row()
        field.mask = 0
        self.assertEqual(self.row()["buckets"]["piece"], "80")

    def test_hidden_child_row_never_returns_a_partial_payroll(self):
        self.hidden.add(("Salary Detail", "SLIP-W"))
        with self.assertRaises(PermissionError):
            self.row()

    def test_company_and_employee_native_read_select_required(self):
        for doctype in ("Company", "Employee", "Salary Component"):
            with self.subTest(doctype=doctype):
                self.denials.add((doctype, "select"))
                with self.assertRaises(PermissionError):
                    self.row()
                self.denials.clear()

    def test_rule_scope_permission_failure_retains_totals_with_no_classified_amounts(self):
        self.rule_module.load_rules.side_effect = PermissionError("PRIVATE-RULE")
        row = self.row()
        self.assertTrue(all(value is None for value in row["buckets"].values()))
        self.assertEqual(row["gross_pay"], "1120")
        self.assertNotIn("PRIVATE-RULE", json.dumps(row))

    def test_only_submitted_slips_and_approved_period_snapshots(self):
        for status in (0, 2):
            self.records["Salary Slip", "SLIP"].docstatus = status
            self.assertEqual(self.module.query_salary_history(self.context)["rows"], [])

    def test_native_docstatus_int_subclass_is_accepted_without_accepting_bool(self):
        class DocStatus(int):
            pass

        for doctype, name in (("Salary Slip", "SLIP"), ("Additional Salary", "AS"), ("Jifeng Wage Entry", "WAGE")):
            self.records[doctype, name].docstatus = DocStatus(1)
        self.assertEqual(self.row()["classification_state"]["earnings"], "verified")
        self.records["Salary Slip", "SLIP"].docstatus = True
        with self.assertRaises(PermissionError):
            self.row()

    def test_explicit_date_bases_and_disabled_date_filter(self):
        self.context.update(use_date=True, date_basis="period_overlap")
        self.assertEqual(len(self.module.query_salary_history(self.context)["rows"]), 1)
        self.context["date_basis"] = "period_end"
        self.assertEqual(self.module.query_salary_history(self.context)["total"], 0)
        self.context["date_basis"] = "period_start"
        self.assertEqual(self.module.query_salary_history(self.context)["total"], 1)
        self.context.update(date_basis="posting_date", from_date="2026-11-01", to_date="2026-11-01")
        self.assertEqual(self.module.query_salary_history(self.context)["total"], 1)

    def test_search_uses_only_explicit_readable_fields(self):
        self.context["search"] = "period department"
        self.assertEqual(self.module.query_salary_history(self.context)["total"], 1)
        self.context["search"] = "PRIVATE-BANK"
        self.assertEqual(self.module.query_salary_history(self.context)["total"], 0)

    def test_strict_context_bool_page_and_get_declarations(self):
        for change in ({"use_date": 1}, {"date_basis": "bank"}, {"search": "x\n"}, {"bank": "private"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.module.query_salary_history({**self.context, **change})
        for page, size in ((True, 15), (1, True), ("01", 15), (1, 16), (0, 15)):
            with self.assertRaises(ValueError):
                self.module.query_salary_history(self.context, page, size)
        self.assertEqual(self.module.query_salary_history(json.dumps(self.context), "1", "15")["total"], 1)
        self.assertEqual(self.whitelisted, [("query_salary_history", {"methods": ["GET"]}),
            ("get_salary_history_detail", {"methods": ["GET"]})])

    def test_detail_requires_visible_filtered_source_and_exact_version(self):
        with self.assertRaises(ValueError):
            self.module.get_salary_history_detail(self.context, "SLIP", LATER)
        self.context["search"] = "not present"
        with self.assertRaises(PermissionError):
            self.module.get_salary_history_detail(self.context, "SLIP", STAMP)

    def test_persisted_policy_change_detected_even_with_native_permission_cache(self):
        def mutate(doctype, filters, offset):
            if doctype == "Salary Detail":
                self.policy = "changed"
        self.list_hook = mutate
        with self.assertRaisesRegex(ValueError, "权限已改变"):
            self.row()

    def test_native_sources_changed_between_document_and_query_are_rejected(self):
        for doctype, name, field, replacement in (
            ("Salary Slip", "SLIP", "modified", LATER),
            ("Salary Detail", "SLIP-B", "amount", 1001),
            ("Salary Component", "WAGES", "type", "Deduction"),
            ("Additional Salary", "AS", "amount", 121),
            ("Jifeng Wage Entry", "WAGE", "piece_amount", 81),
            ("Company", "COMPANY", "default_currency", "USD"),
        ):
            with self.subTest(doctype=doctype):
                original = self.records[doctype, name][field]
                changed = False

                def mutate(kind, filters, offset):
                    nonlocal changed
                    if (kind == doctype and not changed and
                            (filters.get("name") == name or doctype == "Salary Detail")):
                        changed = True
                        self.records[doctype, name][field] = replacement

                self.list_hook = mutate
                try:
                    with self.assertRaisesRegex((ValueError, PermissionError), "已改变|权限"):
                        self.row()
                finally:
                    self.records[doctype, name][field] = original
                    self.list_hook = None

    def test_missing_default_amount_does_not_fabricate_base_salary(self):
        self.records["Salary Detail", "SLIP-B"].default_amount = None
        self.records["Salary Slip", "SLIP"].earnings[0].default_amount = None
        row = self.row()
        self.assertEqual(row["buckets"]["basic"], "1000")
        self.assertIsNone(row["basic_default_amount"])

    def test_two_hundred_batch_scan_includes_later_pages_without_mutations(self):
        self.records = {key: row for key, row in self.records.items() if key[0] not in {"Salary Slip", "Salary Detail"}}
        for number in range(205):
            name = f"SLIP-{number:04}"
            slip = self.slip(name)
            del self.records["Salary Detail", name + "-W"]
            del self.records["Salary Detail", name + "-D"]
            slip.earnings, slip.deductions = slip.earnings[:1], []
            slip.gross_pay, slip.total_deduction, slip.net_pay, slip.rounded_total = 1000, 0, 1000, 1000
        result = self.module.query_salary_history(self.context, page=14)
        self.assertEqual(result["total"], 205)
        self.assertEqual(len(result["rows"]), 10)
        self.assertTrue(any(call[0] == "list" and call[1] == "Salary Slip" and call[4] == 200 for call in self.calls))
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
