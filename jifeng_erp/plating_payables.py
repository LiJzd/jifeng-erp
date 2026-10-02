"""Read an explicit plating archive and its Supplier's complete cutoff AP.

The archive identifies a Supplier; it does not classify that Supplier's debt.
No legacy arrears formula, summary, payment, archive edit or ledger write exists.
"""

import frappe

from jifeng_erp import partner_archive as P
from jifeng_erp import supplier_reconciliation as A


KIND = "电镀厂"
AMOUNT_SCOPE = "关联供应商在本公司截至所选日期的已过账应付，包含该供应商的全部业务"


@frappe.whitelist(methods=["GET"])
def read_payables(company, name, report_date):
    """Return one read-only archive DTO and the unchanged native AP response."""
    profile, supplier = P._load(name, KIND, company)
    supplier_name = supplier.name
    source_before = (P._state(profile), P._state(supplier))
    company_document = P._scope(KIND, company)
    documents = {(P.PROFILE, profile.name): profile, ("Supplier", supplier_name): supplier,
                 ("Company", company): company_document}
    permissions_before = A._permission_state(documents)
    archive = P._pack(profile, supplier, listing=True)
    payables = A.query_payables(company, supplier_name, report_date)
    current_profile, current_supplier = P._load(name, KIND, company)
    if source_before != (P._state(current_profile), P._state(current_supplier)):
        frappe.throw("查询期间工厂档案或关联供应商已改变，请重新查询")
    P._guard(profile, supplier, archive)
    # Raw inputs follow the final guard too: cached native permissions may still
    # allow an unchanged projection after a persisted permission has changed.
    if permissions_before != A._permission_state(documents):
        frappe.throw("查询期间工厂档案或来源权限已改变，请重新查询")
    return {"read_only": True, "kind": KIND, "archive": archive, "payables": payables,
            "amount_scope": AMOUNT_SCOPE}
