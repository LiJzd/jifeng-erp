"""Draft-order customer selection models; no database or record writes."""

from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
STAMP = "2026-10-03T12:01:02.123456"
LATER = "2026-10-03T12:01:03.123456"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Meta:
    def __init__(self, names):
        self.fields = [
            Record(
                fieldname=name,
                fieldtype="Data",
                options="",
                read_only=0,
                permlevel=0,
                mask=0,
            )
            for name in names
        ]

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return [] if kwargs["permission_type"] == "mask" else [0]


class TestOrderCustomerSelection(unittest.TestCase):
    def setUp(self):
        self.records, self.hidden_fields, self.metas = {}, {}, {}
        self.denials, self.hidden_docs, self.list_hidden = set(), set(), set()
        self.omitted_fields, self.projection_override = {}, {}
        self.allowed_companies = None
        self.read_hook, self.list_hook = None, None
        self.doc_calls, self.list_calls, self.permission_calls, self.saves = (
            [],
            [],
            [],
            [],
        )
        self.permission_messages = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.session = Record(user="sales-user@example.invalid")
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(
            error(message)
        )
        self.frappe.has_permission = self.has_permission
        self.frappe.get_meta = lambda doctype: self.metas[doctype]
        self.frappe.get_doc, self.frappe.new_doc, self.frappe.get_list = (
            self.get_doc,
            self.new_doc,
            self.get_list,
        )
        environment = self

        class Document(Record):
            @property
            def meta(self):
                return environment.metas[self.doctype]

            def is_new(self):
                return bool(self.get("__islocal"))

            def check_permission(self, permission):
                if not environment.has_permission(self.doctype, permission, self):
                    environment.permission_messages.append(
                        "private-denial:" + self.doctype + ":" + str(self.name)
                    )
                    raise PermissionError(self.doctype + " " + permission)

            def save(self):
                environment.saves.append(self.doctype)
                raise AssertionError("This adapter must never save")

        self.Document = Document
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        modules = patch.dict(
            sys.modules, {"frappe": self.frappe, "frappe.model": model}
        )
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(
            "order_customer_selection_model",
            ROOT / "jifeng_erp/order_customer_selection.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.metas["Company"] = Meta(["name"])
        self.metas[self.module.ORDER] = Meta(self.module.ORDER_FIELDS)
        self.metas[self.module.PROFILE] = Meta(
            self.module.PROFILE_FIELDS
            | {"legacy_code", "grade", "address", "mobile", "discount", "category"}
        )
        self.metas[self.module.CUSTOMER] = Meta(
            self.module.CUSTOMER_FIELDS | {"customer_name"}
        )
        self.metas[self.module.ORDER].get_field("customer").update(
            fieldtype="Link", options="Customer"
        )
        for name in ("Company A", "Company B"):
            self.add("Company", name)
        for index in (1, 2):
            self.add(
                self.module.CUSTOMER,
                "CUSTOMER-" + str(index),
                disabled=0,
                customer_name="Same display name",
            )
            self.profile("PROFILE-" + str(index), "CUSTOMER-" + str(index))
        self.add(
            self.module.ORDER,
            "SO-1",
            company="Company A",
            customer="CUSTOMER-1",
            docstatus=0,
        )
        self.new_context = {"company": "Company A", "name": None, "version": None}
        self.saved_context = {"company": "Company A", "name": "SO-1", "version": STAMP}

    def add(self, doctype, name, **fields):
        self.records[doctype, name] = {
            "doctype": doctype,
            "name": name,
            "modified": STAMP,
            **fields,
        }

    def profile(self, name, customer, **fields):
        self.add(
            self.module.PROFILE,
            name,
            company="Company A",
            archive_kind="订单客户",
            customer=customer,
            legacy_number="0007",
            display_name="Same display name",
            inactive=0,
            legacy_code="C-007",
            grade="UNREADABLE-GRADE",
            address="PRIVATE-ADDRESS",
            mobile="PRIVATE-MOBILE",
            discount="PRIVATE-DISCOUNT",
            **fields,
        )

    def columns(self, doctype, permission_type="read", **kwargs):
        return {
            field.fieldname for field in self.metas[doctype].fields
        } - self.hidden_fields.get((doctype, permission_type), set())

    def has_permission(self, doctype, permission, doc=None, **kwargs):
        self.permission_calls.append(
            (doctype, permission, doc.get("company") if doc else None)
        )
        if (doctype, permission) in self.denials or (
            doc and (doctype, doc.name) in self.hidden_docs
        ):
            return False
        return not (
            doc
            and doctype == self.module.ORDER
            and self.allowed_companies is not None
            and doc.company not in self.allowed_companies
        )

    def get_doc(self, doctype, name):
        self.doc_calls.append((doctype, name))
        result = self.Document(deepcopy(self.records[doctype, name]))
        if self.read_hook:
            self.read_hook(doctype, name)
        return result

    def new_doc(self, doctype):
        return self.Document(doctype=doctype, __islocal=1, customer=None)

    def get_list(self, doctype, filters, **kwargs):
        self.list_calls.append((doctype, deepcopy(filters), deepcopy(kwargs)))
        rows = []
        for (kind, name), source in self.records.items():
            if kind != doctype or (kind, name) in self.list_hidden:
                continue
            if not all(source.get(field) == value for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(
                value[1].strip("%") in (source.get(field) or "")
                for field, value in kwargs["or_filters"].items()
            ):
                continue
            rows.append(source)
        rows.sort(key=lambda row: row["name"])
        start = kwargs.get("limit_start", 0)
        rows = rows[start : start + kwargs.get("limit_page_length", 200)]
        if kwargs.get("pluck"):
            result = [row[kwargs["pluck"]] for row in rows]
        else:
            result = [
                {
                    field: self.projection_override.get(
                        (doctype, row["name"], field), row.get(field)
                    )
                    for field in kwargs["fields"]
                    if field not in self.omitted_fields.get(doctype, set())
                }
                for row in rows
            ]
        if self.list_hook:
            self.list_hook(doctype, filters, kwargs)
        return result

    def query(self, context=None, **kwargs):
        return self.module.query_customers(context or self.new_context, **kwargs)

    def choose(self, context=None, **kwargs):
        args = dict(
            context=context or self.new_context,
            profile="PROFILE-2",
            profile_version=STAMP,
            customer_version=STAMP,
            customer="CUSTOMER-2",
        )
        args.update(kwargs)
        return self.module.choose_customer(**args)

    def test_query_and_choose_are_readonly_and_use_exact_native_customer(self):
        before = deepcopy(self.records)
        result = self.query()
        self.assertEqual(
            (result["scope"], result["total"], result["total_pages"]),
            ("order_customer", 2, 1),
        )
        self.assertEqual(result["rows"][0]["legacy_number"], "0007")
        chosen = self.choose()
        self.assertEqual(
            (chosen["name"], chosen["customer"]), ("PROFILE-2", "CUSTOMER-2")
        )
        self.assertEqual(self.records, before)
        self.assertEqual(self.saves, [])
        self.assertTrue(chosen["can_select"])
        self.assertFalse(
            set(chosen) & {"address", "mobile", "discount", "grade", "customer_name"}
        )

    def test_readonly_sales_user_does_not_require_profile_create_or_write(self):
        self.denials.update(
            {(self.module.PROFILE, "create"), (self.module.PROFILE, "write")}
        )
        self.assertEqual(self.query()["total"], 2)
        self.assertEqual(self.choose()["customer"], "CUSTOMER-2")
        self.assertFalse(
            any(
                kind == self.module.PROFILE and permission in ("create", "write")
                for kind, permission, _ in self.permission_calls
            )
        )

    def test_private_unrelated_fields_and_dictionary_permissions_are_not_required(self):
        self.hidden_fields[self.module.PROFILE, "read"] = {
            "address",
            "mobile",
            "discount",
            "grade",
            "category",
        }
        self.denials.add(("Jifeng Customer Type", "read"))
        self.assertEqual(self.choose()["customer"], "CUSTOMER-2")
        self.assertTrue(
            all(
                kind in ("Company", self.module.PROFILE, self.module.CUSTOMER)
                for kind, _ in self.doc_calls
            )
        )

    def test_visible_code_search_and_hidden_optional_code_omission(self):
        self.assertEqual(self.query(search="C-007")["total"], 2)
        self.metas[self.module.PROFILE].get_field("legacy_code").mask = 1
        rows = self.query()["rows"]
        self.assertTrue(all("legacy_code" not in row for row in rows))
        self.assertEqual(self.query(search="C-007")["total"], 0)
        self.assertNotIn("legacy_code", self.choose())

    def test_disabled_sources_are_filtered_and_direct_selection_rejected(self):
        for doctype, name, field in (
            (self.module.PROFILE, "PROFILE-2", "inactive"),
            (self.module.CUSTOMER, "CUSTOMER-2", "disabled"),
        ):
            with self.subTest(field=field):
                self.records[doctype, name][field] = 1
                self.assertEqual(self.query()["total"], 1)
                with self.assertRaisesRegex(ValueError, "停用"):
                    self.choose()
                self.records[doctype, name][field] = 0

    def test_other_company_and_sales_kind_are_not_candidates_or_direct_choices(self):
        for field, value in (("company", "Company B"), ("archive_kind", "销售客户")):
            with self.subTest(field=field):
                before = self.records[self.module.PROFILE, "PROFILE-2"][field]
                self.records[self.module.PROFILE, "PROFILE-2"][field] = value
                self.assertEqual(self.query()["total"], 1)
                with self.assertRaises(PermissionError):
                    self.choose()
                self.records[self.module.PROFILE, "PROFILE-2"][field] = before

    def test_associated_customer_read_and_select_permissions_filter_counts(self):
        self.hidden_docs.add((self.module.CUSTOMER, "CUSTOMER-2"))
        self.assertEqual(self.query()["total"], 1)
        with self.assertRaises(PermissionError):
            self.choose()

    def test_filtered_customer_denial_never_calls_noisy_document_check_permission(self):
        self.hidden_docs.add((self.module.CUSTOMER, "CUSTOMER-2"))
        result = self.query()
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.permission_messages, [])

    def test_previously_hidden_customer_becomes_readable_during_later_source_read_rejected(
        self,
    ):
        self.hidden_docs.add((self.module.CUSTOMER, "CUSTOMER-2"))
        self.profile("PROFILE-3", "CUSTOMER-1")
        count = 0

        def hook(doctype, name):
            nonlocal count
            if (doctype, name) == (self.module.CUSTOMER, "CUSTOMER-1"):
                count += 1
                if count == 2:
                    self.hidden_docs.remove((self.module.CUSTOMER, "CUSTOMER-2"))

        self.read_hook = hook
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.query()
        self.assertEqual(self.permission_messages, [])

    def test_previously_hidden_customer_becomes_readable_during_target_reread_rejected(
        self,
    ):
        self.hidden_docs.add((self.module.CUSTOMER, "CUSTOMER-2"))
        count = 0

        def hook(doctype, name):
            nonlocal count
            if (doctype, name) == ("Company", "Company A"):
                count += 1
                if count == 2:
                    self.hidden_docs.remove((self.module.CUSTOMER, "CUSTOMER-2"))

        self.read_hook = hook
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.query()
        self.assertEqual(self.permission_messages, [])

    def test_empty_query_still_requires_profile_and_customer_read_and_select(self):
        for doctype in (self.module.PROFILE, self.module.CUSTOMER):
            for permission in ("read", "select"):
                with self.subTest(doctype=doctype, permission=permission):
                    self.denials.add((doctype, permission))
                    with self.assertRaises(PermissionError):
                        self.query(search="does not exist")
                    self.denials.clear()

    def test_core_source_fields_cannot_be_masked_or_missing(self):
        for doctype, fields in (
            (self.module.PROFILE, self.module.PROFILE_FIELDS),
            (self.module.CUSTOMER, self.module.CUSTOMER_FIELDS),
        ):
            for field in fields:
                with self.subTest(doctype=doctype, field=field):
                    self.hidden_fields[doctype, "read"] = {field}
                    with self.assertRaises(PermissionError):
                        self.choose()
                    self.hidden_fields.clear()
                    self.metas[doctype].get_field(field).mask = 1
                    with self.assertRaises(PermissionError):
                        self.choose()
                    self.metas[doctype].get_field(field).mask = 0

    def test_permission_filtered_projection_is_required_even_when_get_doc_can_read(
        self,
    ):
        for doctype, name in (
            (self.module.PROFILE, "PROFILE-2"),
            (self.module.CUSTOMER, "CUSTOMER-2"),
            (self.module.ORDER, "SO-1"),
        ):
            with self.subTest(doctype=doctype):
                self.list_hidden.add((doctype, name))
                with self.assertRaises(PermissionError):
                    self.choose(self.saved_context)
                self.list_hidden.clear()

    def test_missing_projection_field_is_not_filled_from_full_document(self):
        self.omitted_fields[self.module.PROFILE] = {"display_name"}
        with self.assertRaises(PermissionError):
            self.choose()

    def test_profile_and_customer_versions_are_both_required(self):
        for field in ("profile_version", "customer_version"):
            for value in (
                LATER,
                "2026-10-03",
                None,
                "2026-10-03T12:01:02+00:00",
                datetime.fromisoformat(STAMP),
            ):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(ValueError),
                ):
                    self.choose(**{field: value})

    def test_expected_customer_mapping_is_exact_and_not_display_name(self):
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.choose(customer="CUSTOMER-1")
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.choose(customer="Same display name")

    def test_new_order_requires_native_create_and_write_with_actual_company(self):
        for permission in ("create", "write"):
            with self.subTest(permission=permission):
                self.denials.add((self.module.ORDER, permission))
                with self.assertRaises(PermissionError):
                    self.choose()
                self.denials.clear()
        self.allowed_companies = {"Company A"}
        with self.assertRaises(PermissionError):
            self.query({"company": "Company B", "name": None, "version": None})
        self.assertTrue(
            any(
                kind == self.module.ORDER and company == "Company B"
                for kind, _, company in self.permission_calls
            )
        )

    def test_company_select_permission_required(self):
        self.denials.add(("Company", "select"))
        with self.assertRaises(PermissionError):
            self.choose()

    def test_saved_order_needs_read_write_draft_version_and_exact_company(self):
        self.assertEqual(self.choose(self.saved_context)["customer"], "CUSTOMER-2")
        for permission in ("read", "write"):
            with self.subTest(permission=permission):
                self.denials.add((self.module.ORDER, permission))
                with self.assertRaises(PermissionError):
                    self.choose(self.saved_context)
                self.denials.clear()
        for status in (1, 2):
            self.records[self.module.ORDER, "SO-1"]["docstatus"] = status
            with self.assertRaisesRegex(ValueError, "未提交"):
                self.choose(self.saved_context)
        self.records[self.module.ORDER, "SO-1"]["docstatus"] = 0
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.choose({**self.saved_context, "version": LATER})
        with self.assertRaises(PermissionError):
            self.choose({**self.saved_context, "company": "Company B"})

    def test_native_customer_field_read_write_mask_and_metadata_readonly_are_authoritative(
        self,
    ):
        field = self.metas[self.module.ORDER].get_field("customer")
        for context in (self.new_context, self.saved_context):
            for permission in ("read", "write"):
                with self.subTest(context=context, permission=permission):
                    self.hidden_fields[self.module.ORDER, permission] = {"customer"}
                    with self.assertRaises(PermissionError):
                        self.choose(context)
                    self.hidden_fields.clear()
            for key, value in (
                ("mask", 1),
                ("read_only", 1),
                ("read_only_depends_on", "eval:doc.name"),
                ("options", "Supplier"),
                ("fieldtype", "Data"),
            ):
                with self.subTest(context=context, key=key):
                    before = field.get(key)
                    field[key] = value
                    with self.assertRaises(PermissionError):
                        self.choose(context)
                    field[key] = before
        field.set_only_once = 1
        self.assertTrue(self.choose()["can_select"])
        with self.assertRaises(PermissionError):
            self.choose(self.saved_context)

    def test_saved_target_projection_masks_and_omissions_are_rejected(self):
        for name in self.module.ORDER_FIELDS:
            with self.subTest(field=name):
                self.hidden_fields[self.module.ORDER, "read"] = {name}
                with self.assertRaises(PermissionError):
                    self.choose(self.saved_context)
                self.hidden_fields.clear()
        self.omitted_fields[self.module.ORDER] = {"company"}
        with self.assertRaises(PermissionError):
            self.choose(self.saved_context)

    def test_strict_context_and_guest_rejected(self):
        invalid = (
            None,
            [],
            "bad JSON",
            {},
            {**self.new_context, "doctype": "Quotation"},
            {**self.new_context, "name": ""},
            {**self.new_context, "version": STAMP},
            {**self.saved_context, "version": None},
            {**self.new_context, "company": " Company A"},
        )
        for context in invalid:
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.module.query_customers(context)
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.choose()

    def test_json_context_and_native_space_timestamp_compare_without_precision_loss(
        self,
    ):
        self.records[self.module.ORDER, "SO-1"]["modified"] = datetime.fromisoformat(
            STAMP
        )
        self.records[self.module.PROFILE, "PROFILE-2"]["modified"] = STAMP.replace(
            "T", " "
        )
        result = self.choose(json.dumps(self.saved_context))
        self.assertEqual(result["profile_version"], STAMP)

    def test_pagination_scans_full_candidate_set_and_counts_selectable_customers(self):
        self.records = {
            key: row
            for key, row in self.records.items()
            if key[0] != self.module.PROFILE
        }
        for index in range(413):
            self.profile(f"P-{index:04}", "CUSTOMER-1")
        result = self.query(page=17)
        self.assertEqual(
            (
                result["total"],
                result["total_pages"],
                result["page"],
                len(result["rows"]),
            ),
            (413, 17, 17, 13),
        )
        self.assertEqual(result["rows"][0]["name"], "P-0400")
        scans = [
            kwargs
            for kind, _, kwargs in self.list_calls
            if kind == self.module.PROFILE and kwargs.get("pluck")
        ]
        self.assertTrue(all(row["order_by"] == "name asc" for row in scans))
        self.assertTrue(any(row["limit_start"] == 400 for row in scans))

    def test_page_search_validation_and_last_page_clamp(self):
        self.assertEqual(self.query(page="99")["page"], 1)
        self.assertEqual(self.query(search="no match")["total_pages"], 0)
        for args in (
            {"page": 0},
            {"page": True},
            {"page_size": 101},
            {"page_size": "01"},
            {"page": 9007199254740992},
            {"search": "x" * 141},
            {"search": None},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.query(**args)

    def test_source_change_between_document_and_filtered_projection_rejected(self):
        self.projection_override[self.module.PROFILE, "PROFILE-2", "customer"] = (
            "CUSTOMER-1"
        )
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.choose()

    def test_profile_version_changes_during_customer_read_rejected_at_final_reread(
        self,
    ):
        def hook(doctype, name):
            if (doctype, name) == (self.module.CUSTOMER, "CUSTOMER-2"):
                self.records[self.module.PROFILE, "PROFILE-2"]["modified"] = LATER

        self.read_hook = hook
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.choose()

    def test_linked_read_cannot_expose_newly_masked_profile_field(self):
        def hook(doctype, name):
            if (doctype, name) == (self.module.CUSTOMER, "CUSTOMER-2"):
                self.metas[self.module.PROFILE].get_field("display_name").mask = 1

        self.read_hook = hook
        with self.assertRaises(PermissionError):
            self.choose()

    def test_late_target_company_version_permission_and_readonly_changes_rejected(self):
        actions = (
            lambda: self.records[self.module.ORDER, "SO-1"].update(company="Company B"),
            lambda: self.records[self.module.ORDER, "SO-1"].update(modified=LATER),
            lambda: self.denials.add((self.module.ORDER, "write")),
            lambda: (
                self.metas[self.module.ORDER].get_field("customer").update(read_only=1)
            ),
        )
        for action in actions:
            with self.subTest(action=action):

                def hook(doctype, name):
                    if (doctype, name) == (self.module.CUSTOMER, "CUSTOMER-2"):
                        action()

                self.read_hook = hook
                with self.assertRaises((ValueError, PermissionError)):
                    self.choose(self.saved_context)
                self.records[self.module.ORDER, "SO-1"].update(
                    company="Company A", modified=STAMP
                )
                self.denials.clear()
                self.metas[self.module.ORDER].get_field("customer").read_only = 0

    def test_query_range_drift_rejected_without_partial_totals(self):
        counter = 0

        def hook(doctype, filters, kwargs):
            nonlocal counter
            if doctype == self.module.PROFILE and kwargs.get("pluck"):
                counter += 1
                if counter == 1:
                    self.profile("PROFILE-3", "CUSTOMER-1")

        self.list_hook = hook
        with self.assertRaisesRegex(ValueError, "查询范围已改变"):
            self.query()

    def test_filtered_disabled_source_change_is_not_silently_omitted_from_total(self):
        self.records[self.module.CUSTOMER, "CUSTOMER-1"]["disabled"] = 1

        def hook(doctype, name):
            if (doctype, name) == (self.module.CUSTOMER, "CUSTOMER-2"):
                self.records[self.module.CUSTOMER, "CUSTOMER-1"].update(
                    disabled=0, modified=LATER
                )

        self.read_hook = hook
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.query()

    def test_empty_query_cannot_return_after_late_source_permission_revocation(self):
        def hook(doctype, filters, kwargs):
            if doctype == self.module.PROFILE and kwargs.get("pluck"):
                self.denials.add((self.module.CUSTOMER, "select"))

        self.list_hook = hook
        with self.assertRaises(PermissionError):
            self.query(search="no match")


if __name__ == "__main__":
    unittest.main()
