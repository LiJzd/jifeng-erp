"""Native-like sample lifecycle/production risk models; no database or GUI."""

import ast
from copy import deepcopy
from datetime import datetime
from functools import wraps
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, value):
        self._functions.append(value)

    def reset(self):
        self._functions.clear()


class Meta:
    def __init__(self, fields):
        self.fields = [Record(field) for field in fields]

    def get_field(self, field):
        return next((row for row in self.fields if row.fieldname == field), None)

    def has_field(self, field):
        return self.get_field(field) is not None

    def get_valid_columns(self):
        return {
            "name",
            "modified",
            "docstatus",
            "owner",
            "creation",
            "modified_by",
            "idx",
            "parent",
            "parenttype",
            "parentfield",
        } | {
            row.fieldname
            for row in self.fields
            if row.fieldtype not in ("Table", "Section Break")
        }

    def get_table_fields(self):
        return [row for row in self.fields if row.fieldtype == "Table"]

    def get_permlevel_access(self, permission_type="read", **kwargs):
        return [] if permission_type == "mask" else [0]


class Document:
    environment = None

    def __init__(self, values):
        self.__dict__["values"] = deepcopy(values)

    def __getattr__(self, name):
        return self.values.get(name)

    def __setattr__(self, name, value):
        self.values[name] = value

    def get(self, name, default=None):
        return self.values.get(name, default)

    def set(self, name, value):
        self.values[name] = value

    def as_dict(self):
        def plain(value):
            if isinstance(value, Document):
                return value.as_dict()
            if isinstance(value, list):
                return [plain(row) for row in value]
            return deepcopy(value)

        return {key: plain(value) for key, value in self.values.items()}

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    @property
    def flags(self):
        return self.__dict__.setdefault("native_flags", Record())

    def precision(self, field):
        return self.environment.precision

    def is_new(self):
        return bool(self.get("__islocal"))

    def check_permission(self, permission):
        env = self.environment
        if (self.doctype, permission) in env.denials or self.name in env.hidden:
            raise PermissionError(self.doctype + " " + permission)

    def get_doc_before_save(self):
        if self.is_new():
            return None
        return (
            self.environment.get_doc(self.doctype, self.name)
            if (self.doctype, self.name) in self.environment.records
            else None
        )

    def get_items_and_operations_from_bom(self):
        env = self.environment
        env.expansion_defaults.append(self.use_multi_level_bom)
        self.required_items = [
            Document(
                dict(
                    doctype="Work Order Item",
                    item_code="RAW",
                    stock_uom="Piece",
                    required_qty=self.qty,
                )
            )
        ]
        self.operations = []
        if env.expansion_hook:
            env.expansion_hook(self)

    def insert(self):
        env = self.environment
        self.check_permission("create")
        env.service.validate_sample_work_order(self)
        if env.fail_insert:
            env.frappe.db.after_commit.add(lambda: None)
            env.frappe.flags.currently_saving = ["native-save"]
            env.frappe.local._realtime_log = ["native-event"]
            raise ValueError("native insert failure")
        self.name = "WO-" + str(env.tick)
        self.creation = "2026-10-04T00:00:00.000001"
        self.owner = env.frappe.session.user
        env.tick += 1
        self.modified = "2026-10-04T00:00:" + str(env.tick).zfill(2) + ".123456"
        self.docstatus = 0
        self.set("__islocal", 0)
        env.records[(self.doctype, self.name)] = self.as_dict()
        env.inserts.append(self.doctype)
        return self


