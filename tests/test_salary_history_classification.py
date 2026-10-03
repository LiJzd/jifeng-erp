"""Company version binding, hidden-history guards and explicit publication."""

from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_salary_history_rule/jifeng_salary_history_rule.json").read_text(encoding="utf-8"))
STAMP = "2026-10-03 10:00:00.000001"
DOCTYPE = "Jifeng Salary History Rule"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    environment = None

    def check_permission(self, permission):
        if not self.environment.access(self.doctype, permission, doc=self):
            raise PermissionError("native refusal")

    def is_new(self):
        return bool(self.get("__islocal"))

    def get_doc_before_save(self):
        return self.environment.get_doc(self.doctype, self.name) if self.name in self.environment.rules else None

    def reload(self):
        flags = self.flags
        self.update(deepcopy(self.environment.rules[self.name]))
        self.flags = flags

    def submit(self):
        env = self.environment
        self.docstatus = 1
        env.module.validate_rule(self)
        env.module.before_submit_rule(self)
        self.modified = "2026-10-03 10:00:00.000002"
        env.rules[self.name] = deepcopy(dict(self))
        env.submissions.append(self.name)


class TestSalaryHistoryClassification(unittest.TestCase):
    def setUp(self):
        self.rules, self.slips, self.submissions = {}, [], []
        self.hidden_rules, self.role_denials, self.document_denials, self.field_denials, self.masked = set(), set(), set(), set(), set()
        self.components = {"PAY": "Earning", "BASIC-ID": "Earning", "TAX-ID": "Deduction", "EMPLOYER-ID": "Employer Contribution"}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="hr-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.has_permission = Mock(side_effect=self.access)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("no unscoped data projection"))
        self.frappe.get_meta = Mock(side_effect=self.meta)
        self.frappe.db = Record(sql=Mock(side_effect=self.sql), get_value=Mock(side_effect=self.get_value))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, permission_type, **kwargs: self.fields(doctype) - {
            field for dt, permission, field in self.field_denials if dt == doctype and permission == permission_type
        }
        utils = types.ModuleType("frappe.utils")
        utils.today = lambda: "2026-10-03"
        utils.now_datetime = lambda: datetime(2026, 10, 3, 10, 0, 0, 2)
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("classification_under_test", ROOT / "jifeng_erp/salary_history_classification.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self

    def fields(self, doctype):
        if doctype == DOCTYPE:
            return {field["fieldname"] for field in SCHEMA["fields"]} | {"name", "modified", "docstatus"}
        return {"name", "type"} if doctype == "Salary Component" else {"name"}

    def access(self, doctype, permission, doc=None, **kwargs):
        return (doctype, permission) not in self.role_denials and (
            doc is None or (doctype, doc.name, permission) not in self.document_denials)

    def meta(self, doctype):
        return Record(get_permlevel_access=lambda **kwargs: [], get_field=lambda field: Record(
            permlevel=0, mask=(doctype, field) in self.masked))

    def get_doc(self, doctype, name):
        if doctype == "Company":
            return Document(doctype=doctype, name=name)
        if doctype == "Salary Component":
            return Document(doctype=doctype, name=name, type=self.components[name])
        return Document(deepcopy(self.rules[name]))

    def get_list(self, doctype, filters, fields, limit_start, limit_page_length, **kwargs):
        self.assertEqual(doctype, DOCTYPE)
        rows = [row for row in self.rules.values() if row["name"] not in self.hidden_rules and
                all(row.get(key) == value for key, value in filters.items())]
        rows.sort(key=lambda row: (row["effective_from"], row["name"]))
        return [Record({field: row[field] for field in fields}) for row in rows[limit_start:limit_start + limit_page_length]]

    def get_value(self, doctype, name, fields, **kwargs):
        if doctype == "Company":
            self.assertTrue(kwargs["for_update"])
            return name
        row = self.rules.get(name)
        if row is None:
            return None
        return Record({field: row[field] for field in fields}) if isinstance(fields, list) else row[fields]

    def sql(self, query, parameters):
        self.assertTrue(query.startswith("SELECT EXISTS("), "internal guard returns boolean only")
        if "`tabSalary Slip`" in query:
            result = False
            for slip in self.slips:
                if not (slip["docstatus"] == 1 and slip["company"] == parameters["company"] and
                        slip["component"] == parameters["component"] and slip["parentfield"] == parameters["parentfield"] and
                        slip["end_date"] >= parameters["effective"]):
                    continue
                newer = "AND NOT EXISTS" in query and any(
                    rule["docstatus"] == 1 and rule["company"] == slip["company"] and
                    rule["salary_component"] == slip["component"] and rule["parentfield"] == slip["parentfield"] and
                    parameters["effective"] < rule["effective_from"] <= slip["end_date"] for rule in self.rules.values())
                result |= not newer
            return [(int(result),)]
        if "key" in parameters:
            result = any(rule["binding_key"] == parameters["key"] and rule["name"] != parameters["name"] for rule in self.rules.values())
        elif "component" in parameters:
            result = any(rule["company"] == parameters["company"] and rule["salary_component"] == parameters["component"] and
                         rule["docstatus"] in (1, 2) and rule["name"] != parameters["name"] and
                         ("effective" not in parameters or rule["effective_from"] >= parameters["effective"]) for rule in self.rules.values())
        else:
            result = any(rule["company"] == parameters["company"] and rule["docstatus"] == 1 and
                         rule["name"] not in parameters.get("names", ()) for rule in self.rules.values())
        return [(int(result),)]

    def rule(self, name="R1", *, effective="2026-01-01", status=1, component="PAY", bucket="piece", mode="component", extra="", company="C1"):
        row = Document(doctype=DOCTYPE, name=name, modified=STAMP, docstatus=status, company=company,
                       salary_component=component, parentfield="deductions" if component == "TAX-ID" else "earnings",
                       effective_from=effective, bucket=bucket, classification_mode=mode, extra_bucket=extra,
                       publication_reason="", published_by="hr-manager@example.invalid", published_at=STAMP,
                       historical_publication=0, flags=Record())
        row.binding_key = self.module.binding_key(company, component, effective)
        self.rules[name] = deepcopy(dict(row))
        return row

    def salary(self, end="2026-09-30", component="PAY", parentfield="earnings", company="C1"):
        self.slips.append(dict(docstatus=1, company=company, component=component, parentfield=parentfield, end_date=end))

    def test_precise_temporal_binding_uses_saved_period_end_and_never_names(self):
        self.rule(effective="2026-01-01")
        self.rule("R2", effective="2026-10-04", bucket="time")
        self.rule("D1", component="TAX-ID", bucket="social_insurance")
        rows = self.module.load_rules("C1")
        self.assertEqual(self.module.resolve_rule(rows, "PAY", "earnings", "2026-10-03")["bucket"], "piece")
        self.assertEqual(self.module.resolve_rule(rows, "PAY", "earnings", "2026-10-04")["bucket"], "time")
        self.assertIsNone(self.module.resolve_rule(rows, "PAY", "deductions", "2026-10-04"))
        self.assertIsNone(self.module.resolve_rule(rows, "基本工资", "earnings", "2026-10-04"))
        self.assertIsNone(self.module.resolve_rule(rows, "PAY", "earnings", "2025-12-31"))

    def test_rule_visibility_must_be_complete_and_omission_is_generic(self):
        self.rule()
        self.rule("PRIVATE-NEW-RULE", effective="2026-10-04", bucket="time")
        self.hidden_rules.add("PRIVATE-NEW-RULE")
        with self.assertRaises(PermissionError) as caught:
            self.module.load_rules("C1")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn("2", str(caught.exception))
        self.frappe.get_all.assert_not_called()

    def test_every_company_component_and_rule_requires_read_select_and_unmasked_fields(self):
        self.rule()
        for doctype, name in (("Company", "C1"), ("Salary Component", "PAY"), (DOCTYPE, "R1")):
            for permission in ("read", "select"):
                with self.subTest(doctype=doctype, permission=permission):
                    self.document_denials.add((doctype, name, permission))
                    with self.assertRaises(PermissionError):
                        self.module.load_rules("C1")
                    self.document_denials.clear()
        for doctype, field in (("Company", "name"), ("Salary Component", "type"), (DOCTYPE, "bucket")):
            self.masked.add((doctype, field))
            with self.assertRaises(PermissionError):
                self.module.load_rules("C1")
            self.masked.clear()
        self.field_denials.add((DOCTYPE, "read", "extra_bucket"))
        with self.assertRaises(PermissionError):
            self.module.load_rules("C1")

    def test_snapshot_rechecks_changes_permissions_and_new_rules_even_when_initially_empty(self):
        self.assertEqual(self.module.load_rules("C1"), [])
        self.rule()
        with self.assertRaises(PermissionError):
            self.module.guard_rule_snapshot([], "C1")
        rows = self.module.load_rules("C1")
        self.rules["R1"]["bucket"] = "time"
        with self.assertRaisesRegex(ValueError, "改变"):
            self.module.guard_rule_snapshot(rows, "C1")
        rows = self.module.load_rules("C1")
        self.document_denials.add(("Company", "C1", "select"))
        with self.assertRaises(PermissionError):
            self.module.guard_rule_snapshot(rows, "C1")

    def test_duplicate_dates_and_cross_company_cannot_be_resolved(self):
        rows = [self.rule(), self.rule("R2")]
        with self.assertRaisesRegex(ValueError, "重复"):
            self.module.resolve_rule(rows, "PAY", "earnings", "2026-10-03")
        rows[1].company = "C2"
        with self.assertRaisesRegex(ValueError, "公司"):
            self.module.resolve_rule(rows, "PAY", "earnings", "2026-10-03")

    def test_binding_excludes_parentfield_and_client_key_is_replaced(self):
        self.assertEqual(self.module.binding_key("C1", "PAY", "2026-01-01"), self.rule().binding_key)
        draft = self.rule("DRAFT", status=0, effective="2026-10-04")
        draft.binding_key = "forged"
        self.module.validate_rule(draft)
        self.assertEqual(draft.binding_key, self.module.binding_key("C1", "PAY", "2026-10-04"))
        duplicate = self.rule("SAME-DATE", status=0)
        duplicate.parentfield, duplicate.bucket, duplicate.salary_component = "earnings", "time", "PAY"
        with self.assertRaisesRegex(ValueError, "相同生效日期"):
            self.module.before_submit_rule(duplicate)

    def test_mixed_wage_requires_explicit_earning_extra_bucket_and_matching_type(self):
        draft = self.rule(status=0, mode="wage_breakdown", bucket="other_earning")
        with self.assertRaisesRegex(ValueError, "附加费"):
            self.module.validate_rule(draft)
        draft.extra_bucket = "overtime"
        self.module.validate_rule(draft)
        draft.extra_bucket = "fine"
        with self.assertRaises(ValueError):
            self.module.validate_rule(draft)
        draft.extra_bucket, draft.bucket = "other_earning", "piece"
        with self.assertRaises(ValueError):
            self.module.validate_rule(draft)
        draft = self.rule("TAX", status=0, component="TAX-ID", bucket="loan")
        self.module.validate_rule(draft)
        draft.salary_component = "EMPLOYER-ID"
        with self.assertRaisesRegex(ValueError, "类型"):
            self.module.validate_rule(draft)

    def test_initial_history_requires_explicit_publish_and_json_flags_cannot_forge_it(self):
        draft = self.rule(status=0)
        self.salary()
        draft.flags.salary_history_historical_publish = True
        draft.flags.salary_history_publish_token = True
        with self.assertRaisesRegex(ValueError, "主管明确发布"):
            self.module.before_submit_rule(draft)
        self.module.publish_historical(draft, "核对已保存工资项目与期间记录")
        saved = self.rules["R1"]
        self.assertEqual(saved["publication_reason"], "核对已保存工资项目与期间记录")
        self.assertEqual(saved["historical_publication"], 1)
        self.assertEqual(self.submissions, ["R1"])
        self.assertTrue(SCHEMA["track_changes"])

    def test_explicit_publish_rejects_stale_draft_and_uses_persisted_config(self):
        draft = self.rule(status=0)
        self.salary()
        draft.modified = "2026-10-03 09:00:00"
        with self.assertRaisesRegex(ValueError, "草稿已改变"):
            self.module.publish_historical(draft, "已核对")
        draft.modified, draft.bucket = STAMP, "time"
        self.module.publish_historical(draft, "已核对")
        self.assertEqual(self.rules["R1"]["bucket"], "piece", "unsaved request values are not published")

    def test_explicit_publish_matches_native_rule_then_company_lock_order(self):
        draft = self.rule(status=0)
        self.salary()
        self.module.publish_historical(draft, "已核对")
        locked = [call.args[0] for call in self.frappe.db.get_value.call_args_list
                  if call.kwargs.get("for_update")]
        self.assertEqual(locked[:2], [DOCTYPE, "Company"])

    def test_later_rule_must_be_append_future_and_cannot_cover_hidden_submitted_history(self):
        self.rule()
        for effective, error in (("2026-10-03", "未来"), ("2025-01-01", "未来")):
            draft = self.rule("DRAFT", status=0, effective=effective)
            with self.assertRaisesRegex(ValueError, error):
                self.module.before_submit_rule(draft)
        self.rule("FUTURE", effective="2026-12-01")
        draft = self.rule("DRAFT", status=0, effective="2026-11-01")
        with self.assertRaisesRegex(ValueError, "追加"):
            self.module.before_submit_rule(draft)
        draft = self.rule("DRAFT", status=0, effective="2027-01-01")
        self.salary(end="2027-01-31")
        with self.assertRaisesRegex(ValueError, "已提交工资期间"):
            self.module.before_submit_rule(draft)
        self.slips.clear()
        self.module.before_submit_rule(draft)
        self.assertEqual(draft.historical_publication, 0)

    def test_submitted_and_cancelled_rules_cannot_update_or_delete(self):
        for status in (1, 2):
            rule = self.rule(status=status)
            rule.bucket = "time"
            with self.assertRaisesRegex(ValueError, "不能修改"):
                self.module.validate_rule(rule)
            rule.docstatus = 0  # Delete payload status cannot override persistence.
            with self.assertRaisesRegex(ValueError, "不能删除"):
                self.module.prevent_rule_delete(rule)
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.prevent_rule_update(rule)
        with self.assertRaisesRegex(ValueError, "不能重命名"):
            self.module.prevent_rule_rename(rule, "R1", "REPLACED", True)

    def test_cancel_guard_uses_effective_interval_and_checks_payload_without_leaking_sources(self):
        original = self.rule()
        self.salary()
        with self.assertRaisesRegex(ValueError, "已提交工资期间"):
            self.module.before_cancel_rule(original)
        original.bucket = "time"
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.module.before_cancel_rule(original)
        original = self.get_doc(DOCTYPE, "R1")
        self.rule("R2", effective="2026-08-01", bucket="time")
        self.module.before_cancel_rule(original)  # R2, not R1, applies at this end date.
        self.salary(end="2026-07-31")
        with self.assertRaisesRegex(ValueError, "已提交工资期间"):
            self.module.before_cancel_rule(original)

    def test_schema_has_only_authorized_roles_and_no_submitted_writable_fields(self):
        rows = {row["role"]: row for row in SCHEMA["permissions"]}
        self.assertEqual(set(rows), {"HR User", "HR Manager", "System Manager"})
        self.assertEqual(rows["HR User"], {"role": "HR User", "read": 1, "select": 1})
        for role in ("HR Manager", "System Manager"):
            self.assertEqual(set(rows[role]) - {"role"}, {"read", "select", "create", "write", "submit"})
        self.assertTrue(next(field for field in SCHEMA["fields"] if field["fieldname"] == "binding_key")["unique"])
        self.assertFalse(any(field.get("allow_on_submit") for field in SCHEMA["fields"]))


if __name__ == "__main__":
    unittest.main()
