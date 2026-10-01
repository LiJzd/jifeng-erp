"""Exclusive, rollback-only native qualification acceptance; no GUI or commits.

Root invokes smoke once after migrating the qualification schema and default HR
source read/select permissions. All personnel and manufacturing sources below
are synthetic. No role, DocPerm, controller or account repair is performed.
"""

from copy import deepcopy
from uuid import uuid4

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _digest, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.piece_workers import SNAPSHOT_FIELDS, list_piece_workers, select_piece_worker
from jifeng_erp.wage_workbench import get_entry_source, save_drafts


def _native_contents():
    doctypes = {"Employee", "Jifeng Employee Supplement", "Jifeng Wage Entry", "Jifeng Wage Allocation",
                "Additional Salary", "Salary Component", "Item", "Item Price", "Version", "User", "User Permission", "Manufacturing Settings",
                "Operation", "Workstation", "BOM", "Work Order", "Job Card", "Jifeng Flow Order", "Series"}
    for doctype in tuple(doctypes):
        if frappe.db.exists("DocType", doctype):
            doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    contents = {}
    for doctype in sorted(doctypes):
        if frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).issingle:
            rows = frappe.db.sql("SELECT * FROM `tabSingles` WHERE doctype=%s ORDER BY field", (doctype,), as_dict=True)
        else:
            rows = frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``") + "` ORDER BY name", as_dict=True)
        contents[doctype] = _digest(rows)
    contents["__Auth"] = _digest(frappe.db.sql("SELECT * FROM `__Auth` ORDER BY doctype, name, fieldname", as_dict=True))
    return contents


def smoke(company, other_company, item_group, stock_uom):
    frappe.only_for("System Manager")
    assert company != other_company
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "验收引用必须为既有资料"
    for doctype in ("Jifeng Wage Entry", "Jifeng Wage Allocation Employee"):
        meta = frappe.get_meta(doctype)
        assert all(meta.has_field(field) for field in SNAPSHOT_FIELDS), "请先迁移计件资格快照"
        assert all(meta.get_field(field).permlevel == 1 for field in SNAPSHOT_FIELDS[1:3])
    assert frappe.db.exists("Page", "jifeng-piece-workers"), "请先迁移计件人员页面"
    tables, counts = _tab_counts()
    before, ledger_before = _native_contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    point, prefix = "jf_piece_workers_" + frappe.generate_hash(length=8), "JF-QA-PIECE-" + frappe.generate_hash(length=10)
    users, documents, checks = [], [], []
    current = "fixture_setup"
    _begin_rollback_only(point)

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def insert(doctype, **values):
        doc = frappe.get_doc({"doctype": doctype, **values})
        documents.append(doc)
        return doc.insert()

    def actor(label, role, employee=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix + "-" + label,
                      send_welcome_email=0, roles=[{"role": role}])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=company, apply_to_all_doctypes=1)
        if employee:
            insert("User Permission", user=user.name, allow="Employee", for_value=employee.name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def reject(action, error=frappe.ValidationError, message=None):
        checkpoint = "jf_piece_workers_reject"
        snapshot, local_callbacks, local_context = _ledger_snapshot(), _callbacks(), _local_context()
        frappe.db.savepoint(checkpoint)
        try:
            native(action)
        except error as exception:
            if message:
                assert message in str(exception), "拒绝没有命中预期资格/权限守门"
        else:
            raise AssertionError("无资格或无权限的工资请求被接受")
        finally:
            frappe.db.rollback(save_point=checkpoint)
            _restore_callbacks(local_callbacks)
            _restore_local_context(local_context)
        assert _ledger_snapshot() == snapshot

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before, "建档/资格/工资草稿验收发生库存或财务过账"

    try:
        frappe.set_user("Administrator")
        gender = frappe.db.get_value("Gender", {}, "name")
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
        assert gender and warehouse, "验收公司需启用仓库和性别字典"
        common = {"gender": gender, "date_of_birth": "1990-01-01", "date_of_joining": today(), "status": "Active"}
        piece, peer, timed, unclassified = [insert("Employee", first_name=prefix + "-" + label,
            employee_number=prefix + "-" + label, company=company, **common) for label in ("piece", "peer", "time", "unknown")]
        foreign = insert("Employee", first_name=prefix + "-foreign", company=other_company, **common)
        supplements = {employee.name: insert("Jifeng Employee Supplement", employee=employee.name,
            wage_method=method, notes=prefix + "-private-archive") for employee, method in
            ((piece, "计件"), (peer, "计件"), (timed, "计时"), (foreign, "计件"))}
        component = insert("Salary Component", salary_component=prefix + "-pay", salary_component_abbr="PW" + prefix[-10:],
                           type="Earning", depends_on_payment_days=0, is_tax_applicable=0)
        raw, finished = [insert("Item", item_code=prefix + "-" + label, item_group=item_group,
                               stock_uom=stock_uom, opening_stock=0, standard_rate=0) for label in ("raw", "fg")]
        station = insert("Workstation", workstation_name=prefix + "-ws", production_capacity=1)
        operation = insert("Operation", name=prefix + "-op", workstation=station.name)
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        settings.save()
        bom = insert("BOM", company=company, item=finished.name, quantity=1, with_operations=1,
                     transfer_material_against="Work Order", items=[{"item_code": raw.name, "qty": 1, "rate": 1}],
                     operations=[{"operation": operation.name, "workstation": station.name, "time_in_mins": 1}])
        bom.submit()

        def card(hour, people):
            flow = insert("Jifeng Flow Order", company=company, item_code=finished.name, planned_qty=5, bom=bom.name,
                          materials=[{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 1}])
            work = frappe.get_doc("Work Order", flow.make_work_order())
            documents.append(work)
            work.source_warehouse = work.fg_warehouse = warehouse
            work.skip_transfer = 1
            work.planned_start_date = today() + f" {hour:02d}:00:00"
            work.save()
            work.submit()
            source = frappe.get_doc("Job Card", frappe.get_all("Job Card", filters={"work_order": work.name}, pluck="name")[0])
            documents.append(source)
            source.start_timer(start_time=today() + f" {hour:02d}:00:00", employees=[{"employee": row.name} for row in people])
            source.reload()
            source.complete_job_card(qty=5, for_quantity=5, pending_qty=0, process_loss_qty=0,
                                     end_time=today() + f" {hour + 1:02d}:00:00")
            source.reload()
            source.submit()
            return source

        single, team = card(6, [piece]), card(8, [piece, peer])
        clerk, time_clerk = actor("clerk", "HR User", piece), actor("time", "HR User", timed)
        manager, accountant = actor("manager", "HR Manager"), actor("accounts", "Accounts User")

        def method(employee, value):
            frappe.set_user("Administrator")
            archive = frappe.get_doc("Jifeng Employee Supplement", supplements[employee.name].name)
            archive.wage_method = value
            native(archive.save)
            frappe.clear_document_cache(archive.doctype, archive.name)

        def request(source=single, quantity=1):
            identity = native(lambda: get_entry_source(source.name, company, piece.name))
            return {"request_id": str(uuid4()), "company": company, "employee": piece.name,
                "payroll_date": today(), "salary_component": component.name, "rows": [{
                    "work_date": today(), "operation": identity["operation"], "job_card": source.name,
                    "source_modified": identity["source_modified"], "work_order_modified": identity["work_order_modified"],
                    "quantity": quantity, "stock_uom": identity["stock_uom"], "piece_rate": 2,
                    "hours": 0, "hourly_rate": 0, "extra_amount": 0, "notes": prefix}]}

        current = "pure_hr_user_minimal_projection_and_real_piece_api"
        frappe.set_user(clerk)
        assert sorted(frappe.get_roles()) == sorted(frappe.get_roles(clerk))
        assert "HR User" in frappe.get_roles() and "HR Manager" not in frappe.get_roles() and "System Manager" not in frappe.get_roles()
        assert not frappe.has_permission("Jifeng Employee Supplement", "read")
        assert not frappe.has_permission("Jifeng Employee Supplement", "create")
        listed = native(lambda: list_piece_workers(company, prefix))
        assert [row["name"] for row in listed] == [piece.name]
        assert all(set(row).issubset({"name", "employee_number", "employee_name", "company", "department", "status"}) for row in listed)
        assert native(lambda: select_piece_worker(company, piece.name)) == {"company": company, "employee": piece.name, "eligible": True}
        assert not frappe.has_permission("Job Card", "write", doc=single)
        assert not frappe.has_permission("Work Order", "submit") and not frappe.has_permission("Operation", "write")
        payload = request()
        saved = native(lambda: save_drafts(payload))
        wage = frappe.get_doc("Jifeng Wage Entry", saved["wages"][0]["name"])
        documents.append(wage)
        assert wage.job_card == single.name and wage.docstatus == 0 and wage.piece_worker_qualified
        assert wage.piece_worker_qualification_status == "已核实"
        from frappe.client import get
        visible = native(lambda: get("Jifeng Wage Entry", wage.name))
        assert all(field not in visible for field in SNAPSHOT_FIELDS[1:3]), "hidden不能代替真正字段读取隔离"
        passed(current)

        current = "native_company_employee_user_permissions_and_guest"
        reject(lambda: select_piece_worker(company, peer.name), frappe.PermissionError)
        reject(lambda: select_piece_worker(other_company, foreign.name), frappe.PermissionError)
        reject(lambda: list_piece_workers(other_company), frappe.PermissionError)
        frappe.set_user(accountant)
        reject(lambda: select_piece_worker(company, piece.name), frappe.PermissionError)
        frappe.set_user("Guest")
        reject(lambda: list_piece_workers(company), frappe.PermissionError)
        reject(lambda: select_piece_worker(company, piece.name), frappe.PermissionError)
        passed(current)

        current = "time_pay_keeps_existing_native_and_workbench_contract"
        frappe.set_user(time_clerk)
        timed_payload = {"request_id": str(uuid4()), "company": company, "employee": timed.name,
            "payroll_date": today(), "salary_component": component.name, "rows": [{"work_date": today(),
                "operation": operation.name, "job_card": "", "source_modified": "", "work_order_modified": "",
                "quantity": 0, "stock_uom": "", "piece_rate": 0, "hours": 1, "hourly_rate": 3, "extra_amount": 0, "notes": prefix}]}
        result = native(lambda: save_drafts(timed_payload))
        time_wage = frappe.get_doc("Jifeng Wage Entry", result["wages"][0]["name"])
        documents.append(time_wage)
        assert not time_wage.piece_worker_qualified and time_wage.piece_worker_qualification_status == "不适用"
        passed(current)

        current = "new_edit_and_submit_current_qualification_with_readonly_recovery"
        method(piece, "计时")
        frappe.set_user(clerk)
        assert native(lambda: list_piece_workers(company, prefix)) == []
        assert native(lambda: save_drafts(deepcopy(payload)))["reused"], "重复请求只读复核既有结果"
        reject(lambda: save_drafts(request()), message="工资方式必须为计件")
        changed = frappe.get_doc("Jifeng Wage Entry", wage.name)
        changed.quantity = 2
        reject(changed.save, message="工资方式必须为计件")
        frappe.set_user(manager)
        reject(lambda: frappe.get_doc("Jifeng Wage Entry", wage.name).submit(), message="工资方式必须为计件")
        for employee in (timed, unclassified):
            def forged():
                return insert("Jifeng Wage Entry", company=company, employee=employee.name, operation=operation.name,
                    work_date=today(), payroll_date=today(), salary_component=component.name,
                    quantity=1, stock_uom=stock_uom, piece_rate=2,
                    piece_worker_qualified=1, piece_worker_qualification_status="已核实",
                    piece_worker_qualification_source="a" * 64, piece_worker_qualification_version="b" * 64)
            reject(forged, message="工资方式必须为计件")
        passed(current)

        current = "approved_wage_snapshot_and_legacy_cancel_preserve_history"
        method(piece, "计件")
        frappe.set_user(manager)
        approved = native(lambda: frappe.get_doc("Jifeng Wage Entry", wage.name).submit())
        audit = {field: approved.get(field) for field in SNAPSHOT_FIELDS}
        method(piece, "计时")
        frappe.set_user(manager)
        approved = frappe.get_doc("Jifeng Wage Entry", wage.name)
        assert {field: approved.get(field) for field in SNAPSHOT_FIELDS} == audit
        native(approved.cancel)
        assert approved.docstatus == 2 and {field: approved.get(field) for field in SNAPSHOT_FIELDS} == audit
        passed(current)

        current = "allocation_audit_is_not_authority_for_new_or_submitted_wage"
        method(piece, "计件")
        frappe.set_user(manager)
        allocation = native(lambda: insert("Jifeng Wage Allocation", job_card=team.name, payroll_date=today(), salary_component=component.name,
            employees=[{"employee": piece.name, "quantity": 2, "piece_rate": 2}, {"employee": peer.name, "quantity": 3, "piece_rate": 2}]))
        native(allocation.submit)
        generated = native(allocation.make_wages)
        peer_wage_name = next(name for name in generated if frappe.get_doc("Jifeng Wage Entry", name).employee == peer.name)
        allocation_audit = [{field: row.get(field) for field in SNAPSHOT_FIELDS} for row in frappe.get_doc(allocation.doctype, allocation.name).employees]
        method(peer, "计时")
        frappe.set_user(manager)
        reject(lambda: frappe.get_doc("Jifeng Wage Entry", peer_wage_name).submit(), message="工资方式必须为计件")
        native(lambda: frappe.delete_doc("Jifeng Wage Entry", peer_wage_name))
        reject(lambda: frappe.get_doc(allocation.doctype, allocation.name).make_wages(), message="工资方式必须为计件")
        assert [{field: row.get(field) for field in SNAPSHOT_FIELDS} for row in frappe.get_doc(allocation.doctype, allocation.name).employees] == allocation_audit
        passed(current)

        current = "legacy_submitted_wage_without_snapshot_can_cancel_after_reclassification"
        method(peer, "计件")
        frappe.set_user(manager)
        regenerated = native(lambda: frappe.get_doc(allocation.doctype, allocation.name).make_wages())
        legacy_name = next(name for name in regenerated if frappe.get_doc("Jifeng Wage Entry", name).employee == peer.name)
        legacy = native(lambda: frappe.get_doc("Jifeng Wage Entry", legacy_name).submit())
        frappe.set_user("Administrator")
        # Synthetic simulation of an already-submitted record predating this
        # schema; never certify the missing historical classification as known.
        frappe.db.set_value("Jifeng Wage Entry", legacy.name, dict.fromkeys(SNAPSHOT_FIELDS, None), update_modified=False)
        method(peer, "计时")
        frappe.set_user(manager)
        legacy = frappe.get_doc("Jifeng Wage Entry", legacy.name)
        assert not legacy.piece_worker_qualified
        native(legacy.cancel)
        assert legacy.docstatus == 2 and not legacy.piece_worker_qualified
        passed(current)
    except Exception as error:
        error.add_note("计件人员原生验收失败门槛: " + current)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            _end_rollback_only(point)
        finally:
            _restore_callbacks(callbacks)
            _restore_local_context(context)
            frappe.in_test = original_test
            frappe.set_user(original_user)
            for user in users:
                frappe.clear_cache(user=user)
            for doc in documents:
                if doc.name:
                    frappe.clear_document_cache(doc.doctype, doc.name)
            frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        assert _native_contents() == before, "资格验收改变了既有人员、私密档案、工资、Item原文/子表、版本或账号"
        assert _tab_counts(tables)[1] == counts, "原生验收全表计数未恢复"
        assert _ledger_snapshot() == ledger_before
    checks.append("all_native_contents_all_tab_counts_and_gl_sle_rolled_back")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "pure_hr_user_supplement_read": False, "gui_verified": False,
            "hashes_before": before, "hashes_after": _native_contents(), "whole_ledger_before": ledger_before,
            "whole_ledger_after": _ledger_snapshot(), "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1]}
