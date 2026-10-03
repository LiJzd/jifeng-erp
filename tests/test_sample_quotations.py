"""Source identity and quotation stock capacity, independent of UI previews."""

from datetime import date
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

from jifeng_erp.sample_quote_schema import sample_custom_fields
from jifeng_erp import flow_quantity


class Record(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def set(self, key, value):
        self[key] = value

    def check_permission(self, kind):
        if self.get("denied"):
            raise PermissionError(kind)

    def get_doc_before_save(self):
        return self.get("previous")

    def is_new(self):
        return bool(self.get("__islocal"))

    def precision(self, _field):
        return 6


def load_module():
    def throw(message, exception=ValueError):
        raise exception(message)

    fake = types.SimpleNamespace(
        whitelist=lambda **kwargs: lambda fn: fn,
        throw=throw,
        PermissionError=PermissionError,
    )
    utils = types.SimpleNamespace(
        getdate=lambda value: date.fromisoformat(str(value)),
        today=lambda: "2026-10-04",
        cint=lambda value: int(value or 0),
        add_days=lambda *args: None,
    )
    fake.utils = utils
    atomic = types.SimpleNamespace(_atomic=lambda fn: fn)
    path = pathlib.Path(__file__).parents[1] / "jifeng_erp/sample_quotations.py"
    spec = importlib.util.spec_from_file_location("sample_quotations_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake,
            "frappe.utils": utils,
            "jifeng_erp.personnel_separation": atomic,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake


class NativeQuotation(Record):
    """Native boundary model: validation runs during the real maker's insert."""

    def __init__(self, module, *, fail=False):
        super().__init__(doctype="Quotation", docstatus=0, __islocal=1, items=[])
        self.__dict__["module"] = module
        self.__dict__["fail"] = fail
        self.__dict__["creation_context"] = None

    def append(self, field, values):
        row = Record(values)
        self[field].append(row)
        return row

    def insert(self):
        self.__dict__["creation_context"] = self.module._QUOTATION_CREATING.get()
        self.name = "NEW-QUOTE"
        for index, row in enumerate(self["items"], 1):
            row.name = f"NEW-QUOTE-ROW-{index}"
            row.qty, row.rate = Decimal(row.qty), Decimal(row.rate)
            row.conversion_factor = 12 if row.uom == "Box" else 1
            row.stock_qty = row.qty * row.conversion_factor
        self.module.validate_sample_quotation(self)
        if self.fail:
            raise ValueError("native quotation insert refused")
        self["__islocal"] = 0
        return self


class SampleQuotationTests(unittest.TestCase):
    def setUp(self):
        self.module, self.frappe = load_module()
        self.sample = Record(
            name="SAMPLE", company="JF", customer="CUSTOMER", approved_digest="a" * 64
        )
        self.sample["items"] = [
            Record(
                name="SAMPLE-ROW-1",
                sample_identity="UUID-1",
                item_code="RING",
                stock_uom="Nos",
                qty=1,
            ),
            Record(
                name="SAMPLE-ROW-2",
                sample_identity="UUID-2",
                item_code="RING",
                stock_uom="Nos",
                qty=2,
            ),
        ]
        self.origins = [
            self.origin(row, f"QUOTE-ROW-{i}")
            for i, row in enumerate(self.sample["items"], 1)
        ]
        self.quote = Record(
            name="QUOTE",
            company="JF",
            docstatus=1,
            quotation_to="Customer",
            party_name="CUSTOMER",
            jf_sample_order="SAMPLE",
            items=self.origins,
            jf_sample_request_id="16d579f2-559e-42b6-95b0-81a4e83682df",
            jf_sample_request_digest="b" * 64,
        )

    def origin(self, sample_row, name):
        snapshot = self.module.source_snapshot(self.sample, sample_row)
        return Record(
            name=name,
            item_code="RING",
            qty=1,
            uom="Box",
            conversion_factor=12,
            stock_qty=12,
            stock_uom="Nos",
            rate=15,
            jf_sample_order="SAMPLE",
            jf_sample_order_item=sample_row.name,
            jf_sample_source_digest="a" * 64,
            jf_sample_source_snapshot=self.module._json(snapshot),
        )

    def check_quote(self, quote=None, sample=None):
        with (
            patch.object(self.module, "_sample", return_value=sample or self.sample),
            patch.object(self.module, "check_quantity_fields"),
        ):
            return self.module._check_sources(quote or self.quote)

    def complete_quote(self, *, fresh=False):
        quote = deepcopy(self.quote)
        quote.update(
            doctype="Quotation",
            docstatus=0 if fresh else 1,
            __islocal=int(fresh),
            jf_sample_request_id="16d579f2-559e-42b6-95b0-81a4e83682df",
            jf_sample_request_digest="b" * 64,
        )
        return quote

    @contextmanager
    def native_sources(self):
        """Only replace native persistence/permission boundaries, not source helpers."""
        self.sample.docstatus = self.sample.get("docstatus", 1)
        rows = [
            Record(
                origin,
                name=f"SO-ROW-{index}",
                parent="SO",
                parenttype="Sales Order",
                parentfield="items",
                idx=index,
                prevdoc_docname=self.quote.name,
                quotation_item=origin.name,
                jf_make_flow=1,
            )
            for index, origin in enumerate(self.origins, 1)
        ]
        self.sales_order = Record(
            doctype="Sales Order",
            name="SO",
            docstatus=1,
            company="JF",
            customer="CUSTOMER",
            status="To Deliver and Bill",
            items=rows,
        )
        records = {
            ("Quotation", self.quote.name): self.quote,
            ("Sales Order", self.sales_order.name): self.sales_order,
            ("Item", "RING"): Record(name="RING", stock_uom="Nos"),
        }
        records.update({("Quotation Item", row.name): row for row in self.origins})
        records.update({("Sales Order Item", row.name): row for row in rows})
        self.native_records = records

        def get_value(doctype, filters, fields="name", *, as_dict=False, **kwargs):
            if isinstance(filters, str):
                record = records.get((doctype, filters))
            else:
                record = next(
                    (
                        row
                        for (kind, _), row in records.items()
                        if kind == doctype
                        and all(row.get(key) == value for key, value in filters.items())
                    ),
                    None,
                )
            if record is None:
                return None
            if isinstance(fields, (list, tuple)):
                return (
                    Record({field: record.get(field) for field in fields})
                    if as_dict
                    else tuple(record.get(field) for field in fields)
                )
            return record.get(fields)

        def sample(name, *, active=True, **kwargs):
            self.assertEqual(name, self.sample.name)
            if active and self.sample.docstatus != 1:
                raise ValueError("sample is not an active approved source")
            return self.sample

        self.frappe.get_doc = lambda doctype, name, **kwargs: records[(doctype, name)]
        readable = set(
            self.module.QUOTE_FIELDS
            + self.module.QUOTE_ITEM_FIELDS
            + self.module.SO_ITEM_FIELDS
            + self.module.FLOW_SOURCE_FIELDS
            + flow_quantity.SOURCE_FIELDS
            + flow_quantity.CONFIRMATION_FIELDS
            + flow_quantity.PLAN_FIELDS
        )
        readable.update(
            (
                "customer",
                "docstatus",
                "status",
                "jf_make_flow",
                "sales_order",
                "sales_order_item",
                "source_key",
                "external_order",
                "amended_from",
            )
        )
        native_fields = {
            name: Record(
                fieldname=name,
                fieldtype="Table" if name == "items" else "Data",
                options="Sales Order Item" if name == "items" else None,
                permlevel=0,
                mask=0,
            )
            for name in readable
        }
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(
            fields=list(native_fields.values()),
            has_field=lambda field: field in native_fields,
            get_field=lambda field: native_fields.get(field),
            get_permlevel_access=lambda **kwargs: (
                [] if kwargs["permission_type"] == "mask" else [0]
            ),
        )
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda *args, **kwargs: readable - {"items"}
        self.frappe.db = types.SimpleNamespace(
            get_value=get_value, sql=lambda *args, **kwargs: []
        )
        self.frappe.has_permission = lambda *args, **kwargs: True
        self.frappe.session = types.SimpleNamespace(user="sales-model@example.invalid")
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp/workflow.py"
        spec = importlib.util.spec_from_file_location("jifeng_erp.workflow", path)
        workflow = importlib.util.module_from_spec(spec)
        with (
            patch.dict(
                sys.modules,
                {
                    "frappe": self.frappe,
                    "frappe.utils": self.frappe.utils,
                    "frappe.model": model,
                    "jifeng_erp.sample_quotations": self.module,
                },
            ),
            patch.object(self.module, "_sample", side_effect=sample) as sample_read,
            patch.object(
                self.module,
                "check_quantity_fields",
                wraps=self.module.check_quantity_fields,
            ) as field_read,
        ):
            spec.loader.exec_module(workflow)
            with patch.dict(sys.modules, {"jifeng_erp.workflow": workflow}):
                self.workflow, self.sample_read, self.field_read = (
                    workflow,
                    sample_read,
                    field_read,
                )
                yield

    def sample_flow(self, *, fresh=False, row_index=0):
        row = self.sales_order["items"][row_index]
        fields = self.module.sample_fields_for_flow(self.sales_order, row)
        return Record(
            doctype="Jifeng Flow Order",
            name="FLOW",
            __islocal=int(fresh),
            company="JF",
            item_code=row.item_code,
            sales_order=self.sales_order.name,
            sales_order_item=row.name,
            source_key=self.workflow.sales_order_flow_key(
                self.sales_order.name, row.name
            ),
            **fields,
        )

    def test_same_item_rows_keep_different_production_identities(self):
        self.check_quote()
        self.assertNotEqual(
            self.origins[0].jf_sample_order_item, self.origins[1].jf_sample_order_item
        )
        self.assertNotEqual(
            self.origins[0].jf_sample_source_snapshot,
            self.origins[1].jf_sample_source_snapshot,
        )

    def test_production_quantity_is_not_commercial_limit(self):
        self.check_quote()
        self.assertEqual(self.module.remaining_capacity(12, 5, 7), 0)
        self.assertEqual(self.sample["items"][0].qty, 1)

    def test_source_parent_or_row_or_digest_cannot_be_replaced(self):
        for field, value in [
            ("jf_sample_order", "OTHER"),
            ("jf_sample_order_item", "MISSING"),
            ("jf_sample_source_digest", "b" * 64),
            ("jf_sample_source_snapshot", "{}"),
        ]:
            with self.subTest(field=field):
                original = self.origins[0][field]
                self.origins[0][field] = value
                with self.assertRaises(ValueError):
                    self.check_quote()
                self.origins[0][field] = original

    def test_cross_company_customer_and_non_customer_quote_refused(self):
        for field, value in [
            ("company", "OTHER"),
            ("party_name", "OTHER"),
            ("quotation_to", "Lead"),
        ]:
            with self.subTest(field=field):
                original = self.quote[field]
                self.quote[field] = value
                with self.assertRaises(ValueError):
                    self.check_quote()
                self.quote[field] = original

    def test_quote_permission_and_source_mask_are_not_bypassed(self):
        self.quote.denied = True
        with self.assertRaises(PermissionError):
            self.check_quote()
        self.quote.denied = False
        with patch.object(
            self.module, "check_quantity_fields", side_effect=PermissionError("masked")
        ):
            with self.assertRaises(PermissionError):
                self.module._check_sources(self.quote)

    def test_parentless_source_identity_refused(self):
        self.quote.jf_sample_order = None
        with self.assertRaises(ValueError):
            self.check_quote()

    def test_cancelled_order_releases_its_stock_reservation(self):
        with self.assertRaises(self.module.SampleQuotationError):
            self.module.remaining_capacity(12, 6, 7)
        self.assertEqual(self.module.remaining_capacity(12, 0, 7), 5)

    def test_native_box_conversion_and_stock_capacity(self):
        row = self.origins[0]
        snapshot = self.module.sales_row_snapshot(row, "Nos", 6)
        self.assertEqual(snapshot["source_stock_qty"], Decimal(12))
        with self.assertRaises(ValueError):
            broken = Record(row, stock_qty=1)
            self.module.sales_row_snapshot(broken, "Nos", 6)

    def test_capacity_groups_exact_quote_children_not_item_code(self):
        rows = []
        for origin in self.origins:
            row = Record(origin)
            row.update(
                name="SO-" + origin.name,
                prevdoc_docname="QUOTE",
                quotation_item=origin.name,
                qty=Decimal("0.5"),
                stock_qty=6,
            )
            rows.append(row)
        doc = Record(name="SO", company="JF", customer="CUSTOMER", items=rows)
        seen = []

        def sql(_statement, args):
            seen.append(args)
            return [(6 if args[1] == "QUOTE-ROW-1" else 0,)]

        self.frappe.db = types.SimpleNamespace(sql=sql)
        with (
            patch.object(self.module, "check_quantity_fields"),
            patch.object(self.module, "_candidate", return_value=True),
            patch.object(
                self.module,
                "_quote_row",
                side_effect=lambda _doc, row, **kwargs: (
                    self.quote,
                    next(r for r in self.origins if r.name == row.quotation_item),
                ),
            ),
        ):
            self.module.validate_sample_sales_order(doc)
            rows[0].update(qty=Decimal("0.6"), stock_qty=Decimal("7.2"))
            with self.assertRaises(ValueError):
                self.module.validate_sample_sales_order(doc)
        self.assertEqual(
            seen[:2], [("QUOTE", "QUOTE-ROW-1", "SO"), ("QUOTE", "QUOTE-ROW-2", "SO")]
        )

    def test_zero_price_explicit_and_nonfinite_values_rejected(self):
        self.assertEqual(self.module.decimal_value("0", "单价", zero=True), 0)
        self.assertEqual(
            self.module.decimal_value("0.0001", "单价", zero=True), Decimal("0.0001")
        )
        for value in (None, "", True, "NaN", "Infinity", "-1"):
            with (
                self.subTest(value=value),
                self.assertRaises(self.module.SampleQuotationError),
            ):
                self.module.decimal_value(value, "单价", zero=True)

    def test_native_uuid_canonical_identity(self):
        value = "16d579f2-559e-42b6-95b0-81a4e83682df"
        self.assertEqual(self.module._uuid(value), value)
        for invalid in (None, value.upper(), value.replace("-", ""), "broken"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                self.module._uuid(invalid)

    def test_request_digest_preserves_selected_row_order_and_zero(self):
        payload = {
            "items": [
                {"sample_item": "ONE", "rate": "0"},
                {"sample_item": "TWO", "rate": "1"},
            ]
        }
        self.assertEqual(self.module._digest(payload), self.module._digest(payload))
        reversed_payload = {"items": list(reversed(payload["items"]))}
        self.assertNotEqual(
            self.module._digest(payload), self.module._digest(reversed_payload)
        )

    def test_rollback_cleanup_failure_cannot_look_like_definite_rejection(self):
        def reject():
            error = ValueError("invalid input")
            error.add_note("局部回滚清理失败")
            raise error

        with self.assertRaises(self.module.SampleQuotationWriteUncertain):
            self.module._quotation_atomic(reject)()

    def test_cancel_cannot_change_stock_or_commercial_history(self):
        original = Record(self.quote)
        original["items"] = [Record(row) for row in self.origins]
        current = Record(self.quote, previous=original)
        current["items"] = [Record(row) for row in self.origins]
        self.module._cancel_history(current, ("company", "party_name"))
        for field, value in [
            ("qty", 99),
            ("stock_qty", 99),
            ("rate", 99),
            ("uom", "Other"),
        ]:
            with self.subTest(field=field):
                before = current["items"][0][field]
                current["items"][0][field] = value
                with self.assertRaises(ValueError):
                    self.module._cancel_history(current, ("company", "party_name"))
                current["items"][0][field] = before
        current["items"].pop()
        with self.assertRaises(ValueError):
            self.module._cancel_history(current, ("company", "party_name"))

    def test_cancel_accepts_equal_native_date_and_numeric_encodings(self):
        original = Record(self.quote, transaction_date=date(2026, 10, 4))
        original["items"] = [Record(row) for row in self.origins]
        current = Record(self.quote, transaction_date="2026-10-04", previous=original)
        current["items"] = [Record(row) for row in self.origins]
        current["items"][0].qty = "1.000"
        self.module._cancel_history(current, ("transaction_date",))

    def test_native_new_sample_quote_and_request_only_quote_require_server_context(
        self,
    ):
        with self.native_sources():
            quote = self.complete_quote(fresh=True)
            with self.assertRaises(ValueError):
                self.module.validate_sample_quotation(quote)
            plain = Record(
                doctype="Quotation",
                name="PLAIN",
                __islocal=1,
                items=[],
                jf_sample_request_id=quote.jf_sample_request_id,
                jf_sample_request_digest=quote.jf_sample_request_digest,
            )
            with self.assertRaises(ValueError):
                self.module.validate_sample_quotation(plain)

    def test_creation_context_must_bind_exact_document_uuid_and_digest(self):
        with self.native_sources():
            quote = self.complete_quote(fresh=True)
            wrong_bindings = [
                (
                    deepcopy(quote),
                    quote.jf_sample_request_id,
                    quote.jf_sample_request_digest,
                ),
                (
                    quote,
                    "26d579f2-559e-42b6-95b0-81a4e83682df",
                    quote.jf_sample_request_digest,
                ),
                (quote, quote.jf_sample_request_id, "c" * 64),
            ]
            for binding in wrong_bindings:
                with self.subTest(binding=binding[1:]):
                    token = self.module._QUOTATION_CREATING.set(binding)
                    try:
                        with self.assertRaises(ValueError):
                            self.module.validate_sample_quotation(quote)
                    finally:
                        self.module._QUOTATION_CREATING.reset(token)
            self.assertIsNone(self.module._QUOTATION_CREATING.get())

    def test_real_maker_binds_native_insert_and_resets_context_after_success_and_failure(
        self,
    ):
        with self.native_sources():
            source_get_doc = self.frappe.get_doc
            self.frappe.get_doc = lambda doctype, name, **kwargs: (
                Record(name=name)
                if doctype in ("Customer", "Currency")
                else source_get_doc(doctype, name, **kwargs)
            )
            for fail in (False, True):
                with self.subTest(fail=fail):
                    native = NativeQuotation(self.module, fail=fail)
                    self.frappe.new_doc = lambda doctype: native
                    arguments = dict(
                        sample_order=self.sample.name,
                        version="2026-10-04T00:00:00.123456",
                        customer="CUSTOMER",
                        currency="CNY",
                        valid_till="2026-10-05",
                        items=[
                            dict(
                                sample_item=self.sample["items"][0].name,
                                qty="2",
                                uom="Box",
                                rate="0",
                                make_flow=1,
                            )
                        ],
                        request_id="26d579f2-559e-42b6-95b0-81a4e83682df",
                    )
                    if fail:
                        with self.assertRaisesRegex(
                            ValueError, "native quotation insert refused"
                        ):
                            self.module.make_sample_quotation(**arguments)
                    else:
                        result = self.module.make_sample_quotation(**arguments)
                        self.assertEqual(
                            result,
                            dict(
                                status="found",
                                doctype="Quotation",
                                name="NEW-QUOTE",
                                request_id=arguments["request_id"],
                            ),
                        )
                        self.assertEqual(native["items"][0].rate, Decimal(0))
                        self.assertEqual(native["items"][0].qty, Decimal(2))
                    self.assertIs(native.creation_context[0], native)
                    self.assertEqual(
                        native.creation_context[1:],
                        (arguments["request_id"], native.jf_sample_request_digest),
                    )
                    self.assertIsNone(self.module._QUOTATION_CREATING.get())
                    # A later native insert cannot inherit the maker's private authority.
                    with self.assertRaises(ValueError):
                        self.module.validate_sample_quotation(
                            self.complete_quote(fresh=True)
                        )
                    self.module.validate_sample_quotation(
                        Record(doctype="Quotation", name="PLAIN", __islocal=1, items=[])
                    )

    def test_normal_native_quote_has_no_sample_creation_requirement(self):
        quote = Record(doctype="Quotation", name="PLAIN", __islocal=1, items=[])
        with patch.object(self.module, "_check_sources") as source:
            self.module.validate_sample_quotation(quote)
        source.assert_not_called()

    def test_cancel_cannot_clear_persisted_sample_parent_child_or_request(self):
        with self.native_sources():
            original = self.complete_quote()
            current = deepcopy(original)
            current.update(previous=original, docstatus=2, _action="cancel")
            self.module.cancel_sample_quotation(current)
            self.assertTrue(self.field_read.call_args_list)
            self.assertIs(self.sample_read.call_args.kwargs["active"], False)
            for field in ("jf_sample_order",) + self.module.REQUEST_FIELDS:
                with self.subTest(parent_field=field):
                    altered = deepcopy(current)
                    altered[field] = None
                    with self.assertRaises(ValueError):
                        self.module.cancel_sample_quotation(altered)
            for field in self.module.SOURCE_FIELDS:
                with self.subTest(child_field=field):
                    altered = deepcopy(current)
                    altered["items"][0][field] = None
                    with self.assertRaises(ValueError):
                        self.module.cancel_sample_quotation(altered)
            altered = deepcopy(current)
            altered["items"].pop()
            with self.assertRaises(ValueError):
                self.module.cancel_sample_quotation(altered)
            altered = deepcopy(current)
            altered.jf_sample_order = None
            for field in self.module.REQUEST_FIELDS:
                altered[field] = None
            for row in altered["items"]:
                for field in self.module.SOURCE_FIELDS:
                    row[field] = None
            with self.assertRaises(ValueError):
                self.module.cancel_sample_quotation(altered)

    def test_canceled_so_quote_and_sample_remain_readable_as_history(self):
        with self.native_sources():
            self.sample.docstatus = self.quote.docstatus = (
                self.sales_order.docstatus
            ) = 2
            self.module.load_sample_sales_order(self.sales_order)
            self.module.load_sample_quotation(self.quote)
            self.assertTrue(self.sample_read.call_args_list)
            self.assertTrue(
                all(
                    call.kwargs.get("active") is False
                    for call in self.sample_read.call_args_list
                )
            )
            self.assertEqual(
                self.module.sample_fields_for_flow(
                    self.sales_order, self.sales_order["items"][0], active=False
                )["jf_sample_order"],
                self.sample.name,
            )

    def test_active_order_transfer_rejects_canceled_quote_or_sample(self):
        with self.native_sources():
            for sample_status, quote_status in ((2, 1), (1, 2)):
                with self.subTest(
                    sample_status=sample_status, quote_status=quote_status
                ):
                    self.sample.docstatus, self.quote.docstatus = (
                        sample_status,
                        quote_status,
                    )
                    with self.assertRaises(ValueError):
                        self.module.prepare_sample_sales_order(self.sales_order)
                    with self.assertRaises(ValueError):
                        self.module.sample_fields_for_flow(
                            self.sales_order, self.sales_order["items"][0]
                        )

    def test_flow_six_source_fields_are_exact_and_immutable(self):
        fields = (
            "jf_sample_order",
            "jf_sample_order_item",
            "jf_sample_source_digest",
            "jf_sample_source_snapshot",
            "jf_source_quotation",
            "jf_source_quotation_item",
        )
        with self.native_sources():
            original = self.sample_flow()
            self.assertEqual(
                set(
                    self.module.sample_fields_for_flow(
                        self.sales_order, self.sales_order["items"][0]
                    )
                ),
                set(fields),
            )
            for field in fields:
                for value in (None, "forged"):
                    with self.subTest(field=field, value=value):
                        current = deepcopy(original)
                        current.previous = original
                        current[field] = value
                        with self.assertRaises(ValueError):
                            self.module.validate_sample_flow(current)
            current = deepcopy(original)
            current.previous = original
            for field in fields:
                current[field] = None
            with self.assertRaises(ValueError):
                self.module.validate_sample_flow(current)

    def test_existing_flow_native_parent_row_company_item_and_source_key_cannot_rebind(
        self,
    ):
        with self.native_sources():
            original = self.sample_flow()
            for field in (
                "sales_order",
                "sales_order_item",
                "company",
                "item_code",
                "source_key",
            ):
                with self.subTest(field=field):
                    current = deepcopy(original)
                    current.previous = original
                    current[field] = "OTHER"
                    with self.assertRaises(ValueError):
                        self.module.validate_sample_flow(current)

    def test_new_flow_with_sample_identity_requires_exact_sales_order_source(self):
        with self.native_sources():
            flow = self.sample_flow(fresh=True)
            flow.sales_order = flow.sales_order_item = None
            with self.assertRaises(ValueError):
                self.module.validate_sample_flow(flow)

    def test_new_flow_empty_sample_fields_cannot_detach_sample_sales_order_row(self):
        with self.native_sources():
            flow = self.sample_flow(fresh=True)
            for field in (
                "jf_sample_order",
                "jf_sample_order_item",
                "jf_sample_source_digest",
                "jf_sample_source_snapshot",
                "jf_source_quotation",
                "jf_source_quotation_item",
            ):
                flow[field] = None
            with self.assertRaises(ValueError):
                self.module.validate_sample_flow(flow)
            self.assertTrue(
                all(flow.get(field) is None for field in self.module.SOURCE_FIELDS)
            )

    def test_flow_does_not_merge_different_sample_rows_with_the_same_item(self):
        with self.native_sources():
            flow = self.sample_flow(fresh=True)
            other = self.sales_order["items"][1]
            self.assertEqual(flow.item_code, other.item_code)
            flow.sales_order_item = other.name
            flow.source_key = self.workflow.sales_order_flow_key(
                self.sales_order.name, other.name
            )
            with self.assertRaises(ValueError):
                self.module.validate_sample_flow(flow)

    def test_valid_new_flow_and_existing_canceled_upstream_history(self):
        with self.native_sources():
            flow = self.sample_flow(fresh=True)
            self.module.validate_sample_flow(flow)
            original = deepcopy(flow)
            original["__islocal"] = 0
            current = deepcopy(original)
            current.previous = original
            self.sample.docstatus = self.quote.docstatus = (
                self.sales_order.docstatus
            ) = 2
            self.module.validate_sample_flow(current)
            self.assertIs(self.sample_read.call_args.kwargs.get("active"), False)

    def test_stock_plan_gate_rejects_canceled_or_tampered_sample_sources(self):
        with self.native_sources():
            flow = self.sample_flow(fresh=True)
            row = self.sales_order["items"][0]
            flow.update(flow_quantity.sales_row_snapshot(row, "Nos", 6), planned_qty=12)
            self.assertEqual(self.workflow.stock_plan_quantity(flow), Decimal(12))
            for upstream in (self.sample, self.quote, self.sales_order):
                with self.subTest(canceled=upstream.name):
                    upstream.docstatus = 2
                    with self.assertRaises(ValueError):
                        self.workflow.stock_plan_quantity(flow)
                    upstream.docstatus = 1
            for field in self.module.FLOW_SOURCE_FIELDS:
                with self.subTest(forged_flow_field=field):
                    changed = deepcopy(flow)
                    changed[field] = "forged"
                    with self.assertRaises(ValueError):
                        self.workflow.stock_plan_quantity(changed)
            self.sample.approved_digest = "c" * 64
            with self.assertRaises(ValueError):
                self.workflow.stock_plan_quantity(flow)
            self.sample.approved_digest = "a" * 64
            self.sample["items"][0].qty = 2
            with self.assertRaises(ValueError):
                self.workflow.stock_plan_quantity(flow)

    def test_stock_plan_gate_keeps_ordinary_flow_quantity_rules(self):
        with self.native_sources():
            row = self.sales_order["items"][0]
            for field in self.module.SOURCE_FIELDS + (
                "prevdoc_docname",
                "quotation_item",
            ):
                row[field] = None
            flow = Record(
                doctype="Jifeng Flow Order",
                name="PLAIN-FLOW",
                company="JF",
                item_code="RING",
                sales_order="SO",
                sales_order_item=row.name,
                planned_qty=12,
                **flow_quantity.sales_row_snapshot(row, "Nos", 6),
            )
            self.assertEqual(self.workflow.stock_plan_quantity(flow), Decimal(12))
            self.sample_read.assert_not_called()
            flow.quantity_basis = "pending"
            with self.assertRaises(ValueError):
                self.workflow.stock_plan_quantity(flow)
            self.sample_read.assert_not_called()

    def test_plain_flow_keeps_native_behavior_without_sample_reads(self):
        with self.native_sources():
            for linked in (False, True):
                with self.subTest(linked=linked):
                    row = self.sales_order["items"][0]
                    for field in self.module.SOURCE_FIELDS + (
                        "prevdoc_docname",
                        "quotation_item",
                    ):
                        row[field] = None
                    flow = Record(
                        doctype="Jifeng Flow Order",
                        name="PLAIN-FLOW",
                        __islocal=1,
                        company="JF",
                        item_code="RING",
                        sales_order="SO" if linked else None,
                        sales_order_item=row.name if linked else None,
                    )
                    self.sample_read.reset_mock()
                    self.field_read.reset_mock()
                    with patch.object(
                        self.frappe,
                        "get_doc",
                        side_effect=AssertionError("unexpected document read"),
                    ):
                        self.module.validate_sample_flow(flow)
                    self.sample_read.assert_not_called()
                    self.field_read.assert_not_called()

    def quote_amendment(self, *, clear=False):
        self.quote.doctype = "Quotation"
        self.quote.docstatus = 2
        for old in self.quote["items"]:
            old.update(parent=self.quote.name, parenttype="Quotation", parentfield="items")
        result = deepcopy(self.quote)
        result.update(name="AMENDED-QUOTE", docstatus=0, amended_from=self.quote.name,
                      __islocal=1, previous=None)
        for index, (row, old) in enumerate(zip(result["items"], self.quote["items"], strict=True)):
            row.update(name=f"NEW-ROW-{index}", _amended_from=old.name)
            if clear:
                for field in self.module.SOURCE_FIELDS:
                    row[field] = None
        if clear:
            for field in ("jf_sample_order",) + self.module.REQUEST_FIELDS:
                result[field] = None
        return result

    def test_native_quote_amend_keeps_exact_rows_explicit_prices_and_new_receipt(self):
        with self.native_sources():
            amended = self.quote_amendment(clear=True)
            amended["items"].reverse()
            amended["items"][0].qty, amended["items"][0].rate = "2.50", "0"
            self.module.validate_sample_quotation(amended, "before_validate")
            new_id = amended.jf_sample_request_id
            self.assertNotEqual(new_id, self.quote.jf_sample_request_id)
            self.assertEqual(self.module._uuid(new_id), new_id)
            self.assertEqual([r.jf_sample_order_item for r in amended["items"]],
                             [r.jf_sample_order_item for r in reversed(self.quote["items"])])
            self.assertEqual((amended["items"][0].qty, amended["items"][0].rate), ("2.50", "0"))
            amended["items"][0].qty, amended["items"][0].rate = 2.5, 0.0
            self.module.validate_sample_quotation(amended, "validate")
            self.assertEqual(amended.jf_sample_request_id, new_id)

    def test_amend_copied_uuid_is_replaced_and_input_cannot_authorize_new_quote(self):
        with self.native_sources():
            amended = self.quote_amendment()
            amended._jf_sample_amend = [True, self.quote.name,
                                       self.quote.jf_sample_request_id, "b" * 64, []]
            self.module.validate_sample_quotation(amended)
            self.assertNotEqual(amended.jf_sample_request_id, self.quote.jf_sample_request_id)
            amended["items"][0].rate = 999
            with self.assertRaisesRegex(ValueError, "改变了修改版指定"):
                self.module.validate_sample_quotation(amended)

    def test_amend_missing_or_forged_exact_row_and_wrong_sample_are_rejected(self):
        with self.native_sources():
            for field, value in (("_amended_from", "OTHER-QUOTE-ROW"),
                                 ("jf_sample_order_item", "OTHER-SAMPLE-ROW"),
                                 ("item_code", "OTHER-ITEM")):
                with self.subTest(field=field):
                    amended = self.quote_amendment()
                    amended["items"][0][field] = value
                    with self.assertRaises(ValueError):
                        self.module.validate_sample_quotation(amended)
            amended = self.quote_amendment(clear=True)
            amended["items"][0]._amended_from = None
            with self.assertRaisesRegex(ValueError, "准确明细"):
                self.module.validate_sample_quotation(amended)

    def test_quote_amend_parent_permissions_masks_active_sample_and_cancel_required(self):
        with self.native_sources():
            amended = self.quote_amendment()
            self.quote.denied = True
            with self.assertRaises(PermissionError):
                self.module.validate_sample_quotation(amended)
            self.quote.denied = False
            with patch.object(self.module, "check_quantity_fields", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    self.module.validate_sample_quotation(amended)
            self.sample.docstatus = 2
            with self.assertRaises(ValueError):
                self.module.validate_sample_quotation(amended)
            self.sample.docstatus = 1
            self.quote.docstatus = 1
            with self.assertRaisesRegex(ValueError, "先反审"):
                self.module.validate_sample_quotation(amended)

    def test_quote_amend_child_parenttype_parentfield_must_be_exact(self):
        with self.native_sources():
            for field in ("parent", "parenttype", "parentfield"):
                amended = self.quote_amendment()
                self.quote["items"][0][field] = "OTHER"
                with self.assertRaisesRegex(ValueError, "准确明细归属"):
                    self.module.validate_sample_quotation(amended)

    def test_quote_amend_repeated_sample_rows_need_original_quote_row_identity(self):
        with self.native_sources():
            self.quote["items"][1] = Record(self.quote["items"][0], name="QUOTE-ROW-2")
            amended = self.quote_amendment()
            for row in amended["items"]:
                row._amended_from = None
            with self.assertRaisesRegex(ValueError, "准确明细"):
                self.module.validate_sample_quotation(amended)
            amended = self.quote_amendment()
            self.module.validate_sample_quotation(amended)

    def test_sample_sales_amend_restores_exact_quote_identity_and_keeps_qty(self):
        with self.native_sources():
            self.sales_order.docstatus = 2
            amended = deepcopy(self.sales_order)
            amended.update(name="AMENDED-SO", docstatus=0, amended_from=self.sales_order.name,
                           previous=None, __islocal=1)
            for row, old in zip(amended["items"], self.sales_order["items"], strict=True):
                row._amended_from = old.name
                for field in self.module.SOURCE_FIELDS + ("prevdoc_docname", "quotation_item"):
                    row[field] = None
            amended["items"][0].qty = "0.5"
            self.module.prepare_sample_sales_order(amended)
            self.assertEqual(amended["items"][0].qty, "0.5")
            for row, old in zip(amended["items"], self.sales_order["items"], strict=True):
                self.assertEqual(row.quotation_item, old.quotation_item)
                self.assertEqual(row.jf_sample_order_item, old.jf_sample_order_item)
            self.quote.docstatus = 2
            with self.assertRaises(ValueError):
                self.module.prepare_sample_sales_order(amended)

    def test_normal_quote_and_sales_amend_do_not_add_sample_gates(self):
        with self.native_sources():
            for field in ("jf_sample_order",) + self.module.REQUEST_FIELDS:
                self.quote[field] = None
            for record in (self.quote, self.sales_order):
                for row in record["items"]:
                    for field in self.module.SOURCE_FIELDS + ("prevdoc_docname", "quotation_item"):
                        row[field] = None
            quote = Record(doctype="Quotation", amended_from=self.quote.name, items=[])
            sales = Record(doctype="Sales Order", amended_from=self.sales_order.name, items=[])
            self.module.validate_sample_quotation(quote)
            self.module.prepare_sample_sales_order(sales)
            self.sample_read.assert_not_called()
            self.field_read.assert_not_called()

    def test_sales_amend_cannot_strip_all_row_identity_and_bypass_cancelled_sources(self):
        with self.native_sources():
            self.sales_order.docstatus = self.quote.docstatus = self.sample.docstatus = 2
            amended = deepcopy(self.sales_order)
            amended.update(name="CLEARED-SO", docstatus=0, amended_from=self.sales_order.name,
                           previous=None, __islocal=1)
            for row in amended["items"]:
                for field in self.module.SOURCE_FIELDS + (
                    "prevdoc_docname", "quotation_item", "_amended_from"
                ):
                    row[field] = None
            with self.assertRaisesRegex(ValueError, "缺少准确原明细"):
                self.module.prepare_sample_sales_order(amended)

    def test_mixed_sales_amend_keeps_exact_ordinary_row_but_rejects_one_stripped_sample(self):
        with self.native_sources():
            self.sales_order.docstatus = 2
            ordinary = self.sales_order["items"][1]
            for field in self.module.SOURCE_FIELDS + ("prevdoc_docname", "quotation_item"):
                ordinary[field] = None
            amended = deepcopy(self.sales_order)
            amended.update(name="MIXED-AMEND", docstatus=0, amended_from=self.sales_order.name,
                           previous=None, __islocal=1)
            for row, old in zip(amended["items"], self.sales_order["items"], strict=True):
                row._amended_from = old.name
            self.module.prepare_sample_sales_order(amended)
            self.assertFalse(self.module._candidate(amended["items"][1]))
            for field in self.module.SOURCE_FIELDS + (
                "prevdoc_docname", "quotation_item", "_amended_from"
            ):
                amended["items"][0][field] = None
            with self.assertRaisesRegex(ValueError, "缺少准确原明细"):
                self.module.prepare_sample_sales_order(amended)

    def test_saved_sample_amend_parent_cannot_be_cleared_or_replaced(self):
        with self.native_sources():
            for kind in ("Quotation", "Sales Order"):
                original = deepcopy(self.quote if kind == "Quotation" else self.sales_order)
                original.amended_from = "CANCELLED-ORIGINAL"
                for value in (None, "OTHER-CANCELLED-ORIGINAL"):
                    changed = deepcopy(original)
                    changed.previous = original
                    changed.amended_from = value
                    actions = (self.module.validate_sample_quotation,
                               self.module.cancel_sample_quotation) if kind == "Quotation" else (
                                   self.module.prepare_sample_sales_order,
                                   self.module.guard_sample_sales_order_history)
                    for action in actions:
                        with self.subTest(doctype=kind, action=action.__name__, value=value):
                            with self.assertRaisesRegex(ValueError, "不能替换或清除"):
                                action(changed)


class SampleSchemaTests(unittest.TestCase):
    def test_receipt_uuid_unique_and_sources_not_copied_on_normal_duplicate(self):
        definitions = sample_custom_fields()
        for doctype in ("Work Order", "Quotation"):
            fields = {field["fieldname"]: field for field in definitions[doctype]}
            self.assertEqual(fields["jf_sample_request_id"]["unique"], 1)
        for doctype in ("Quotation Item", "Sales Order Item", "Jifeng Flow Order"):
            fields = {field["fieldname"]: field for field in definitions[doctype]}
            for name in (
                "jf_sample_order",
                "jf_sample_order_item",
                "jf_sample_source_digest",
            ):
                self.assertEqual(fields[name]["no_copy"], 1)
                self.assertEqual(fields[name]["read_only"], 1)
        for fields in definitions.values():
            self.assertEqual(len({f["fieldname"] for f in fields}), len(fields))
            self.assertFalse(any("role" in field for field in fields))

    def test_quote_requirements_match_existing_sales_order_field_types(self):
        fields = {
            field["fieldname"]: field
            for field in sample_custom_fields()["Quotation Item"]
        }
        self.assertEqual(fields["jf_requirements"]["fieldtype"], "Small Text")
        self.assertEqual(fields["jf_packaging"]["fieldtype"], "Small Text")
        self.assertEqual(fields["jf_requirements_attachment"]["fieldtype"], "Attach")
        self.assertEqual(fields["jf_make_flow"]["fieldtype"], "Check")


if __name__ == "__main__":
    unittest.main()
