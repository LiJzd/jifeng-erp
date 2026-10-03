"""Source permission, native lifecycle and share invariant models; no DB/GUI."""

from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import jifeng_erp


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Meta:
    def __init__(self, tables=(), fields=(), *, istable=False, issingle=False):
        self.fields = [Row(fieldname=name, fieldtype="Table", options=child)
                       for name, child in tables]
        self.fields += [Row(fieldname=name, fieldtype="Data") for name in fields]
        self.istable, self.issingle = istable, issingle

    def get_table_fields(self):
        return [df for df in self.fields if df.fieldtype == "Table"]

    def get_field(self, name):
        return next((df for df in self.fields if df.fieldname == name), None)

    def get_valid_columns(self):
        return {df.fieldname for df in self.fields if df.fieldtype != "Table"} | {
            "name", "modified", "docstatus", "owner", "parent", "parenttype", "parentfield"}


class Doc(Row):
    env = None

    @property
    def meta(self):
        return self.env.metas[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def has_permission(self, permtype="read", *, debug=False, user=None):
        return self.env.native_permission(self, permtype, user)

    def check_permission(self, permtype):
        if not self.has_permission(permtype):
            raise PermissionError("native denied")


class Environment:
    def __init__(self):
        self.user = "test-user"
        self.records, self.metas = {}, {}
        self.hidden, self.masked, self.denied_roles = set(), set(), set()
        self.locks, self.calls, self.source_calls = [], [], []
        self.module = None
        self._counter = 0

    def put(self, doc):
        doc = Doc(deepcopy(doc))
        self.records[doc.doctype, doc.name] = doc
        for df in self.metas[doc.doctype].get_table_fields():
            for row in doc.get(df.fieldname) or []:
                self.records[row.doctype, row.name] = Doc(deepcopy(row))
        return doc

    def get_doc(self, doctype, name, for_update=False):
        if for_update:
            self.locks.append((doctype, name, "doc"))
        return Doc(deepcopy(self.records[doctype, name]))

    @staticmethod
    def matches(doc, filters):
        if isinstance(filters, str):
            return doc.get("name") == filters
        for key, expected in (filters or {}).items():
            if isinstance(expected, list):
                if expected[0] == "in" and doc.get(key) not in expected[1]:
                    return False
            elif doc.get(key) != expected:
                return False
        return True

    def all_records(self, doctype):
        if doctype == "DocField":
            return [Row(name=f"{parent}:{df.fieldname}", parent=parent, **df)
                    for parent, meta in self.metas.items() for df in meta.fields]
        return [doc for (dt, _), doc in self.records.items() if dt == doctype]

    def get_all(self, doctype, filters=None, fields=None, limit_start=0,
                limit_page_length=200, **kwargs):
        records = sorted((doc for doc in self.all_records(doctype)
                          if self.matches(doc, filters)), key=lambda doc: doc.name or "")
        records = records[limit_start:limit_start + limit_page_length]
        return [Row({field: deepcopy(doc.get(field)) for field in fields}) for doc in records]

    def exists(self, doctype, filters):
        return next((doc.name for doc in self.all_records(doctype) if self.matches(doc, filters)), None)

    def get_value(self, doctype, filters, fields, as_dict=False, for_update=False):
        if for_update:
            self.locks.append((doctype, deepcopy(filters), "value"))
        doc = next((doc for doc in self.all_records(doctype) if self.matches(doc, filters)), None)
        if not doc:
            return None
        if isinstance(fields, str):
            return doc.get(fields)
        return Row({field: deepcopy(doc.get(field)) for field in fields})

    def native_permission(self, doc, permtype="read", user=None):
        self._counter += 1
        if self._counter > 2000:
            raise RuntimeError("recursive authorization")
        user = user or self.user
        hook = (self.module.sample_source_has_permission(doc, permtype, user)
                if doc.doctype in self.module.PARENTS or doc.meta.istable else True)
        native = doc.name not in self.hidden and (doc.doctype, permtype) not in self.denied_roles
        shared = bool(self.exists("DocShare", {"share_doctype": doc.doctype, "share_name": doc.name}))
        return bool(native and hook or shared)  # reproduces native post-hook sharing

    def public_has_permission(self, doctype=None, ptype="read", doc=None, user=None,
                              throw=False, *, parent_doctype=None, debug=False,
                              ignore_share_permissions=False):
        # Exact Frappe public wrapper signature. In particular print_logs belongs
        # to frappe.permissions.has_permission, not to frappe.has_permission.
        return self.native_permission(doc, ptype, user)

    def fields(self, doctype, fields, permission="read", parenttype=None):
        self.calls.append(("fields", doctype, set(fields), parenttype))
        if any((doctype, field) in self.masked for field in fields):
            raise PermissionError("masked required field")

    def table(self, doc, field, child, fields):
        self.fields(doc.doctype, {field})
        self.fields(child, fields, parenttype=doc.doctype)
        rows = doc.get(field) or []
        if len({row.name for row in rows}) != len(rows):
            raise PermissionError("duplicate identity")
        for row in rows:
            if (row.parent, row.parenttype, row.parentfield) != (doc.name, doc.doctype, field):
                raise PermissionError("alien child")

    def quantity_fields(self, doctype, fields, **kwargs):
        self.fields(doctype, fields, parenttype=kwargs.get("parenttype"))

    def source(self, doctype, name):
        self.source_calls.append((doctype, name))
        if name in self.hidden:
            raise PermissionError("hidden source")

    def authorize(self, doc, active=False):
        self.calls.append(("authorize", doc.name, active))
        doc.check_permission("read")
        self.table(doc, "items", "Jifeng Sample Order Item", self.samples.ITEM_FIELDS)
        self.source("Company", doc.company)
        if doc.get("customer"):
            self.source("Customer", doc.customer)
        for row in doc.get("items") or []:
            self.source("Item", row.item_code)
            self.fields("Item", {"stock_uom"})
            if row.get("product_profile"):
                self.source("Jifeng Product Profile", row.product_profile)
            if row.get("bom"):
                self.source("BOM", row.bom)
        return types.SimpleNamespace(check=lambda: self.calls.append(("source_check", doc.name)))

    @staticmethod
    def approved(doc):
        if not doc.get("approved_digest"):
            raise ValueError("missing approval")

    def quotation_scope(self, doc):
        return bool(doc.get("jf_sample_order") or doc.get("jf_sample_request_id")
                    or doc.get("jf_sample_request_digest") or any(
            self.module._has_hint(row) for row in doc.get("items") or []))

    def quotation_receipt(self, doc):
        self.calls.append(("quote_receipt", doc.get("name")))
        if not doc.get("jf_sample_order"):
            raise ValueError("receipt requires exact sample parent")

    def quote_check(self, doc, active=False, lock=False):
        self.calls.append(("quote_source", doc.name, active, lock))
        doc.check_permission("read")
        sample = self.get_doc("Jifeng Sample Order", doc.jf_sample_order)
        if int(sample.get("docstatus") or 0) not in (1, 2):
            raise ValueError("sample history requires approval")
        # Match the historical service's two independent complete source reads.
        # A delegated controller token does not replace these business passes.
        for source in (sample, None):
            current = source or self.get_doc("Jifeng Sample Order", doc.jf_sample_order)
            self.authorize(current).check()
            self.approved(current)
            self.fields(current.doctype, self.samples.PARENT_FIELDS | self.samples.APPROVAL)
            current.check_permission("read")
        if self.samples._freeze(sample) != self.samples._freeze(current):
            raise ValueError("changed historical sample")
        for row in doc.get("items") or []:
            if not any(old.name == row.jf_sample_order_item for old in sample.get("items") or []):
                raise ValueError("wrong exact sample child")

    def quote_row(self, doc, row, **kwargs):
        if not row.get("prevdoc_docname") or not row.get("quotation_item"):
            raise ValueError("missing quote identity")
        quote = self.get_doc("Quotation", row.prevdoc_docname)
        self.quote_check(quote, active=kwargs.get("active", True), lock=kwargs.get("lock", False))
        self.quotation_receipt(quote)
        origin = next((old for old in quote.get("items") if old.name == row.quotation_item), None)
        if not origin or origin.item_code != row.item_code:
            raise ValueError("wrong exact quote row")
        return quote, origin

    def historical_sales_quote_row(self, order, row, quote):
        self.calls.append(("sales_quote_history", order.name, row.name, quote.name))
        self.quote_check(quote, active=False, lock=False)
        self.quotation_receipt(quote)
        origin = next((old for old in quote.get("items") if old.name == row.quotation_item), None)
        hints = ("jf_sample_order", "jf_sample_order_item", "jf_sample_source_digest", "jf_sample_source_snapshot")
        if (quote.name != row.prevdoc_docname or quote.docstatus not in (1, 2)
                or not origin or origin.item_code != row.item_code
                or any(row.get(field) != origin.get(field) for field in hints)):
            raise ValueError("wrong exact historical quote row")
        return quote, origin

    def flow_check(self, doc, active=False, lock=False):
        self.calls.append(("flow_source", doc.name, active, lock))
        if not doc.get("jf_sample_order"):
            raise ValueError("lost flow trace")
        order = self.get_doc("Sales Order", doc.sales_order)
        order.check_permission("read")
        if not any(row.name == doc.sales_order_item for row in order.get("items") or []):
            raise ValueError("wrong exact SO row")

    def receipt(self, doc):
        self.calls.append(("work_receipt", doc.name))
        self.get_doc("Jifeng Sample Order", doc.jf_sample_order).check_permission("read")

    def load(self):
        self.stack = ExitStack()
        fake = types.SimpleNamespace(
            session=Row(user=self.user), PermissionError=PermissionError,
            DoesNotExistError=KeyError, ValidationError=ValueError,
            throw=lambda message, error=ValueError: (_ for _ in ()).throw(error(message)),
            get_meta=lambda dt: self.metas[dt], get_doc=self.get_doc, get_all=self.get_all,
            has_permission=self.public_has_permission,
            db=types.SimpleNamespace(exists=self.exists, get_value=self.get_value,
                                     escape=lambda value: "'" + value.replace("'", "''") + "'"),
        )

        class Sources:
            def __init__(inner, company):
                self.calls.append(("sources", company))

            def bom(inner, name, item_code, active=True):
                self.calls.append(("bom", name, item_code, active))
                self.source("BOM", name)

            def _links(inner, doc, active=True):
                self.calls.append(("links", doc.get("name"), active))
                if doc.get("source_warehouse"):
                    self.source("Warehouse", doc.source_warehouse)

            def item(inner, name, **kw):
                self.source("Item", name)
                return Doc(item_code=name), None

            def _item_defaults(inner, item, active=True):
                self.calls.append(("defaults", item.item_code, active))

            def check(inner):
                self.calls.append(("generated_check",))

        self.samples = types.SimpleNamespace(
            _authorize=self.authorize, _approved=self.approved, _fields=self.fields,
            _table=self.table, _Sources=Sources, _receipt=self.receipt,
            _freeze=lambda doc: deepcopy(dict(doc)), _decimal=lambda value: Decimal(str(value)),
            PARENT_FIELDS={"company", "customer"}, APPROVAL={"approved_digest"},
            ITEM_FIELDS={"name", "item_code", "stock_uom", "qty", "sample_identity"},
            WO_REQUIRED={"name", "company", "production_item", "stock_uom", "qty", "bom_no"},
        )
        hints = ("jf_sample_order", "jf_sample_order_item", "jf_sample_source_digest", "jf_sample_source_snapshot")
        quotes = types.SimpleNamespace(
            _quotation_scope=self.quotation_scope, _receipt=self.quotation_receipt,
            _check_sources=self.quote_check, _quote_row=self.quote_row, SOURCE_FIELDS=hints,
            _historical_sales_quote_row=self.historical_sales_quote_row,
            _candidate=lambda row: bool(row.get("jf_sample_order")),
            _sample_flow_candidate=lambda doc: self.module._flow_scope(doc, frozenset(), None)
            or self.module._has_hint(doc), check_sample_flow_source=self.flow_check,
            QUOTE_FIELDS=("name", "company", "items", "jf_sample_order"),
            QUOTE_ITEM_FIELDS=("name", "item_code", "rate") + hints,
            SO_ITEM_FIELDS=("name", "item_code", "qty", "quotation_item") + hints,
            FLOW_SOURCE_FIELDS=hints + ("jf_source_quotation", "jf_source_quotation_item"),
        )
        flow = types.SimpleNamespace(check_quantity_fields=self.quantity_fields)
        modules = {"frappe": fake, "jifeng_erp.sample_orders": self.samples,
                   "jifeng_erp.sample_quotations": quotes, "jifeng_erp.flow_quantity": flow}
        self.stack.enter_context(patch.dict(sys.modules, modules))
        for name, value in (("sample_orders", self.samples), ("sample_quotations", quotes), ("flow_quantity", flow)):
            self.stack.enter_context(patch.object(jifeng_erp, name, value, create=True))
        spec = importlib.util.spec_from_file_location("sample_permissions_model",
            Path(__file__).parents[1] / "jifeng_erp/sample_source_permissions.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Doc.env = self
        return self.module


def child(doctype, parenttype, parent, name, **values):
    return Doc(doctype=doctype, parenttype=parenttype, parent=parent,
               parentfield="items", name=name, **values)


class SampleSourcePermissionTests(unittest.TestCase):
    def setUp(self):
        self.env = env = Environment()
        hint_names = {"jf_sample_order", "jf_sample_order_item", "jf_sample_source_digest",
                      "jf_sample_source_snapshot", "jf_sample_request_id", "jf_sample_request_digest",
                      "jf_source_quotation", "jf_source_quotation_item", "amended_from"}
        for dt, tables in (
            ("Jifeng Sample Order", [("items", "Jifeng Sample Order Item")]),
            ("Quotation", [("items", "Quotation Item"), ("taxes", "Sales Taxes and Charges")]),
            ("Sales Order", [("items", "Sales Order Item")]),
            ("Jifeng Flow Order", []),
            ("Work Order", [("required_items", "Work Order Item"), ("operations", "Work Order Operation")]),
            ("Sales Invoice", [("taxes", "Sales Taxes and Charges")]),
        ):
            env.metas[dt] = Meta(tables, hint_names | {"company", "sales_order", "jf_flow_order"})
        for dt in ("Jifeng Sample Order Item", "Quotation Item", "Sales Order Item", "Sales Taxes and Charges",
                   "Work Order Item", "Work Order Operation"):
            env.metas[dt] = Meta(fields=hint_names | {"item_code", "stock_uom", "qty", "rate",
                "prevdoc_docname", "quotation_item"}, istable=True)
        for dt in ("DocShare", "DocField", "Custom Field", "Property Setter", "Item"):
            env.metas[dt] = Meta()
        sample_rows = [child("Jifeng Sample Order Item", "Jifeng Sample Order", "S", "S1",
                             item_code="I", qty=1, stock_uom="Nos")]
        env.put(Doc(doctype="Jifeng Sample Order", name="S", company="C", docstatus=1,
                    approved_digest="approved", items=sample_rows))
        sources = dict(jf_sample_order="S", jf_sample_order_item="S1", jf_sample_source_digest="approved",
                       jf_sample_source_snapshot="snapshot")
        env.put(Doc(doctype="Quotation", name="Q", company="C", docstatus=1, jf_sample_order="S", taxes=[],
                    items=[child("Quotation Item", "Quotation", "Q", "Q1", item_code="I", **sources)]))
        env.put(Doc(doctype="Sales Order", name="SO", company="C", customer="K", docstatus=1,
                    items=[child("Sales Order Item", "Sales Order", "SO", "SO1", item_code="I",
                                 prevdoc_docname="Q", quotation_item="Q1", **sources)]))
        env.put(Doc(doctype="Jifeng Flow Order", name="F", company="C", item_code="I",
                    sales_order="SO", sales_order_item="SO1", **sources))
        env.put(Doc(doctype="Work Order", name="W", company="C", production_item="I", sales_order="SO",
                    jf_flow_order="F", bom_no="B", required_items=[], operations=[]))
        env.put(Doc(doctype="Quotation", name="PLAIN", company="C", items=[], taxes=[]))
        self.module = env.load()
        self.addCleanup(env.stack.close)

    def allowed(self, doctype, name, **kwargs):
        return self.module.sample_source_has_permission(self.env.get_doc(doctype, name), "read", **kwargs)

    def share(self, doctype, name):
        return Doc(doctype="DocShare", name="share", share_doctype=doctype, share_name=name, read=1)

    def test_complete_chain_and_recursion_token_restore(self):
        for dt, name in (("Jifeng Sample Order", "S"), ("Quotation", "Q"), ("Sales Order", "SO"),
                         ("Jifeng Flow Order", "F"), ("Work Order", "W")):
            self.assertTrue(self.allowed(dt, name), (dt, name))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertIn(("bom", "B", "I", False), self.env.calls)

    def test_hidden_item_denies_parent_child_and_query(self):
        self.env.hidden.add("I")
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertFalse(self.allowed("Quotation Item", "Q1"))
        sql = self.module.sample_source_query_conditions(doctype="Quotation")
        self.assertNotIn("'Q'", sql)
        self.assertIn("'PLAIN'", sql)

    def test_required_mask_and_other_users_deny(self):
        self.env.masked.add(("Item", "stock_uom"))
        self.assertFalse(self.allowed("Sales Order", "SO"))
        self.assertFalse(self.allowed("Quotation", "Q", user="another-user"))
        self.assertFalse(self.allowed("Quotation", "Q", user="Guest"))
        self.assertTrue(self.allowed("Quotation", "PLAIN", user="another-user"))

    def test_native_self_user_permission_is_not_bypassed_by_context(self):
        self.env.hidden.add("Q")
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_dirty_draft_payload_checks_persistent_before_after(self):
        dirty = self.env.get_doc("Quotation", "Q")
        dirty.get("items")[0]["rate"] = 999
        dirty.company = "other-input-company"
        self.assertTrue(self.module.sample_source_has_permission(dirty, "write"))
        # This gate authorizes the existing source; business validate rejects
        # forbidden edits and authorizes any proposed new source separately.

    def test_new_sample_nameless_rows_do_not_require_approval_or_uuid(self):
        doc = Doc(doctype="Jifeng Sample Order", name=None, __islocal=1, company="C", docstatus=0,
                  items=[Doc(name=None, item_code="I"), Doc(name=None, item_code="I")])
        self.assertTrue(self.module.sample_source_has_permission(doc, "create"))
        self.assertFalse(any(call[0] == "authorize" for call in self.env.calls))

    def test_saved_sample_draft_needs_sources_but_no_approval(self):
        draft = self.env.get_doc("Jifeng Sample Order", "S")
        draft.docstatus, draft.approved_digest = 0, None
        self.env.put(draft)
        self.assertTrue(self.allowed("Jifeng Sample Order", "S"))
        self.env.hidden.add("I")
        self.assertFalse(self.allowed("Jifeng Sample Order", "S"))

    def test_plain_unrelated_mask_does_not_add_source_requirement(self):
        self.env.masked.add(("Quotation", "commission_rate"))
        self.assertTrue(self.allowed("Quotation", "Q"))
        self.assertTrue(self.allowed("Quotation", "PLAIN"))

    def test_ordinary_user_permission_hidden_name_is_not_in_query_sql(self):
        self.env.hidden.add("PLAIN")
        sql = self.module.sample_source_query_conditions(doctype="Quotation")
        self.assertNotIn("'PLAIN'", sql)
        self.assertIn("'Q'", sql)

    def test_ordinary_select_only_link_name_remains_available(self):
        self.env.denied_roles.add(("Quotation", "read"))
        sql = self.module.sample_source_query_conditions(doctype="Quotation")
        self.assertIn("'PLAIN'", sql)
        self.assertNotIn("'Q'", sql)  # protected sources need complete read

    def test_ordinary_without_read_or_select_is_absent_from_query_sql(self):
        self.env.denied_roles.update({("Quotation", "read"), ("Quotation", "select")})
        self.assertEqual(self.module.sample_source_query_conditions(doctype="Quotation"), "1=0")

    def test_one_lost_so_row_cannot_hide_behind_another_good_row(self):
        order = self.env.get_doc("Sales Order", "SO")
        order.get("items").append(child("Sales Order Item", "Sales Order", "SO", "LOST", item_code="I",
                                 prevdoc_docname="Q", quotation_item=None))
        self.env.put(order)
        self.assertFalse(self.allowed("Sales Order", "SO"))

    def test_new_native_so_mapper_can_await_no_copy_inheritance(self):
        doc = Doc(doctype="Sales Order", name=None, __islocal=1, company="C", customer="K",
                  items=[Doc(name=None, item_code="I", prevdoc_docname="Q", quotation_item="Q1")])
        self.assertTrue(self.module.sample_source_has_permission(doc, "create"))
        self.env.hidden.add("I")
        self.assertFalse(self.module.sample_source_has_permission(doc, "create"))

    def test_amendment_chain_cannot_drop_scope(self):
        doc = Doc(doctype="Quotation", name="A", amended_from="Q", items=[], taxes=[])
        self.env.put(doc)
        self.assertFalse(self.allowed("Quotation", "A"))
        fresh = Doc(doctype="Quotation", name=None, __islocal=1, amended_from="Q", items=[], taxes=[])
        self.assertTrue(self.module.sample_source_has_permission(fresh, "create"))
        self.env.hidden.add("I")
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))

    def _quote_chain(self):
        previous = "Q"
        for name in ("A", "B", "C"):
            doc = self.env.get_doc("Quotation", "Q")
            doc.name, doc.amended_from = name, previous
            for row in doc.get("items"):
                row.name, row.parent = name + "1", name
            self.env.put(doc)
            previous = name

    def test_amendment_ancestors_each_keep_one_complete_gate(self):
        self._quote_chain()
        with (
            patch.object(self.module, "_gate", wraps=self.module._gate) as gate,
            patch.object(self.module, "_native_read", wraps=self.module._native_read) as native,
            patch.object(self.module, "_complete_fields", wraps=self.module._complete_fields) as fields,
            patch.object(self.module, "_storage_identity", wraps=self.module._storage_identity) as identity,
            patch.object(self.module, "_unshared", wraps=self.module._unshared) as share,
            patch.object(self.env.samples, "_freeze", wraps=self.env.samples._freeze) as freeze,
        ):
            self.assertTrue(self.allowed("Quotation", "C"))
        self.assertEqual([call.args[0].name for call in gate.call_args_list], ["Q", "A", "B", "C"])
        for name in ("Q", "A", "B", "C"):
            def count(mock):
                return len([call for call in mock.call_args_list
                            if call.args[0].doctype == "Quotation" and call.args[0].name == name])

            with self.subTest(ancestor=name):
                self.assertEqual(count(native), 2)
                self.assertEqual(count(fields), 1)
                self.assertGreaterEqual(count(identity), 1)
                self.assertEqual(count(share), 2)
                self.assertEqual(count(freeze), 2)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_each_hidden_or_shared_amendment_ancestor_is_denied(self):
        self._quote_chain()
        for name in ("Q", "A", "B"):
            with self.subTest(hidden_ancestor=name):
                self.env.hidden = {name}
                self.assertFalse(self.allowed("Quotation", "C"))
                self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.env.hidden.clear()
            with self.subTest(shared_ancestor=name):
                self.env.put(self.share("Quotation", name))
                self.assertFalse(self.allowed("Quotation", "C"))
                self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.env.records.pop(("DocShare", "share"))

    def test_ancestor_raw_child_identity_and_late_freeze_still_deny(self):
        self._quote_chain()
        self.env.records["Quotation Item", "A1"].parenttype = "Sales Order"
        self.assertFalse(self.allowed("Quotation", "C"))
        self.env.records["Quotation Item", "A1"].parenttype = "Quotation"
        check = self.env.quote_check

        def change_ancestor_after_history(doc, active=False, lock=False):
            check(doc, active, lock)
            if doc.name == "A":
                self.env.records["Quotation", "A"].modified = "changed"

        with patch.object(sys.modules["jifeng_erp.sample_quotations"], "_check_sources",
                          side_effect=change_ancestor_after_history):
            self.assertFalse(self.allowed("Quotation", "C"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_missing_or_cyclic_deep_ancestor_denies_before_any_business_gate(self):
        self._quote_chain()
        for name in ("MISSING", "B", "Q"):
            self.env.records["Quotation", "Q"].amended_from = name
            with self.subTest(deep_link=name):
                with patch.object(self.module, "_gate", wraps=self.module._gate) as gate:
                    self.assertFalse(self.allowed("Quotation", "C"))
                gate.assert_not_called()
                self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_plain_intermediate_cannot_hide_older_protected_ancestor(self):
        middle = self.env.get_doc("Quotation", "PLAIN")
        middle.name, middle.amended_from = "MIDDLE", "Q"
        self.env.put(middle)
        leaf = self.env.get_doc("Quotation", "Q")
        leaf.name, leaf.amended_from = "LEAF", "MIDDLE"
        for row in leaf.get("items"):
            row.name, row.parent = "LEAF1", "LEAF"
        self.env.put(leaf)
        with patch.object(self.module, "_gate", wraps=self.module._gate) as gate:
            self.assertFalse(self.allowed("Quotation", "LEAF"))
        self.assertEqual([call.args[0].name for call in gate.call_args_list], ["Q", "MIDDLE"])
        self.assertIn(("quote_source", "Q", False, False), self.env.calls)
        self.env.hidden.add("Q")
        self.assertFalse(self.allowed("Quotation", "LEAF"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_all_plain_ancestors_do_not_add_source_native_permissions(self):
        older = self.env.get_doc("Quotation", "PLAIN")
        older.name = "OLDER"
        self.env.put(older)
        self.env.records["Quotation", "PLAIN"].amended_from = "OLDER"
        leaf = self.env.get_doc("Quotation", "Q")
        leaf.amended_from = "PLAIN"
        self.env.put(leaf)
        self.env.hidden.update(("PLAIN", "OLDER"))
        with patch.object(self.module, "_gate", wraps=self.module._gate) as gate:
            self.assertTrue(self.allowed("Quotation", "Q"))
        self.assertEqual([call.args[0].name for call in gate.call_args_list], ["Q"])

    def test_new_request_scoped_quote_amendment_can_await_server_source_inheritance(self):
        original = self.env.get_doc("Quotation", "Q")
        original.docstatus = 2
        original.jf_sample_request_id = "old-request"
        original.jf_sample_request_digest = "0" * 64
        self.env.put(original)
        fresh = Doc(doctype="Quotation", name=None, __islocal=1, company="C", docstatus=0,
                    amended_from="Q", jf_sample_order=None,
                    jf_sample_request_id=original.jf_sample_request_id,
                    jf_sample_request_digest=original.jf_sample_request_digest,
                    items=[Doc(doctype="Quotation Item", name=None, item_code="I",
                               _amended_from="Q1", jf_sample_order=None,
                               jf_sample_order_item=None, jf_sample_source_digest=None,
                               jf_sample_source_snapshot=None)], taxes=[])
        self.assertTrue(self.module.sample_source_has_permission(fresh, "create"))
        self.assertIn(("quote_receipt", "Q"), self.env.calls)
        self.assertNotIn(("quote_receipt", None), self.env.calls)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_new_request_scoped_quote_amendment_still_needs_complete_origin_permission(self):
        fresh = Doc(doctype="Quotation", name=None, __islocal=1, company="C", docstatus=0,
                    amended_from="Q", jf_sample_order=None,
                    jf_sample_request_id="old-request", jf_sample_request_digest="0" * 64,
                    items=[], taxes=[])
        self.env.hidden.add("I")
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.env.hidden.clear()
        self.env.masked.add(("Jifeng Sample Order Item", "qty"))
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.env.masked.clear()
        self.env.denied_roles.add(("Quotation", "read"))
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.env.denied_roles.clear()
        self.env.put(self.share("Quotation", "Q"))
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.env.records.pop(("DocShare", "share"))
        fresh.amended_from = "MISSING"
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_saved_request_scoped_quote_amendment_cannot_keep_uninherited_sources(self):
        saved = Doc(doctype="Quotation", name="AMENDED", company="C", docstatus=0,
                    amended_from="Q", jf_sample_order=None,
                    jf_sample_request_id="old-request", jf_sample_request_digest="0" * 64,
                    items=[], taxes=[])
        self.env.put(saved)
        self.assertFalse(self.allowed("Quotation", "AMENDED"))
        self.assertNotIn("'AMENDED'", self.module.sample_source_query_conditions(doctype="Quotation"))
        self.assertIn(("quote_receipt", "AMENDED"), self.env.calls)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_new_quote_amendment_pending_normalization_cannot_bypass_mask_or_named_share(self):
        fresh = Doc(doctype="Quotation", name="FUTURE", __islocal=1, company="C", docstatus=0,
                    amended_from="Q", jf_sample_order=None,
                    jf_sample_request_id="old-request", jf_sample_request_digest="0" * 64,
                    items=[], taxes=[])
        self.env.masked.add(("Quotation", "jf_sample_request_id"))
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.env.masked.clear()
        self.env.put(self.share("Quotation", "FUTURE"))
        self.assertFalse(self.module.sample_source_has_permission(fresh, "create"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_invalid_child_identity_closes_parent_join_for_plain_and_sample(self):
        self.env.records["Quotation Item", "ALIEN"] = child("Quotation Item", "Sales Order", "PLAIN", "ALIEN")
        self.assertFalse(self.allowed("Quotation", "PLAIN"))
        self.assertNotIn("'PLAIN'", self.module.sample_source_query_conditions(doctype="Quotation"))
        self.env.records["Quotation Item", "ALIEN"]["parent"] = "Q"
        self.assertFalse(self.allowed("Quotation", "Q"))

    def test_hidden_malformed_row_still_marks_parent_as_derived(self):
        self.env.records["Quotation Item", "BAD"] = child("Quotation Item", "other-parent", "PLAIN", "BAD",
                                                          jf_sample_order="S")
        self.assertTrue(self.module._scope(self.env.get_doc("Quotation", "PLAIN")))
        self.assertFalse(self.allowed("Quotation", "PLAIN"))

    def test_wrong_parentfield_direct_child_denies(self):
        row = self.env.get_doc("Quotation Item", "Q1")
        row.parentfield = "taxes"
        self.assertFalse(self.module.sample_source_has_permission(row, "read"))

    def test_shared_child_type_retains_ordinary_invoice_behavior(self):
        row = child("Sales Taxes and Charges", "Sales Invoice", "INV", "TAX")
        row.parentfield = "taxes"
        self.env.put(Doc(doctype="Sales Invoice", name="INV", taxes=[row]))
        self.assertTrue(self.allowed("Sales Taxes and Charges", "TAX"))
        self.module.before_validate_docshare(self.share("Sales Taxes and Charges", "TAX"))
        sql = self.module.sample_child_query_conditions(doctype="Sales Taxes and Charges")
        self.assertIn("'Sales Invoice'", sql)
        self.assertIn("'taxes'", sql)
        self.assertIn("'INV'", sql)

    def test_child_query_uses_exact_parent_type_field_and_permission_set(self):
        sql = self.module.sample_child_query_conditions(doctype="Quotation Item")
        self.assertIn("`parenttype`='Quotation'", sql)
        self.assertIn("`parentfield` in ('items')", sql)
        self.env.hidden.add("I")
        self.assertNotIn("'Q'", self.module.sample_child_query_conditions(doctype="Quotation Item"))

    def test_derived_sharing_denies_but_plain_sharing_remains_native(self):
        for dt, name in (("Jifeng Sample Order", "S"), ("Quotation", "Q"), ("Quotation Item", "Q1"),
                         ("Sales Order", "SO"), ("Jifeng Flow Order", "F"), ("Work Order", "W")):
            with self.assertRaisesRegex(PermissionError, "不能独立分享") as error:
                self.module.before_validate_docshare(self.share(dt, name))
            self.assertNotIn(name, str(error.exception))
        self.module.before_validate_docshare(self.share("Quotation", "PLAIN"))

    def test_validate_share_accepts_direct_and_native_event_argument_layouts(self):
        ordinary = self.env.get_doc("Quotation", "PLAIN")
        share = self.share("Quotation", "PLAIN")
        self.module.validate_sample_source_share(ordinary, share)
        self.module.validate_sample_source_share(ordinary, "validate_share", share)
        with self.assertRaises(PermissionError):
            self.module.validate_sample_source_share(self.env.get_doc("Quotation", "Q"),
                                                      "validate_share", self.share("Quotation", "Q"))

    def test_binding_checks_same_record_lock_and_current_share_read(self):
        self.env.put(self.share("Quotation", "PLAIN"))
        current = self.env.get_doc("Quotation", "PLAIN")
        current.jf_sample_order = "S"
        with self.assertRaises(PermissionError):
            self.module.before_validate_source_binding(current)
        self.assertIn(("Quotation", "PLAIN", "value"), self.env.locks)
        self.assertIn(("Quotation", "PLAIN", "doc"), self.env.locks)
        self.assertTrue(any(dt == "DocShare" and kind == "value" for dt, _, kind in self.env.locks))

    def test_source_upgrade_checks_preexisting_indirect_shared_work_order(self):
        order = self.env.get_doc("Sales Order", "SO")
        for row in order.get("items"):
            for field in self.module.HINTS:
                row[field] = None
            row.prevdoc_docname, row.quotation_item = None, None
        self.env.put(order)
        flow = self.env.get_doc("Jifeng Flow Order", "F")
        for field in self.module.HINTS:
            flow[field] = None
        self.env.put(flow)
        self.env.put(self.share("Work Order", "W"))
        self.assertFalse(self.module._scope(self.env.get_doc("Work Order", "W")))
        amended = self.env.get_doc("Sales Order", "SO")
        amended.get("items")[0].prevdoc_docname, amended.get("items")[0].quotation_item = "Q", "Q1"
        with self.assertRaises(PermissionError):
            self.module.before_validate_source_binding(amended)

    def test_native_share_override_requires_deployment_preflight(self):
        self.env.put(self.share("Quotation", "Q"))  # deliberately violates guarded writes
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertTrue(self.env.native_permission(self.env.get_doc("Quotation", "Q")))
        with self.assertRaises(PermissionError):
            self.module.assert_sample_share_invariant()
        with self.assertRaises(PermissionError):
            self.module.sample_source_query_conditions(doctype="Quotation")
        # Native share-only lists bypass PQC altogether. The module explicitly
        # requires preflight plus mediated share/binding writes, not just hooks.

    def test_controller_mixin_is_final_after_native_share_override(self):
        self.env.put(self.share("Quotation", "Q"))
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        self.assertFalse(doc.has_permission("read"))

    def test_mixin_receipt_runs_complete_gate_once_per_call_not_per_request(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        for expected in (1, 2):
            self.assertTrue(doc.has_permission("read"))
            checks = [call for call in self.env.calls if call[0:2] == ("quote_source", "Q")]
            self.assertEqual(len(checks), expected)
            self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_quote_delegation_keeps_two_complete_history_passes_without_extra_sample_gate(self):
        with patch.object(self.module, "_gate", wraps=self.module._gate) as gate:
            self.assertTrue(self.allowed("Quotation", "Q"))
        self.assertEqual([call.args[0].doctype for call in gate.call_args_list], ["Quotation"])
        self.assertEqual(len([call for call in self.env.calls
                              if call[0:2] == ("authorize", "S")]), 2)
        self.assertEqual(len([call for call in self.env.calls
                              if call[0:2] == ("source_check", "S")]), 2)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_saved_so_fixed_history_keeps_complete_gates_and_removes_external_reentry(self):
        with (
            patch.object(self.module, "_gate", wraps=self.module._gate) as gate,
            patch.object(self.module, "_native_read", wraps=self.module._native_read) as native,
            patch.object(self.module, "_storage_identity", wraps=self.module._storage_identity) as identity,
            patch.object(self.module, "_complete_fields", wraps=self.module._complete_fields) as fields,
        ):
            self.assertTrue(self.allowed("Sales Order", "SO"))
        self.assertEqual([call.args[0].doctype for call in gate.call_args_list], ["Sales Order", "Quotation"])
        self.assertEqual(self.env.calls.count(("sales_quote_history", "SO", "SO1", "Q")), 1)
        self.assertEqual(self.env.calls.count(("quote_source", "Q", False, False)), 1)
        self.assertEqual(len([call for call in self.env.calls if call[0:2] == ("authorize", "S")]), 2)
        for doctype in ("Quotation", "Jifeng Sample Order"):
            with self.subTest(complete_gate=doctype):
                self.assertEqual(len([call for call in native.call_args_list
                                      if call.args[0].doctype == doctype]), 2)
                self.assertEqual(len([call for call in identity.call_args_list
                                      if call.args[0].doctype == doctype]), 2)
                self.assertEqual(len([call for call in fields.call_args_list
                                      if call.args[0].doctype == doctype]), 2)
        self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
        self.assertEqual(self.module._SALES_HISTORY.get(), ())
        self.env.calls.clear()
        order = self.env.get_doc("Sales Order", "SO")
        with patch.object(self.module, "_gate", wraps=self.module._gate) as external_gate:
            self.env.quote_row(order, order.get("items")[0], active=False, lock=False)
        self.assertEqual(self.env.calls.count(("quote_source", "Q", False, False)), 2)
        self.assertEqual(len([call for call in external_gate.call_args_list
                              if call.args[0].doctype == "Jifeng Sample Order"]), 4)

    def test_saved_so_history_cannot_open_from_json_old_read_or_same_name_copy(self):
        order = self.env.get_doc("Sales Order", "SO")
        row = order.get("items")[0]
        order._sales_history = {"user": self.env.user, "order": "SO", "row": "SO1"}
        for phase in ("before_read", "after_read"):
            with self.subTest(phase=phase):
                with self.assertRaises(PermissionError):
                    self.module._saved_sales_quote_history(order, row, self.env.user)
            self.assertTrue(self.allowed("Sales Order", "SO"))
        token = self.module._AUTHORIZING.set({self.module._key(order, self.env.user)})
        documents = self.module._CHECKING_DOCUMENTS.set(((order, self.env.user),))
        try:
            duplicate = Doc(deepcopy(order))
            with self.assertRaises(PermissionError):
                self.module._saved_sales_quote_history(duplicate, duplicate.get("items")[0], self.env.user)
            with self.assertRaises(PermissionError):
                self.module._saved_sales_quote_history(order, row, "other-user")
        finally:
            self.module._CHECKING_DOCUMENTS.reset(documents)
            self.module._AUTHORIZING.reset(token)
        self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_saved_so_history_frame_rejects_wrong_row_identity_and_dirty_loaded_row(self):
        helper = self.module._saved_sales_quote_history

        for change in ("copy", "parenttype", "parentfield", "dirty"):
            def corrupt_before_frame(order, row, user):
                if change == "copy":
                    row = Doc(deepcopy(row))
                elif change == "dirty":
                    row.qty = 99
                else:
                    row[change] = "foreign"
                return helper(order, row, user)

            self.env.calls.clear()
            with self.subTest(change=change), patch.object(
                    self.module, "_saved_sales_quote_history", side_effect=corrupt_before_frame):
                self.assertFalse(self.allowed("Sales Order", "SO"))
            self.assertFalse(any(call[0] == "sales_quote_history" for call in self.env.calls))
            self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
            self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_saved_so_history_checks_nonselected_quote_and_sample_sources(self):
        sample = self.env.get_doc("Jifeng Sample Order", "S")
        sample.get("items").append(child("Jifeng Sample Order Item", "Jifeng Sample Order", "S", "S2",
                                         item_code="OTHER_ITEM", qty=1, stock_uom="Nos"))
        self.env.put(sample)
        quote = self.env.get_doc("Quotation", "Q")
        second = Doc(deepcopy(quote.get("items")[0]))
        second.name, second.item_code, second.jf_sample_order_item = "Q2", "OTHER_ITEM", "S2"
        quote.get("items").append(second)
        self.env.put(quote)
        self.assertTrue(self.allowed("Sales Order", "SO"))
        self.env.hidden.add("OTHER_ITEM")
        self.assertFalse(self.allowed("Sales Order", "SO"))
        self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_saved_so_history_preserves_quote_and_sample_ancestor_gates(self):
        old_sample = self.env.get_doc("Jifeng Sample Order", "S")
        old_sample.name = "OLD_SAMPLE"
        for row in old_sample.get("items"):
            row.name, row.parent, row.item_code = "OLD_SAMPLE_ROW", "OLD_SAMPLE", "OLD_ITEM"
        self.env.put(old_sample)
        self.env.records["Jifeng Sample Order", "S"].amended_from = "OLD_SAMPLE"
        old_quote = self.env.get_doc("Quotation", "Q")
        old_quote.name, old_quote.jf_sample_order = "OLD_QUOTE", "OLD_SAMPLE"
        for row in old_quote.get("items"):
            row.name, row.parent, row.item_code = "OLD_QUOTE_ROW", "OLD_QUOTE", "OLD_ITEM"
            row.jf_sample_order, row.jf_sample_order_item = "OLD_SAMPLE", "OLD_SAMPLE_ROW"
        self.env.put(old_quote)
        self.env.records["Quotation", "Q"].amended_from = "OLD_QUOTE"
        self.assertTrue(self.allowed("Sales Order", "SO"))
        for hidden in ("OLD_QUOTE", "OLD_SAMPLE", "OLD_ITEM"):
            with self.subTest(hidden=hidden):
                self.env.hidden = {hidden}
                self.assertFalse(self.allowed("Sales Order", "SO"))
                self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
                self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_saved_so_history_final_freeze_identity_share_and_fields_are_not_skipped(self):
        body = self.env.historical_sales_quote_row
        for change in ("quote_freeze", "sample_freeze", "quote_identity", "quote_share", "quote_mask", "dirty_row"):
            def mutate_after_body(order, row, quote):
                result = body(order, row, quote)
                if change == "quote_freeze":
                    self.env.records["Quotation", "Q"].modified = "changed"
                elif change == "sample_freeze":
                    self.env.records["Jifeng Sample Order", "S"].modified = "changed"
                elif change == "quote_identity":
                    self.env.records["Quotation Item", "ALIEN"] = child("Quotation Item", "Sales Order", "Q", "ALIEN")
                elif change == "quote_share":
                    self.env.put(self.share("Quotation", "Q"))
                elif change == "quote_mask":
                    self.env.masked.add(("Quotation Item", "rate"))
                else:
                    row.qty = 99
                return result

            with self.subTest(change=change), patch.object(
                    sys.modules["jifeng_erp.sample_quotations"], "_historical_sales_quote_row",
                    side_effect=mutate_after_body):
                self.assertFalse(self.allowed("Sales Order", "SO"))
            self.env.records["Quotation", "Q"].pop("modified", None)
            self.env.records["Jifeng Sample Order", "S"].pop("modified", None)
            self.env.records.pop(("Quotation Item", "ALIEN"), None)
            self.env.records.pop(("DocShare", "share"), None)
            self.env.masked.clear()
            self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
            self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_saved_so_history_recursive_body_cannot_return_pair_or_complete_proof(self):
        mixed = type("ProtectedSO", (self.module.SampleSourcePermissionMixin, Doc), {})
        order = mixed(self.env.get_doc("Sales Order", "SO"))

        def recurse_before_body(doc, row, quote):
            self.assertTrue(self.module._PERMISSION_CALLS.get())
            self.assertIsNone(self.module._PERMISSION_CALLS.get()[0]["result"])
            return self.module._saved_sales_quote_history(doc, row, self.env.user)

        with patch.object(sys.modules["jifeng_erp.sample_quotations"], "_historical_sales_quote_row",
                          side_effect=recurse_before_body):
            self.assertFalse(order.has_permission("read"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
        self.assertEqual(self.module._SALES_HISTORY.get(), ())
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_saved_so_history_is_closed_during_native_and_ancestor_prechecks(self):
        native, origins = self.module._native_read, self.module._origins
        stages = []

        def attempt(doc, stage):
            if doc.doctype == "Sales Order":
                stages.append(stage)
                self.assertFalse(any(current is doc for current, _ in self.module._CHECKING_DOCUMENTS.get()))
                with self.assertRaises(PermissionError):
                    self.module._saved_sales_quote_history(doc, doc.get("items")[0], self.env.user)

        def native_check(doc, user):
            attempt(doc, "native")
            return native(doc, user)

        def origin_check(doc):
            attempt(doc, "origins")
            return origins(doc)

        with (
            patch.object(self.module, "_native_read", side_effect=native_check),
            patch.object(self.module, "_origins", side_effect=origin_check),
        ):
            self.assertTrue(self.allowed("Sales Order", "SO"))
        self.assertEqual(stages, ["native", "origins", "native"])
        self.assertEqual(self.env.calls.count(("sales_quote_history", "SO", "SO1", "Q")), 1)

    def test_saved_so_parent_body_spans_nested_final_checks_but_their_own_body_is_closed(self):
        native = self.module._native_read
        reads = {}
        checked = []

        def observe_final(doc, user):
            reads[doc.doctype] = reads.get(doc.doctype, 0) + 1
            if doc.doctype in ("Quotation", "Jifeng Sample Order") and reads[doc.doctype] == 2:
                frames = self.module._CHECKING_DOCUMENTS.get()
                self.assertTrue(any(current.doctype == "Sales Order" for current, _ in frames))
                self.assertFalse(any(current is doc for current, _ in frames))
                with self.assertRaises(PermissionError):
                    self.module._saved_sales_quote_history(doc, Doc(name="SO1"), user)
                checked.append(doc.doctype)
            return native(doc, user)

        with patch.object(self.module, "_native_read", side_effect=observe_final):
            self.assertTrue(self.allowed("Sales Order", "SO"))
        self.assertEqual(checked, ["Jifeng Sample Order", "Quotation"])
        self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())

    def test_saved_so_fixed_body_exceptions_clear_all_activity_and_permission_frames(self):
        mixed = type("ProtectedSO", (self.module.SampleSourcePermissionMixin, Doc), {})
        order = mixed(self.env.get_doc("Sales Order", "SO"))
        for error in (ValueError("business rejected"), RuntimeError("unexpected failure")):
            with self.subTest(error=type(error).__name__), patch.object(
                    sys.modules["jifeng_erp.sample_quotations"], "_historical_sales_quote_row", side_effect=error):
                if isinstance(error, ValueError):
                    self.assertFalse(order.has_permission("read"))
                else:
                    with self.assertRaises(RuntimeError):
                        order.has_permission("read")
            self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
            self.assertEqual(self.module._SALES_HISTORY.get(), ())
            self.assertEqual(self.module._PERMISSION_CALLS.get(), ())
        self.assertTrue(order.has_permission("read"))

    def test_quote_delegation_requires_exact_active_quote_and_current_user(self):
        sample = self.env.get_doc("Jifeng Sample Order", "S")
        quote = self.env.get_doc("Quotation", "Q")
        with self.assertRaises(PermissionError):
            self.module._check(sample, self.env.user, quotation=quote)
        token = self.module._AUTHORIZING.set({self.module._key(quote, self.env.user)})
        try:
            with self.assertRaises(PermissionError):
                self.module._check(sample, "other-user", quotation=quote)
            for changes in ({"jf_sample_order": "OTHER"}, {"doctype": "Sales Order"}):
                wrong = Doc(quote)
                wrong.update(changes)
                with self.assertRaises(PermissionError):
                    self.module._check(sample, self.env.user, quotation=wrong)
            local_sample = Doc(sample, __islocal=1)
            with self.assertRaises(PermissionError):
                self.module._check(local_sample, self.env.user, quotation=quote)
        finally:
            self.module._AUTHORIZING.reset(token)
        self.assertFalse(self.env.calls)

    def test_quote_delegation_preserves_native_source_mask_and_approval_denials(self):
        for hidden in ("S", "I", "C", "K"):
            sample = self.env.get_doc("Jifeng Sample Order", "S")
            sample.customer = "K"
            self.env.put(sample)
            self.env.hidden = {hidden}
            with self.subTest(hidden=hidden):
                self.assertFalse(self.allowed("Quotation", "Q"))
            self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.env.hidden.clear()
        self.env.masked.add(("Jifeng Sample Order Item", "qty"))
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.env.masked.clear()
        sample = self.env.get_doc("Jifeng Sample Order", "S")
        sample.docstatus = 0
        self.env.put(sample)
        self.assertFalse(self.allowed("Quotation", "Q"))

    def test_quote_delegation_second_history_pass_rechecks_source_permission(self):
        authorize = self.env.authorize

        def hide_after_first_pass(doc, active=False):
            sources = authorize(doc, active)
            self.env.hidden.add("I")
            return sources

        with patch.object(self.env, "authorize", side_effect=hide_after_first_pass):
            self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_quote_delegation_final_freeze_detects_source_change(self):
        check = self.env.quote_check

        def change_after_history(doc, active=False, lock=False):
            check(doc, active, lock)
            self.env.records["Jifeng Sample Order", "S"]["modified"] = "changed"

        with patch.object(sys.modules["jifeng_erp.sample_quotations"], "_check_sources",
                          side_effect=change_after_history):
            self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())

    def test_quote_delegation_final_identity_share_and_mask_are_not_skipped(self):
        check = self.env.quote_check
        for alteration in ("identity", "share", "mask"):
            def alter_after_history(doc, active=False, lock=False):
                check(doc, active, lock)
                if alteration == "identity":
                    self.env.records["Jifeng Sample Order Item", "ALIEN"] = child(
                        "Jifeng Sample Order Item", "Quotation", "S", "ALIEN")
                elif alteration == "share":
                    self.env.put(self.share("Jifeng Sample Order", "S"))
                else:
                    self.env.masked.add(("Jifeng Sample Order Item", "qty"))

            with self.subTest(alteration=alteration):
                with patch.object(sys.modules["jifeng_erp.sample_quotations"], "_check_sources",
                                  side_effect=alter_after_history):
                    self.assertFalse(self.allowed("Quotation", "Q"))
                self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
            self.env.records.pop(("Jifeng Sample Order Item", "ALIEN"), None)
            self.env.records.pop(("DocShare", "share"), None)
            self.env.masked.clear()

    def test_quote_delegation_keeps_quote_validation_inside_recursive_sample_gate(self):
        sample = self.env.get_doc("Jifeng Sample Order", "S")
        quote = self.env.get_doc("Quotation", "Q")
        frame = {"key": (id(quote), "read", self.env.user), "result": None, "subject": None}
        token = self.module._AUTHORIZING.set({self.module._key(sample, self.env.user),
                                             self.module._key(quote, self.env.user)})
        calls = self.module._PERMISSION_CALLS.set((frame,))
        try:
            self.module._check(sample, self.env.user, quotation=quote)
            self.assertEqual(len([call for call in self.env.calls
                                  if call[0:2] == ("quote_source", "Q")]), 1)
            self.assertIsNone(frame["result"])  # the enclosing gates are still incomplete
            self.env.hidden.add("I")
            with self.assertRaises(PermissionError):
                self.module._check(sample, self.env.user, quotation=quote)
        finally:
            self.module._PERMISSION_CALLS.reset(calls)
            self.module._AUTHORIZING.reset(token)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_quote_delegation_missing_source_preserves_refusal_and_context_restore(self):
        quote = self.env.get_doc("Quotation", "Q")
        quote.jf_sample_order = "MISSING"
        self.env.put(quote)
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_mixin_fallback_when_native_controller_hook_is_skipped(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        self.env.hidden.add("I")
        with patch.object(self.env, "native_permission", return_value=True):
            self.assertFalse(doc.has_permission("read"))
        self.assertTrue(any(call[0:2] == ("quote_source", "Q") for call in self.env.calls))
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_mixin_final_share_scan_after_completed_source_gate(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        native = self.env.native_permission

        def grant_after_hook(subject, permtype="read", user=None):
            result = native(subject, permtype, user)
            if subject is doc:
                self.env.put(self.share("Quotation", "Q"))
            return result

        with patch.object(self.env, "native_permission", side_effect=grant_after_hook):
            self.assertFalse(doc.has_permission("read"))
        self.assertEqual(len([call for call in self.env.calls
                              if call[0:2] == ("quote_source", "Q")]), 1)
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_mixin_does_not_reuse_another_document_hook_result(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        ordinary = self.env.get_doc("Quotation", "PLAIN")
        self.env.hidden.add("I")

        def wrong_document_hook(subject, permtype="read", user=None):
            if subject is doc:
                self.assertTrue(self.module.sample_source_has_permission(ordinary, permtype, user))
            return True

        with patch.object(self.env, "native_permission", side_effect=wrong_document_hook):
            self.assertFalse(doc.has_permission("read"))
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_mixin_false_receipt_is_not_revived_by_native_share(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        self.env.hidden.add("I")
        native = self.env.native_permission

        def revive_after_denial(subject, permtype="read", user=None):
            result = native(subject, permtype, user)
            if subject is doc:
                self.assertFalse(result)
                self.env.put(self.share("Quotation", "Q"))
                return True
            return result

        with patch.object(self.env, "native_permission", side_effect=revive_after_denial):
            self.assertFalse(doc.has_permission("read"))
        self.assertEqual(len([call for call in self.env.calls
                              if call[0:2] == ("quote_source", "Q")]), 1)
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_mixin_receipt_context_restores_on_native_exception(self):
        mixed = type("ProtectedDoc", (self.module.SampleSourcePermissionMixin, Doc), {})
        doc = mixed(self.env.get_doc("Quotation", "Q"))
        with patch.object(self.env, "native_permission", side_effect=RuntimeError("native failure")):
            with self.assertRaisesRegex(RuntimeError, "native failure"):
                doc.has_permission("read")
        self.assertEqual(self.module._PERMISSION_CALLS.get(), ())

    def test_named_new_parent_cannot_acquire_preexisting_orphan_share(self):
        self.env.put(self.share("Quotation", "FUTURE"))
        doc = Doc(doctype="Quotation", name="FUTURE", __islocal=1,
                  company="C", jf_sample_order="S", items=[], taxes=[])
        with self.assertRaises(PermissionError):
            self.module.before_validate_source_binding(doc)
        self.assertTrue(any(dt == "DocShare" and kind == "value" for dt, _, kind in self.env.locks))

    def test_share_guard_denies_missing_known_parent_and_child_targets(self):
        for doctype in (*self.module.PARENTS, "Quotation Item", "Sales Taxes and Charges"):
            with self.subTest(doctype=doctype), self.assertRaisesRegex(PermissionError, "不能独立分享"):
                self.module.before_validate_docshare(self.share(doctype, "MISSING"))
        # Unrelated types retain their existing native validation contract.
        self.module.before_validate_docshare(self.share("Item", "MISSING"))

    def test_preflight_denies_orphan_parent_child_shares_without_mutation(self):
        for doctype in ("Quotation", "Work Order", "Quotation Item", "Sales Taxes and Charges"):
            share = self.env.put(self.share(doctype, "MISSING"))
            before = deepcopy(self.env.records)
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.module.assert_sample_share_invariant()
            self.assertEqual(self.env.records, before)
            del self.env.records["DocShare", share.name]
        self.env.put(self.share("Quotation", "PLAIN"))
        self.module.assert_sample_share_invariant()

    def test_actual_selected_bom_and_warehouse_sources_are_required(self):
        self.env.hidden.add("B")
        self.assertFalse(self.allowed("Work Order", "W"))
        self.env.hidden.clear()
        work = self.env.get_doc("Work Order", "W")
        work.source_warehouse = "WH"
        self.env.put(work)
        self.env.hidden.add("WH")
        self.assertFalse(self.allowed("Work Order", "W"))

    def test_pagination_does_not_truncate_permission_candidates(self):
        for index in range(self.module.BATCH + 2):
            self.env.put(Doc(doctype="Quotation", name=f"P{index:04}", items=[], taxes=[]))
        sql = self.module.sample_source_query_conditions(doctype="Quotation")
        self.assertIn("'P0201'", sql)

    def test_failed_source_check_resets_context_before_next_plain_request(self):
        self.env.hidden.add("I")
        self.assertFalse(self.allowed("Quotation", "Q"))
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertTrue(self.allowed("Quotation", "PLAIN"))


if __name__ == "__main__":
    unittest.main()
