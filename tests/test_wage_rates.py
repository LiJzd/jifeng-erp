"""Historical rate authority, complete pagination and immutable reference models."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
STAMP = "2026-10-03T12:01:02.123456"
LATER = "2026-10-03T12:01:03.123456"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Meta:
    def __init__(self, fields):
        self.fields = [
            Record(
                fieldname=name,
                fieldtype="Data",
                options="",
                read_only=0,
                permlevel=0,
                mask=0,
            )
            for name in fields
        ]
        self.read_levels = [0]
        self.mask_levels = []

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return (
            self.mask_levels
            if kwargs["permission_type"] == "mask"
            else self.read_levels
        )


class TestWageRates(unittest.TestCase):
    def setUp(self):
        self.records, self.metas, self.hidden_fields = {}, {}, {}
        self.denials, self.list_hidden = set(), set()
        self.calls, self.messages, self.locks = [], [], []
        self.list_hook, self.doc_hook = None, None
        self.whitelisted = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = KeyError
        self.frappe.session = Record(user="hr-user@example.invalid")
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(
            error(message)
        )
        self.frappe.whitelist = self.whitelist
        self.frappe.has_permission = self.permission
        self.frappe.get_meta = lambda doctype: self.metas[doctype]
        self.frappe.get_doc, self.frappe.new_doc, self.frappe.get_list = (
            self.get_doc,
            self.new_doc,
            self.get_list,
        )
        self.frappe.share = Record(get_shared=lambda *args, **kwargs: [])
        self.frappe.db = Record(
            sql=self.sql,
            commit=Mock(side_effect=AssertionError("no commit")),
            set_value=Mock(side_effect=AssertionError("no write")),
        )
        environment = self

        class Document(Record):
            @property
            def meta(self):
                return environment.metas[self.doctype]

            def set(self, name, value):
                self[name] = value

            def is_new(self):
                return bool(self.get("__islocal"))

            def check_permission(self, permission):
                if not environment.permission(self.doctype, permission, self):
                    environment.messages.append(
                        "private:" + self.doctype + ":" + str(self.name)
                    )
                    raise PermissionError("private document")

            def save(self):
                raise AssertionError("no save")

        self.Document = Document
        model, native_document = (
            types.ModuleType("frappe.model"),
            types.ModuleType("frappe.model.document"),
        )
        model.get_permitted_fields = self.columns
        native_document.Document = Document
        modules = patch.dict(
            sys.modules,
            {
                "frappe": self.frappe,
                "frappe.model": model,
                "frappe.model.document": native_document,
            },
        )
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(
            "wage_rates_model", ROOT / "jifeng_erp/wage_rates.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.metas[self.module.WAGE] = Meta(
            self.module.WAGE_FIELDS
            | self.module.TARGET_FIELDS
            | {"hours", "hourly_rate", "notes", "salary_component"}
        )
        self.metas["Employee"] = Meta(self.module.EMPLOYEE_FIELDS)
        self.metas["Job Card"] = Meta(self.module.CARD_FIELDS | {"time_logs"})
        self.metas["Work Order"] = Meta(self.module.WORK_FIELDS | {"operations"})
        self.metas["Jifeng Flow Order"] = Meta({"name", "modified", "company"})
        for doctype, table, child in (
            ("Job Card", "time_logs", "Job Card Time Log"),
            ("Work Order", "operations", "Work Order Operation"),
        ):
            self.metas[doctype].get_field(table).update(
                fieldtype="Table", options=child
            )
        self.metas["Job Card Time Log"] = Meta(
            {"name", "parent", "parenttype", "parentfield", "employee"}
        )
        self.metas["Work Order Operation"] = Meta(
            {"name", "parent", "parenttype", "parentfield", "operation"}
        )
        for doctype, name in (
            ("Company", "Company A"),
            ("Company", "Company B"),
            ("Operation", "Op A"),
            ("Operation", "Op B"),
            ("UOM", "Nos"),
            ("UOM", "Kg"),
        ):
            self.metas.setdefault(doctype, Meta({"name", "modified"}))
            self.add(doctype, name)
        self.add(
            "Employee",
            "EMP-1",
            company="Company A",
            status="Active",
            employee_name="Private employee name",
        )
        self.add("Jifeng Flow Order", "FLOW-1", company="Company A")
        self.source("CURRENT")
        self.source("HISTORY")
        self.wage("WAGE-1", piece_rate=0)
        self.wage("WAGE-2", piece_rate="0.123456")
        self.context = {
            "company": "Company A",
            "employee": "EMP-1",
            "operation": "Op A",
            "work_date": "2026-10-03",
            "stock_uom": "Nos",
            "job_card": "JC-CURRENT",
            "source_modified": STAMP,
            "work_order_modified": STAMP,
        }

    def whitelist(self, **kwargs):
        def decorate(function):
            self.whitelisted.append((function.__name__, kwargs))
            return function

        return decorate

    def add(self, doctype, name, **fields):
        self.records[doctype, name] = {
            "doctype": doctype,
            "name": name,
            "modified": STAMP,
            **fields,
        }

    def source(self, suffix):
        work, card = "WO-" + suffix, "JC-" + suffix
        operation_row = Record(
            name="OPROW-" + suffix,
            parent=work,
            parenttype="Work Order",
            parentfield="operations",
            operation="Op A",
        )
        time_row = Record(
            name="TIMEROW-" + suffix,
            parent=card,
            parenttype="Job Card",
            parentfield="time_logs",
            employee="EMP-1",
        )
        self.add(
            "Work Order",
            work,
            docstatus=1,
            company="Company A",
            jf_flow_order="FLOW-1",
            operations=[operation_row],
        )
        self.add(
            "Job Card",
            card,
            docstatus=1,
            company="Company A",
            operation="Op A",
            operation_id=operation_row.name,
            work_order=work,
            stock_uom="Nos",
            posting_date="2026-10-01",
            is_corrective_job_card=0,
            time_logs=[time_row],
        )
        self.records["Work Order Operation", operation_row.name] = dict(
            operation_row, doctype="Work Order Operation"
        )
        self.records["Job Card Time Log", time_row.name] = dict(
            time_row, doctype="Job Card Time Log"
        )

    def wage(self, name, **changes):
        self.add(
            self.module.WAGE,
            name,
            docstatus=1,
            company="Company A",
            employee="EMP-1",
            operation="Op A",
            work_date="2026-10-01",
            stock_uom="Nos",
            piece_rate=1.25,
            quantity=2,
            job_card="JC-HISTORY",
            work_order="WO-HISTORY",
            flow_order="FLOW-1",
            hours=99,
            hourly_rate=888,
            notes="PRIVATE-NOTES",
            salary_component="PRIVATE-SALARY",
        )
        self.records[self.module.WAGE, name].update(changes)

    def permission(self, doctype, permission, doc=None, **kwargs):
        name = doc.get("name") if doc else None
        return (doctype, permission) not in self.denials and (
            doctype,
            name,
            permission,
        ) not in self.denials

    def columns(self, doctype, permission_type="read", **kwargs):
        return {
            field.fieldname for field in self.metas[doctype].fields
        } - self.hidden_fields.get((doctype, permission_type), set())

    def get_doc(self, doctype, name=None, **kwargs):
        self.calls.append(("doc", doctype, name, kwargs))
        if self.doc_hook:
            self.doc_hook(doctype, name, kwargs)
        return self.Document(deepcopy(self.records[doctype, name]))

    def new_doc(self, doctype):
        return self.Document(doctype=doctype, __islocal=1)

    def get_list(
        self,
        doctype,
        *,
        filters,
        fields,
        limit_page_length,
        limit_start=0,
        order_by=None,
        **kwargs,
    ):
        self.calls.append(
            (
                "list",
                doctype,
                deepcopy(filters),
                limit_start,
                limit_page_length,
                order_by,
                kwargs,
            )
        )
        if self.list_hook:
            self.list_hook(doctype, filters, limit_start)
        result = []
        for (kind, name), source in self.records.items():
            if (
                kind != doctype
                or (kind, name) in self.list_hidden
                or not self.permission(kind, "read", self.Document(source))
            ):
                continue
            accepted = True
            for field, criterion in filters.items():
                value = source.get(field)
                if isinstance(criterion, list):
                    operator, expected = criterion
                    if operator == ">":
                        try:
                            valid = Decimal(str(value)) > expected
                        except (ValueError, TypeError, ArithmeticError):
                            valid = False
                    elif operator == "!=":
                        valid = value not in (expected, None)
                    elif operator == "<=":
                        valid = value is not None and str(value) <= expected
                    else:
                        raise AssertionError(operator)
                else:
                    valid = value == criterion
                accepted = accepted and valid
            if accepted:
                result.append(
                    {
                        field: source[field]
                        for field in fields
                        if field in source and field in self.columns(doctype)
                    }
                )
        result.sort(key=lambda row: row.get("name", ""))
        if order_by == "work_date desc, name asc":
            result.sort(key=lambda row: row["work_date"], reverse=True)
        return result[limit_start : limit_start + limit_page_length]

    def sql(self, query, values):
        self.assertEqual(
            query,
            "SELECT name FROM `tabJifeng Wage Entry` WHERE name=%s FOR UPDATE NOWAIT",
        )
        self.assertEqual(len(values), 1)
        self.locks.append(values[0])
        return [(values[0],)]

    def query(self, **kwargs):
        return self.module.query_piece_rates(self.context, **kwargs)

    def choose(self, **kwargs):
        return self.module.choose_piece_rate(
            self.context,
            kwargs.get("source", "WAGE-1"),
            kwargs.get("version", STAMP),
            kwargs.get("piece_rate", "0"),
        )

    def target(self):
        return self.Document(
            doctype=self.module.WAGE,
            **{
                key: value
                for key, value in self.context.items()
                if key not in {"source_modified", "work_order_modified"}
            },
        )

    def test_minimal_dto_zero_and_explicit_choice_no_writes(self):
        before = deepcopy(self.records)
        result = self.query()
        self.assertEqual(
            set(result),
            {"scope", "readonly", "page", "page_size", "total", "total_pages", "rows"},
        )
        self.assertEqual(result["scope"], "wage_piece_rates")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["rows"][0]["piece_rate"], "0")
        self.assertEqual(result["rows"][1]["piece_rate"], "0.123456")
        self.assertEqual(
            set(result["rows"][0]),
            {
                "name",
                "version",
                "work_date",
                "stock_uom",
                "piece_rate",
                "company",
                "employee",
                "operation",
            },
        )
        self.assertEqual(
            self.choose(),
            {"scope": "wage_piece_rates", "readonly": True, **result["rows"][0]},
        )
        self.assertEqual(self.records, before)
        self.assertFalse(self.locks)
        self.assertFalse(self.messages)
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.set_value.assert_not_called()
        self.assertEqual(
            self.whitelisted,
            [
                ("query_piece_rates", {"methods": ["GET"]}),
                ("choose_piece_rate", {"methods": ["GET"]}),
            ],
        )

    def test_complete_two_scan_pagination_over_200_and_stable_ties(self):
        for index in range(220):
            self.wage("EXTRA-%03d" % index)
        result = self.query(page=3, page_size=100)
        self.assertEqual(result["total"], 222)
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual(len(result["rows"]), 22)
        self.assertEqual(result["rows"][-1]["name"], "WAGE-2")
        scans = [
            call
            for call in self.calls
            if call[0] == "list"
            and call[1] == self.module.WAGE
            and "name" not in call[2]
        ]
        self.assertEqual([call[3] for call in scans], [0, 200, 0, 200])
        self.assertEqual(self.query(page=4, page_size=100)["rows"], [])

    def test_exact_scope_approval_quantity_source_and_date_filters(self):
        changes = [
            {"company": "Company B"},
            {"employee": "EMP-2"},
            {"operation": "Op B"},
            {"stock_uom": "Kg"},
            {"docstatus": 0},
            {"docstatus": 2},
            {"quantity": 0},
            {"quantity": -1},
            {"job_card": ""},
            {"job_card": None},
            {"work_date": "2026-10-04"},
        ]
        for index, change in enumerate(changes):
            self.wage("INVALID-%02d" % index, **change)
        self.assertEqual(
            [row["name"] for row in self.query()["rows"]], ["WAGE-1", "WAGE-2"]
        )
        for index in range(len(changes)):
            with self.subTest(index=index), self.assertRaises(PermissionError):
                self.choose(source="INVALID-%02d" % index, piece_rate=1.25)

    def test_native_permissions_company_employee_links_and_new_target(self):
        for denial in [
            ("Company", "Company A", "select"),
            ("Employee", "EMP-1", "read"),
            ("Operation", "Op A", "select"),
            ("UOM", "Nos", "select"),
            (self.module.WAGE, "create"),
            (self.module.WAGE, "write"),
            (self.module.WAGE, "read"),
        ]:
            with self.subTest(denial=denial):
                self.denials.add(denial)
                with self.assertRaises(PermissionError):
                    self.query()
                self.denials.clear()
        self.assertFalse(self.messages)

    def test_readonly_and_field_permission_mask_guards(self):
        for field in ("piece_rate", "employee", "stock_uom"):
            with self.subTest(field=field):
                self.hidden_fields[self.module.WAGE, "write"] = {field}
                with self.assertRaises(PermissionError):
                    self.query()
        self.hidden_fields.clear()
        rate = self.metas[self.module.WAGE].get_field("piece_rate")
        for option in ("read_only", "read_only_depends_on", "mask"):
            rate[option] = 1
            with self.subTest(option=option), self.assertRaises(PermissionError):
                self.query()
            rate[option] = 0
        self.hidden_fields[self.module.WAGE, "read"] = {"quantity"}
        with self.assertRaises(PermissionError):
            self.query()

    def test_core_parent_and_child_masks_reject_current_source(self):
        for doctype, field in [
            ("Employee", "company"),
            ("Job Card", "stock_uom"),
            ("Work Order", "jf_flow_order"),
            ("Job Card", "time_logs"),
            ("Job Card Time Log", "employee"),
            ("Work Order", "operations"),
            ("Work Order Operation", "operation"),
        ]:
            with self.subTest(doctype=doctype, field=field):
                self.metas[doctype].get_field(field).mask = 1
                with self.assertRaises(PermissionError):
                    self.query()
                self.metas[doctype].get_field(field).mask = 0

    def test_hidden_historical_link_is_filtered_without_messages_or_identity(self):
        self.source("PRIVATE")
        self.wage("PRIVATE-WAGE", job_card="JC-PRIVATE", work_order="WO-PRIVATE")
        for doctype, name in [("Job Card", "JC-PRIVATE"), ("Work Order", "WO-PRIVATE")]:
            with self.subTest(doctype=doctype):
                self.denials.add((doctype, name, "read"))
                result = self.query()
                self.assertEqual(result["total"], 2)
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertFalse(self.messages)
                with self.assertRaises(PermissionError) as error:
                    self.choose(source="PRIVATE-WAGE", piece_rate=1.25)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertFalse(self.messages)
                self.denials.clear()

    def test_native_get_list_user_permission_on_wage_parent_and_child(self):
        self.list_hidden.add((self.module.WAGE, "WAGE-1"))
        self.assertEqual(self.query()["total"], 1)
        with self.assertRaises(PermissionError):
            self.choose()
        self.list_hidden.clear()
        self.list_hidden.add(("Job Card", "JC-HISTORY"))
        self.assertEqual(self.query()["total"], 0)
        self.list_hidden.clear()
        self.list_hidden.add(("Job Card Time Log", "TIMEROW-HISTORY"))
        self.assertEqual(self.query()["total"], 0)
        self.assertFalse(self.messages)

    def test_current_source_cannot_be_spoofed_or_stale(self):
        for field, value, expected in [
            ("company", "Company B", PermissionError),
            ("operation", "Op B", PermissionError),
            ("stock_uom", "Kg", PermissionError),
            ("source_modified", LATER, ValueError),
            ("work_order_modified", LATER, ValueError),
        ]:
            original = self.context[field]
            self.context[field] = value
            with self.subTest(field=field), self.assertRaises(expected):
                self.query()
            self.context[field] = original
        self.records["Employee", "EMP-1"]["status"] = "Left"
        with self.assertRaises(PermissionError):
            self.query()

    def test_source_native_invariants_and_snapshot_link_consistency(self):
        for doctype, name, field, value in [
            ("Job Card", "JC-HISTORY", "docstatus", 2),
            ("Job Card", "JC-HISTORY", "is_corrective_job_card", 1),
            ("Job Card", "JC-HISTORY", "operation_id", "WRONG"),
            ("Work Order", "WO-HISTORY", "docstatus", 0),
            (self.module.WAGE, "WAGE-1", "work_order", "WO-CURRENT"),
            (self.module.WAGE, "WAGE-1", "flow_order", "WRONG"),
        ]:
            original = self.records[doctype, name][field]
            self.records[doctype, name][field] = value
            with (
                self.subTest(doctype=doctype, field=field),
                self.assertRaises(PermissionError),
            ):
                self.choose()
            self.records[doctype, name][field] = original

    def test_numeric_contract_rejects_invalid_history_and_expected_price(self):
        for value in (
            True,
            None,
            "",
            "-1",
            "NaN",
            "Infinity",
            float("inf"),
            "1e999999",
        ):
            self.records[self.module.WAGE, "WAGE-1"]["piece_rate"] = value
            with self.subTest(value=value):
                self.assertEqual(self.query()["total"], 1)
                with self.assertRaises(ValueError):
                    self.choose(piece_rate=value)
        self.records[self.module.WAGE, "WAGE-1"]["piece_rate"] = Decimal("0.000000")
        self.assertEqual(self.choose(piece_rate=0)["piece_rate"], "0")

    def test_choose_exact_id_version_and_price(self):
        for values in [
            {"source": "MISSING"},
            {"version": LATER},
            {"piece_rate": "0.01"},
            {"version": "2026-10-03"},
            {"source": " WAGE-1"},
        ]:
            with (
                self.subTest(values=values),
                self.assertRaises((ValueError, PermissionError)),
            ):
                self.choose(**values)
        self.assertEqual(
            self.choose(version=STAMP.replace("T", " "), piece_rate="0.000000")["name"],
            "WAGE-1",
        )

    def test_complete_scan_detects_insert_delete_and_price_change(self):
        for mutation in ("insert", "delete", "price"):
            scan = 0

            def change(doctype, filters, start):
                nonlocal scan
                if doctype == self.module.WAGE and "name" not in filters and start == 0:
                    scan += 1
                    if scan == 2:
                        if mutation == "insert":
                            self.wage("NEW-WAGE")
                        elif mutation == "delete":
                            self.records.pop((self.module.WAGE, "WAGE-2"))
                        else:
                            self.records[self.module.WAGE, "WAGE-2"]["piece_rate"] = 9

            self.list_hook = change
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.query()
            self.list_hook = None
            self.records.pop((self.module.WAGE, "NEW-WAGE"), None)
            self.wage("WAGE-2", piece_rate="0.123456")

    def test_classification_and_current_source_changes_midquery_reject(self):
        scan = 0

        def hide(doctype, filters, start):
            nonlocal scan
            if doctype == self.module.WAGE and "name" not in filters and start == 0:
                scan += 1
                if scan == 2:
                    self.denials.add(("Job Card", "JC-HISTORY", "read"))

        self.list_hook = hide
        with self.assertRaises(ValueError):
            self.query()
        self.denials.clear()
        self.list_hook = None
        count = 0

        def mutate(doctype, name, kwargs):
            nonlocal count
            if doctype == "Job Card" and name == "JC-CURRENT":
                count += 1
                if count == 2:
                    self.records[doctype, name]["modified"] = LATER

        self.doc_hook = mutate
        with self.assertRaises(ValueError):
            self.query()
        self.assertFalse(self.messages)

    def test_loaded_document_and_native_projection_must_agree(self):
        once = False

        def mutate(doctype, filters, start):
            nonlocal once
            if (
                doctype == self.module.WAGE
                and filters.get("name") == "WAGE-1"
                and not once
            ):
                once = True
                self.records[doctype, "WAGE-1"]["modified"] = LATER

        self.list_hook = mutate
        with self.assertRaises(ValueError):
            self.choose()

    def test_private_resolver_accepts_native_target_without_create_write_gate(self):
        self.denials.update({(self.module.WAGE, "create"), (self.module.WAGE, "write")})
        self.hidden_fields[self.module.WAGE, "write"] = {"piece_rate"}
        self.assertEqual(
            self.module.resolve_reference(self.target(), "WAGE-1", STAMP, 0)["name"],
            "WAGE-1",
        )
        with self.assertRaises(PermissionError):
            self.choose()
        with self.assertRaises(PermissionError):
            self.module.resolve_reference(
                dict(self.target(), can_write=True), "WAGE-1", STAMP, 0
            )

    def test_private_nowait_source_lock_after_quiet_access_and_no_get_lock(self):
        result = self.module.resolve_reference(
            self.target(), "WAGE-1", STAMP, 0, for_update=True
        )
        self.assertEqual(result["name"], "WAGE-1")
        self.assertEqual(self.locks, ["WAGE-1"])
        locked_reads = [
            call
            for call in self.calls
            if call[0] == "doc"
            and call[1] == self.module.WAGE
            and call[3].get("for_update")
        ]
        self.assertEqual(len(locked_reads), 2)
        self.locks.clear()
        self.denials.add((self.module.WAGE, "WAGE-1", "read"))
        with self.assertRaises(PermissionError):
            self.module.resolve_reference(
                self.target(), "WAGE-1", STAMP, 0, for_update=True
            )
        self.assertFalse(self.locks)

    def test_private_nowait_conflict_propagates_without_retry(self):
        lock = Mock(side_effect=RuntimeError("lock busy"))
        self.frappe.db.sql = lock
        with self.assertRaisesRegex(RuntimeError, "lock busy"):
            self.module.resolve_reference(
                self.target(), "WAGE-1", STAMP, 0, for_update=True
            )
        self.assertEqual(lock.call_count, 1)

    def test_standalone_native_job_card_compatibility_is_exact(self):
        self.records["Job Card", "JC-CURRENT"]["work_order"] = None
        self.context["work_order_modified"] = ""
        self.assertEqual(self.query()["total"], 2)
        self.context["work_order_modified"] = STAMP
        with self.assertRaises(ValueError):
            self.query()

    def test_strict_context_paging_and_guest(self):
        for context in [
            dict(self.context, can_write=True),
            {key: value for key, value in self.context.items() if key != "stock_uom"},
            dict(self.context, work_date="2026-1-1"),
            dict(self.context, source_modified="2026-10-03T12:01:02+08:00"),
            "not json",
        ]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.module.query_piece_rates(context)
        for kwargs in [
            {"page": True},
            {"page": 0},
            {"page_size": 101},
            {"page": " 1"},
            {"page": "一"},
            {"page_size": 1.5},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.query(**kwargs)
        self.assertEqual(
            self.module.query_piece_rates(json.dumps(self.context), page="1")["total"],
            2,
        )
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.query()

    def test_native_datetime_date_normalization_preserves_microseconds(self):
        for document in self.records.values():
            if "modified" in document:
                document["modified"] = datetime.fromisoformat(STAMP)
        self.assertEqual(self.choose()["version"], STAMP)
        with self.assertRaises(ValueError):
            self.choose(version=STAMP[:-1])


if __name__ == "__main__":
    unittest.main()
