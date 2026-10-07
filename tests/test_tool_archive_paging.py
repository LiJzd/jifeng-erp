"""Actual archive paging over complete, isolated native permission projections."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestToolArchivePaging(unittest.TestCase):
    def setUp(self):
        # Compose the existing fixture without inheriting/discovering its tests.
        self.fixtures = load("paging_tool_fixtures", "tests/test_tool_archive.py")
        self.env = self.fixtures.TestToolArchive(
            "test_metadata_preserves_existing_roles_business_identity_and_original_order")
        self.addCleanup(self.env.doCleanups)
        self.env.setUp()
        self.T = self.env.module
        schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_plate_modeling/jifeng_plate_modeling.json").read_text(encoding="utf-8"))
        doctype = "Jifeng Plate Modeling"
        self.env.fields[doctype] = {df["fieldname"]: self.fixtures.Record(df) for df in schema["fields"]}
        self.env.metadata[doctype] = types.SimpleNamespace(
            get_field=lambda field: self.env.fields[doctype].get(field))
        self.env.fields["Jifeng Expense Voucher"] = {
            field: self.fixtures.Record(fieldname=field, fieldtype="Data")
            for field in ("name", "company", "docstatus")}
        self.M = load("paging_modeling_actual", "jifeng_erp/plate_modeling.py")
        package = types.ModuleType("jifeng_erp")
        package.__path__ = [str(ROOT / "jifeng_erp")]
        package.tool_archive, package.plate_modeling = self.T, self.M
        modules = patch.dict(sys.modules, {"jifeng_erp": package,
            "jifeng_erp.tool_archive": self.T, "jifeng_erp.plate_modeling": self.M})
        modules.start()
        self.addCleanup(modules.stop)
        self.P = load("tool_archive_paging_actual", "jifeng_erp/tool_archive_paging.py")

    def add(self, kind, index=0, **values):
        name = "SYNTHETIC-" + kind.upper() + f"-{index:04d}"
        fields = self.M.EDIT_FIELDS if kind == "modeling" else self.T.EDIT_FIELDS[kind]
        row = {field: None for field in fields}
        row.update(doctype=self.P.DOCTYPES[kind], name=name, company="C-1", __islocal=0,
            modified="2026-10-01T12:00:00.123456", legacy_number=f"{index:04d}",
            product_code="原产品", legacy_copy=0, mold_no=name + "-BUSINESS",
            plate_no=name + "-BUSINESS", model_number=name + "-BUSINESS",
            quantity=2.5 if kind == "mold" else "0001.0000000000000007",
            amount=12.25, assessment_score="-0", scrapped=0, retired=0,
            mold_date="2026-10-01", plate_date="2026-10-01", model_date="2026-10-01",
            reference_unit_price="0002.000000000000001")
        row.update(values)
        self.env.documents[name] = self.fixtures.Record(row)
        return self.env.documents[name]

    def many(self, kind, count=130):
        return [self.add(kind, index) for index in range(count)]

    def query(self, kind="mold", **options):
        return self.P.query_archives(kind, "C-1", **options)

    def legacy(self, kind, **options):
        return (self.M.list_records("C-1", **options) if kind == "modeling"
                else self.T.list_records(kind, "C-1", **options))

    def second_round(self, change):
        """Mutate before round two; each individual native projection is coherent."""
        calls = 0

        def get_list(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                change()
            return self.env.get_list(*args, **kwargs)

        self.env.frappe.get_list.reset_mock()
        self.env.frappe.get_list.side_effect = get_list

    def assert_changed(self, kind):
        with self.assertRaisesRegex(PermissionError, "已变化"):
            self.query(kind, page_size=25)
        self.assertEqual(self.env.frappe.get_list.call_count, 2)
        self.env.frappe.get_list.side_effect = self.env.get_list

    def test_three_types_page_all_130_records_at_25_50_and_100(self):
        for kind in self.P.DOCTYPES:
            records = self.many(kind)
            before = deepcopy((self.env.documents, self.env.versions, self.env.snapshots))
            for size in (25, 50, 100):
                with self.subTest(kind=kind, size=size):
                    self.env.frappe.get_list.reset_mock()
                    total_pages = (130 + size - 1) // size
                    names = []
                    for number in range(1, total_pages + 1):
                        result = self.query(kind, page=number, page_size=size)
                        self.assertEqual(result["record_type"], kind)
                        self.assertEqual(result["company"], "C-1")
                        self.assertEqual(result["search"], "")
                        self.assertEqual(result["include_inactive"], 0)
                        self.assertEqual((result["requested_page"], result["page"], result["page_size"]),
                                         (number, number, size))
                        self.assertEqual((result["total"], result["total_pages"]), (130, total_pages))
                        self.assertEqual(len(result["rows"]), min(size, 130 - (number - 1) * size))
                        names.extend(row["name"] for row in result["rows"])
                    self.assertEqual(names, [record.name for record in records])
                    self.assertEqual(len(set(names)), 130)
                    self.assertEqual(self.env.frappe.get_list.call_count, total_pages * 2)
                    for call in self.env.frappe.get_list.call_args_list:
                        self.assertEqual(call.kwargs["limit_page_length"], 0)
                        self.assertEqual(call.kwargs["filters"]["company"], "C-1")
                        self.assertEqual(call.kwargs["order_by"], "model_date desc, name asc"
                                         if kind == "modeling" else "modified desc, name asc")
            self.assertEqual((self.env.documents, self.env.versions, self.env.snapshots), before)
        self.env.frappe.get_all.assert_not_called()

    def test_requested_page_clamps_to_shrunk_tail_and_empty_first_page(self):
        for kind in self.P.DOCTYPES:
            with self.subTest(kind=kind):
                records = self.many(kind)
                result = self.query(kind, page=6, page_size=25)
                self.assertEqual([row["name"] for row in result["rows"]], [row.name for row in records[125:]])
                for record in records[120:]:
                    del self.env.documents[record.name]
                shrunk = self.query(kind, page=6, page_size=25)
                self.assertEqual((shrunk["requested_page"], shrunk["page"], shrunk["total"], shrunk["total_pages"]),
                                 (6, 5, 120, 5))
                self.assertEqual([row["name"] for row in shrunk["rows"]], [row.name for row in records[100:120]])
                for record in records[:120]:
                    del self.env.documents[record.name]
                empty = self.query(kind, page="2147483647", page_size=100)
                self.assertEqual((empty["requested_page"], empty["page"], empty["total"], empty["total_pages"], empty["rows"]),
                                 (2147483647, 1, 0, 0, []))

    def test_counts_only_company_and_record_authorized_rows_with_readable_fields(self):
        for kind, doctype in self.P.DOCTYPES.items():
            with self.subTest(kind=kind):
                records = self.many(kind)
                for row in records[120:]:
                    row.company = "C-2"
                for row in records[110:120]:
                    self.env.denials.add((row.name, "read"))
                self.env.denials.add((records[0].name, "write"))
                self.env.hidden[doctype, "read"] = {"product_code"}
                self.env.hidden[doctype, "write"] = {"legacy_number"}
                result = self.query(kind, page=5, page_size=25)
                self.assertEqual((result["total"], result["total_pages"], len(result["rows"])), (110, 5, 10))
                self.assertEqual([row["name"] for row in result["rows"]], [row.name for row in records[100:110]])
                first = self.query(kind, page_size=25)["rows"][0]
                self.assertFalse(first["can_write"])
                for row in result["rows"]:
                    values = row if kind == "modeling" else row["values"]
                    self.assertNotIn("product_code", values)
                    self.assertNotIn("product_code", row["field_meta"])
                    self.assertTrue(row["field_meta"]["legacy_number"]["read_only"])
                self.env.hidden.clear()
                self.env.denials.clear()

    def test_company_doctype_and_required_field_permission_denials_fail_before_query(self):
        for kind, doctype in self.P.DOCTYPES.items():
            self.add(kind)
            required = {"name", "modified", "company"}
            if kind != "modeling":
                required.update((self.T.TITLE[kind], self.T.INACTIVE[kind]))
            cases = [("company", None), ("doctype", None), ("company_field", None)]
            cases.extend(("field", field) for field in required)
            for category, field in cases:
                with self.subTest(kind=kind, category=category, field=field):
                    self.env.denied_companies.clear()
                    self.env.denials.clear()
                    self.env.hidden.clear()
                    if category == "company":
                        self.env.denied_companies.add("C-1")
                    elif category == "doctype":
                        self.env.denials.add((doctype, "read"))
                    elif category == "company_field":
                        self.env.hidden["Company", "select"] = {"name"}
                    else:
                        self.env.hidden[doctype, "read"] = {field}
                    self.env.frappe.get_list.reset_mock()
                    with self.assertRaises(PermissionError):
                        self.query(kind)
                    self.env.frappe.get_list.assert_not_called()
            self.env.denied_companies.clear()
            self.env.denials.clear()
            self.env.hidden.clear()

    def test_inactive_filter_count_and_selection_state(self):
        for kind in ("mold", "plate"):
            with self.subTest(kind=kind):
                records = self.many(kind)
                for record in records[100:]:
                    record[self.T.INACTIVE[kind]] = 1
                active = self.query(kind, page_size=100)
                self.assertEqual((active["total"], active["total_pages"]), (100, 1))
                all_rows = self.query(kind, include_inactive="1", page=2, page_size=100)
                self.assertEqual((all_rows["total"], all_rows["total_pages"], len(all_rows["rows"])), (130, 2, 30))
                self.assertTrue(all(not row["can_select"] for row in all_rows["rows"]))
                self.assertTrue(all(row["values"][self.T.INACTIVE[kind]] == 1 for row in all_rows["rows"]))

    def test_second_full_round_rejects_changed_off_page_values(self):
        for kind in self.P.DOCTYPES:
            with self.subTest(kind=kind):
                records = self.many(kind)
                last = records[-1]
                self.second_round(lambda: last.update(product_code="页外改动"))
                self.assert_changed(kind)

    def test_second_full_round_rejects_off_page_insert_delete_and_record_permission_loss(self):
        for kind in self.P.DOCTYPES:
            records = self.many(kind)
            for action in ("insert", "delete", "read"):
                with self.subTest(kind=kind, action=action):
                    if action == "insert":
                        change = lambda: self.add(kind, 130)
                    elif action == "delete":
                        change = lambda: self.env.documents.pop(records[-1].name)
                    else:
                        change = lambda: self.env.denials.add((records[-2].name, "read"))
                    self.second_round(change)
                    self.assert_changed(kind)
            self.env.denials.clear()

    def test_two_rounds_reject_field_permissions_changing_even_without_rows(self):
        for kind, doctype in self.P.DOCTYPES.items():
            for permission in ("read", "write"):
                with self.subTest(kind=kind, permission=permission):
                    self.second_round(lambda: self.env.hidden.update({(doctype, permission): {"product_code"}}))
                    self.assert_changed(kind)
                    self.env.hidden.clear()

    def test_second_round_rechecks_final_company_and_doctype_permissions(self):
        for kind, doctype in self.P.DOCTYPES.items():
            for permission in ("company", "doctype"):
                with self.subTest(kind=kind, permission=permission):
                    calls = 0

                    def get_list(*args, **kwargs):
                        nonlocal calls
                        calls += 1
                        rows = self.env.get_list(*args, **kwargs)
                        if calls == 2:
                            if permission == "company":
                                self.env.denied_companies.add("C-1")
                            else:
                                self.env.denials.add((doctype, "read"))
                        return rows

                    # Empty projections reach the final guard without per-record reads.
                    self.env.frappe.get_list.reset_mock()
                    self.env.frappe.get_list.side_effect = get_list
                    with self.assertRaises(PermissionError):
                        self.query(kind)
                    self.assertEqual(self.env.frappe.get_list.call_count, 2)
                    self.env.denied_companies.clear()
                    self.env.denials.clear()
                    self.env.frappe.get_list.side_effect = self.env.get_list

    def test_off_page_modeling_link_and_bound_image_permission_changes_reject(self):
        records = self.many("modeling")
        last = records[-1]
        last.expense_voucher = "EXPENSE"
        self.env.documents["EXPENSE"] = self.fixtures.Record(doctype="Jifeng Expense Voucher",
            name="EXPENSE", company="C-1", docstatus=1)
        self.second_round(lambda: self.env.documents["EXPENSE"].update(docstatus=2))
        self.assert_changed("modeling")
        self.second_round(lambda: self.env.denials.add(("EXPENSE", "read")))
        self.assert_changed("modeling")
        self.env.denials.clear()
        last.design_image = "/private/files/off-page.png"
        self.env.documents["FILE"] = self.fixtures.Record(doctype="File", name="FILE",
            company="C-1", file_url=last.design_image, is_private=1,
            attached_to_doctype=self.M.DOCTYPE, attached_to_name=last.name, attached_to_field="design_image")
        self.second_round(lambda: self.env.download_denials.add("FILE"))
        self.assert_changed("modeling")

    def test_hidden_keyword_fields_cannot_select_or_count_records(self):
        for kind, doctype in self.P.DOCTYPES.items():
            with self.subTest(kind=kind):
                self.add(kind, product_code="HIDDEN-NEEDLE")
                self.env.hidden[doctype, "read"] = {"product_code"}
                self.env.frappe.get_list.reset_mock()
                result = self.query(kind, search="HIDDEN-NEEDLE")
                self.assertEqual((result["total"], result["rows"]), (0, []))
                for call in self.env.frappe.get_list.call_args_list:
                    self.assertNotIn("product_code", call.kwargs["fields"])
                    self.assertNotIn("product_code", {field for field, _, _ in call.kwargs["or_filters"]})
                self.env.hidden.clear()
        self.env.hidden[self.M.DOCTYPE, "read"] = set(self.M.RAW_FIELDS)
        self.env.frappe.get_list.reset_mock()
        with self.assertRaisesRegex(PermissionError, "关键词查询字段"):
            self.query("modeling", search="HIDDEN-NEEDLE")
        self.env.frappe.get_list.assert_not_called()

    def test_search_counts_full_authorized_matches_past_first_100(self):
        for kind in self.P.DOCTYPES:
            with self.subTest(kind=kind):
                records = self.many(kind)
                for row in records[120:]:
                    row.product_code = "OTHER"
                self.env.denials.add((records[119].name, "read"))
                result = self.query(kind, search="原产品", page=5, page_size=25)
                self.assertEqual((result["search"], result["total"], result["total_pages"]), ("原产品", 119, 5))
                self.assertEqual([row["name"] for row in result["rows"]], [row.name for row in records[100:119]])
                self.env.denials.clear()

    def test_modeling_raw_text_full_metadata_and_legacy_array_shape(self):
        records = self.many("modeling")
        raw = {"legacy_number": " 0007 原号 ", "model_number": " 模型原号 ", "product_code": " 原产品 ",
            "reference_unit_price": "0002.000000000000001", "modeler_text": " 旧建模人员 ",
            "remarks": " 说明\n第二行 ", "legacy_copy": 1}
        records[-1].update(raw)
        self.env.hidden[self.M.DOCTYPE, "write"] = {"reference_unit_price"}
        page = self.query("modeling", page=6, page_size=25)
        row = page["rows"][-1]
        for field, value in raw.items():
            self.assertEqual(row[field], value)
        self.assertNotIn("values", row)
        self.assertEqual(row["version"], records[-1].modified)
        self.assertEqual(set(row["field_meta"]), set(self.M.FIELDS))
        self.assertTrue(all(set(meta) == {"fieldtype", "options", "reqd", "read_only", "description"}
                            for meta in row["field_meta"].values()))
        self.assertTrue(row["field_meta"]["company"]["read_only"])
        self.assertTrue(row["field_meta"]["reference_unit_price"]["read_only"])
        self.assertTrue(row["can_write"])
        self.assertEqual(row["image_state"], {"url": "", "state": "未上传"})
        self.assertEqual(row["expense_voucher_state"], "未关联")
        legacy = self.legacy("modeling")
        self.assertIsInstance(legacy, list)
        self.assertEqual(len(legacy), 130)
        self.assertTrue(all("field_meta" not in value and "can_write" not in value for value in legacy))
        self.assertEqual({key: value for key, value in row.items() if key not in ("field_meta", "can_write")}, legacy[-1])

    def test_mold_and_plate_legacy_arrays_preserve_complete_dto_shapes(self):
        for kind in ("mold", "plate"):
            with self.subTest(kind=kind):
                self.many(kind)
                legacy = self.legacy(kind)
                self.assertIsInstance(legacy, list)
                self.assertEqual(len(legacy), 130)
                last_page = self.query(kind, page=2, page_size=100)
                self.assertEqual(last_page["rows"], legacy[100:])
                self.assertEqual(set(legacy[0]), {"record_type", "name", "version", "values", "field_meta",
                    "image_states", "can_write", "can_select", "request_id", "request_digest"})

    def test_page_and_page_size_reject_noncanonical_or_out_of_range_values(self):
        invalid = (None, True, False, 0, -1, 1.0, 1.5, "", "0", "01", " 1", "1 ", "+1", "1.0", "1e2", [], {})
        for field, label, maximum in (("page", "页码", 2147483647), ("page_size", "每页条数", 100)):
            for value in (*invalid, maximum + 1, str(maximum + 1), "10000000000"):
                with self.subTest(field=field, value=value):
                    self.env.frappe.get_list.reset_mock()
                    with self.assertRaisesRegex(ValueError, label):
                        self.query(**{field: value})
                    self.env.frappe.get_list.assert_not_called()
        for size in (1, "25", 50, "100"):
            with self.subTest(valid_size=size):
                result = self.query(page="2147483647", page_size=size)
                self.assertEqual(result["requested_page"], 2147483647)
                self.assertEqual(result["page_size"], int(size))

    def test_include_inactive_accepts_only_exact_check_values_and_modeling_has_no_inactive(self):
        for value in (None, 0.0, 1.0, 2, -1, "", "yes", "true", "False", " 0", "01", [], {}):
            for kind in self.P.DOCTYPES:
                with self.subTest(kind=kind, value=value):
                    self.env.frappe.get_list.reset_mock()
                    with self.assertRaisesRegex(ValueError, "勾选"):
                        self.query(kind, include_inactive=value)
                    self.env.frappe.get_list.assert_not_called()
        for kind in self.P.DOCTYPES:
            for value in (False, 0, "0"):
                self.assertEqual(self.query(kind, include_inactive=value)["include_inactive"], 0)
        for value in (True, 1, "1"):
            for kind in ("mold", "plate"):
                self.assertEqual(self.query(kind, include_inactive=value)["include_inactive"], 1)
            self.env.frappe.get_list.reset_mock()
            with self.assertRaisesRegex(ValueError, "没有报废状态"):
                self.query("modeling", include_inactive=value)
            self.env.frappe.get_list.assert_not_called()

    def test_login_type_company_and_search_validation_precedes_record_query(self):
        self.env.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.query()
        self.env.frappe.session.user = "stock@example.invalid"
        for kind in (None, "", "MOLD", "employee", [], {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.P.query_archives(kind, "C-1")
        for company in (None, "", " C-1", "C-1 ", [], {}):
            with self.subTest(company=company), self.assertRaises(ValueError):
                self.P.query_archives("mold", company)
        for kind in self.P.DOCTYPES:
            for search in (None, 1, True, [], {}, "x" * 141):
                with self.subTest(kind=kind, search=search), self.assertRaises(ValueError):
                    self.query(kind, search=search)
        self.env.frappe.get_list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
