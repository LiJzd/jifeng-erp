"""Installed ERPNext 16.36 native GL callbacks drive receipt risk models."""

import sys
import types
import unittest
from unittest.mock import patch
import copy
from decimal import Decimal
from copy import deepcopy

# Exact callbacks captured from the installed runtime; not a rewritten formula.
NATIVE_SOURCES = {'StockController.make_gl_entries': 'def make_gl_entries(self, gl_entries=None, from_repost=False, '
                                    'via_landed_cost_voucher=False):\n'
                                    '\tif self.docstatus == 2:\n'
                                    '\t\tmake_reverse_gl_entries(voucher_type=self.doctype, '
                                    'voucher_no=self.name)\n'
                                    '\n'
                                    '\tprovisional_accounting_for_non_stock_items = cint(\n'
                                    '\t\tfrappe.get_cached_value(\n'
                                    '\t\t\t"Company", self.company, '
                                    '"enable_provisional_accounting_for_non_stock_items"\n'
                                    '\t\t)\n'
                                    '\t)\n'
                                    '\n'
                                    '\tis_asset_pr = any(d.get("is_fixed_asset") for d in '
                                    'self.get("items"))\n'
                                    '\tneed_inventory_map = (self.get_stock_items() or '
                                    'self.get("packed_items")) and (\n'
                                    '\t\t'
                                    'cint(erpnext.is_perpetual_inventory_enabled(self.company))\n'
                                    '\t)\n'
                                    '\n'
                                    '\tinventory_account_map = frappe._dict()\n'
                                    '\tif need_inventory_map:\n'
                                    '\t\tinventory_account_map = self.get_inventory_account_map()\n'
                                    '\n'
                                    '\tif need_inventory_map or '
                                    'provisional_accounting_for_non_stock_items or is_asset_pr:\n'
                                    '\t\tif self.docstatus == 1:\n'
                                    '\t\t\tif not gl_entries:\n'
                                    '\t\t\t\tgl_entries = (\n'
                                    '\t\t\t\t\tself.get_gl_entries(inventory_account_map, '
                                    'via_landed_cost_voucher)\n'
                                    '\t\t\t\t\tif self.doctype == "Purchase Receipt"\n'
                                    '\t\t\t\t\telse self.get_gl_entries(inventory_account_map)\n'
                                    '\t\t\t\t)\n'
                                    '\t\t\tmake_gl_entries(gl_entries, from_repost=from_repost)\n',
 'StockController.add_gl_entry': 'def add_gl_entry(\n'
                                 '\tself,\n'
                                 '\tgl_entries,\n'
                                 '\taccount,\n'
                                 '\tcost_center,\n'
                                 '\tdebit,\n'
                                 '\tcredit,\n'
                                 '\tremarks,\n'
                                 '\tagainst_account,\n'
                                 '\tdebit_in_account_currency=None,\n'
                                 '\tcredit_in_account_currency=None,\n'
                                 '\taccount_currency=None,\n'
                                 '\tproject=None,\n'
                                 '\tvoucher_detail_no=None,\n'
                                 '\titem=None,\n'
                                 '\tposting_date=None,\n'
                                 '\tdimensions=None,\n'
                                 '):\n'
                                 '\tgl_entry = {\n'
                                 '\t\t"account": account,\n'
                                 '\t\t"cost_center": cost_center,\n'
                                 '\t\t"debit": debit,\n'
                                 '\t\t"credit": credit,\n'
                                 '\t\t"against": against_account,\n'
                                 '\t\t"remarks": remarks,\n'
                                 '\t}\n'
                                 '\n'
                                 '\tif project:\n'
                                 '\t\tgl_entry.update({"project": project})\n'
                                 '\n'
                                 '\tif voucher_detail_no:\n'
                                 '\t\tgl_entry.update({"voucher_detail_no": voucher_detail_no})\n'
                                 '\n'
                                 '\tif debit_in_account_currency:\n'
                                 '\t\tgl_entry.update({"debit_in_account_currency": '
                                 'debit_in_account_currency})\n'
                                 '\n'
                                 '\tif credit_in_account_currency:\n'
                                 '\t\tgl_entry.update({"credit_in_account_currency": '
                                 'credit_in_account_currency})\n'
                                 '\n'
                                 '\tif posting_date:\n'
                                 '\t\tgl_entry.update({"posting_date": posting_date})\n'
                                 '\n'
                                 '\tif dimensions:\n'
                                 '\t\tgl_entry.update(dimensions)\n'
                                 '\n'
                                 '\tgl_entries.append(self.get_gl_dict(gl_entry, item=item))\n',
 'StockController.get_inventory_account_dict': 'def get_inventory_account_dict(\n'
                                               '\tself, row, inventory_account_map, '
                                               'warehouse_field=None, *, raise_error=True\n'
                                               '):\n'
                                               '\taccount_dict = frappe._dict()\n'
                                               '\n'
                                               '\tif isinstance(row, dict):\n'
                                               '\t\trow = frappe._dict(row)\n'
                                               '\n'
                                               '\tif self.use_item_inventory_account:\n'
                                               '\t\titem_code = (\n'
                                               '\t\t\trow.rm_item_code if hasattr(row, '
                                               '"rm_item_code") and row.rm_item_code else '
                                               'row.item_code\n'
                                               '\t\t)\n'
                                               '\n'
                                               '\t\taccount_dict = '
                                               'inventory_account_map.get(item_code)\n'
                                               '\n'
                                               '\t\tif not account_dict:\n'
                                               '\t\t\tfrappe.throw(\n'
                                               '\t\t\t\t_(\n'
                                               '\t\t\t\t\t"Please set default inventory account '
                                               'for item {0}, or their item group or brand."\n'
                                               '\t\t\t\t).format(bold(item_code))\n'
                                               '\t\t\t)\n'
                                               '\n'
                                               '\t\treturn account_dict\n'
                                               '\n'
                                               '\tif not warehouse_field:\n'
                                               '\t\twarehouse_field = "warehouse"\n'
                                               '\n'
                                               '\twarehouse = row.get(warehouse_field)\n'
                                               '\tif not warehouse:\n'
                                               '\t\twarehouse = self.get(warehouse_field)\n'
                                               '\n'
                                               '\tif warehouse:\n'
                                               '\t\taccount_dict = '
                                               'inventory_account_map.get(warehouse)\n'
                                               '\t\tif not account_dict and raise_error:\n'
                                               '\t\t\taccount = '
                                               'get_warehouse_account(frappe.get_cached_doc("Warehouse", '
                                               'warehouse))\n'
                                               '\t\t\taccount_dict = frappe._dict(\n'
                                               '\t\t\t\taccount=account,\n'
                                               '\t\t\t\t'
                                               'account_currency=frappe.get_cached_value("Account", '
                                               'account, "account_currency"),\n'
                                               '\t\t\t)\n'
                                               '\t\t\tinventory_account_map[warehouse] = '
                                               'account_dict\n'
                                               '\n'
                                               '\treturn account_dict\n',
 'SubcontractingReceipt.make_item_gl_entries': 'def make_item_gl_entries(self, gl_entries, '
                                               'inventory_account_map=None):\n'
                                               '\twarehouse_with_no_account = []\n'
                                               '\n'
                                               '\tsupplied_items_details = frappe._dict()\n'
                                               '\tfor item in self.supplied_items:\n'
                                               '\t\t'
                                               'supplied_items_details.setdefault(item.reference_name, '
                                               '[]).append(\n'
                                               '\t\t\tfrappe._dict(\n'
                                               '\t\t\t\t{\n'
                                               '\t\t\t\t\t"item_code": item.rm_item_code,\n'
                                               '\t\t\t\t\t"amount": item.amount,\n'
                                               '\t\t\t\t\t"expense_account": '
                                               'item.expense_account,\n'
                                               '\t\t\t\t\t"cost_center": item.cost_center,\n'
                                               '\t\t\t\t}\n'
                                               '\t\t\t)\n'
                                               '\t\t)\n'
                                               '\n'
                                               '\tfor item in self.items:\n'
                                               '\t\tif flt(item.rate) and flt(item.qty):\n'
                                               '\t\t\t_inv_dict = '
                                               'self.get_inventory_account_dict(item, '
                                               'inventory_account_map)\n'
                                               '\n'
                                               '\t\t\tif _inv_dict.get("account"):\n'
                                               '\t\t\t\tstock_value_diff = frappe.db.get_value(\n'
                                               '\t\t\t\t\t"Stock Ledger Entry",\n'
                                               '\t\t\t\t\t{\n'
                                               '\t\t\t\t\t\t"voucher_type": "Subcontracting '
                                               'Receipt",\n'
                                               '\t\t\t\t\t\t"voucher_no": self.name,\n'
                                               '\t\t\t\t\t\t"voucher_detail_no": item.name,\n'
                                               '\t\t\t\t\t\t"warehouse": item.warehouse,\n'
                                               '\t\t\t\t\t\t"is_cancelled": 0,\n'
                                               '\t\t\t\t\t},\n'
                                               '\t\t\t\t\t"stock_value_difference",\n'
                                               '\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\tremarks = self.get("remarks") or '
                                               '_("Accounting Entry for Stock")\n'
                                               '\n'
                                               '\t\t\t\t# Accepted Warehouse Account (Debit)\n'
                                               '\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\taccount=_inv_dict["account"],\n'
                                               '\t\t\t\t\tcost_center=item.cost_center,\n'
                                               '\t\t\t\t\tdebit=stock_value_diff,\n'
                                               '\t\t\t\t\tcredit=0.0,\n'
                                               '\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\tagainst_account=item.expense_account,\n'
                                               '\t\t\t\t\t'
                                               'account_currency=_inv_dict["account_currency"],\n'
                                               '\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\tservice_cost = flt(\n'
                                               '\t\t\t\t\titem.service_cost_per_qty, '
                                               'item.precision("service_cost_per_qty")\n'
                                               '\t\t\t\t) * flt(item.qty, item.precision("qty"))\n'
                                               '\t\t\t\t# Expense Account (Credit)\n'
                                               '\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\taccount=item.expense_account,\n'
                                               '\t\t\t\t\tcost_center=item.cost_center,\n'
                                               '\t\t\t\t\tdebit=0.0,\n'
                                               '\t\t\t\t\tcredit=flt(stock_value_diff) - '
                                               'service_cost,\n'
                                               '\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\tagainst_account=_inv_dict["account"],\n'
                                               '\t\t\t\t\t'
                                               'account_currency=get_account_currency(item.expense_account),\n'
                                               '\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\tservice_account = '
                                               'item.service_expense_account or '
                                               'item.expense_account\n'
                                               '\t\t\t\t# Expense Account (Credit)\n'
                                               '\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\taccount=service_account,\n'
                                               '\t\t\t\t\tcost_center=item.cost_center,\n'
                                               '\t\t\t\t\tdebit=0.0,\n'
                                               '\t\t\t\t\tcredit=service_cost,\n'
                                               '\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\tagainst_account=_inv_dict["account"],\n'
                                               '\t\t\t\t\t'
                                               'account_currency=get_account_currency(service_account),\n'
                                               '\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\tif flt(item.rm_supp_cost):\n'
                                               '\t\t\t\t\tfor rm_item in '
                                               'supplied_items_details.get(item.name):\n'
                                               '\t\t\t\t\t\t_inv_dict = '
                                               'self.get_inventory_account_dict(\n'
                                               '\t\t\t\t\t\t\trm_item, inventory_account_map, '
                                               '"supplier_warehouse"\n'
                                               '\t\t\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\t\t\t# Supplier Warehouse Account (Credit)\n'
                                               '\t\t\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\t\t\taccount=_inv_dict.get("account"),\n'
                                               '\t\t\t\t\t\t\tcost_center=rm_item.cost_center or '
                                               'item.cost_center,\n'
                                               '\t\t\t\t\t\t\tdebit=0.0,\n'
                                               '\t\t\t\t\t\t\tcredit=flt(rm_item.amount),\n'
                                               '\t\t\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\t\t\t'
                                               'against_account=rm_item.expense_account or '
                                               'item.expense_account,\n'
                                               '\t\t\t\t\t\t\t'
                                               'account_currency=_inv_dict.get("account_currency"),\n'
                                               '\t\t\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t\t\t)\n'
                                               '\t\t\t\t\t\t# Expense Account (Debit)\n'
                                               '\t\t\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\t\t\taccount=rm_item.expense_account or '
                                               'item.expense_account,\n'
                                               '\t\t\t\t\t\t\tcost_center=rm_item.cost_center or '
                                               'item.cost_center,\n'
                                               '\t\t\t\t\t\t\tdebit=flt(rm_item.amount),\n'
                                               '\t\t\t\t\t\t\tcredit=0.0,\n'
                                               '\t\t\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\t\t\t'
                                               'against_account=_inv_dict.get("account"),\n'
                                               '\t\t\t\t\t\t\t'
                                               'account_currency=get_account_currency(item.expense_account),\n'
                                               '\t\t\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\t# Expense Account (Debit)\n'
                                               '\t\t\t\tif item.additional_cost_per_qty:\n'
                                               '\t\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\t\taccount=item.expense_account,\n'
                                               '\t\t\t\t\t\tcost_center=self.cost_center or '
                                               'self.get_company_default("cost_center"),\n'
                                               '\t\t\t\t\t\tdebit=item.qty * '
                                               'item.additional_cost_per_qty,\n'
                                               '\t\t\t\t\t\tcredit=0.0,\n'
                                               '\t\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\t\tagainst_account=None,\n'
                                               '\t\t\t\t\t\t'
                                               'account_currency=get_account_currency(item.expense_account),\n'
                                               '\t\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\tif divisional_loss := flt(item.amount - '
                                               'stock_value_diff, item.precision("amount")):\n'
                                               '\t\t\t\t\tloss_account = '
                                               'self.get_company_default(\n'
                                               '\t\t\t\t\t\t"stock_adjustment_account", '
                                               'ignore_validation=True\n'
                                               '\t\t\t\t\t)\n'
                                               '\n'
                                               '\t\t\t\t\t# Loss Account (Credit)\n'
                                               '\t\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\t\taccount=loss_account,\n'
                                               '\t\t\t\t\t\tcost_center=item.cost_center,\n'
                                               '\t\t\t\t\t\tdebit=0.0,\n'
                                               '\t\t\t\t\t\tcredit=divisional_loss,\n'
                                               '\t\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\t\tagainst_account=item.expense_account,\n'
                                               '\t\t\t\t\t\t'
                                               'account_currency=get_account_currency(loss_account),\n'
                                               '\t\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t\t)\n'
                                               '\t\t\t\t\t# Expense Account (Debit)\n'
                                               '\t\t\t\t\tself.add_gl_entry(\n'
                                               '\t\t\t\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\t\t\t\taccount=item.expense_account,\n'
                                               '\t\t\t\t\t\tcost_center=item.cost_center,\n'
                                               '\t\t\t\t\t\tdebit=divisional_loss,\n'
                                               '\t\t\t\t\t\tcredit=0.0,\n'
                                               '\t\t\t\t\t\tremarks=remarks,\n'
                                               '\t\t\t\t\t\tagainst_account=loss_account,\n'
                                               '\t\t\t\t\t\t'
                                               'account_currency=get_account_currency(item.expense_account),\n'
                                               '\t\t\t\t\t\tproject=item.project,\n'
                                               '\t\t\t\t\t\titem=item,\n'
                                               '\t\t\t\t\t)\n'
                                               '\t\t\telif (\n'
                                               '\t\t\t\titem.warehouse not in '
                                               'warehouse_with_no_account\n'
                                               '\t\t\t\tor item.rejected_warehouse not in '
                                               'warehouse_with_no_account\n'
                                               '\t\t\t):\n'
                                               '\t\t\t\t'
                                               'warehouse_with_no_account.append(item.warehouse)\n'
                                               '\n'
                                               '\tfor row in self.additional_costs:\n'
                                               '\t\tcredit_amount = (\n'
                                               '\t\t\tflt(row.base_amount)\n'
                                               '\t\t\tif (row.base_amount or row.account_currency '
                                               '!= self.company_currency)\n'
                                               '\t\t\telse flt(row.amount)\n'
                                               '\t\t)\n'
                                               '\n'
                                               '\t\t# Additional Cost Expense Account (Credit)\n'
                                               '\t\tself.add_gl_entry(\n'
                                               '\t\t\tgl_entries=gl_entries,\n'
                                               '\t\t\taccount=row.expense_account,\n'
                                               '\t\t\tcost_center=self.cost_center or '
                                               'self.get_company_default("cost_center"),\n'
                                               '\t\t\tdebit=0.0,\n'
                                               '\t\t\tcredit=credit_amount,\n'
                                               '\t\t\tremarks=remarks,\n'
                                               '\t\t\tagainst_account=None,\n'
                                               '\t\t\t'
                                               'account_currency=get_account_currency(row.expense_account),\n'
                                               '\t\t)\n'
                                               '\n'
                                               '\tif warehouse_with_no_account:\n'
                                               '\t\tfrappe.msgprint(\n'
                                               '\t\t\t_("No accounting entries for the following '
                                               'warehouses")\n'
                                               '\t\t\t+ ": \\n"\n'
                                               '\t\t\t+ "\\n".join(warehouse_with_no_account)\n'
                                               '\t\t)',
 'general_ledger.get_merge_properties': 'def get_merge_properties(dimensions=None):\n'
                                        '\tmerge_properties = [\n'
                                        '\t\t"account",\n'
                                        '\t\t"cost_center",\n'
                                        '\t\t"party",\n'
                                        '\t\t"party_type",\n'
                                        '\t\t"voucher_detail_no",\n'
                                        '\t\t"against_voucher",\n'
                                        '\t\t"against_voucher_type",\n'
                                        '\t\t"project",\n'
                                        '\t\t"finance_book",\n'
                                        '\t\t"voucher_no",\n'
                                        '\t\t"advance_voucher_type",\n'
                                        '\t\t"advance_voucher_no",\n'
                                        '\t]\n'
                                        '\tif dimensions:\n'
                                        '\t\tmerge_properties.extend(dimensions)\n'
                                        '\treturn merge_properties\n',
 'general_ledger.merge_similar_entries': 'def merge_similar_entries(gl_map, precision=None):\n'
                                         '\tmerged_gl_map = []\n'
                                         '\taccounting_dimensions = get_accounting_dimensions()\n'
                                         '\tmerge_properties = '
                                         'get_merge_properties(accounting_dimensions)\n'
                                         '\n'
                                         '\tfor entry in gl_map:\n'
                                         '\t\tif entry._skip_merge:\n'
                                         '\t\t\tmerged_gl_map.append(entry)\n'
                                         '\t\t\tcontinue\n'
                                         '\n'
                                         '\t\tentry.merge_key = get_merge_key(entry, '
                                         'merge_properties)\n'
                                         '\t\t# if there is already an entry in this account then '
                                         'just add it\n'
                                         '\t\t# to that entry\n'
                                         '\t\tsame_head = check_if_in_list(entry, merged_gl_map)\n'
                                         '\t\tif same_head:\n'
                                         '\t\t\tsame_head.debit = flt(same_head.debit) + '
                                         'flt(entry.debit)\n'
                                         '\t\t\tsame_head.debit_in_account_currency = '
                                         'flt(same_head.debit_in_account_currency) + flt(\n'
                                         '\t\t\t\tentry.debit_in_account_currency\n'
                                         '\t\t\t)\n'
                                         '\t\t\tsame_head.debit_in_transaction_currency = '
                                         'flt(same_head.debit_in_transaction_currency) + flt(\n'
                                         '\t\t\t\tentry.debit_in_transaction_currency\n'
                                         '\t\t\t)\n'
                                         '\t\t\tsame_head.credit = flt(same_head.credit) + '
                                         'flt(entry.credit)\n'
                                         '\t\t\tsame_head.credit_in_account_currency = '
                                         'flt(same_head.credit_in_account_currency) + flt(\n'
                                         '\t\t\t\tentry.credit_in_account_currency\n'
                                         '\t\t\t)\n'
                                         '\t\t\tsame_head.credit_in_transaction_currency = '
                                         'flt(same_head.credit_in_transaction_currency) + flt(\n'
                                         '\t\t\t\tentry.credit_in_transaction_currency\n'
                                         '\t\t\t)\n'
                                         '\t\telse:\n'
                                         '\t\t\tmerged_gl_map.append(entry)\n'
                                         '\n'
                                         '\tcompany = gl_map[0].company if gl_map else '
                                         'erpnext.get_default_company()\n'
                                         '\tcompany_currency = '
                                         'erpnext.get_company_currency(company)\n'
                                         '\n'
                                         '\tif not precision:\n'
                                         '\t\tprecision = get_field_precision(\n'
                                         '\t\t\tfrappe.get_meta("GL Entry").get_field("debit"), '
                                         'currency=company_currency\n'
                                         '\t\t)\n'
                                         '\n'
                                         '\t# filter zero debit and credit entries\n'
                                         '\tmerged_gl_map = filter(\n'
                                         '\t\tlambda x: flt(x.debit, precision) != 0\n'
                                         '\t\tor flt(x.credit, precision) != 0\n'
                                         '\t\tor (\n'
                                         '\t\t\tx.voucher_type == "Journal Entry"\n'
                                         '\t\t\tand frappe.get_cached_value("Journal Entry", '
                                         'x.voucher_no, "voucher_type")\n'
                                         '\t\t\t== "Exchange Gain Or Loss"\n'
                                         '\t\t),\n'
                                         '\t\tmerged_gl_map,\n'
                                         '\t)\n'
                                         '\tmerged_gl_map = list(merged_gl_map)\n'
                                         '\n'
                                         '\treturn merged_gl_map\n',
 'general_ledger.process_gl_map': 'def process_gl_map(gl_map, merge_entries=True, precision=None, '
                                  'from_repost=False):\n'
                                  '\tif not gl_map:\n'
                                  '\t\treturn []\n'
                                  '\n'
                                  '\tif gl_map[0].voucher_type != "Period Closing Voucher":\n'
                                  '\t\tgl_map = '
                                  'distribute_gl_based_on_cost_center_allocation(gl_map, '
                                  'precision, from_repost)\n'
                                  '\n'
                                  '\tif merge_entries:\n'
                                  '\t\tgl_map = merge_similar_entries(gl_map, precision)\n'
                                  '\n'
                                  '\tgl_map = toggle_debit_credit_if_negative(gl_map)\n'
                                  '\n'
                                  '\treturn gl_map\n',
 'general_ledger.distribute_gl_based_on_cost_center_allocation': 'def '
                                                                 'distribute_gl_based_on_cost_center_allocation(gl_map, '
                                                                 'precision=None, '
                                                                 'from_repost=False):\n'
                                                                 '\tround_off_account, '
                                                                 'default_currency = '
                                                                 'frappe.get_cached_value(\n'
                                                                 '\t\t"Company", '
                                                                 'gl_map[0].company, '
                                                                 '["round_off_account", '
                                                                 '"default_currency"]\n'
                                                                 '\t)\n'
                                                                 '\tif not precision:\n'
                                                                 '\t\tprecision = '
                                                                 'get_field_precision(\n'
                                                                 '\t\t\tfrappe.get_meta("GL '
                                                                 'Entry").get_field("debit"),\n'
                                                                 '\t\t\t'
                                                                 'currency=default_currency,\n'
                                                                 '\t\t)\n'
                                                                 '\n'
                                                                 '\tnew_gl_map = []\n'
                                                                 '\tfor d in gl_map:\n'
                                                                 '\t\tcost_center = '
                                                                 'd.get("cost_center")\n'
                                                                 '\n'
                                                                 '\t\tcost_center_allocation = '
                                                                 'get_cost_center_allocation_data(\n'
                                                                 '\t\t\tgl_map[0]["company"], '
                                                                 'gl_map[0]["posting_date"], '
                                                                 'cost_center\n'
                                                                 '\t\t)\n'
                                                                 '\n'
                                                                 '\t\tif not '
                                                                 'cost_center_allocation:\n'
                                                                 '\t\t\tnew_gl_map.append(d)\n'
                                                                 '\t\t\tcontinue\n'
                                                                 '\n'
                                                                 '\t\t# Validate budget against '
                                                                 'main cost center\n'
                                                                 '\t\tif not from_repost:\n'
                                                                 '\t\t\t'
                                                                 'validate_expense_against_budget(\n'
                                                                 '\t\t\t\td, '
                                                                 'expense_amount=flt(d.debit, '
                                                                 'precision) - flt(d.credit, '
                                                                 'precision)\n'
                                                                 '\t\t\t)\n'
                                                                 '\n'
                                                                 '\t\tif d.account == '
                                                                 'round_off_account:\n'
                                                                 '\t\t\td.cost_center = '
                                                                 'cost_center_allocation[0][0]\n'
                                                                 '\t\t\tnew_gl_map.append(d)\n'
                                                                 '\t\t\tcontinue\n'
                                                                 '\n'
                                                                 '\t\tfor sub_cost_center, '
                                                                 'percentage in '
                                                                 'cost_center_allocation:\n'
                                                                 '\t\t\tgle = copy.deepcopy(d)\n'
                                                                 '\t\t\tgle.cost_center = '
                                                                 'sub_cost_center\n'
                                                                 '\t\t\tfor field in ("debit", '
                                                                 '"credit", '
                                                                 '"debit_in_account_currency", '
                                                                 '"credit_in_account_currency"):\n'
                                                                 '\t\t\t\tgle[field] = '
                                                                 'flt(flt(d.get(field)) * '
                                                                 'percentage / 100, precision)\n'
                                                                 '\t\t\tnew_gl_map.append(gle)\n'
                                                                 '\n'
                                                                 '\treturn new_gl_map\n',
 'general_ledger.get_merge_key': 'def get_merge_key(entry, merge_properties):\n'
                                 '\tmerge_key = []\n'
                                 '\tfor fieldname in merge_properties:\n'
                                 '\t\tmerge_key.append(entry.get(fieldname, ""))\n'
                                 '\n'
                                 '\treturn tuple(merge_key)\n',
 'general_ledger.check_if_in_list': 'def check_if_in_list(gle, gl_map):\n'
                                    '\tfor e in gl_map:\n'
                                    '\t\tif e.merge_key == gle.merge_key:\n'
                                    '\t\t\treturn e\n',
 'general_ledger.toggle_debit_credit_if_negative': 'def toggle_debit_credit_if_negative(gl_map):\n'
                                                   '\tdebit_credit_field_map = {\n'
                                                   '\t\t"debit": "credit",\n'
                                                   '\t\t"debit_in_account_currency": '
                                                   '"credit_in_account_currency",\n'
                                                   '\t\t"debit_in_transaction_currency": '
                                                   '"credit_in_transaction_currency",\n'
                                                   '\t}\n'
                                                   '\n'
                                                   '\tfor entry in gl_map:\n'
                                                   '\t\t# toggle debit, credit if negative entry\n'
                                                   '\t\tfor debit_field, credit_field in '
                                                   'debit_credit_field_map.items():\n'
                                                   '\t\t\tdebit = flt(entry.get(debit_field))\n'
                                                   '\t\t\tcredit = flt(entry.get(credit_field))\n'
                                                   '\n'
                                                   '\t\t\tif debit < 0 and credit < 0 and debit == '
                                                   'credit:\n'
                                                   '\t\t\t\tdebit *= -1\n'
                                                   '\t\t\t\tcredit *= -1\n'
                                                   '\n'
                                                   '\t\t\tif debit < 0:\n'
                                                   '\t\t\t\tcredit = credit - debit\n'
                                                   '\t\t\t\tdebit = 0.0\n'
                                                   '\n'
                                                   '\t\t\tif credit < 0:\n'
                                                   '\t\t\t\tdebit = debit - credit\n'
                                                   '\t\t\t\tcredit = 0.0\n'
                                                   '\n'
                                                   '\t\t\t# update net values\n'
                                                   '\t\t\t# In some scenarios net value needs to '
                                                   'be shown in the ledger\n'
                                                   '\t\t\t# This method updates net values as '
                                                   'debit or credit\n'
                                                   '\t\t\tif entry.post_net_value and debit and '
                                                   'credit:\n'
                                                   '\t\t\t\tif debit > credit:\n'
                                                   '\t\t\t\t\tdebit = debit - credit\n'
                                                   '\t\t\t\t\tcredit = 0.0\n'
                                                   '\n'
                                                   '\t\t\t\telse:\n'
                                                   '\t\t\t\t\tcredit = credit - debit\n'
                                                   '\t\t\t\t\tdebit = 0.0\n'
                                                   '\n'
                                                   '\t\t\tentry[debit_field] = debit\n'
                                                   '\t\t\tentry[credit_field] = credit\n'
                                                   '\n'
                                                   '\treturn gl_map\n'}



