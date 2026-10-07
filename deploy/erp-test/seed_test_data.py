"""Seed one fresh, shared QA site through native setup and document controllers.

Run once inside the test image, after initialize.py and before normal workers.
A private receipt survives failure; an existing receipt always forbids a rerun.
The official setup wizard can commit internally. Synthetic business documents
are subsequently created in one transaction with exactly one explicit commit.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import frappe

BENCH = Path("/home/frappe/frappe-bench")
SITE = "jifeng-erp-test.localhost"
DB_NAME = "jifeng_erp_test"
SEED_NAMESPACE = "jifeng-qa-seed-r2"
WORKSPACE = "集沣生产协作"
COMPANY = "集沣 QA 测试公司"
QA_USER = "qa@jifeng.test"
CUSTOMER = "QA 测试客户"
SUPPLIER = "QA 测试电镀供应商"
ROLES = (
    "Stock Manager", "Manufacturing Manager", "Manufacturing User", "Sales Manager", "Purchase Manager",
    "Accounts Manager", "HR Manager", "Quality Manager", "Projects Manager",
    "Item Manager", "Sales Master Manager", "Purchase Master Manager",
)
ITEMS = (
    ("JF-QA-RAW-001", "QA 测试金属配件", 0),
    ("JF-QA-RAW-002", "QA 测试包装盒", 0),
    ("JF-QA-FG-001", "QA 测试饰品", 1),
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Receipt:
    def __init__(self):
        self.path = Path(frappe.get_site_path("private", SEED_NAMESPACE + ".json"))
        self.data = {
            "namespace": SEED_NAMESPACE, "site": SITE, "database": DB_NAME,
            "status": "started", "phase": "preflight", "started_at": stamp(),
            "source_data_imported": False, "original_sessions_imported": False,
            "credentials_recorded": False, "rerun_allowed": False,
            "setup_may_have_committed": False, "documents": [], "commits": [],
            "seed_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        with self.path.open("x", encoding="utf-8") as stream:
            os.chmod(self.path, 0o600)
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        self.allow_business_commit = False
        frappe.db.before_commit.add(self.before_commit)

    def save(self):
        temporary = self.path.with_name(self.path.name + ".next")
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, self.path)

    def phase(self, name):
        self.data["phase"] = name
        self.save()

    def before_commit(self):
        if self.data["phase"] == "business_seed":
            require(self.allow_business_commit, "Unexpected commit during business seed; inspect the receipt")
        entry = {"number": len(self.data["commits"]) + 1, "phase": self.data["phase"],
                 "started_at": stamp(), "confirmed": False}
        self.data["commits"].append(entry)
        self.save()

        def after_commit():
            entry.update(confirmed=True, confirmed_at=stamp())
            self.save()
            # Re-arm the other queue only: CallbackManager.run drains its own
            # queue until empty, so self-rearming that queue would loop forever.
            frappe.db.before_commit.add(self.before_commit)

        frappe.db.after_commit.add(after_commit)

    def document(self, doc):
        self.data["documents"].append({"doctype": doc.doctype, "name": doc.name,
                                       "docstatus": doc.docstatus, "pending_commit": True})
        self.save()
        return doc


def insert(receipt, values):
    return receipt.document(frappe.get_doc(values).insert())


def leaf(doctype, filters=None):
    names = frappe.get_list(doctype, filters={"is_group": 0, **(filters or {})},
                            pluck="name", order_by="name asc", limit_page_length=1)
    require(bool(names), "Missing native setup fixture: " + doctype)
    return names[0]


def preflight():
    require(frappe.local.site == SITE and frappe.conf.db_name == DB_NAME, "Wrong test site or database")
    require(frappe.get_installed_apps() == ["frappe", "erpnext", "hrms", "jifeng_erp"], "Unexpected installed applications")
    require(not frappe.is_setup_complete(), "Setup already complete; inspect existing site instead of seeding again")
    require(frappe.db.count("Company") == 0, "Company already exists; inspect partial setup")
    require(bool(frappe.conf.mute_emails), "Test site must have mute_emails enabled")
    require(not frappe.conf.get("pulse_api_key") and not frappe.conf.get("pulse_force_enabled"), "Telemetry provider configured on QA site")
    for doctype, value in (("User", QA_USER), ("Item", "JF-QA-RAW-001"), ("Item", "JF-QA-RAW-002"),
                          ("Item", "JF-QA-FG-001"), ("Workspace", WORKSPACE)):
        expected = doctype == "Workspace"
        require(bool(frappe.db.exists(doctype, value)) == expected, "Unexpected QA namespace or missing workspace: " + doctype)
    require(not frappe.db.exists("Customer", {"customer_name": CUSTOMER}), "QA customer already exists")
    require(not frappe.db.exists("Supplier", {"supplier_name": SUPPLIER}), "QA supplier already exists")
    require(frappe.db.exists("Language", "zh"), "Simplified Chinese language is missing")
    require(frappe.db.get_value("Language", "zh", "language_name") == "中文", "Unexpected Chinese setup language name")
    for role in ROLES:
        require(frappe.db.exists("Role", {"name": role, "disabled": 0}), "Required business role is missing: " + role)
    required_fields = {
        "User": {"email", "first_name", "language", "time_zone", "roles", "send_welcome_email", "default_workspace"},
        "User Permission": {"user", "allow", "for_value", "is_default", "apply_to_all_doctypes"},
        "Employee": {"first_name", "company", "gender", "date_of_birth", "date_of_joining", "employee_number", "status"},
        "Item": {"item_code", "item_name", "item_group", "stock_uom", "is_stock_item", "is_sales_item", "item_defaults"},
        "Customer": {"customer_name", "customer_type", "customer_group", "territory", "default_currency"},
        "Supplier": {"supplier_name", "supplier_type", "supplier_group", "default_currency"},
        "Jifeng Product Profile": {"item_code", "company", "display_name", "materials", "requirements"},
        "Jifeng Partner Profile": {"company", "archive_kind", "supplier", "legacy_number", "display_name", "request_id"},
        "Sales Order": {"customer", "company", "items", "selling_price_list", "delivery_date", "currency"},
    }
    for doctype, fields in required_fields.items():
        meta = frappe.get_meta(doctype)
        require(all(meta.get_field(name) for name in fields), "Installed native schema differs: " + doctype)
    password = Path("/run/secrets/qa_password").read_text().strip()
    require(len(password) >= 16, "QA secret is missing or too short")
    return password


def setup_company(receipt):
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

    receipt.data.update(setup_may_have_committed=True, official_setup_commit_points=[
        "update_global_settings: Chinese default language commit",
        "run_post_setup_complete: wrapping-up commit",
    ])
    receipt.phase("official_setup")
    # No first-user email/password: the wizard otherwise adds System Manager.
    result = setup_complete({
        "language": "中文", "lang": "中文", "country": "China", "timezone": "Asia/Shanghai",
        "currency": "CNY", "company_name": COMPANY, "company_abbr": "JFQ",
        "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31",
        "chart_of_accounts": "Standard", "domain": "Manufacturing",
        "setup_demo": 0, "enable_telemetry": 0,
    })
    require(result == {"status": "ok"}, "Official setup did not return success")
    require(frappe.is_setup_complete(), "Official setup applications are incomplete")
    require(bool(frappe.db.get_single_value("System Settings", "setup_complete")), "System setup marker is incomplete")
    company = frappe.get_doc("Company", COMPANY)
    require(company.default_currency == "CNY" and company.country == "China", "Company setup differs")
    require(company.default_receivable_account and company.default_payable_account, "Company native chart is incomplete")
    fiscal = frappe.get_doc("Fiscal Year", "2026")
    require(str(fiscal.year_start_date) == "2026-01-01" and str(fiscal.year_end_date) == "2026-12-31", "Fiscal year differs")
    receipt.data["official_setup_verified"] = True
    receipt.save()


def seed_business(receipt, password):
    from frappe.utils.password import update_password
    from frappe.utils.scheduler import toggle_scheduler
    from jifeng_erp.partner_archive import read_supplier, save_archive

    receipt.phase("business_seed")
    settings = frappe.get_single("System Settings")
    settings.update({"language": "zh", "time_zone": "Asia/Shanghai", "enable_telemetry": 0})
    settings.save()
    toggle_scheduler(True)
    user = insert(receipt, {
        "doctype": "User", "email": QA_USER, "first_name": "业务测试主管", "enabled": 1,
        "user_type": "System User", "language": "zh", "time_zone": "Asia/Shanghai",
        "send_welcome_email": 0, "default_workspace": WORKSPACE,
        "roles": [{"role": role} for role in ROLES],
    })
    update_password(user.name, password)
    insert(receipt, {"doctype": "User Permission", "user": user.name, "allow": "Company",
                     "for_value": COMPANY, "is_default": 1, "apply_to_all_doctypes": 1})
    frappe.defaults.set_user_default("Company", COMPANY, user=user.name)
    frappe.set_user(user.name)
    frappe.flags.mute_emails = True
    require("System Manager" not in frappe.get_roles(), "QA user unexpectedly has System Manager")
    frappe.get_doc("Company", COMPANY).check_permission("read")
    groups = {doctype: leaf(doctype) for doctype in ("Item Group", "Customer Group", "Supplier Group", "Territory")}
    for group in ("Raw Material", "Products"):
        document = frappe.get_doc("Item Group", group)
        document.check_permission("select")
        require(not document.is_group, "Native item setup fixture is not a leaf")
    warehouse = leaf("Warehouse", {"company": COMPANY, "disabled": 0})
    require(frappe.db.exists("UOM", "Nos") and frappe.db.exists("Gender", "Male"), "Native UOM or gender fixture missing")
    prices = frappe.get_list("Price List", filters={"selling": 1, "enabled": 1, "currency": "CNY"},
                             pluck="name", order_by="name asc", limit_page_length=1)
    require(bool(prices), "Native CNY selling price list missing")
    receipt.data["selected_native_fixtures"] = {**groups, "raw_item_group": "Raw Material", "finished_item_group": "Products",
                                               "warehouse": warehouse, "selling_price_list": prices[0]}
    for number, name, birth in (("QA-EMP-001", "QA 测试员工甲", "1990-01-15"),
                                ("QA-EMP-002", "QA 测试员工乙", "1992-05-20")):
        insert(receipt, {"doctype": "Employee", "first_name": name, "employee_number": number,
                         "company": COMPANY, "status": "Active", "gender": "Male",
                         "date_of_birth": birth, "date_of_joining": "2026-01-01",
                         "create_user_automatically": 0, "create_user_permission": 0})
    customer = insert(receipt, {"doctype": "Customer", "customer_name": CUSTOMER,
                               "customer_type": "Company", "customer_group": groups["Customer Group"],
                               "territory": groups["Territory"], "default_currency": "CNY", "language": "zh"})
    supplier = insert(receipt, {"doctype": "Supplier", "supplier_name": SUPPLIER,
                               "supplier_type": "Company", "supplier_group": groups["Supplier Group"],
                               "default_currency": "CNY", "language": "zh"})
    for code, name, is_sales in ITEMS:
        insert(receipt, {"doctype": "Item", "item_code": code, "item_name": name,
                         "item_group": "Products" if is_sales else "Raw Material", "stock_uom": "Nos", "is_stock_item": 1,
                         "is_sales_item": is_sales, "is_purchase_item": 1,
                         "item_defaults": [{"company": COMPANY, "default_warehouse": warehouse}]})
    insert(receipt, {"doctype": "Jifeng Product Profile", "item_code": "JF-QA-FG-001", "company": COMPANY,
                     "display_name": "QA 测试饰品档案", "legacy_number": "QA-FG-001", "display_uom": "个",
                     "requirements": "合成测试：按样确认颜色和表面处理", "packaging": "合成测试：单件装盒",
                     "materials": [{"item_code": "JF-QA-RAW-001", "uom": "Nos", "qty_per_unit": 2, "unit_price": 0.5},
                                   {"item_code": "JF-QA-RAW-002", "uom": "Nos", "qty_per_unit": 1, "unit_price": 0.2}]})
    source = read_supplier(supplier.name, "电镀厂", COMPANY)
    partner = save_archive({"kind": "电镀厂", "company": COMPANY, "request_id": str(uuid4()),
                            "supplier_version": source["version"],
                            "profile": {"supplier": supplier.name, "legacy_number": "QA-PL-001",
                                        "display_name": "QA 测试电镀厂", "owner_name": "测试负责人",
                                        "address": "合成地址：QA 测试园区", "notes": "仅用于两套界面切换对比"}})
    receipt.document(frappe.get_doc("Jifeng Partner Profile", partner["name"]))
    order = insert(receipt, {"doctype": "Sales Order", "customer": customer.name, "company": COMPANY,
                            "order_type": "Sales", "transaction_date": "2026-10-02", "delivery_date": "2026-10-15",
                            "currency": "CNY", "conversion_rate": 1, "selling_price_list": prices[0],
                            "price_list_currency": "CNY", "plc_conversion_rate": 1,
                            "items": [{"item_code": "JF-QA-FG-001", "qty": 100, "uom": "Nos", "conversion_factor": 1,
                                       "rate": 12, "warehouse": warehouse, "delivery_date": "2026-10-15"}]})
    require(order.docstatus == 0 and order.status == "Draft", "QA sales order must remain a draft")
    for record in receipt.data["documents"]:
        if record["doctype"] not in ("User", "User Permission"):
            frappe.get_doc(record["doctype"], record["name"]).check_permission("read")
    require(frappe.db.count("GL Entry") == 0 and frappe.db.count("Stock Ledger Entry") == 0, "QA seed unexpectedly wrote a ledger")
    receipt.data["qa_user"] = user.name
    receipt.data["qa_roles"] = sorted(frappe.get_roles(user.name))
    receipt.data["sales_order"] = order.name
    receipt.allow_business_commit = True
    frappe.db.commit()
    confirmed = [entry for entry in receipt.data["commits"] if entry["phase"] == "business_seed" and entry["confirmed"]]
    require(len(confirmed) == 1, "Business seed commit count differs; inspect receipt")
    for record in receipt.data["documents"]:
        record["pending_commit"] = False
    receipt.data.update(status="complete", phase="complete", completed_at=stamp(), business_commit_count=1)
    receipt.save()


def main():
    os.umask(0o077)
    os.chdir(BENCH / "sites")
    receipt = None
    frappe.init(site=SITE)
    try:
        frappe.connect()
        frappe.set_user("Administrator")
        frappe.flags.mute_emails = True
        require(not Path(frappe.get_site_path("private", SEED_NAMESPACE + ".json")).exists(), "QA seed receipt exists; do not rerun")
        password = preflight()
        receipt = Receipt()
        setup_company(receipt)
        seed_business(receipt, password)
        print("Shared QA setup and synthetic data seeded; private receipt written")
    except Exception as error:
        frappe.db.rollback()
        if receipt:
            receipt.data.update(status="failed", failed_at=stamp(), error_type=type(error).__name__,
                                commit_outcome_uncertain=any(not entry["confirmed"] for entry in receipt.data["commits"]),
                                next_action="Inspect private receipt, setup markers and recorded documents before any recovery; never rerun this namespace.")
            receipt.save()
        # A full exception/context traceback could include the in-memory secret.
        print("QA seed stopped; inspect its private receipt. Error type: " + type(error).__name__)
        raise SystemExit(1) from None
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
