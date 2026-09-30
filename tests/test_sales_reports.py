"""Sales documents keep their business identity through native POS consolidation."""

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def __getattribute__(self, name):
        if name == "items" and dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        return super().__getattribute__(name)


class TestSalesReports(unittest.TestCase):
    def setUp(self):
        self.hidden, self.full_denied = set(), set()
        self.permissions = {"Sales Invoice", "POS Invoice", "POS Closing Entry", "POS Invoice Merge Log", "Item", "Company", "Customer", "POS Profile"}
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict = Record
        self.frappe.PermissionError = PermissionError
        self.frappe.ValidationError = ValueError
        self.frappe.session = Record(user="sales@example.test")

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        self.frappe.has_permission = Mock(side_effect=lambda doctype, *args, **kwargs: doctype in self.permissions)
        self.frappe.get_list = Mock(side_effect=lambda doctype, filters=None, **kwargs: self.query(doctype, filters, visible=True, **kwargs))
        self.frappe.get_all = Mock(side_effect=self.query)
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[doctype][name])
        self.frappe.get_cached_doc = self.frappe.get_doc
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.flt = lambda value, *args: float(value or 0)
        utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(str(value))
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, kind="Data", options=None, width=130: {
            "fieldname": field, "label": label, "fieldtype": kind, "options": options,
        }

        def decimal_number(value):
            result = Decimal(str(value if value not in (None, "") else 0))
            if not result.is_finite():
                raise ValueError("有限数字")
            return result

        def report_scope(filters, doctypes):
            filters = Record(filters or {})
            if self.frappe.session.user == "Guest":
                raise PermissionError("没有读取权限")
            if not all(filters.get(field) for field in ("company", "from_date", "to_date")):
                raise ValueError("请选择公司、开始日期和结束日期")
            if filters.from_date > filters.to_date:
                raise ValueError("开始日期不能晚于结束日期")
            return filters, filters.from_date, filters.to_date

        scope.decimal_number = decimal_number
        scope.report_scope = Mock(side_effect=report_scope)
        scope.readable_documents = Mock(side_effect=self.readable)
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "sales_reports.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                    "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location("sales_reports_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.scope = scope
        self.filters = Record(company="JF", from_date="2026-09-01", to_date="2026-09-30")
        self.item = Record(name="SII-1", idx=1, item_code="FG", item_name="成品", uom="Box",
                           stock_uom="Nos", qty=2, conversion_factor=3, stock_qty=6,
                           warehouse="MAIN", jf_flow_order="FLOW", amount=100,
                           base_amount=700, net_amount=100, base_net_amount=700)
        self.sale = self.document("Sales Invoice", "SI-1", [self.item])
        self.documents = {
            "Sales Invoice": {self.sale.name: self.sale}, "POS Invoice": {}, "POS Closing Entry": {}, "POS Invoice Merge Log": {},
            "Item": {"FG": Record(name="FG", stock_uom="Nos", is_stock_item=1,
                                    uoms=[Record(uom="Box", conversion_factor=3)])},
            "Company": {"JF": Record(name="JF", default_currency="CNY")},
            "Customer": {name: Record(name=name) for name in ("CUSTOMER", "OTHER")},
            "POS Profile": {name: Record(name=name, company="JF") for name in ("POS-PROFILE", "OTHER")},
        }
        for doctype, documents in self.documents.items():
            for doc in documents.values():
                self.attach_permissions(doctype, doc)

    def attach_permissions(self, doctype, doc):
        doc.has_permission = lambda *args, **kwargs: doctype in self.permissions and (doctype, doc.name) not in self.hidden | self.full_denied

        def check_permission(*args, **kwargs):
            if not doc.has_permission():
                raise PermissionError("来源读取权限")

        doc.check_permission = check_permission
        return doc

    def document(self, doctype, name, items):
        return self.attach_permissions(doctype, Record(
            doctype=doctype, name=name, company="JF", docstatus=1, posting_date="2026-09-05",
            posting_time="10:00:00", is_return=0, is_pos=0, is_consolidated=0,
            status="Paid", customer="CUSTOMER", pos_profile="POS-PROFILE", owner="cashier@example.test",
            currency="USD", conversion_rate=7, net_total=100, base_net_total=700,
            grand_total=110, base_grand_total=770, paid_amount=130, base_paid_amount=910,
            change_amount=15, base_change_amount=105, write_off_amount=0, base_write_off_amount=0,
            loyalty_amount=35, redeem_loyalty_points=1, items=items,
            payments=[Record(name=f"{name}-PAY-1", mode_of_payment="Cash", account="CASH", amount=125, base_amount=875)],
        ))

    def matches(self, doc, query):
        for field, value in (query or {}).items():
            actual = doc.get(field)
            if isinstance(value, (list, tuple)):
                operator, target = value
                if operator == "between" and not str(target[0]) <= str(actual) <= str(target[1]):
                    return False
                if operator == "in" and actual not in target:
                    return False
                if operator == "not in" and actual in target:
                    return False
                if operator in ("!=", "<>") and actual == target:
                    return False
                if operator == "=" and actual != target:
                    return False
                if operator == "is" and target == "set" and actual in (None, ""):
                    return False
                if operator == "is" and target == "not set" and actual not in (None, ""):
                    return False
            elif actual != value:
                return False
        return True

    def query(self, doctype, filters=None, or_filters=None, pluck=None, visible=False, **kwargs):
        if doctype == "POS Invoice Reference":
            docs = []
            for parenttype in ("POS Invoice Merge Log", "POS Closing Entry"):
                for parent in self.documents.get(parenttype, {}).values():
                    for child in parent.get("pos_invoices") or []:
                        docs.append(Record(child, parent=parent.name, parenttype=parenttype, docstatus=parent.docstatus))
        elif doctype.endswith(" Item"):
            docs = []
            for parent in self.documents.get(doctype[:-5], {}).values():
                for child in parent.items:
                    docs.append(Record(child, parent=parent.name, parenttype=doctype[:-5], docstatus=parent.docstatus))
        else:
            docs = list(self.documents.get(doctype, {}).values())
        result = [doc for doc in docs if self.matches(doc, filters)
                  and (not or_filters or any(self.matches(doc, {field: value}) for field, value in or_filters.items()))
                  and (not visible or (doctype in self.permissions and (doctype, doc.name) not in self.hidden))]
        return list(dict.fromkeys(doc.get(pluck) for doc in result)) if pluck else result

    def readable(self, doctype, query, complete=False):
        candidates = self.query(doctype, query)
        documents = {doc.name: doc for doc in candidates if doc.has_permission("read")}
        if complete and set(doc.name for doc in candidates) != set(documents):
            raise PermissionError("来源读取权限")
        return documents

    def transaction_rows(self, **filters):
        return self.module.sales_transaction_ledger({**self.filters, **filters})[1]

    def posted_rows(self, **filters):
        return self.module.posted_sales_ledger({**self.filters, **filters})[1]

    def add_pos(self, name="POS-1"):
        item = Record(self.item, name=f"{name}-ITEM")
        pos = self.document("POS Invoice", name, [item])
        pos.update(is_pos=1, is_created_using_pos=1)
        self.documents["POS Invoice"][name] = pos
        return pos

    def add_mirror(self, pos=None, name="SI-MERGED"):
        pos = pos or self.add_pos()
        items = [Record(item, name=f"{name}-ITEM-{index}", pos_invoice=pos.name, pos_invoice_item=item.name)
                 for index, item in enumerate(pos.items, 1)]
        mirror = self.document("Sales Invoice", name, items)
        mirror.update(is_pos=1, is_consolidated=1, is_return=pos.is_return, return_against=pos.get("return_against"))
        pos.update(consolidated_invoice=name, status="Consolidated")
        self.documents["Sales Invoice"][name] = mirror
        return mirror

    def add_return(self, source=None, name="RETURN"):
        source = source or self.sale
        reference = "sales_invoice_item" if source.doctype == "Sales Invoice" else "pos_invoice_item"
        item = Record(source.items[0], name=f"{name}-ITEM", qty=-1, stock_qty=-3,
                      amount=-50, base_amount=-350, net_amount=-50, base_net_amount=-350)
        item[reference] = source.items[0].name
        returned = self.document(source.doctype, name, [item])
        returned.update(is_return=1, is_pos=source.is_pos, return_against=source.name,
                        posting_date="2026-09-30", posting_time="11:00:00", net_total=-50, base_net_total=-350,
                        grand_total=-55, base_grand_total=-385, paid_amount=-55, base_paid_amount=-385,
                        change_amount=0, base_change_amount=0,
                        payments=[Record(name=f"{name}-PAY", amount=-55, base_amount=-385,
                                         mode_of_payment="Cash", account="CASH")])
        self.documents[source.doctype][name] = returned
        return returned

    def add_group_merge(self):
        first = self.add_pos()
        second = self.add_pos("POS-2")
        second.customer = "SECOND-CUSTOMER"
        invoice = self.add_mirror(first)
        second.update(consolidated_invoice=invoice.name, status="Consolidated")
        invoice.customer = "MERGE-CUSTOMER"
        invoice.pos_profile = ""
        invoice.items.append(Record(second.items[0], name="GROUP-SII-2", idx=2,
                                    pos_invoice=second.name, pos_invoice_item=second.items[0].name))
        log = self.attach_permissions("POS Invoice Merge Log", Record(
            name="GROUP-LOG", company="JF", docstatus=1, merge_invoices_based_on="Customer Group",
            customer=invoice.customer, customer_group="GROUP", consolidated_invoice=invoice.name,
            pos_invoices=[Record(pos_invoice=first.name), Record(pos_invoice=second.name)],
        ))
        self.documents["POS Invoice Merge Log"][log.name] = log
        return invoice, first, second, log

    def test_header_currency_and_stored_loyalty_inclusive_payment_remain_distinct(self):
        row = self.transaction_rows()[0]
        self.assertEqual((row["currency"], row["company_currency"], row["conversion_rate"]), ("USD", "CNY", 7))
        self.assertEqual((row["paid_amount"], row["base_paid_amount"]), (130, 910))
        self.assertEqual((row["gross_payment_amount"], row["base_gross_payment_amount"]), (125, 875))
        self.assertEqual((row["net_tender"], row["base_net_tender"]), (110, 770))
        self.assertEqual((row["net_total"], row["base_net_total"], row["grand_total"], row["base_grand_total"]), (100, 700, 110, 770))
        self.assertEqual(row["base_loyalty_amount"], 35)

    def test_change_deducts_once_when_two_modes_use_the_same_cash_account(self):
        self.sale.payments.append(Record(name="SECOND-PAY", amount=25, base_amount=175, mode_of_payment="Other Cash", account="CASH"))
        row = self.transaction_rows()[0]
        self.assertEqual((row["gross_payment_amount"], row["net_tender"], row["base_net_tender"]), (150, 135, 945))

    def test_write_off_is_not_counted_as_cash_tender(self):
        self.sale.update(write_off_amount=4, base_write_off_amount=28)
        row = self.transaction_rows()[0]
        self.assertEqual((row["write_off_amount"], row["base_write_off_amount"], row["net_tender"]), (4, 28, 110))

    def test_business_ledger_counts_pos_before_and_after_consolidation_once(self):
        pos = self.add_pos()
        before = self.transaction_rows()
        self.assertEqual({row["document"] for row in before}, {self.sale.name, pos.name})
        mirror = self.add_mirror(pos)
        after = self.transaction_rows()
        self.assertEqual({row["document"] for row in after}, {self.sale.name, pos.name})
        self.assertEqual(sum(row["base_net_total"] for row in after), sum(row["base_net_total"] for row in before))
        self.assertNotIn(mirror.name, {row["document"] for row in after})

    def test_posted_ledger_includes_only_submitted_sales_invoices(self):
        pos = self.add_pos()
        self.assertEqual({row["document"] for row in self.posted_rows()}, {self.sale.name})
        mirror = self.add_mirror(pos)
        self.assertEqual({row["document"] for row in self.posted_rows()}, {self.sale.name, mirror.name})

    def test_is_consolidated_flag_alone_cannot_erase_an_independent_invoice(self):
        self.sale.update(is_consolidated=1)
        self.assertEqual([row["document"] for row in self.transaction_rows()], [self.sale.name])

    def test_mirror_without_complete_source_metadata_is_rejected(self):
        mirror = self.add_mirror()
        mirror.items[0].pop("pos_invoice_item")
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_source_metadata_without_matching_pos_back_reference_is_rejected(self):
        pos = self.add_pos()
        self.add_mirror(pos)
        pos.consolidated_invoice = "ANOTHER-INVOICE"
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_existing_back_reference_with_missing_mirror_items_is_rejected(self):
        mirror = self.add_mirror()
        mirror.items[0].pop("pos_invoice")
        mirror.items[0].pop("pos_invoice_item")
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_hidden_pos_source_cannot_silently_remove_a_visible_mirror(self):
        pos = self.add_pos()
        self.add_mirror(pos)
        self.hidden.add(("POS Invoice", pos.name))
        with self.assertRaises(PermissionError):
            self.transaction_rows()

    def test_list_visible_but_full_read_denied_pos_source_is_rejected(self):
        pos = self.add_pos()
        self.add_mirror(pos)
        self.full_denied.add(("POS Invoice", pos.name))
        with self.assertRaises(PermissionError):
            self.transaction_rows()

    def test_mirror_requires_submitted_pos_sources(self):
        pos = self.add_pos()
        self.add_mirror(pos)
        pos.docstatus = 2
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_mirror_rejects_wrong_source_child_identity_even_for_same_item(self):
        mirror = self.add_mirror()
        mirror.items[0].pos_invoice_item = "NONEXISTENT-CHILD"
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_mirror_requires_complete_one_to_one_source_item_coverage(self):
        pos = self.add_pos()
        pos.items.append(Record(pos.items[0], name="POS-SECOND-ITEM", idx=2))
        mirror = self.add_mirror(pos)
        mirror.items.pop()
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_mirror_cannot_use_one_source_child_twice(self):
        mirror = self.add_mirror()
        mirror.items.append(Record(mirror.items[0], name="SECOND-SI-ITEM", idx=2))
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_duplicate_item_codes_with_distinct_source_ids_are_valid(self):
        pos = self.add_pos()
        pos.items.append(Record(pos.items[0], name="POS-SECOND-ITEM", idx=2))
        mirror = self.add_mirror(pos)
        self.assertNotIn(mirror.name, {row["document"] for row in self.transaction_rows()})

    def test_mirror_rejects_company_customer_currency_and_return_direction_mismatch(self):
        for field, value in (("company", "OTHER"), ("customer", "OTHER"), ("currency", "EUR"), ("is_return", 1)):
            with self.subTest(field=field):
                pos = self.add_pos()
                self.add_mirror(pos)
                previous = pos[field]
                pos[field] = value
                with self.assertRaises(ValueError):
                    self.transaction_rows()
                pos[field] = previous

    def test_mirror_rejects_quantity_uom_and_conversion_mismatch(self):
        for field, value in (("qty", 1), ("stock_qty", 3), ("uom", "Nos"), ("stock_uom", "Kg"), ("conversion_factor", 2), ("item_code", "OTHER")):
            with self.subTest(field=field):
                mirror = self.add_mirror()
                previous = mirror.items[0][field]
                mirror.items[0][field] = value
                with self.assertRaises(ValueError):
                    self.transaction_rows()
                mirror.items[0][field] = previous

    def test_customer_group_merge_preserves_original_pos_customers(self):
        invoice, first, second, _ = self.add_group_merge()
        rows = self.transaction_rows()
        self.assertNotIn(invoice.name, {row["document"] for row in rows})
        parties = {row["document"]: row["customer"] for row in rows}
        self.assertEqual((parties[first.name], parties[second.name]), ("CUSTOMER", "SECOND-CUSTOMER"))

    def test_customer_group_merge_requires_a_matching_log(self):
        _, _, _, log = self.add_group_merge()
        self.documents["POS Invoice Merge Log"].pop(log.name)
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_customer_group_merge_cannot_use_hidden_or_full_denied_log(self):
        _, _, _, log = self.add_group_merge()
        for denied in (self.hidden, self.full_denied):
            with self.subTest(denied="hidden" if denied is self.hidden else "full_read"):
                denied.add(("POS Invoice Merge Log", log.name))
                with self.assertRaises(PermissionError):
                    self.transaction_rows()
                denied.clear()

    def test_customer_group_merge_requires_company_customer_and_full_membership(self):
        _, _, _, log = self.add_group_merge()
        for field, value in (("company", "OTHER"), ("customer", "OTHER"),
                             ("pos_invoices", [log.pos_invoices[0]])):
            with self.subTest(field=field):
                previous = log[field]
                log[field] = value
                with self.assertRaises(ValueError):
                    self.transaction_rows()
                log[field] = previous

    def test_customer_group_log_does_not_permit_mixed_invoice_currencies(self):
        _, _, second, _ = self.add_group_merge()
        second.currency = "EUR"
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_si_return_uses_exact_source_outside_the_report_date_range(self):
        returned = self.add_return()
        self.sale.posting_date = "2026-08-31"
        row = self.transaction_rows()[0]
        self.assertEqual((row["document"], row["entry_type"], row["source_doctype"], row["source_document"]),
                         (returned.name, "退货", "Sales Invoice", self.sale.name))
        self.assertEqual((row["base_net_total"], row["base_net_tender"]), (-350, -385))

    def test_pos_return_with_mirrored_credit_note_stays_one_business_return(self):
        pos = self.add_pos()
        self.add_mirror(pos)
        returned = self.add_return(pos, "POS-RETURN")
        credit_note = self.add_mirror(returned, "SI-CREDIT")
        # Native red credit note refers to the positive consolidated Sales Invoice.
        credit_note.return_against = pos.consolidated_invoice
        credit_note.items[0].sales_invoice_item = self.documents["Sales Invoice"][pos.consolidated_invoice].items[0].name
        rows = self.transaction_rows(only_returns=1)
        self.assertEqual([row["document"] for row in rows], [returned.name])
        self.assertEqual((rows[0]["source_doctype"], rows[0]["source_document"]), ("POS Invoice", pos.name))

    def test_hidden_return_original_is_rejected_even_when_outside_dates(self):
        self.add_return()
        self.sale.posting_date = "2026-08-31"
        self.hidden.add(("Sales Invoice", self.sale.name))
        with self.assertRaises(PermissionError):
            self.transaction_rows()

    def test_return_rejects_missing_original_or_wrong_child_id(self):
        returned = self.add_return()
        returned.items[0].sales_invoice_item = "MISSING-SOURCE-ROW"
        with self.assertRaises(ValueError):
            self.transaction_rows()
        returned.items[0].sales_invoice_item = self.sale.items[0].name
        returned.return_against = "MISSING-INVOICE"
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_return_rejects_header_source_or_child_source_half_links(self):
        returned = self.add_return()
        returned.items[0].pop("sales_invoice_item")
        with self.assertRaises(ValueError):
            self.transaction_rows()
        returned.items[0].sales_invoice_item = self.sale.items[0].name
        returned.return_against = None
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_return_rejects_positive_quantity_and_wrong_customer_currency_company(self):
        returned = self.add_return()
        for field, value in (("company", "OTHER"), ("customer", "OTHER"), ("currency", "EUR")):
            with self.subTest(field=field):
                previous = self.sale[field]
                self.sale[field] = value
                with self.assertRaises(ValueError):
                    self.transaction_rows()
                self.sale[field] = previous
        returned.items[0].update(qty=1, stock_qty=3)
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_standalone_return_is_legal_and_has_no_invented_source(self):
        returned = self.add_return()
        returned.return_against = None
        returned.items[0].pop("sales_invoice_item")
        row = self.transaction_rows(only_returns=1)[0]
        self.assertEqual((row["document"], row["entry_type"]), (returned.name, "退货"))
        self.assertFalse(row.get("source_document"))

    def test_optional_posted_and_closing_links_require_full_read_permission(self):
        pos = self.add_pos()
        mirror = self.add_mirror(pos)
        closing = self.attach_permissions("POS Closing Entry", Record(name="CLOSING", company="JF", docstatus=1,
                                                                       status="Submitted", pos_invoices=[Record(pos_invoice=pos.name)]))
        self.documents["POS Closing Entry"][closing.name] = closing
        self.sale.pos_closing_entry = closing.name
        self.hidden.update({("Sales Invoice", mirror.name), ("POS Closing Entry", closing.name)})
        rows = self.transaction_rows()
        pos_row = next(row for row in rows if row["document"] == pos.name)
        sale_row = next(row for row in rows if row["document"] == self.sale.name)
        self.assertFalse(pos_row.get("posted_invoice"))
        self.assertFalse(sale_row.get("closing_entry"))
        self.assertNotIn(mirror.name, str(pos_row))
        self.assertNotIn(closing.name, str(sale_row))

    def test_full_read_denied_optional_link_does_not_leak_its_name(self):
        pos = self.add_pos()
        mirror = self.add_mirror(pos)
        self.full_denied.add(("Sales Invoice", mirror.name))
        pos_row = next(row for row in self.transaction_rows() if row["document"] == pos.name)
        self.assertFalse(pos_row.get("posted_invoice"))
        self.assertNotIn(mirror.name, str(pos_row))

    def test_ordinary_invoice_is_usable_without_pos_read_permission(self):
        self.permissions.remove("POS Invoice")
        self.assertEqual([row["document"] for row in self.transaction_rows()], [self.sale.name])

    def test_drafts_cancellations_and_other_company_documents_are_excluded(self):
        for index, state in enumerate((0, 2), 1):
            doc = self.document("Sales Invoice", f"IGNORED-{index}", [Record(self.item)])
            doc.docstatus = state
            self.documents["Sales Invoice"][doc.name] = doc
        other = self.document("POS Invoice", "OTHER", [Record(self.item)])
        other.company = "OTHER"
        self.documents["POS Invoice"][other.name] = other
        self.assertEqual([row["document"] for row in self.transaction_rows()], [self.sale.name])

    def test_header_customer_profile_owner_and_only_returns_filters(self):
        self.assertEqual(self.transaction_rows(customer="OTHER"), [])
        self.assertEqual(self.transaction_rows(pos_profile="OTHER"), [])
        self.assertEqual(self.transaction_rows(owner="other@example.test"), [])
        self.assertEqual(self.transaction_rows(only_returns=1), [])
        self.assertEqual(len(self.transaction_rows(customer="CUSTOMER", pos_profile="POS-PROFILE", owner="cashier@example.test")), 1)

    def test_cashier_is_an_exact_native_owner_filter(self):
        self.add_pos()
        self.assertEqual(len(self.transaction_rows(cashier="cashier@example.test")), 2)
        self.assertEqual(self.transaction_rows(cashier="other@example.test"), [])
        self.assertEqual(self.transaction_rows(cashier="cashier"), [])

    def test_cashier_and_legacy_owner_conflict_is_rejected(self):
        with self.assertRaises(ValueError):
            self.transaction_rows(cashier="cashier@example.test", owner="other@example.test")
        self.assertEqual(len(self.transaction_rows(cashier="cashier@example.test", owner="cashier@example.test")), 1)

    def test_business_source_type_accepts_chinese_or_native_doctype_names(self):
        pos = self.add_pos()
        for source_type in ("销售发票", "Sales Invoice"):
            with self.subTest(source_type=source_type):
                self.assertEqual([row["document"] for row in self.transaction_rows(source_type=source_type)], [self.sale.name])
        for source_type in ("零售凭证", "POS Invoice"):
            with self.subTest(source_type=source_type):
                self.assertEqual([row["document"] for row in self.transaction_rows(source_type=source_type)], [pos.name])

    def test_posted_report_rejects_pos_only_source_filter(self):
        for source_type in ("零售凭证", "POS Invoice"):
            with self.subTest(source_type=source_type):
                with self.assertRaises(ValueError):
                    self.posted_rows(source_type=source_type)
        self.assertEqual(len(self.posted_rows(source_type="Sales Invoice")), 1)

    def test_unknown_source_type_is_rejected_instead_of_becoming_all_sales(self):
        for report in (self.transaction_rows, self.posted_rows):
            with self.subTest(report=report.__name__):
                with self.assertRaises(ValueError):
                    report(source_type="Unknown Sales")

    def test_item_warehouse_and_flow_filters_cannot_misstate_header_totals(self):
        for field in ("item_code", "warehouse", "flow_order"):
            for report in (self.transaction_rows, self.posted_rows):
                with self.subTest(field=field, report=report.__name__):
                    with self.assertRaises(ValueError):
                        report(**{field: "VALUE"})

    def test_scope_requires_company_and_date_bounds(self):
        for field in ("company", "from_date", "to_date"):
            filters = dict(self.filters)
            filters.pop(field)
            for report in (self.module.sales_transaction_ledger, self.module.posted_sales_ledger):
                with self.subTest(field=field, report=report.__name__):
                    with self.assertRaises(ValueError):
                        report(filters)

    def test_guest_cannot_use_either_report(self):
        self.frappe.session.user = "Guest"
        for report in (self.transaction_rows, self.posted_rows):
            with self.subTest(report=report.__name__):
                with self.assertRaises(PermissionError):
                    report()

    def test_non_finite_persisted_money_is_rejected(self):
        for field in ("net_total", "base_net_total", "grand_total", "conversion_rate", "change_amount"):
            with self.subTest(field=field):
                previous = self.sale[field]
                self.sale[field] = "NaN"
                with self.assertRaises(ValueError):
                    self.transaction_rows()
                self.sale[field] = previous
        self.sale.payments[0].base_amount = "Infinity"
        with self.assertRaises(ValueError):
            self.transaction_rows()

    def test_document_column_is_a_dynamic_link_using_doc_type(self):
        for report in (self.module.sales_transaction_ledger, self.module.posted_sales_ledger):
            columns, _ = report(self.filters)
            column = next(column for column in columns if column["fieldname"] == "document")
            self.assertEqual((column["fieldtype"], column["options"]), ("Dynamic Link", "doc_type"))

    def test_explicit_pos_source_without_pos_read_is_denied(self):
        self.permissions.remove("POS Invoice")
        with self.assertRaises(PermissionError):
            self.transaction_rows(source_type="零售凭证")
        self.assertEqual(len(self.transaction_rows()), 1)

    def test_profile_select_filter_does_not_require_full_profile_read(self):
        self.permissions.remove("POS Profile")
        self.frappe.has_permission.side_effect = lambda doctype, ptype="read", **kwargs: (
            ptype == "select" if doctype == "POS Profile" else doctype in self.permissions)
        self.frappe.get_list.side_effect = lambda doctype, filters=None, **kwargs: self.query(
            doctype, filters, visible=doctype != "POS Profile", **kwargs)
        self.assertEqual(len(self.transaction_rows(pos_profile="POS-PROFILE")), 1)
        self.assertFalse(any(call.args[0] == "POS Profile" for call in self.module.readable_documents.mock_calls))
        self.assertEqual(self.transaction_rows()[0]["pos_profile"], "POS-PROFILE")

    def test_unselectable_profile_is_denied_without_returning_config(self):
        self.permissions.remove("POS Profile")
        with self.assertRaises(PermissionError):
            self.transaction_rows(pos_profile="POS-PROFILE")

    def test_profile_from_other_company_cannot_filter_sales(self):
        self.documents["POS Profile"]["POS-PROFILE"].company = "OTHER"
        with self.assertRaises(ValueError):
            self.transaction_rows(pos_profile="POS-PROFILE")


if __name__ == "__main__":
    unittest.main()