PERSISTED_GL_MERGE_FIELDS = {'account','cost_center','party','party_type','voucher_detail_no',
    'against_voucher','against_voucher_type','project','finance_book','voucher_no'}
GL_SOURCE_FIELDS = {'name','modified','company','voucher_type','voucher_no','voucher_detail_no',
    'account','cost_center','project','debit','credit','is_cancelled','account_currency',
    'debit_in_account_currency','credit_in_account_currency','transaction_currency','transaction_exchange_rate',
    'debit_in_transaction_currency','credit_in_transaction_currency'}


def install_native_gl(frappe, document_class, row_class, ledger_read):
    """Bind captured native callbacks; only external DB/account lookup is modelled."""
    frappe._dict = row_class
    frappe._ = lambda value: value
    namespace = dict(frappe=frappe, flt=lambda v, precision=None: round(float(v or 0), precision) if precision is not None else float(v or 0),
        cint=lambda v: int(v or 0), _=lambda v: v, bold=lambda v: v,
        get_account_currency=lambda account: 'CNY', copy=copy,
        dimensions=[], cost_allocations={}, get_field_precision=lambda *args, **kw: 6,
        erpnext=types.SimpleNamespace(is_perpetual_inventory_enabled=lambda company: True,
            get_company_currency=lambda company: 'CNY', get_default_company=lambda: 'JF'))
    namespace['get_accounting_dimensions']=lambda: list(namespace['dimensions'])
    namespace['get_cost_center_allocation_data']=lambda company,date,center: namespace['cost_allocations'].get(
        (str(date),center),namespace['cost_allocations'].get(center,[]))
    namespace['validate_expense_against_budget']=lambda *args,**kw: (_ for _ in ()).throw(AssertionError('proof must not validate a budget again'))
    for source in NATIVE_SOURCES.values():
        exec(source, namespace)
    native = types.ModuleType('erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt')
    native.get_account_currency = namespace['get_account_currency']
    native.SubcontractingReceipt = type('NativeReceipt', (), {'make_item_gl_entries': namespace['make_item_gl_entries']})
    sys.modules[native.__name__] = native
    ledger = types.ModuleType('erpnext.accounts.general_ledger')
    for key in ('get_merge_properties','process_gl_map','get_cost_center_allocation_data'):
        setattr(ledger,key,namespace[key])
    ledger.get_field_precision=lambda *args,**kw: namespace['get_field_precision'](*args,**kw)
    sys.modules[ledger.__name__]=ledger
    dimensions = types.ModuleType('erpnext.accounts.doctype.accounting_dimension.accounting_dimension')
    dimensions.get_accounting_dimensions=namespace['get_accounting_dimensions']
    sys.modules[dimensions.__name__]=dimensions
    original_meta=frappe.get_meta
    def meta(doctype):
        result=original_meta(doctype)
        if doctype=='GL Entry' and (not hasattr(result,'get_valid_columns') or
                                   getattr(result,'_captured_gl_columns',False)):
            # Actual 16.36 metadata has these ten merge fields. The two
            # advance identities exist only inside the native GL map.
            result.get_valid_columns=lambda: GL_SOURCE_FIELDS | PERSISTED_GL_MERGE_FIELDS | set(namespace['dimensions'])
            result._captured_gl_columns=True
        return result
    frappe.get_meta=meta
    cached=frappe.get_cached_value
    frappe.get_cached_value=lambda kind,name,field: (('ROUND','CNY') if isinstance(field,list) else 'CNY') if kind=='Company' and (isinstance(field,list) or field=='default_currency') else cached(kind,name,field)
    document_class.add_gl_entry = namespace['add_gl_entry']
    document_class.get_inventory_account_dict = namespace['get_inventory_account_dict']
    document_class.get_inventory_account_map = lambda self: {warehouse: row_class(account='STOCK-'+warehouse, account_currency='CNY')
        for warehouse in ('Good','Rejected','Supplier','FG-WH','REJECT-WH','RM-WH')}
    document_class.get_company_default = lambda self, field, **kw: 'HEADER-CC' if field == 'cost_center' else 'ADJUST'
    def gl_dict(self,value,item=None):
        entry=row_class(company=self.company,voucher_type=self.doctype,voucher_no=self.name,
            posting_date=self.posting_date,is_cancelled=0,finance_book=self.get('finance_book'),project=self.get('project'))
        for dim in namespace['dimensions']:
            entry[dim]=item.get(dim) if item is not None and item.get(dim) else self.get(dim)
        entry.update(value)
        entry.update(account_currency='CNY', transaction_currency='CNY', transaction_exchange_rate=1,
            debit_in_account_currency=entry.get('debit',0), credit_in_account_currency=entry.get('credit',0),
            debit_in_transaction_currency=entry.get('debit',0), credit_in_transaction_currency=entry.get('credit',0))
        return entry
    document_class.get_gl_dict = gl_dict
    if not hasattr(document_class, 'precision'):
        document_class.precision = lambda self, field: 6
    if not hasattr(frappe.db, 'exists'):
        frappe.db.exists = lambda *args, **kw: False
    def value(doctype, filters, field):
        rows = ledger_read(filters['voucher_no'])
        selected = [row for row in rows if all(row.get(k) == v for k,v in filters.items())]
        return selected[0].get(field) if selected else None
    frappe.db.get_value = value
    return namespace


