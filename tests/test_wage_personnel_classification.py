"""Approval actors, protected payloads and live source authorization boundaries."""

import copy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1] / "jifeng_erp"


class Row(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


class Doc(types.SimpleNamespace):
    def get(self, field):
        return getattr(self, field, None)

    def check_permission(self, permission):
        self.checks.append(permission)
        if permission in self.denied:
            raise PermissionError("native denial")
        if self.on_check:
            self.on_check(permission)


class Meta:
    def __init__(self, fields):
        self.readable = set(fields)
        self.masked = set()
        self.mask_levels = set()

    def get_permitted_fieldnames(self, **kwargs):
        return self.readable

    def get_permlevel_access(self, **kwargs):
        return self.mask_levels

    def get_field(self, name):
        return Row(mask=name in self.masked, permlevel=0)


def doc(**kwargs):
    if "term_type" in kwargs:
        kwargs.setdefault("creation", "2026-09-01 08:00:00.000001")
    return Doc(checks=[], denied=set(), on_check=None, **kwargs)


class Database:
    def __init__(self, model, **settings):
        self.__dict__.update(settings)
        self.begin = Mock(side_effect=lambda **kwargs: model.event("begin", kwargs))
        self.rollback = Mock(side_effect=lambda: model.event("rollback"))
        self.close = Mock(side_effect=lambda: model.event("close"))


class TestWagePersonnelClassification(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Doc(user="HR-USER")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.whitelisted = []
        self.frappe.whitelist = lambda **kwargs: (self.whitelisted.append(kwargs) or (lambda fn: fn))
        self.frappe.has_permission = Mock(side_effect=lambda doctype, *args, **kwargs:
            doctype != "Jifeng Employee Supplement" or self.frappe.session.user != "HR-USER")
        self.frappe.get_roles = Mock(return_value=["System Manager"])
        self.metas = {"Jifeng Wage Entry": Meta({"company", "employee", "personnel_type_snapshot_status"}),
                      "Jifeng Employee Supplement": Meta({"company", "employee", "term_type"}),
                      "Employee": Meta({"company", "status", "employee_name"})}
        self.frappe.get_meta = Mock(side_effect=self.metas.__getitem__)
        self.wage = doc(doctype="Jifeng Wage Entry", name="WAGE-1", company="C-1", employee="E-1",
                        modified="2026-10-07 10:00:00.000001", docstatus=0, flags=Doc())
        self.person = doc(name="E-1", company="C-1", status="Active")
        self.company = doc(name="C-1")
        self.source = doc(name="PRIVATE-SUP", employee="E-1", company="C-1", term_type="临时",
                          modified="2026-10-07 09:00:00.000001", identity_number="PRIVATE-ID")
        self.sources = [self.source]
        self.deleted = []
        self.sql_calls = []
        self.after_list = None
        self.list_count = 0
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.db = Doc(sql=Mock(side_effect=self.sql))
        self.auth = {"DocPerm": [Row(name="original-source-read", parent="Jifeng Employee Supplement", role="HR Manager", read=1)],
                     "Property Setter": [Row(name="original-type-mask", doc_type="Jifeng Employee Supplement", field_name="term_type", property="mask", value="0")]}
        self.stale_auth, self.stale_sources, self.on_fresh = None, None, None
        self.events, self.failures = [], set()
        self.settings = {"socket": "/synthetic/wage.sock", "host": "synthetic-primary", "port": 13316,
                         "user": "synthetic-wage-reader", "password": "synthetic-only", "cur_db_name": "synthetic-db"}
        self.original = Database(self, **self.settings)
        self.frappe.local = Doc(db=self.original, site="synthetic.localhost", conf=Doc(
            db_type="mariadb", db_socket=self.settings["socket"], db_host=self.settings["host"],
            db_port=self.settings["port"], db_user=self.settings["user"], db_password=self.settings["password"], db_name=self.settings["cur_db_name"]))
        self.factory = Mock(side_effect=self.fresh_db)
        dbmodule = types.ModuleType("frappe.database")
        dbmodule.get_db = self.factory
        utils = types.ModuleType("frappe.utils")
        self.clock = datetime(2026, 10, 7, 10, 1)
        utils.now_datetime = lambda: self.clock
        context = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "frappe.database": dbmodule,
                                          "jifeng_erp.tool_archive": Doc(DOCTYPES={}),
                                          "jifeng_erp.plate_modeling": Doc(DOCTYPE="Jifeng Plate Modeling")})
        context.start()
        self.addCleanup(context.stop)
        tools_spec = importlib.util.spec_from_file_location("wage_classification_fresh_guard", ROOT / "tool_archive_export.py")
        self.tools = importlib.util.module_from_spec(tools_spec)
        tools_spec.loader.exec_module(self.tools)
        imports = patch.dict(sys.modules, {"jifeng_erp.tool_archive_export": self.tools})
        imports.start()
        self.addCleanup(imports.stop)
        self.whitelisted.clear()
        spec = importlib.util.spec_from_file_location("wage_classification_under_test", ROOT / "wage_personnel_classification.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def event(self, name, options=None):
        self.events.append((name, options))
        if name in self.failures:
            raise RuntimeError("synthetic " + name + " failure")

    def fresh_db(self, **settings):
        if self.on_fresh:
            self.on_fresh()
        return Database(self, **settings)

    def current_sources(self):
        return self.stale_sources if self.stale_sources is not None and self.frappe.local.db is self.original else self.sources

    def get_all(self, doctype, filters, fields, **kwargs):
        self.assertIn(doctype, {"User", "Has Role", "User Permission", "DocPerm", "Custom DocPerm", "DocType",
                               "DocField", "Custom Field", "Property Setter", "DocShare"}, "get_all must never read HR business data")
        if doctype in {"DocPerm", "Custom DocPerm", "DocField"}:
            self.assertEqual(set(filters["parent"][1]), set(self.module.SOURCES))
        auth = self.stale_auth if self.stale_auth is not None and self.frappe.local.db is self.original else self.auth
        if doctype == "User":
            return [Row(name=self.frappe.session.user, enabled=1, user_type="System User")]
        def matches(row):
            return all(row.get(key) in value[1] if isinstance(value, (list, tuple)) and value[0] == "in"
                       else row.get(key) == value for key, value in filters.items())
        return [Row(copy.deepcopy(row) if fields == ["*"] else {key: row.get(key) for key in fields})
                for row in auth.get(doctype, []) if matches(row)]

    def get_doc(self, doctype, name=None, **kwargs):
        if doctype == "Jifeng Wage Entry":
            self.assertEqual(name, self.wage.name)
            return self.wage
        if doctype == "Company":
            self.assertEqual(name, self.company.name)
            return self.company
        if doctype == "Employee":
            self.assertEqual(name, self.person.name)
            self.assertFalse(kwargs.get("for_update"), "must not add inverse Employee locks")
            return self.person
        if doctype == "Jifeng Employee Supplement":
            self.assertEqual(set(kwargs), {"for_update"})
            return next(row for row in self.current_sources() if row.name == name)
        raise AssertionError(doctype)

    def sql(self, query, args, **kwargs):
        self.sql_calls.append((query, args))
        if "tabJifeng Employee Supplement" in query:
            self.assertEqual(args, (self.wage.employee,))
            return [Row(name=row.name) for row in self.current_sources() if row.employee == self.wage.employee][:2]
        if "tabDeleted Document" in query:
            return self.deleted
        raise AssertionError(query)

    def get_list(self, doctype, **kwargs):
        self.assertEqual(doctype, "Jifeng Employee Supplement")
        self.list_count += 1
        source = next(row for row in self.current_sources() if row.name == kwargs["filters"]["name"])
        result = [Row({field: source.get(field) for field in kwargs["fields"]})]
        if self.after_list:
            self.after_list(self.list_count)
        return result

    def manager(self):
        self.frappe.session.user = "HR-MANAGER"

    def approve(self):
        self.manager()
        before = copy.deepcopy(self.wage)
        before.docstatus = 0
        self.wage.get_doc_before_save = lambda: before
        self.wage.docstatus = 1
        self.module.bind_classification(self.wage)
        approved = copy.deepcopy(self.wage)
        self.wage.get_doc_before_save = lambda: approved
        return approved

    def read(self):
        return self.module.read_classification(self.wage.name, self.wage.modified)

    def test_normal_hr_user_draft_does_not_query_private_source_but_manager_submission_classifies(self):
        self.module.bind_classification(self.wage)
        self.assertEqual(self.wage.personnel_type_snapshot_status, "无权核实")
        self.assertIsNone(self.wage.personnel_type_at_approval)
        self.frappe.db.sql.assert_not_called()
        self.frappe.get_list.assert_not_called()
        self.clock += timedelta(minutes=3)
        self.approve()
        self.assertEqual(self.wage.personnel_type_at_approval, "临时")
        self.assertEqual(self.wage.personnel_type_snapshot_status, "已核实")
        self.assertEqual(self.wage.personnel_type_snapshot_time, self.clock)
        self.assertIn("submit", self.wage.checks)
        self.assertTrue(all("FOR UPDATE" in query for query, _ in self.sql_calls))

    def test_metadata_keeps_all_raw_evidence_out_of_existing_hr_native_list_and_api_rights(self):
        metadata = json.loads((ROOT / "jifeng_erp" / "doctype" / "jifeng_wage_entry" / "jifeng_wage_entry.json").read_text(encoding="utf-8"))
        protected = set(self.module.SNAPSHOT_FIELDS) - {"personnel_type_snapshot_status"}
        fields = {field["fieldname"]: field for field in metadata["fields"]}
        for name in protected:
            self.assertEqual(fields[name]["permlevel"], 1)
            self.assertTrue(fields[name]["read_only"])
            self.assertTrue(fields[name]["no_copy"])
            self.assertFalse(fields[name].get("allow_on_submit"))
        level_one_readers = {row["role"] for row in metadata["permissions"]
                             if row.get("permlevel") == 1 and row.get("read")}
        self.assertEqual(level_one_readers, {"System Manager"})
        self.assertFalse(any(row.get("submit") for row in metadata["permissions"] if row["role"] == "HR User"))

    def test_draft_type_can_change_then_first_submit_freezes_actual_approval_type(self):
        self.manager()
        self.module.bind_classification(self.wage)
        self.source.term_type = "长期"
        self.source.modified = "2026-10-07 09:01:00.000001"
        approved = self.approve()
        self.assertEqual(approved.personnel_type_at_approval, "长期")
        self.source.term_type = "临时"
        self.frappe.db.sql.reset_mock()
        self.module.bind_classification(self.wage)
        self.assertEqual(self.wage.personnel_type_at_approval, "长期")
        self.frappe.db.sql.assert_not_called()

    def test_draft_get_refuses_stale_saved_observation_after_same_archive_changes(self):
        self.manager()
        self.module.bind_classification(self.wage)
        self.assertEqual(self.read()["classification_basis"], "current_draft")
        self.source.term_type = "长期"
        self.source.modified = "2026-10-07 11:00:00.000001"
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.read()
        self.module.bind_classification(self.wage)
        self.assertEqual(self.read()["personnel_type"], "长期")

    def test_authorized_business_projection_preserves_old_type_after_current_source_change(self):
        approved = self.approve()
        self.source.term_type = "长期"
        self.source.modified = "2026-10-07 11:00:00.000001"
        self.person.status = "Left"
        result = self.read()
        self.assertEqual(result["personnel_type"], "临时")
        self.assertEqual(result["recorded_at"], approved.personnel_type_snapshot_time.isoformat(timespec="microseconds"))
        self.assertEqual(result["classification_basis"], "first_native_submission")
        self.assertNotIn("PRIVATE-SUP", str(result))
        self.assertNotIn("PRIVATE-ID", str(result))
        self.assertFalse(any("FOR UPDATE" in query for query, _ in self.sql_calls[-2:]))

    def test_plain_hr_user_cannot_project_the_raw_type_from_a_readable_wage(self):
        self.approve()
        self.frappe.session.user = "HR-USER"
        self.frappe.db.sql.reset_mock()
        with self.assertRaises(PermissionError):
            self.read()
        self.frappe.db.sql.assert_not_called()

    def test_missing_or_empty_classification_keeps_normal_wage_approval_available(self):
        for sources in ([], [doc(name="S", company="C-1", employee="E-1", term_type="", modified="2026-10-07 09:00:00")]):
            with self.subTest(sources=bool(sources)):
                self.sources = sources
                self.wage.get_doc_before_save = lambda: None
                self.wage.docstatus = 0
                self.approve()
                self.assertEqual(self.wage.personnel_type_snapshot_status, "档案未记录")
                self.assertIsNone(self.wage.personnel_type_at_approval)

    def test_source_masks_and_hidden_fields_do_not_get_certified_on_approval(self):
        self.manager()
        meta = self.metas["Jifeng Employee Supplement"]
        for field in ("company", "employee", "term_type"):
            with self.subTest(field=field):
                meta.masked = {field}
                self.module.bind_classification(self.wage)
                self.assertEqual(self.wage.personnel_type_snapshot_status, "无权核实")
                self.assertIsNone(self.wage.personnel_type_at_approval)
                self.frappe.db.sql.assert_not_called()
        meta.masked = set()
        meta.readable.remove("term_type")
        self.module.bind_classification(self.wage)
        self.frappe.db.sql.assert_not_called()

    def test_individual_source_read_denial_does_not_expose_its_name_or_type(self):
        self.manager()
        self.source.denied = {"read"}
        self.module.bind_classification(self.wage)
        self.assertEqual(self.wage.personnel_type_snapshot_status, "无权核实")
        self.assertIsNone(self.wage.personnel_type_snapshot_source)
        self.frappe.get_list.assert_not_called()

    def test_ambiguous_wrong_company_or_unknown_source_type_is_not_guessed(self):
        self.manager()
        for sources in ([self.source, doc(name="OTHER", employee="E-1", company="C-1", term_type="长期", modified=self.source.modified)],
                        [doc(name="OTHER", employee="E-1", company="C-2", term_type="临时", modified=self.source.modified)],
                        [doc(name="OTHER", employee="E-1", company="C-1", term_type="自定义未知", modified=self.source.modified)]):
            with self.subTest(names=len(sources)):
                self.sources = sources
                with self.assertRaisesRegex(ValueError, "来源已变化"):
                    self.module.bind_classification(self.wage)

    def test_source_changed_during_observation_rejects_the_whole_approval(self):
        self.manager()
        self.after_list = lambda count: setattr(self.source, "term_type", "长期") if count == 1 else None
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.bind_classification(self.wage)
        self.assertIsNone(getattr(self.wage, "personnel_type_at_approval", None))

    def test_legacy_approved_wage_stays_unrecorded_without_current_archive_lookup(self):
        self.wage.docstatus = 1
        old = copy.deepcopy(self.wage)
        self.wage.get_doc_before_save = lambda: old
        self.module.bind_classification(self.wage)
        self.manager()
        result = self.read()
        self.assertEqual(result["status"], "历史未记录")
        self.assertIsNone(result["personnel_type"])
        self.assertIsNone(result["recorded_at"])
        self.frappe.db.sql.assert_not_called()
        self.assertIsNone(getattr(self.wage, "personnel_type_at_approval", None))

    def test_missing_hidden_fields_in_native_manager_cancel_payload_preserve_approved_values(self):
        old = self.approve()
        self.wage.docstatus = 2
        for field in self.module.SNAPSHOT_FIELDS:
            if field != "personnel_type_snapshot_status":
                setattr(self.wage, field, None)
        self.module.guard_approved_classification(self.wage)
        for field in self.module.SNAPSHOT_FIELDS:
            self.assertEqual(getattr(self.wage, field), getattr(old, field))

    def test_unreadable_hidden_fields_cannot_be_overwritten_or_used_as_guessing_oracle(self):
        old = self.approve()
        for candidate in (None, "临时", "长期", "forged"):
            self.wage.personnel_type_at_approval = candidate
            self.module.guard_approved_classification(self.wage)
            self.assertEqual(self.wage.personnel_type_at_approval, old.personnel_type_at_approval)

    def test_privileged_snapshot_tampering_and_company_employee_changes_are_rejected(self):
        self.approve()
        self.frappe.session.user = "Administrator"
        for field in ("company", "employee", *self.module.SNAPSHOT_FIELDS):
            original = getattr(self.wage, field)
            setattr(self.wage, field, "forged")
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.guard_approved_classification(self.wage)
            setattr(self.wage, field, original)

    def test_forged_new_snapshot_is_replaced_by_authorized_source(self):
        self.manager()
        for field in self.module.SNAPSHOT_FIELDS:
            setattr(self.wage, field, "forged")
        self.module.bind_classification(self.wage)
        self.assertEqual(self.wage.personnel_type_at_approval, "临时")
        self.assertNotEqual(self.wage.personnel_type_snapshot_source, "forged")

    def test_source_deletion_or_replacement_refuses_projection_but_keeps_original_audit(self):
        old = self.approve()
        for sources in ([], [doc(name="REPLACEMENT", employee="E-1", company="C-1", term_type="临时", modified=self.source.modified)]):
            self.sources = sources
            with self.subTest(sources=bool(sources)), self.assertRaises(PermissionError):
                self.read()
            self.assertEqual(self.wage.personnel_type_snapshot_source, old.personnel_type_snapshot_source)
            self.assertEqual(self.wage.personnel_type_at_approval, "临时")

    def test_native_same_name_recreated_supplement_is_not_the_original_historical_source(self):
        old = self.approve()
        self.sources = [doc(name=self.source.name, creation="2026-10-07 12:00:00.000001",
                            employee="E-1", company="C-1", term_type="临时", modified=self.source.modified)]
        with self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(self.wage.personnel_type_snapshot_source, old.personnel_type_snapshot_source)

    def test_projection_does_not_invent_a_type_if_authorized_archive_was_empty_at_approval(self):
        self.source.term_type = ""
        self.approve()
        self.source.term_type = "临时"
        self.source.modified = "2026-10-07 11:00:00.000001"
        result = self.read()
        self.assertEqual(result["status"], "档案未记录")
        self.assertIsNone(result["personnel_type"])

    def test_get_enforces_company_employee_wage_scope_and_both_wage_and_source_field_masks(self):
        self.approve()
        for target in (self.wage, self.person, self.company, self.source):
            target.denied = {"select" if target is self.company else "read"}
            with self.subTest(target=target.name), self.assertRaises(PermissionError):
                self.read()
            target.denied = set()
        for doctype, field in (("Jifeng Wage Entry", "employee"), ("Jifeng Wage Entry", "personnel_type_at_approval"),
                                ("Employee", "company"), ("Jifeng Employee Supplement", "term_type")):
            self.metas[doctype].masked = {field}
            with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                self.read()
            self.metas[doctype].masked = set()

    def test_late_permission_revocation_or_source_change_never_returns_partial_projection(self):
        self.approve()
        self.list_count = 0
        self.after_list = lambda count: self.source.denied.add("read") if count == 1 else None
        with self.assertRaises(PermissionError):
            self.read()
        self.source.denied = set()
        self.list_count = 0
        self.after_list = lambda count: setattr(self.source, "term_type", "长期") if count == 1 else None
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.read()

    def test_permission_or_field_change_between_the_two_approval_reads_cannot_certify_a_stale_type(self):
        self.manager()
        self.after_list = lambda count: self.metas["Jifeng Employee Supplement"].masked.add("term_type") if count == 1 else None
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.module.bind_classification(self.wage)
        self.assertIsNone(getattr(self.wage, "personnel_type_at_approval", None))

    def test_read_requires_exact_version_and_is_get_only_without_mutations(self):
        self.approve()
        for version in (None, "2026-10-07", "2026-10-07 10:00:00.000002"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.module.read_classification(self.wage.name, version)
        self.assertEqual(self.whitelisted, [{"methods": ["GET"]}])
        self.frappe.session.user = "Guest"
        self.frappe.get_doc.reset_mock()
        with self.assertRaises(PermissionError):
            self.read()
        self.frappe.get_doc.assert_not_called()

    def test_fresh_primary_permission_commit_is_rejected_even_when_native_role_cache_stays_allowed(self):
        self.approve()
        self.stale_auth = copy.deepcopy(self.auth)
        self.on_fresh = lambda: self.auth["DocPerm"][0].update(read=0)
        with self.assertRaises(PermissionError):
            self.read()
        self.assertTrue(self.frappe.has_permission("Jifeng Employee Supplement", "read"), "modeled native role cache deliberately remains stale")
        self.assertIs(self.frappe.local.db, self.original)
        self.assertEqual(self.events[-2:], [("rollback", None), ("close", None)])

    def test_fresh_raw_role_removal_is_rejected_while_modeled_native_role_cache_is_unchanged(self):
        self.approve()
        self.auth["Has Role"] = [Row(parenttype="User", parent="HR-MANAGER", role="HR Manager")]
        self.stale_auth = copy.deepcopy(self.auth)
        self.on_fresh = lambda: self.auth["Has Role"].clear()
        with self.assertRaises(PermissionError):
            self.read()
        self.assertTrue(self.frappe.has_permission("Jifeng Employee Supplement", "read"))
        self.assertIs(self.frappe.local.db, self.original)

    def test_fresh_raw_field_mask_commit_is_rejected_while_native_meta_cache_is_unchanged(self):
        self.approve()
        self.stale_auth = copy.deepcopy(self.auth)
        self.on_fresh = lambda: self.auth["Property Setter"][0].update(value="1")
        with self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(self.metas["Jifeng Employee Supplement"].masked, set())
        self.assertIs(self.frappe.local.db, self.original)

    def test_fresh_primary_source_commit_is_rejected_despite_original_repeatable_read_view(self):
        self.approve()
        self.stale_sources = copy.deepcopy(self.sources)
        self.on_fresh = lambda: setattr(self.source, "term_type", "长期")
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.read()
        self.assertIs(self.frappe.local.db, self.original)
        self.assertEqual(self.events[-2:], [("rollback", None), ("close", None)])

    def test_fresh_connection_is_read_only_and_restored_before_return(self):
        self.approve()
        result = self.read()
        self.assertEqual(result["personnel_type"], "临时")
        self.assertEqual(self.events, [("begin", {"read_only": True}), ("rollback", None), ("close", None)])
        self.assertIs(self.frappe.local.db, self.original)
        self.factory.assert_called_once_with(**self.settings)

    def test_fresh_connection_cleanup_failure_refuses_result_and_always_restores_original(self):
        self.approve()
        for failure in ("begin", "rollback", "close"):
            self.failures = {failure}
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, failure):
                self.read()
            self.assertIs(self.frappe.local.db, self.original)
            self.assertEqual(self.events[-2:], [("rollback", None), ("close", None)])

    def test_exact_deleted_cancelled_wage_restores_old_snapshot_and_modified_restore_is_refused(self):
        old = self.approve()
        self.wage.docstatus = 2
        self.wage.get_doc_before_save = lambda: None
        self.wage.flags = Doc(from_restore=True)
        self.frappe.session.user = "Administrator"
        original = {key: value for key, value in vars(self.wage).items()
                    if key not in {"flags", "checks", "denied", "on_check", "get_doc_before_save"}}
        original["personnel_type_snapshot_time"] = self.module._timestamp(original["personnel_type_snapshot_time"])
        self.deleted = [Row(data=json.dumps(original))]
        self.module.authorize_classification_restore(self.wage)
        self.source.term_type = "长期"
        # The real insert's higher-permission validation can clear these
        # before controller validate; recover only the exact checked evidence.
        for field in self.module.SNAPSHOT_FIELDS:
            if field != "personnel_type_snapshot_status":
                setattr(self.wage, field, None)
        self.module.bind_classification(self.wage)
        self.assertEqual(self.wage.personnel_type_at_approval, old.personnel_type_at_approval)
        self.wage.flags = Doc(from_restore=True)
        self.wage.personnel_type_at_approval = "长期"
        with self.assertRaisesRegex(ValueError, "准确原删除"):
            self.module.authorize_classification_restore(self.wage)

    def test_exact_native_restore_of_legacy_approved_or_cancelled_wage_does_not_infer_any_current_type(self):
        self.frappe.session.user = "Administrator"
        for status in (1, 2):
            with self.subTest(status=status):
                self.wage.docstatus = status
                self.wage.flags = Doc(from_restore=True)
                for field in self.module.SNAPSHOT_FIELDS:
                    setattr(self.wage, field, None)
                original = {"doctype": self.wage.doctype, "name": self.wage.name, "company": self.wage.company,
                            "employee": self.wage.employee, "docstatus": status}
                self.deleted = [Row(data=json.dumps(original))]
                self.module.authorize_classification_restore(self.wage)
                self.frappe.get_list.reset_mock()
                self.module.bind_classification(self.wage)
                for field in self.module.SNAPSHOT_FIELDS:
                    self.assertIsNone(getattr(self.wage, field))
                self.frappe.get_list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
