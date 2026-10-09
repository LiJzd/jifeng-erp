"""Targeted saved-penny risks through captured installed native GL callbacks."""

from copy import deepcopy
from decimal import Decimal
import unittest

import test_subcontracting_accounting as native_accounting
from test_subcontracting_accounting import ledger_rows, net


class ReceiptReturnAllocationTest(unittest.TestCase):
    def setUp(self):
        self.h = native_accounting.ReceiptAccountingRiskTest()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.subject = self.h.subject
        self.Row = self.h.Row
        self.h.native['get_field_precision'] = lambda *args, **kw: 2

    def physical_rows(self, document):
        if not document.is_return:
            document.supplied_items[0].consumed_qty = 1
            for entry in self.h.sle[document.name]:
                if entry.item_code == 'RM':
                    entry.actual_qty = -1
        for row in document.items:
            row.update(doctype='Subcontracting Receipt Item', parent=document.name,
                       parenttype='Subcontracting Receipt', parentfield='items')
        for row in document.supplied_items:
            row.update(doctype='Subcontracting Receipt Supplied Item', parent=document.name,
                       parenttype='Subcontracting Receipt', parentfield='supplied_items')

    def returned(self, name, *, fee=0.02, fg=-0.02):
        document = self.h.receipt(qty=-1, raw=0, fee=fee, fg=fg, is_return=1)
        document.name, document._action = name, 'submit'
        old_fg, old_raw = document.items[0].name, document.supplied_items[0].name
        document.items[0].name = name + '-FG'
        document.supplied_items[0].name = name + '-RM'
        document.supplied_items[0].reference_name = document.items[0].name
        document.supplied_items[0].consumed_qty = -1 / 3
        self.h.sle[name] = self.h.sle.pop('CURRENT')
        for row in self.h.sle[name]:
            row.voucher_no = name
            row.name = name + '-' + row.name
            row.voucher_detail_no = document.items[0].name if row.voucher_detail_no == old_fg else document.supplied_items[0].name
            if row.item_code == 'RM':
                row.actual_qty = 1 / 3
        self.physical_rows(document)
        return document

    def remember(self, document):
        document._action = None
        self.h.h.prior_docs[document.name] = document
        for row in document.items:
            self.h.h.prior.append(self.Row(row, return_company='JF', return_supplier='SUP',
                                          return_supplier_warehouse='Supplier'))
        for row in document.supplied_items:
            self.h.h.prior_raw.append(self.Row({key: value for key, value in row.items() if key != 'doctype'},
                                             source_fg_row=document.items[0].subcontracting_receipt_item))

    def post(self, document):
        document.calculate_additional_costs()
        entries = self.h.calculate(document)
        actual = self.h.native['process_gl_map'](deepcopy(entries), from_repost=True)
        self.h.gl[document.name] = ledger_rows(actual, self.Row)
        self.remember(document)
        return actual

    def test_native_marked_center_pennies_three_slices_reverse_every_actual_unit(self):
        self.h.native['cost_allocations']['FG-CC'] = [('CC-A', 33), ('CC-B', 67)]
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        self.physical_rows(source)
        self.h.book_source(source)
        total = {}
        for index in range(3):
            returned = self.returned('RET-' + str(index))
            actual = self.post(returned)
            self.assertEqual(returned.items[0].jf_processing_reversal_amount, -0.02)
            self.assertEqual(returned.jf_return_allocation_version, 'v1')
            self.subject._prove_receipt_fees(returned, for_return=True)
            for key, value in net(actual).items():
                total[key] = total.get(key, Decimal(0)) + value
        self.assertEqual({key: value for key, value in total.items() if value},
                         {key: -value for key, value in net(self.h.gl[source.name]).items()})

    def test_saved_charge_cannot_point_to_another_valid_original_account(self):
        source = self.h.receipt(qty=3, raw=0, fee=0, fg=0.03)
        source.items[0].additional_cost_per_qty = 0.01
        source.additional_costs = [self.Row(name='CH-A', expense_account='FREIGHT-A', account_currency='CNY',
                                           exchange_rate=1, amount=0.01, base_amount=0.01),
                                   self.Row(name='CH-B', expense_account='FREIGHT-B', account_currency='CNY',
                                           exchange_rate=1, amount=0.02, base_amount=0.02)]
        self.physical_rows(source)
        self.h.book_source(source)
        returned = self.returned('RET-CHARGE', fee=0, fg=-0.01)
        self.post(returned)
        returned.additional_costs[0].jf_return_source_charge = 'CH-B'
        with self.assertRaisesRegex(ValueError, '原费用行标识'):
            self.subject._prove_receipt_fees(returned, for_return=True)

    def test_source_actual_center_rounding_budget_closes_instead_of_raw_unit_price(self):
        self.h.native['cost_allocations']['FG-CC'] = [('CC-A', 50), ('CC-B', 50)]
        source = self.h.receipt(qty=3, raw=0, fee=0.01, fg=0.03)
        self.physical_rows(source)
        self.h.book_source(source)
        proof = self.subject._prove_receipt_fees(source, for_return=True)
        actual_fee = sum(Decimal(str(row.credit or 0)) - Decimal(str(row.debit or 0))
                         for row in self.h.gl[source.name] if row.account == 'SERVICE')
        self.assertNotEqual(actual_fee, Decimal('0.03'))
        self.assertEqual(proof.amount(source.items[0], 'service_cost_per_qty'), actual_fee)
        total, fees = {}, Decimal(0)
        for index in range(3):
            returned = self.returned('ROUND-' + str(index), fee=0.01, fg=-0.01)
            actual = self.post(returned)
            fees -= Decimal(str(returned.items[0].jf_processing_reversal_amount))
            self.subject._prove_receipt_fees(returned, for_return=True)
            for key, value in net(actual).items():
                total[key] = total.get(key, Decimal(0)) + value
        self.assertEqual(fees, actual_fee)
        self.assertEqual({key: value for key, value in total.items() if value},
                         {key: -value for key, value in net(self.h.gl[source.name]).items()})

    def test_same_receipt_multiple_finished_goods_returns_only_selected_original_row(self):
        self.h.native['cost_allocations']['FG-CC'] = [('CC-A', 33), ('CC-B', 67)]
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        second = self.h.add_receipt_row(source, name='SECOND-FG', qty=3, raw_qty=1, raw=0, fee=0.03, fg=0.09)
        self.physical_rows(source)
        self.h.book_source(source)
        returned = self.returned('SELECTED-SECOND', fee=0.03, fg=-0.03)
        returned.items[0].subcontracting_receipt_item = second.name
        actual = self.post(returned)
        self.assertEqual(returned.items[0].jf_processing_reversal_amount, -0.03)
        self.assertTrue(all(row.voucher_detail_no == returned.items[0].name for row in actual))
        self.assertEqual(net(actual)[('SERVICE', 'CC-A', 'PROJECT')], Decimal('0.01'))
        self.assertEqual(net(actual)[('SERVICE', 'CC-B', 'PROJECT')], Decimal('0.02'))
        self.subject._prove_receipt_fees(returned, for_return=True)

    def test_saved_slice_does_not_reprice_when_prior_slice_is_cancelled(self):
        source = self.h.receipt(qty=3, raw=0, fee=0, fg=1)
        source.items[0].additional_cost_per_qty = 1 / 3
        source.additional_costs = [self.Row(name='CH', expense_account='FREIGHT', account_currency='CNY',
                                           exchange_rate=1, amount=1, base_amount=1)]
        self.physical_rows(source)
        self.h.book_source(source)
        first = self.returned('FIRST', fee=0, fg=-1 / 3)
        self.post(first)
        second = self.returned('SECOND', fee=0, fg=-1 / 3)
        self.post(second)
        saved = deepcopy(second.items[0])
        first.docstatus = 2
        for row in [*self.h.h.prior, *self.h.h.prior_raw]:
            if row.parent == first.name:
                row.docstatus = 2
        second.calculate_additional_costs()
        self.assertEqual(second.items[0], saved)
        self.assertEqual(second.items[0].jf_additional_reversal_amount, -0.34)
        third = self.returned('AFTER-CANCEL', fee=0, fg=-1 / 3)
        self.post(third)
        fourth = self.returned('FINAL', fee=0, fg=-1 / 3)
        self.post(fourth)
        self.assertEqual(sum(Decimal(str(doc.items[0].jf_additional_reversal_amount))
            for doc in (second, third, fourth)), Decimal('-1'))
        self.assertEqual(sum(Decimal(str(doc.additional_costs[0].amount))
            for doc in (second, third, fourth)), Decimal('-1'))

    def test_cancelled_center_slice_releases_only_its_actual_remaining_units(self):
        self.h.native['cost_allocations']['FG-CC'] = [('CC-A', 50), ('CC-B', 50)]
        source = self.h.receipt(qty=3, raw=0, fee=0.01, fg=0.03)
        self.physical_rows(source)
        self.h.book_source(source)
        first = self.returned('CENTER-FIRST', fee=0.01, fg=-0.01)
        self.post(first)
        second = self.returned('CENTER-SECOND', fee=0.01, fg=-0.01)
        self.post(second)
        saved, saved_gl = deepcopy(second.items[0]), deepcopy(self.h.gl[second.name])
        first.docstatus = 2
        for row in [*self.h.h.prior, *self.h.h.prior_raw]:
            if row.parent == first.name:
                row.docstatus = 2
        second.calculate_additional_costs()
        self.assertEqual(second.items[0], saved)
        self.assertEqual(self.h.gl[second.name], saved_gl)
        effective = [second]
        for name in ('CENTER-AFTER-CANCEL', 'CENTER-FINAL'):
            returned = self.returned(name, fee=0.01, fg=-0.01)
            self.post(returned)
            effective.append(returned)
            self.subject._prove_receipt_fees(returned, for_return=True)
        combined = net([row for document in effective for row in self.h.gl[document.name]])
        self.assertEqual(combined, {key: -value for key, value in net(self.h.gl[source.name]).items()})
        # The middle slice is still proved from its saved ledger after the
        # formerly preceding slice has ceased to be effective.
        self.subject._prove_receipt_fees(second, for_return=True)

    def test_form_recalculation_overwrites_client_money_and_bounds_quantity(self):
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        self.physical_rows(source)
        self.h.book_source(source)
        returned = self.returned('CLIENT-VALUES')
        returned.jf_return_allocation_version = 'client'
        returned.items[0].jf_processing_reversal_amount = -999
        returned.items[0].jf_additional_reversal_amount = -999
        returned.calculate_additional_costs()
        self.assertEqual(returned.jf_return_allocation_version, 'v1')
        self.assertEqual(returned.items[0].jf_processing_reversal_amount, -0.02)
        self.assertEqual(returned.items[0].jf_additional_reversal_amount, 0)
        returned.items[0].qty = -4
        with self.assertRaisesRegex(ValueError, '累计实物退回超过'):
            returned.calculate_additional_costs()

    def test_posted_update_and_cancelled_recalculation_cannot_replace_saved_slice(self):
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        self.physical_rows(source)
        self.h.book_source(source)
        returned = self.returned('POSTED-GUARD')
        self.post(returned)
        saved = deepcopy(returned.items[0])
        returned._action = 'update_after_submit'
        with self.assertRaisesRegex(ValueError, '已提交委外退回'):
            returned.validate()
        returned._action, returned.docstatus = None, 2
        with self.assertRaisesRegex(ValueError, '已取消退回'):
            returned.calculate_additional_costs()
        self.assertEqual(returned.items[0], saved)

    def test_unknown_posted_allocation_version_is_not_read_as_legacy_fee(self):
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        self.physical_rows(source)
        self.h.book_source(source)
        returned = self.returned('UNKNOWN-VERSION')
        self.post(returned)
        returned.jf_return_allocation_version = 'v2'
        with self.assertRaisesRegex(ValueError, '版本不受支持'):
            self.subject._prove_receipt_fees(returned, for_return=True)

    def test_source_transaction_rate_change_cannot_hide_behind_unchanged_base_totals(self):
        source = self.h.receipt(qty=3, raw=0, fee=0.02, fg=0.06)
        self.physical_rows(source)
        self.h.book_source(source)
        original_gl = deepcopy(self.h.gl[source.name])
        for value in (0, 2):
            self.h.gl[source.name] = deepcopy(original_gl)
            self.h.gl[source.name][0].transaction_exchange_rate = value
            with self.assertRaisesRegex(ValueError, '交易汇率|有效会计分录'):
                self.subject._prove_receipt_fees(source, for_return=True)


if __name__ == '__main__':
    unittest.main()