def ledger_rows(entries, row_class):
    from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
    fields = GL_SOURCE_FIELDS | PERSISTED_GL_MERGE_FIELDS | set(get_accounting_dimensions())
    return [row_class({field: entry.get(field) for field in fields}, name='GL-'+str(index))
            for index, entry in enumerate(entries)]


def net(entries):
    values = {}
    for entry in entries:
        key = (entry.get('account'), entry.get('cost_center'), entry.get('project'))
        values[key] = values.get(key, Decimal(0)) + Decimal(str(entry.get('debit') or 0)) - Decimal(str(entry.get('credit') or 0))
    return {key: value for key,value in values.items() if value}


class ReceiptAccountingRiskTest(unittest.TestCase):
    def setUp(self):
        from test_subcontracting_returns import SubcontractingReturnTest, Document, Row
        self.h = SubcontractingReturnTest()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.Row, self.Document = Row, Document
        self.subject, self.frappe = self.h.subject, self.h.frappe
        self.sle, self.gl = {}, {}
        self.original_get_all = self.frappe.get_all
        self.original_get_list = self.frappe.get_list
        def list_rows(doctype, **kw):
            if doctype == 'GL Entry':
                return self.gl.get(kw['filters']['voucher_no'], [])
            if doctype == 'Stock Ledger Entry' and kw['filters']['voucher_no'] in self.sle:
                return self.sle[kw['filters']['voucher_no']]
            return self.original_get_list(doctype, **kw)
        def all_rows(doctype, **kw):
            if doctype in ('Stock Ledger Entry','GL Entry'):
                rows = list_rows(doctype, **kw)
                return [row.name for row in rows] if kw.get('pluck') else rows
            return self.original_get_all(doctype, **kw)
        self.frappe.get_list, self.frappe.get_all = list_rows, all_rows
        self.native = install_native_gl(self.frappe, Document, Row, lambda name:self.sle[name])
        self.DocClass = type('Receipt', (self.subject.SubcontractingReceiptReturnMixin, Document), {})
        self.saved = self.h.source
        self.saved.use_item_inventory_account=False
        self.saved.company_currency='CNY'
        self.saved.cost_center='HEADER-CC'
        self.saved.additional_costs=[]
        self.saved.supplied_items=[]

    def receipt(self, *, qty=1, rejected=0, raw=8, fee=2, fg=10, is_return=0, warehouse='Good', raw_expense='EXPENSE', raw_center='FG-CC'):
        Row = self.Row
        name = 'CURRENT' if is_return else 'SOURCE'
        row = Row(self.h.source_row, name='RET-FG' if is_return else 'SRC-ROW', qty=qty,
            rejected_qty=rejected, process_loss_qty=0, conversion_factor=1, received_qty=qty+rejected,
            warehouse=warehouse, rejected_warehouse='Rejected', rate=fg/(qty or rejected), amount=fg,
            rm_supp_cost=raw, rm_cost_per_qty=raw/(qty+rejected), service_cost_per_qty=fee,
            additional_cost_per_qty=0, cost_center='FG-CC', project='PROJECT', expense_account='EXPENSE',
            service_expense_account='SERVICE', subcontracting_receipt_item='SRC-ROW' if is_return else None)
        raw_row = Row(name=name+'-RM', rm_item_code='RM', main_item_code='FG', reference_name=row.name,
            consumed_qty=1 if raw>0 else -1, amount=raw, rate=abs(raw), stock_uom='Kg',
            expense_account=raw_expense, cost_center=raw_center)
        doc = self.h.document(name,[row],cls=self.DocClass,is_return=is_return,docstatus=1,
            supplied_items=[raw_row],additional_costs=[],use_item_inventory_account=False,
            company_currency='CNY',cost_center='HEADER-CC')
        self.sle[name] = []
        for index, (detail,item,wh,quantity,amount) in enumerate(((row.name,'FG',warehouse,qty,fg if qty else 0),
                (row.name,'FG','Rejected',rejected,0),(raw_row.name,'RM','Supplier',-raw_row.consumed_qty,-raw))):
            if quantity:
                self.sle[name].append(Row(name=name+'-SLE-'+str(index),company='JF',voucher_type='Subcontracting Receipt',
                    voucher_no=name,voucher_detail_no=detail,item_code=item,warehouse=wh,actual_qty=quantity,
                    stock_value_difference=amount,is_cancelled=0))
        if is_return:
            self.h.doc=doc
        else:
            self.h.source=doc
        return doc

    def book_source(self, source, *, legacy=False):
        entries=[]
        callback = self.native['make_item_gl_entries'] if legacy else self.subject.SubcontractingReceiptReturnMixin.make_item_gl_entries
        callback(source,entries,source.get_inventory_account_map())
        self.gl[source.name]=ledger_rows(self.native['process_gl_map'](deepcopy(entries),from_repost=True),self.Row)
        return entries

    def add_receipt_row(self, document, *, name, qty, raw_qty, raw, fee, fg, source_row=None,
                        center='FG-CC', project='PROJECT', warehouse='Good', item='FG', raw_item='RM',
                        expense='EXPENSE', service='SERVICE', raw_center=None, raw_expense=None):
        row=self.Row(document.items[0],name=name,qty=qty,rejected_qty=0,received_qty=qty,
            item_code=item,warehouse=warehouse,rate=fg/qty,amount=fg,rm_supp_cost=raw,rm_cost_per_qty=raw/qty,
            service_cost_per_qty=fee,cost_center=center,project=project,expense_account=expense,
            service_expense_account=service,subcontracting_receipt_item=source_row)
        material=self.Row(name=name+'-RM',rm_item_code=raw_item,main_item_code=item,reference_name=name,
            consumed_qty=raw_qty,amount=raw,rate=raw/raw_qty,stock_uom='Kg',
            cost_center=raw_center or center,expense_account=raw_expense or expense)
        document.items.append(row)
        document.supplied_items.append(material)
        for detail,code,wh,quantity,value in ((name,item,warehouse,qty,fg),
                (material.name,raw_item,'Supplier',-raw_qty,-raw)):
            self.sle[document.name].append(self.Row(name=detail+'-SLE',company='JF',voucher_type='Subcontracting Receipt',
                voucher_no=document.name,voucher_detail_no=detail,item_code=code,warehouse=wh,
                actual_qty=quantity,stock_value_difference=value,is_cancelled=0))
        return row

    def calculate(self, doc):
        entries=[]
        doc.make_item_gl_entries(entries,doc.get_inventory_account_map())
        self.assertEqual(sum(net(entries).values()),0)
        return entries

    def test_normal_receipt_and_return_preserve_native_accounts_and_amounts(self):
        source=self.receipt()
        actual=self.book_source(source,legacy=True)
        self.assertEqual(net(self.calculate(source)),net(actual))
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(actual).items()})

    def test_normal_native_default_shared_expense_account_remains_returnable(self):
        source=self.receipt()
        source.items[0].service_expense_account=None
        actual=self.book_source(source,legacy=True)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        # Explicitly choosing the native fallback account is equivalent to a
        # blank source service account, and must preserve old operation.
        returned.items[0].service_expense_account='EXPENSE'
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(actual).items()})

    def test_processing_uses_native_operand_precision_before_multiplication(self):
        source=self.receipt(qty=100,raw=800,fee=2.004,fg=1000)
        class NativeCurrencyRow(self.Row):
            def precision(self, field):
                return 2 if field=='service_cost_per_qty' else 6
        source.items[0]=NativeCurrencyRow(source.items[0])
        native=[]
        self.native['make_item_gl_entries'](source,native,source.get_inventory_account_map())
        candidate=self.calculate(source)
        self.assertEqual(net(candidate),net(native))
        self.assertEqual(self.subject.receipt_accounting_fee(source,source.items[0],'service_cost_per_qty'),200)
        self.assertEqual(net(candidate)[('SERVICE','FG-CC','PROJECT')],-200)
        self.gl[source.name]=ledger_rows(native,self.Row)
        returned=self.receipt(qty=-100,raw=-800,fee=2.004,fg=-1000,is_return=1)
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(native).items()})
        self.assertEqual(self.subject.receipt_accounting_fee(returned,returned.items[0],
            'service_cost_per_qty',source.items[0]),-200)

    def test_only_rejected_zero_value_source_and_return_have_material_gl_without_estimated_fee(self):
        source=self.receipt(qty=0,rejected=1,fg=0)
        source.items[0].rate=10 # saved estimate does not create accepted processing liability
        source.items[0].amount=10
        before=deepcopy(source.items[0])
        entries=self.book_source(source)
        self.assertEqual(net(entries),{('STOCK-Supplier','FG-CC','PROJECT'):Decimal(-8),('ADJUST','FG-CC','PROJECT'):Decimal(8)})
        self.assertEqual(source.items[0],before)
        returned=self.receipt(qty=0,rejected=-1,raw=-8,fg=0,is_return=1)
        inverse=self.calculate(returned)
        self.assertEqual(net(inverse),{key:-value for key,value in net(entries).items()})
        self.assertTrue(all(not e.credit and not e.debit for e in inverse if e.account=='SERVICE'))

    def test_zero_fg_rate_with_accepted_service_books_actual_fee_and_exact_inverse(self):
        source=self.receipt(qty=1,fg=0)
        entries=self.book_source(source)
        self.assertEqual(net(entries)[('SERVICE','FG-CC','PROJECT')],-2)
        self.assertEqual(net(entries)[('ADJUST','FG-CC','PROJECT')],10)
        returned=self.receipt(qty=-1,raw=-8,fg=0,is_return=1)
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(entries).items()})

    def test_historical_native_zero_rate_skipped_gl_never_creates_a_return_fee(self):
        source=self.receipt(fg=0)
        self.book_source(source,legacy=True)
        returned=self.receipt(qty=-1,raw=-8,fg=0,is_return=1)
        with self.assertRaisesRegex(ValueError,'没有有效会计'):
            self.calculate(returned)

    def test_zero_fee_legacy_rejected_missing_gl_is_also_blocked(self):
        source=self.receipt(qty=0,rejected=1,fg=0,fee=0)
        self.book_source(source,legacy=True)
        returned=self.receipt(qty=0,rejected=-1,raw=-8,fg=0,fee=0,is_return=1)
        with self.assertRaisesRegex(ValueError,'没有有效会计'):
            self.calculate(returned)

    def test_true_free_stock_with_exact_zero_value_sle_and_empty_gl_is_returnable(self):
        source=self.receipt(raw=0,fee=0,fg=0)
        source.supplied_items[0].consumed_qty=1
        self.sle[source.name][-1].actual_qty=-1
        self.book_source(source,legacy=True)
        self.assertEqual(self.gl[source.name],[])
        returned=self.receipt(qty=-1,raw=0,fee=0,fg=0,is_return=1)
        self.assertEqual(net(self.calculate(returned)),{})
        self.assertEqual([(row.actual_qty,row.stock_value_difference) for row in self.sle[source.name]],[(1,0),(-1,0)])
        self.sle[source.name]=[row for row in self.sle[source.name] if row.item_code=='RM']
        delattr(source,'_jifeng_return_stock_entries')
        with self.assertRaisesRegex(ValueError,'不能把缺失估值当零'):
            self.calculate(returned)

    def test_mixed_accepted_rejected_return_reverses_only_accepted_fee(self):
        source=self.receipt(qty=1,rejected=1,raw=16,fg=10)
        saved=self.book_source(source)
        returned=self.receipt(qty=-1,rejected=-1,raw=-16,fg=-10,is_return=1)
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(saved).items()})
        self.assertEqual(self.subject.receipt_accounting_fee(returned,returned.items[0],
            'service_cost_per_qty',source.items[0]),-2)

    def test_rejected_mapper_qty_does_not_reverse_original_accepted_processing_fee(self):
        source=self.receipt(qty=1,rejected=1,raw=16,fg=10)
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=-8,fg=0,is_return=1,warehouse='Rejected')
        values=net(self.calculate(returned))
        self.assertNotIn(('SERVICE','FG-CC','PROJECT'),values)
        self.assertEqual(values[('STOCK-Supplier','FG-CC','PROJECT')],8)
        self.assertEqual(values[('ADJUST','FG-CC','PROJECT')],-8)

    def test_stock_submit_does_not_require_general_gl_read_permission(self):
        source=self.receipt()
        self.book_source(source,legacy=True)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        original=self.frappe.get_list
        self.frappe.get_list=lambda kind,**kw: (_ for _ in ()).throw(PermissionError('no general GL read')) if kind=='GL Entry' else original(kind,**kw)
        self.calculate(returned)
        source.unreadable=True
        with self.assertRaises(PermissionError):
            self.calculate(returned)

    def test_receipt_proof_selects_only_real_persisted_gl_columns(self):
        source=self.receipt()
        self.book_source(source)
        columns=self.frappe.get_meta('GL Entry').get_valid_columns()
        fields=self.subject.receipt_gl_proof_fields()
        self.assertTrue(PERSISTED_GL_MERGE_FIELDS.issubset(fields))
        self.assertTrue({'advance_voucher_no','advance_voucher_type'}.isdisjoint(fields))
        self.assertTrue(all({'advance_voucher_no','advance_voucher_type'}.isdisjoint(row)
                            for row in self.gl[source.name]))
        original=self.frappe.get_all
        def persisted_select(kind,**options):
            if kind=='GL Entry':
                self.assertTrue(set(options['fields']).issubset(columns),'Unknown column in native GL SELECT')
            return original(kind,**options)
        self.frappe.get_all=persisted_select
        self.subject._receipt_fee_ledger(source)
        self.subject._prove_receipt_fees(source,for_return=True)

    def test_nonpersistent_native_merge_identity_is_not_silently_erased(self):
        source=self.receipt()
        self.book_source(source)
        native_module=sys.modules['erpnext.accounts.general_ledger']
        original_dict=self.DocClass.get_gl_dict
        original_process=native_module.process_gl_map
        for field,value in (('advance_voucher_no','ADVANCE'),('advance_voucher_type','Payment Entry'),
                            ('advance_voucher_no',0)):
            with self.subTest(field=field,value=value,phase='before native merge'):
                def gl_dict(document,values,item=None):
                    result=original_dict(document,values,item)
                    result[field]=value
                    return result
                with patch.object(self.DocClass,'get_gl_dict',gl_dict):
                    with self.assertRaisesRegex(ValueError,'非持久化合并标识'):
                        self.subject._prove_receipt_fees(source,for_return=True)
            with self.subTest(field=field,value=value,phase='after native merge'):
                def process(values,**options):
                    result=original_process(values,**options)
                    for row in result:
                        row[field]=value
                    return result
                with patch.object(native_module,'process_gl_map',process):
                    with self.assertRaisesRegex(ValueError,'非持久化合并标识'):
                        self.subject._prove_receipt_fees(source,for_return=True)

    def test_changed_saved_fee_cannot_reverse_different_actual_source_gl(self):
        source=self.receipt()
        self.book_source(source,legacy=True)
        source.items[0].service_cost_per_qty=9
        returned=self.receipt(qty=-1,raw=-8,fg=-10,fee=9,is_return=1)
        with self.assertRaisesRegex(ValueError,'有效会计分录'):
            self.calculate(returned)

    def test_multi_primary_source_is_not_guessed_from_one_merged_gl_total(self):
        source=self.receipt()
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14)
        self.book_source(source,legacy=True)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        with self.assertRaisesRegex(ValueError,'逐行核实'):
            self.calculate(returned)

    def test_marked_multi_same_accounts_keep_exact_fees_and_partial_second_row_return(self):
        source=self.receipt()
        second=self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14)
        self.book_source(source)
        self.assertEqual({row.voucher_detail_no for row in self.gl[source.name]}, {'SRC-ROW','SECOND'})
        returned=self.receipt(qty=-1,raw=-4,fee=3,fg=-7,is_return=1)
        returned.items[0].subcontracting_receipt_item=second.name
        entries=self.calculate(returned)
        self.assertEqual(net(entries),{('STOCK-Good','FG-CC','PROJECT'):Decimal(-7),
            ('STOCK-Supplier','FG-CC','PROJECT'):Decimal(4),('SERVICE','FG-CC','PROJECT'):Decimal(3)})
        self.assertTrue(all(row.voucher_detail_no=='RET-FG' for row in entries))

    def test_marked_multi_full_return_is_exact_across_accounts_centers_and_warehouses(self):
        source=self.receipt()
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14,
            center='CC2',project='PROJECT2',warehouse='Rejected',item='FG2',raw_item='RM2',
            raw_center='RAW-CC2',raw_expense='RAW-EXP2',service='SERVICE2')
        original=self.book_source(source)
        returned=self.receipt(qty=-1,raw=-8,fee=2,fg=-10,is_return=1)
        self.add_receipt_row(returned,name='RET2',qty=-2,raw_qty=-2,raw=-8,fee=3,fg=-14,source_row='SECOND',
            center='CC2',project='PROJECT2',warehouse='Rejected',item='FG2',raw_item='RM2',
            raw_center='RAW-CC2',raw_expense='RAW-EXP2',service='SERVICE2')
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(original).items()})

    def test_marked_fee_moved_to_other_row_is_rejected_even_when_whole_totals_match(self):
        source=self.receipt()
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14)
        self.book_source(source)
        service=next(row for row in self.gl[source.name] if row.account=='SERVICE' and row.voucher_detail_no=='SRC-ROW')
        service.voucher_detail_no='SECOND'
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        with self.assertRaisesRegex(ValueError,'有效会计分录'):
            self.calculate(returned)

    def test_legacy_multi_disjoint_centers_are_uniquely_proved(self):
        source=self.receipt()
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14,center='CC2')
        self.book_source(source,legacy=True)
        self.assertTrue(all(not row.voucher_detail_no for row in self.gl[source.name]))
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        self.calculate(returned)

    def test_native_dimension_merge_and_allocation_retain_per_row_proof(self):
        self.native['dimensions'][:]=['department']
        self.native['cost_allocations']['FG-CC']=[('CC-A',50),('CC-B',50)]
        source=self.receipt()
        second=self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14)
        source.department='HEADER-DIM'
        source.items[0].department='DEPT-A'
        second.department='DEPT-B'
        self.book_source(source)
        self.assertEqual({row.department for row in self.gl[source.name]}, {'DEPT-A','DEPT-B'})
        self.assertEqual({row.cost_center for row in self.gl[source.name]}, {'CC-A','CC-B'})
        returned=self.receipt(qty=-1,raw=-4,fee=3,fg=-7,is_return=1)
        returned.department='HEADER-DIM'
        returned.items[0].department='DEPT-B'
        returned.items[0].subcontracting_receipt_item=second.name
        self.calculate(returned)
        changed=next(row for row in self.gl[source.name] if row.voucher_detail_no=='SECOND' and row.account=='SERVICE')
        changed.department='DEPT-A'
        with self.assertRaisesRegex(ValueError,'有效会计分录'):
            self.calculate(returned)

    def test_return_account_and_raw_center_changes_do_not_rewrite_original_fee_ownership(self):
        source=self.receipt()
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        returned.items[0].service_expense_account='OTHER-SERVICE'
        with self.assertRaisesRegex(ValueError,'会计科目或维度'):
            self.calculate(returned)
        returned.items[0].service_expense_account='SERVICE'
        returned.supplied_items[0].cost_center='OTHER-CC'
        with self.assertRaisesRegex(ValueError,'原耗料'):
            self.calculate(returned)

    def test_multi_return_source_is_loaded_and_proved_once_without_client_flags(self):
        source=self.receipt()
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=14)
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        self.add_receipt_row(returned,name='RET2',qty=-2,raw_qty=-2,raw=-8,fee=3,fg=-14,source_row='SECOND')
        self.h.events.clear()
        self.calculate(returned)
        self.assertEqual(len([event for event in self.h.events if event[:2]==('source','SOURCE')]),1)
        with self.assertRaisesRegex(ValueError,'客户端标志'):
            self.subject.receipt_accounting_fee(returned,returned.items[0],'service_cost_per_qty',
                source.items[0],fee_source=source,_proof={'passed':True})
        self.book_source(returned)
        reads=[]
        def reader(document):
            reads.append(document.name)
            return self.gl[document.name]
        proof=self.subject._prove_receipt_fees(source,ledger_reader=reader,for_return=True)
        original_all=self.frappe.get_all
        self.frappe.get_all=lambda kind,**kw: self.fail('cost proof must keep its GL reader') if kind=='GL Entry' else original_all(kind,**kw)
        self.h.events.clear()
        self.subject._prove_receipt_fees(returned,ledger_reader=reader,_source_proof=proof)
        self.assertEqual(reads,['SOURCE','CURRENT'])
        self.assertEqual([event for event in self.h.events if event[0]=='source'],[])

    def test_multi_additional_costs_keep_parent_charge_identity_and_exact_partial_allocation(self):
        source=self.receipt(fg=11)
        source.items[0].additional_cost_per_qty=1
        second=self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=8,fee=3,fg=16)
        second.additional_cost_per_qty=1
        source.additional_costs=[self.Row(name='SOURCE-COST',expense_account='FREIGHT',
            account_currency='CNY',amount=3,base_amount=3)]
        self.book_source(source)
        self.assertEqual({row.voucher_detail_no for row in self.gl[source.name]}, {'SRC-ROW','SECOND','SOURCE-COST'})
        returned=self.receipt(qty=-1,raw=-4,fee=3,fg=-8,is_return=1)
        returned.items[0].subcontracting_receipt_item='SECOND'
        returned.items[0].additional_cost_per_qty=1
        returned.additional_costs=[self.Row(name='RETURN-COST',expense_account='FREIGHT',
            account_currency='CNY',amount=-1,base_amount=-1)]
        values=net(self.calculate(returned))
        self.assertEqual(values[('FREIGHT','HEADER-CC',None)],1)
        self.assertEqual(self.subject.receipt_accounting_fee(returned,returned.items[0],
            'additional_cost_per_qty',second),-1)
        charge=next(row for row in self.gl[source.name] if row.voucher_detail_no=='SOURCE-COST')
        charge.voucher_detail_no='SECOND'
        with self.assertRaisesRegex(ValueError,'有效会计分录'):
            self.calculate(returned)

    def test_changed_native_allocation_on_return_date_cannot_redirect_original_balance(self):
        self.native['cost_allocations'][('2026-10-08','FG-CC')]=[('CC-A',50),('CC-B',50)]
        source=self.receipt()
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        returned.posting_date='2026-10-09'
        self.native['cost_allocations'][('2026-10-09','FG-CC')]=[('CC-A',100)]
        with self.assertRaisesRegex(ValueError,'分摊与原已入账'):
            self.calculate(returned)
        self.native['cost_allocations'][('2026-10-09','FG-CC')]=[('CC-A',50),('CC-B',50)]
        self.calculate(returned)

    def test_partial_allocation_rounding_cannot_accumulate_different_centers_but_full_return_works(self):
        self.native['get_field_precision']=lambda *args,**kw:2
        self.native['cost_allocations']['FG-CC']=[('CC-A',33),('CC-B',67)]
        source=self.receipt(qty=3,raw=0,fee=0.02,fg=0.06)
        source.supplied_items[0].consumed_qty=1
        self.sle[source.name][-1].actual_qty=-1
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=0,fee=0.02,fg=-0.02,is_return=1)
        with self.assertRaisesRegex(ValueError,'分钱舍入'):
            self.calculate(returned)
        returned=self.receipt(qty=-3,raw=0,fee=0.02,fg=-0.06,is_return=1)
        candidate=self.native['process_gl_map'](self.calculate(returned),from_repost=True)
        self.assertEqual(net(candidate),{key:-value for key,value in net(self.gl[source.name]).items()})
        # An exact source split also cannot permit a newly rounded partial
        # split. The usual exact 50/50 partial case above remains supported.
        source=self.receipt(qty=2,raw=0,fee=0.5,fg=1)
        source.supplied_items[0].consumed_qty=1
        self.sle[source.name][-1].actual_qty=-1
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=0,fee=0.5,fg=-0.5,is_return=1)
        with self.assertRaisesRegex(ValueError,'分钱舍入'):
            self.calculate(returned)

    def test_untouched_rounded_source_row_does_not_block_another_exact_partial_return(self):
        self.native['get_field_precision']=lambda *args,**kw:2
        self.native['cost_allocations']['FG-CC']=[('CC-A',33),('CC-B',67)]
        source=self.receipt(qty=3,raw=0,fee=0.02,fg=0.06)
        source.supplied_items[0].consumed_qty=1
        self.sle[source.name][-1].actual_qty=-1
        self.add_receipt_row(source,name='SECOND',qty=2,raw_qty=2,raw=0,fee=1,fg=2)
        self.book_source(source)
        returned=self.receipt(qty=-1,raw=0,fee=1,fg=-1,is_return=1)
        returned.items[0].subcontracting_receipt_item='SECOND'
        candidate=self.native['process_gl_map'](self.calculate(returned),from_repost=True)
        self.assertEqual(net(candidate)[('STOCK-Good','CC-A','PROJECT')],Decimal('-0.33'))
        self.assertEqual(net(candidate)[('SERVICE','CC-B','PROJECT')],Decimal('0.67'))

    def test_legacy_divisional_loss_direction_is_not_recomputed_into_new_return(self):
        source=self.receipt(fg=8)
        source.items[0].amount=10
        self.book_source(source,legacy=True)
        returned=self.receipt(qty=-1,raw=-8,fg=-8,is_return=1)
        with self.assertRaisesRegex(ValueError,'历史会计差额'):
            self.calculate(returned)

    def test_raw_expense_and_center_remain_separate_and_balanced(self):
        source=self.receipt(raw_expense='RAW-EXP',raw_center='RAW-CC')
        original=self.book_source(source,legacy=True)
        self.assertEqual(net(self.calculate(source)),net(original))
        self.assertEqual(net(original)[('RAW-EXP','RAW-CC','PROJECT')],8)
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1,raw_expense='RAW-EXP',raw_center='RAW-CC')
        self.assertEqual(net(self.calculate(returned)),{key:-value for key,value in net(original).items()})

    def test_zero_value_requires_actual_exact_rejected_sle(self):
        source=self.receipt(qty=0,rejected=1,fg=0)
        self.sle[source.name]=[row for row in self.sle[source.name] if row.item_code=='RM']
        with self.assertRaisesRegex(ValueError,'不能把缺失估值当零'):
            self.calculate(source)

    def test_wrong_company_or_raw_amount_does_not_create_partial_accounting(self):
        source=self.receipt()
        self.sle[source.name][0].company='OTHER'
        with self.assertRaisesRegex(ValueError,'公司'):
            self.calculate(source)
        self.sle[source.name][0].company='JF'
        source.supplied_items[0].amount=7
        with self.assertRaisesRegex(ValueError,'原料金额'):
            self.calculate(source)

    def test_cancel_native_dispatch_reverses_saved_gl_without_recomputing_costs(self):
        source=self.receipt()
        saved=self.book_source(source)
        calls=[]
        namespace=self.native
        source.docstatus=2
        source.get_stock_items=lambda:['FG','RM']
        source.get_gl_entries=lambda *args:self.fail('cancel must not recalculate fees/GL')
        source.items[0].service_cost_per_qty=999
        # Retain the function before replacing its external global writer.
        method_ns=dict(namespace)
        exec(NATIVE_SOURCES['StockController.make_gl_entries'],method_ns)
        method=method_ns['make_gl_entries']
        method_ns['make_gl_entries']=lambda *args,**kw:self.fail('new GL writer called')
        method_ns['make_reverse_gl_entries']=lambda **kw:calls.append(kw)
        method(source)
        self.assertEqual(calls,[{'voucher_type':'Subcontracting Receipt','voucher_no':'SOURCE'}])
        self.assertEqual(sum(net(saved).values()),0)