class TestSampleOrders(unittest.TestCase):
    def setUp(self):
        self.records, self.metas, self.snapshots = {}, {}, {}
        self.denials, self.hidden, self.hidden_fields = set(), set(), {}
        self.precision, self.tick, self.fail_insert = 3, 1, False
        self.inserts, self.expansion_defaults = [], []
        self.expansion_hook, self.read_hook = None, None
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = (
            PermissionError,
            RuntimeError,
        )
        self.frappe.session, self.frappe.flags = (
            Record(user="manufacturer@example.invalid"),
            Record(),
        )
        self.frappe.local = types.SimpleNamespace()
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(
            error(message)
        )
        self.frappe.get_doc, self.frappe.new_doc, self.frappe.get_list = (
            self.get_doc,
            self.new_doc,
            self.get_list,
        )
        self.frappe.get_meta = lambda name: self.metas[name]
        self.frappe.has_permission = lambda doctype, permission, **kwargs: (
            (doctype, permission) not in self.denials
        )
        self.frappe.db = types.SimpleNamespace(
            get_value=self.get_value,
            sql=self.sql,
            savepoint=self.savepoint,
            rollback=self.rollback,
            **{
                name: Queue()
                for name in (
                    "before_commit",
                    "after_commit",
                    "before_rollback",
                    "after_rollback",
                )
            },
        )
        model, utils, separation = (
            types.ModuleType("frappe.model"),
            types.ModuleType("frappe.utils"),
            types.ModuleType("jifeng_erp.personnel_separation"),
        )
        model.get_permitted_fields = self.columns
        utils.flt = lambda value, precision: round(float(value), precision)
        utils.now_datetime = lambda: datetime(2026, 10, 4, 0, 0, 0, 123456)
        tree = ast.parse(
            (ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8")
        )
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_atomic"
        )
        namespace = dict(
            frappe=self.frappe, wraps=wraps, deepcopy=deepcopy, uuid4=uuid4
        )
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]), "native_atomic", "exec"
            ),
            namespace,
        )
        separation._atomic = namespace["_atomic"]
        self.patch = patch.dict(
            sys.modules,
            {
                "frappe": self.frappe,
                "frappe.model": model,
                "frappe.utils": utils,
                "jifeng_erp.personnel_separation": separation,
            },
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location(
            "sample_orders_model", ROOT / "jifeng_erp/sample_orders.py"
        )
        self.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.service)
        Document.environment = self
        for folder in ("jifeng_sample_order", "jifeng_sample_order_item"):
            meta = json.loads(
                (
                    ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")
                ).read_text(encoding="utf-8")
            )
            self.metas[meta["name"]] = Meta(meta["fields"])

        def fields(*names):
            return [{"fieldname": name, "fieldtype": "Data"} for name in names]

        self.metas["Company"] = Meta([])
        self.metas["Item"] = Meta(
            fields("stock_uom", "disabled", "is_stock_item", "has_variants")
        )
        self.metas["UOM"] = Meta(fields("must_be_whole_number"))
        self.metas["Customer"] = Meta(fields("disabled"))
        self.metas["Jifeng Product Profile"] = Meta(fields("company", "item_code"))
        self.metas["BOM"] = Meta(
            fields(
                "company",
                "item",
                "quantity",
                "uom",
                "is_active",
                "track_semi_finished_goods",
            )
            + [
                {"fieldname": "items", "fieldtype": "Table", "options": "BOM Item"},
                {
                    "fieldname": "operations",
                    "fieldtype": "Table",
                    "options": "BOM Operation",
                },
            ]
        )
        self.metas["BOM Item"] = Meta(
            fields(
                "parent",
                "parenttype",
                "parentfield",
                "idx",
                "item_code",
                "bom_no",
                "stock_qty",
            )
        )
        self.metas["BOM Operation"] = Meta(
            fields(
                "parent",
                "parenttype",
                "parentfield",
                "idx",
                "operation",
                "time_in_mins",
            )
        )
        self.metas["Work Order"] = Meta(
            fields(*self.service.WO_REQUIRED, "jf_flow_order", "use_multi_level_bom")
        )
        self.metas["Work Order"].fields.extend(
            [
                Record(
                    fieldname="required_items",
                    fieldtype="Table",
                    options="Work Order Item",
                ),
                Record(
                    fieldname="operations",
                    fieldtype="Table",
                    options="Work Order Operation",
                ),
            ]
        )
        self.metas["Work Order Item"] = Meta(
            fields("item_code", "stock_uom", "required_qty")
        )
        self.metas["Work Order Operation"] = Meta(fields("operation", "workstation"))
        self.metas["Quotation"] = Meta(fields("jf_sample_order"))
        self.metas["Quotation Item"] = Meta(
            fields("jf_sample_order", "jf_sample_order_item", "parent")
        )
        self.metas["File"] = Meta(
            fields("file_url", "is_private", "attached_to_doctype", "attached_to_name")
        )
        for name in ("Company A", "Company B"):
            self.record("Company", name)
        self.record("UOM", "Piece", must_be_whole_number=0)
        for name in ("FG", "RAW"):
            self.record(
                "Item",
                name,
                stock_uom="Piece",
                disabled=0,
                is_stock_item=1,
                has_variants=0,
            )
        self.record("Customer", "CUST", disabled=0)
        self.record(
            "Jifeng Product Profile", "PROFILE", company="Company A", item_code="FG"
        )
        self.record(
            "BOM",
            "BOM-FG",
            company="Company A",
            item="FG",
            quantity=1,
            uom="Piece",
            is_active=1,
            track_semi_finished_goods=0,
            docstatus=1,
            items=[
                dict(
                    doctype="BOM Item",
                    name="BOM-LINE",
                    parent="BOM-FG",
                    parenttype="BOM",
                    parentfield="items",
                    idx=1,
                    item_code="RAW",
                    stock_qty=1,
                )
            ],
            operations=[],
        )

    def record(self, doctype, name, **values):
        self.records[(doctype, name)] = dict(
            doctype=doctype, name=name, modified="2026-10-04T00:00:00.123456", **values
        )

    def document(self, record):
        doc = Document(record)
        for df in doc.meta.get_table_fields():
            doc.set(
                df.fieldname,
                [self.document(row) for row in record.get(df.fieldname, [])],
            )
        return doc

    def get_doc(self, doctype, name, **kwargs):
        doc = self.document(self.records[(doctype, name)])
        if self.read_hook:
            self.read_hook(doctype, name, kwargs)
        return doc

    def new_doc(self, doctype):
        doc = Document(dict(doctype=doctype, __islocal=1, docstatus=0))
        if doctype == "Work Order":
            doc.use_multi_level_bom = 1
        return doc

    def columns(self, doctype, permission_type="read", **kwargs):
        return self.metas[doctype].get_valid_columns() - self.hidden_fields.get(
            (doctype, permission_type), set()
        )

    def matches(self, row, filters):
        for field, expected in filters.items():
            actual = row.get(field)
            if isinstance(expected, list):
                op, value = expected
                if op == "<" and not actual < value:
                    return False
                if op == "!=" and actual == value:
                    return False
                if op == "in" and actual not in value:
                    return False
            elif actual != expected:
                return False
        return True

    def get_value(self, doctype, filters, field, **kwargs):
        if isinstance(filters, str):
            return self.records.get((doctype, filters), {}).get(field)
        row = next(
            (
                row
                for (kind, _), row in self.records.items()
                if kind == doctype and self.matches(row, filters)
            ),
            None,
        )
        return row.get(field) if row else None

    def get_list(self, doctype, filters, **kwargs):
        names = sorted(
            name
            for (kind, name), row in self.records.items()
            if kind == doctype
            and name not in self.hidden
            and self.matches(row, filters)
            and (
                not kwargs.get("or_filters")
                or any(
                    test[1].strip("%") in (row.get(field) or "")
                    for field, test in kwargs["or_filters"].items()
                )
            )
        )
        start, length = (
            kwargs.get("limit_start", 0),
            kwargs.get("limit_page_length", 200),
        )
        names = names[start : start + length] if length else names[start:]
        if kwargs.get("fields"):
            return [
                {
                    field: self.records[(doctype, name)].get(field)
                    for field in kwargs["fields"]
                }
                for name in names
            ]
        return names

    def sql(self, query, parameters):
        return [
            dict(name=name)
            for (kind, name), row in self.records.items()
            if kind == "Quotation Item"
            and (
                row.get("jf_sample_order") in parameters
                or row.get("jf_sample_order_item") in parameters
            )
            and self.records.get(("Quotation", row.get("parent")), {}).get(
                "docstatus", 0
            )
            < 2
        ]

    def savepoint(self, name):
        self.snapshots[name] = deepcopy((self.records, self.inserts))

    def rollback(self, save_point=None):
        self.records, self.inserts = deepcopy(self.snapshots[save_point])

    def draft(self, name="SAMPLE", qty="2", bom="BOM-FG", count=1):
        values = dict(
            doctype=self.service.DOCTYPE,
            name=name,
            company="Company A",
            customer=None,
            transaction_date="2026-10-04",
            required_by="2026-10-10",
            remarks="  原文备注  ",
            docstatus=0,
            modified="2026-10-04T00:00:00.123456",
            __islocal=1,
            items=[],
        )
        for index in range(count):
            values["items"].append(
                dict(
                    doctype=self.service.CHILD,
                    name=name + "-LINE-" + str(index),
                    parent=name,
                    parenttype=self.service.DOCTYPE,
                    parentfield="items",
                    idx=index + 1,
                    item_code="FG",
                    product_profile=None,
                    stock_uom=None,
                    qty=qty,
                    bom=bom,
                    requirements=" 制作要求 ",
                    packaging=" 原始包装 ",
                    requirements_attachment=None,
                    notes="备注",
                )
            )
        return self.document(values)

    def persist(self, document):
        document.set("__islocal", 0)
        self.records[(document.doctype, document.name)] = document.as_dict()
        for row in document.items:
            self.records[(row.doctype, row.name)] = row.as_dict()

    def approved(self, count=1, **kwargs):
        document = self.draft(count=count, **kwargs)
        self.service.validate_sample_order(document)
        self.persist(document)
        document = self.get_doc(document.doctype, document.name)
        document.docstatus = 1
        self.service.validate_sample_order(document)
        self.service.before_submit_sample_order(document)
        self.persist(document)
        return self.get_doc(document.doctype, document.name)

    def make(self, document=None, row=0, request_id=None, **kwargs):
        document = document or self.approved()
        return self.service.make_sample_work_order(
            document.name,
            document.items[row].name,
            document.modified,
            request_id=request_id or str(uuid4()),
            **kwargs,
        )

    def test_same_item_rows_have_distinct_server_identities_and_exact_approval(self):
        document = self.approved(count=2)
        snapshot = json.loads(document.approved_snapshot)
        self.assertEqual(len({row.sample_identity for row in document.items}), 2)
        self.assertEqual([row["qty"] for row in snapshot["items"]], ["2", "2"])
        self.assertEqual(
            [row["stock_uom"] for row in snapshot["items"]], ["Piece", "Piece"]
        )
        self.assertEqual(snapshot["items"][0]["requirements"], " 制作要求 ")
        self.assertEqual(document.approved_digest, self.service._hash(snapshot))

    def test_customer_state_uses_read_columns_and_refuses_select_only_actor(self):
        native_columns = self.service._columns

        def columns(doctype, permission="read", parenttype=None):
            if doctype == "Customer" and permission == "select":
                return {"name", "modified"}
            return native_columns(doctype, permission, parenttype)

        with patch.object(self.service, "_columns", side_effect=columns):
            document = self.draft()
            document.customer = "CUST"
            self.service.validate_sample_order(document)
        self.denials.add(("Customer", "read"))
        with self.assertRaises(PermissionError):
            document = self.draft()
            document.customer = "CUST"
            self.service.validate_sample_order(document)

    def test_native_docstatus_integer_wrapper_is_not_a_content_change(self):
        class NativeDocStatus(int):
            pass

        self.assertEqual(self.service._freeze(1), self.service._freeze(NativeDocStatus(1)))
        self.assertNotEqual(self.service._freeze(1), self.service._freeze(NativeDocStatus(2)))
        self.assertNotEqual(self.service._freeze(True), self.service._freeze(1))

    def test_quantity_uses_current_native_precision_no_rounding_or_uom_guess(self):
        for value in (False, 0, -1, "NaN", "Infinity", "0.0001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.validate_sample_order(self.draft(qty=value))
        self.precision = 4
        document = self.approved(qty="0.0001")
        self.assertEqual(
            json.loads(document.approved_snapshot)["items"][0]["qty"], "0.0001"
        )
        self.records[("UOM", "Piece")]["must_be_whole_number"] = 1
        with self.assertRaisesRegex(ValueError, "准确整数"):
            self.service.validate_sample_order(self.draft(qty="1.001"))

    def test_disabled_nonstock_variant_and_wrong_unit_rejected(self):
        for field, value in (
            ("disabled", 1),
            ("is_stock_item", 0),
            ("has_variants", 1),
        ):
            original = self.records[("Item", "FG")][field]
            self.records[("Item", "FG")][field] = value
            with self.assertRaises(ValueError):
                self.service.validate_sample_order(self.draft())
            self.records[("Item", "FG")][field] = original
        document = self.draft()
        document.items[0].stock_uom = "Box"
        with self.assertRaisesRegex(ValueError, "不推测换算"):
            self.service.validate_sample_order(document)

    def test_product_profile_explicit_company_item_and_native_permission(self):
        document = self.draft()
        self.service.validate_sample_order(document)
        self.assertIsNone(document.items[0].product_profile)
        document = self.draft()
        document.items[0].product_profile = "PROFILE"
        self.hidden.add("PROFILE")
        with self.assertRaises(PermissionError):
            self.service.validate_sample_order(document)
        self.hidden.clear()
        self.records[("Jifeng Product Profile", "PROFILE")]["company"] = "Company B"
        with self.assertRaisesRegex(ValueError, "当前公司"):
            self.service.validate_sample_order(self.draft_with_profile())

    def draft_with_profile(self):
        document = self.draft()
        document.items[0].product_profile = "PROFILE"
        return document

    def test_native_submit_permission_and_client_snapshot_spoof_rejected(self):
        document = self.draft()
        document.approved_snapshot = "{}"
        with self.assertRaisesRegex(ValueError, "服务器生成"):
            self.service.validate_sample_order(document)
        self.denials.add((self.service.DOCTYPE, "submit"))
        document = self.draft()
        self.service.validate_sample_order(document)
        self.persist(document)
        with self.assertRaises(PermissionError):
            self.service.before_submit_sample_order(document)

    def test_approved_quantity_row_requirements_and_server_receipt_are_immutable(self):
        document = self.approved()
        for field, value in (
            ("qty", 3),
            ("requirements", "changed"),
            ("sample_identity", str(uuid4())),
            ("bom", None),
        ):
            edited = self.get_doc(document.doctype, document.name)
            edited.items[0].set(field, value)
            with self.assertRaises(ValueError):
                self.service.validate_sample_order(edited)
        edited = self.get_doc(document.doctype, document.name)
        edited.approved_by = "forged"
        with self.assertRaisesRegex(ValueError, "追溯字段"):
            self.service.validate_sample_order(edited)

    def test_bom_audit_timestamp_does_not_change_content_digest_but_content_drift_blocks_make(
        self,
    ):
        document = self.approved()
        digest = document.approved_digest
        self.records[("BOM", "BOM-FG")]["modified"] = "2026-10-04T00:00:01.123456"
        self.assertEqual(
            self.service.read_sample_source(document.name).approved_digest, digest
        )
        self.records[("BOM", "BOM-FG")]["quantity"] = 2
        with self.assertRaisesRegex(ValueError, "BOM 内容已变更"):
            self.make(document)

    def test_approved_source_requires_exact_version_and_submitted_state(self):
        document = self.approved()
        with self.assertRaises(RuntimeError):
            self.service.read_sample_source(document.name, "2020-01-01T00:00:00")
        self.records[(document.doctype, document.name)]["docstatus"] = 0
        with self.assertRaisesRegex(ValueError, "已批准"):
            self.service.read_sample_source(document.name)

    def test_manufacturing_readonly_sample_can_create_native_draft_work_order(self):
        document = self.approved()
        self.denials.add((self.service.DOCTYPE, "write"))
        result = self.make(document)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["doctype"], "Work Order")
        work = self.records[("Work Order", result["name"])]
        self.assertEqual(work["docstatus"], 0)
        self.assertEqual(work["qty"], 2.0)
        self.assertEqual(work["jf_sample_order_item"], document.items[0].name)
        self.assertEqual(work["jf_sample_source_digest"], document.approved_digest)
        self.assertEqual(self.expansion_defaults, [1])
        self.assertEqual(self.inserts, ["Work Order"])

    def test_no_native_work_order_create_permission_no_grant(self):
        document = self.approved()
        self.denials.add(("Work Order", "create"))
        with self.assertRaises(PermissionError):
            self.make(document)
        self.assertEqual(self.inserts, [])

    def test_native_bom_semifinished_flag_is_set_before_operation_expansion(self):
        self.records[("BOM", "BOM-FG")]["track_semi_finished_goods"] = 1
        document = self.approved()
        self.expansion_hook = lambda work: self.assertEqual(
            work.track_semi_finished_goods, 1
        )
        result = self.make(document)
        self.assertEqual(
            self.records[("Work Order", result["name"])]["track_semi_finished_goods"], 1
        )

    def test_uuid_receipt_idempotency_cross_row_and_new_uuid_active_row_rejection(self):
        document = self.approved(count=2)
        request = str(uuid4())
        result = self.make(document, request_id=request)
        self.assertEqual(self.make(document, request_id=request), result)
        self.assertEqual(self.service.get_sample_work_order_request(request), result)
        with self.assertRaisesRegex(ValueError, "不同样品"):
            self.make(document, row=1, request_id=request)
        with self.assertRaisesRegex(ValueError, "当前新请求未记录"):
            self.make(document)
        self.assertEqual(self.inserts, ["Work Order"])

    def test_unknown_uuid_get_readonly_and_denied_receipt_does_not_reveal_name(self):
        request = str(uuid4())
        before = deepcopy(self.records)
        self.assertEqual(
            self.service.get_sample_work_order_request(request),
            dict(status="absent", doctype="Work Order", request_id=request),
        )
        self.assertEqual(self.records, before)
        result = self.make()
        self.hidden.add(result["name"])
        with self.assertRaises(PermissionError):
            self.service.get_sample_work_order_request(result["request_id"])

    def test_canceled_work_order_requires_new_uuid_and_preserves_old_readonly_receipt(
        self,
    ):
        document = self.approved()
        request = str(uuid4())
        result = self.make(document, request_id=request)
        work = self.records[("Work Order", result["name"])]
        work["docstatus"] = 2
        self.assertEqual(self.make(document, request_id=request), result)
        next_result = self.make(document)
        self.assertNotEqual(next_result["name"], result["name"])
        self.assertEqual(len(self.inserts), 2)

    def test_raw_downstream_hidden_work_order_and_quotation_block_sample_cancel(self):
        document = self.approved()
        result = self.make(document)
        self.hidden.add(result["name"])
        with self.assertRaisesRegex(ValueError, "未取消"):
            self.service.before_cancel_sample_order(document)
        self.records[("Work Order", result["name"])]["docstatus"] = 2
        self.record("Quotation", "QUOTE", jf_sample_order=document.name, docstatus=0)
        self.hidden.add("QUOTE")
        with self.assertRaisesRegex(ValueError, "未取消"):
            self.service.before_cancel_sample_order(document)

    def test_native_work_order_source_spoof_flow_identity_and_quantity_edits_rejected(
        self,
    ):
        document = self.approved()
        result = self.make(document)
        work = self.get_doc("Work Order", result["name"])
        for field, value in (
            ("jf_sample_request_id", str(uuid4())),
            ("jf_sample_order_item", "other"),
            ("qty", 4),
            ("jf_flow_order", "FLOW"),
        ):
            changed = self.get_doc("Work Order", work.name)
            changed.set(field, value)
            with self.assertRaises(ValueError):
                self.service.validate_sample_work_order(changed)
        forged = self.new_doc("Work Order")
        for field in self.service.WO_REQUIRED:
            forged.set(field, work.get(field))
        forged.set("__islocal", 1)
        with self.assertRaisesRegex(ValueError, "入口生成"):
            self.service.validate_sample_work_order(forged)

    def test_clearing_all_sample_identity_fields_cannot_detach_or_delete_persisted_work_order(
        self,
    ):
        result = self.make()
        work = self.get_doc("Work Order", result["name"])
        for field in self.service.WO_SOURCE:
            work.set(field, None)
        with self.assertRaises(ValueError):
            self.service.validate_sample_work_order(work)
        with self.assertRaises(ValueError):
            self.service.prevent_sample_work_order_delete(work)
        with self.assertRaises(ValueError):
            self.service.prevent_sample_work_order_rename(work)

    def test_cancel_work_order_retains_history_even_when_bom_disabled(self):
        document = self.approved()
        result = self.make(document)
        self.records[("BOM", "BOM-FG")]["is_active"] = 0
        work = self.get_doc("Work Order", result["name"])
        work.docstatus = 2
        self.service.validate_sample_work_order(work)
        work.jf_sample_source_digest = "changed"
        with self.assertRaisesRegex(ValueError, "追溯身份"):
            self.service.validate_sample_work_order(work)

    def test_native_submitted_progress_update_keeps_history_without_new_source_eligibility(
        self,
    ):
        document = self.approved()
        result = self.make(document)
        self.records[("Work Order", result["name"])]["docstatus"] = 1
        self.records[("BOM", "BOM-FG")]["is_active"] = 0
        self.records[("Item", "RAW")]["disabled"] = 1
        work = self.get_doc("Work Order", result["name"])
        work.produced_qty = 1
        self.service.validate_sample_work_order(
            work, method="before_update_after_submit"
        )
        work.qty = 3
        with self.assertRaisesRegex(ValueError, "制作依据"):
            self.service.validate_sample_work_order(
                work, method="before_update_after_submit"
            )

    def test_hidden_component_or_bom_child_mask_refuses_whole_native_expansion(self):
        document = self.approved()
        self.hidden.add("RAW")
        with self.assertRaises(PermissionError):
            self.make(document)
        self.hidden.clear()
        self.hidden_fields[("BOM Item", "read")] = {"stock_qty"}
        with self.assertRaises(PermissionError):
            self.make(document)
        self.assertEqual(self.inserts, [])

    def test_expansion_source_change_and_native_insert_failure_roll_back_whole_request(
        self,
    ):
        document = self.approved()
        before = deepcopy((self.records, self.inserts))
        self.expansion_hook = lambda work: self.records[("Item", "RAW")].update(
            disabled=1
        )
        with self.assertRaises(ValueError):
            self.make(document)
        self.assertEqual((self.records, self.inserts), before)
        self.expansion_hook = None
        self.fail_insert = True
        with self.assertRaisesRegex(ValueError, "native insert failure"):
            self.make(document)
        self.assertEqual((self.records, self.inserts), before)
        self.assertEqual(self.frappe.db.after_commit._functions, [])
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))
        self.assertIsNone(self.service._MAKING.get())

    def test_rollback_cleanup_uncertainty_is_server_error_not_correctable_rejection(
        self,
    ):
        document = self.approved()
        self.fail_insert = True
        self.frappe.db.rollback = lambda **kwargs: (_ for _ in ()).throw(
            ValueError("rollback failed")
        )
        with self.assertRaises(self.service.SampleWorkOrderWriteUncertain) as failure:
            self.make(document)
        self.assertIsInstance(failure.exception, RuntimeError)
        self.assertIn("结果未知", str(failure.exception))

    def test_nested_bom_content_is_frozen_but_nested_audit_time_is_not(self):
        self.record(
            "BOM",
            "BOM-RAW",
            company="Company A",
            item="RAW",
            quantity=1,
            uom="Piece",
            is_active=1,
            docstatus=1,
            items=[],
            operations=[],
        )
        self.records[("BOM", "BOM-FG")]["items"][0]["bom_no"] = "BOM-RAW"
        document = self.approved()
        self.records[("BOM", "BOM-RAW")]["modified"] = "2026-10-04T00:00:02.123456"
        self.service.read_sample_source(document.name)
        self.records[("BOM", "BOM-RAW")]["quantity"] = 3
        with self.assertRaisesRegex(ValueError, "BOM 内容已变更"):
            self.make(document)

    def test_sensitive_source_parent_child_masks_and_late_revocation_block_read(self):
        document = self.approved()
        self.hidden_fields[(self.service.CHILD, "read")] = {"requirements"}
        with self.assertRaises(PermissionError):
            self.service.get_sample_order(document.name)
        self.hidden_fields.clear()
        calls = 0

        def late(kind, name, kwargs):
            nonlocal calls
            if kind == "Item":
                calls += 1
                if calls == 2:
                    self.hidden_fields[("Item", "read")] = {"stock_uom"}

        self.read_hook = late
        with self.assertRaises(PermissionError):
            self.service.read_sample_source(document.name)

    def test_historical_source_never_reads_work_order_descendants(self):
        document = self.approved()
        document.docstatus = 2
        self.persist(document)
        with patch.object(self.service, "_dto", side_effect=AssertionError("unexpected descendants")):
            history = self.service.read_sample_history(document.name)
            self.assertEqual(history.approved_digest, document.approved_digest)
        self.hidden_fields[(self.service.CHILD, "read")] = {"requirements"}
        with self.assertRaises(PermissionError):
            self.service.read_sample_history(document.name)

    def test_company_and_bom_identity_active_and_native_read_permissions(self):
        self.hidden.add("Company A")
        with self.assertRaises(PermissionError):
            self.service.validate_sample_order(self.draft())
        self.hidden.clear()
        for field, value in (
            ("company", "Company B"),
            ("item", "RAW"),
            ("docstatus", 0),
            ("is_active", 0),
        ):
            previous = self.records[("BOM", "BOM-FG")][field]
            self.records[("BOM", "BOM-FG")][field] = value
            with self.assertRaises(ValueError):
                self.service.validate_sample_order(self.draft())
            self.records[("BOM", "BOM-FG")][field] = previous

    def test_no_bom_approval_requires_explicit_effective_bom_without_qty_conversion(
        self,
    ):
        document = self.approved(bom=None)
        with self.assertRaises(ValueError):
            self.make(document)
        result = self.make(document, bom="BOM-FG")
        self.assertEqual(self.records[("Work Order", result["name"])]["qty"], 2.0)

    def test_rows_do_not_merge_and_quotation_quantity_is_not_in_sample_creation_contract(
        self,
    ):
        document = self.approved(count=2)
        first, second = self.make(document, row=0), self.make(document, row=1)
        self.assertNotEqual(first["name"], second["name"])
        snapshot = json.loads(document.approved_snapshot)
        self.assertNotIn("quotation_qty", snapshot["items"][0])
        self.assertNotIn("max_quotation_qty", snapshot["items"][0])

    def test_attachment_must_be_readable_private_and_bound_to_saved_parent(self):
        document = self.draft()
        document.items[0].requirements_attachment = "/private/files/qa.txt"
        self.record(
            "File",
            "FILE",
            file_url="/private/files/qa.txt",
            is_private=1,
            attached_to_doctype=self.service.DOCTYPE,
            attached_to_name="OTHER",
        )
        with self.assertRaisesRegex(ValueError, "本样品单"):
            self.service.validate_sample_order(document)
        self.records[("File", "FILE")]["attached_to_name"] = document.name
        document = self.draft()
        document.items[0].requirements_attachment = "/private/files/qa.txt"
        self.service.validate_sample_order(document)
        self.records[("File", "FILE")]["is_private"] = 0
        with self.assertRaises(ValueError):
            self.service.sample_order_onload(document)

    def test_delete_rename_and_normal_work_order_no_sample_scope(self):
        for action in (
            self.service.prevent_sample_order_delete,
            self.service.prevent_sample_order_rename,
        ):
            with self.assertRaises(ValueError):
                action(self.draft())
        normal = self.new_doc("Work Order")
        self.service.validate_sample_work_order(normal)
        self.service.prevent_sample_work_order_delete(normal)
        linked = self.new_doc("Work Order")
        linked.jf_sample_order = "SAMPLE"
        with self.assertRaises(ValueError):
            self.service.validate_sample_work_order(linked)
        with self.assertRaises(ValueError):
            self.service.prevent_sample_work_order_delete(linked)

    def test_complete_list_over_1000_and_clamped_offset_with_readonly_rows(self):
        document = self.draft(bom=None)
        self.service.validate_sample_order(document)
        self.persist(document)
        template = document.as_dict()
        for index in range(1004):
            record = deepcopy(template)
            record["name"] = "BULK-" + str(index).zfill(4)
            for row in record["items"]:
                row["parent"] = record["name"]
                row["name"] = record["name"] + "-ROW"
            self.records[(self.service.DOCTYPE, record["name"])] = record
        result = self.service.query_sample_orders(
            "Company A", start=9999, page_length=100
        )
        self.assertEqual(result["total"], 1005)
        self.assertEqual(result["start"], 1000)
        self.assertEqual(len(result["rows"]), 5)
        self.assertTrue(result["readonly"])


if __name__ == "__main__":
    unittest.main()
