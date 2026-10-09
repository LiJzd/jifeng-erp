"""Installed ERPNext 16.36 native GL callbacks drive receipt risk models."""

import sys
import types
import unittest
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
                                               '\t\t)'}



def install_native_gl(frappe, document_class, row_class, ledger_read):
    """Bind captured native callbacks; only external DB/account lookup is modelled."""
    frappe._dict = row_class
    frappe._ = lambda value: value
    namespace = dict(frappe=frappe, flt=lambda v, precision=None: round(float(v or 0), precision) if precision is not None else float(v or 0),
        cint=lambda v: int(v or 0), _=lambda v: v, bold=lambda v: v,
        get_account_currency=lambda account: 'CNY', erpnext=types.SimpleNamespace(is_perpetual_inventory_enabled=lambda company: True))
    for source in NATIVE_SOURCES.values():
        exec(source, namespace)
    native = types.ModuleType('erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt')
    native.get_account_currency = namespace['get_account_currency']
    native.SubcontractingReceipt = type('NativeReceipt', (), {'make_item_gl_entries': namespace['make_item_gl_entries']})
    sys.modules[native.__name__] = native
    document_class.add_gl_entry = namespace['add_gl_entry']
    document_class.get_inventory_account_dict = namespace['get_inventory_account_dict']
    document_class.get_inventory_account_map = lambda self: {warehouse: row_class(account='STOCK-'+warehouse, account_currency='CNY')
        for warehouse in ('Good','Rejected','Supplier','FG-WH','REJECT-WH','RM-WH')}
    document_class.get_company_default = lambda self, field, **kw: 'HEADER-CC' if field == 'cost_center' else 'ADJUST'
    document_class.get_gl_dict = lambda self, value, item=None: row_class(value, company=self.company,
        voucher_type=self.doctype, voucher_no=self.name, is_cancelled=0)
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
    fields = {'name','modified','company','voucher_type','voucher_no','voucher_detail_no',
              'account','cost_center','project','debit','credit','is_cancelled'}
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
        self.gl[source.name]=ledger_rows(entries,self.Row)
        return entries

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
        returned.items[0].service_expense_account=None
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

    def test_changed_saved_fee_cannot_reverse_different_actual_source_gl(self):
        source=self.receipt()
        self.book_source(source,legacy=True)
        source.items[0].service_cost_per_qty=9
        returned=self.receipt(qty=-1,raw=-8,fg=-10,fee=9,is_return=1)
        with self.assertRaisesRegex(ValueError,'有效会计分录'):
            self.calculate(returned)

    def test_multi_primary_source_is_not_guessed_from_one_merged_gl_total(self):
        source=self.receipt()
        self.book_source(source,legacy=True)
        source.items.append(self.Row(source.items[0],name='SECOND'))
        returned=self.receipt(qty=-1,raw=-8,fg=-10,is_return=1)
        with self.assertRaisesRegex(ValueError,'逐行核实'):
            self.calculate(returned)

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
