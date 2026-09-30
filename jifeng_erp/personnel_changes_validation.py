"""One native HR promotion/transfer experiment, followed by complete rollback.

After migration, run once in an exclusive business-write window. Do not use
``bench execute``: its exception fallback can invoke a failing helper twice::

    cd /home/jifengtest/jifeng-dev/bench/sites
    /home/jifengtest/jifeng-dev/bench/env/bin/python - <<'PY'
    import json
    import frappe
    from jifeng_erp.personnel_changes_validation import smoke
    frappe.init(site="jifeng.localhost", sites_path="/home/jifengtest/jifeng-dev/bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(json.dumps(smoke(company="Jifeng ERP UI Validation"),
                         ensure_ascii=False, default=str))
    finally:
        frappe.destroy()
    PY

Only synthetic native masters, HR documents, roles and User Permissions are
created. Business actions use the actual HR Manager role and native submit /
cancel, with in_test=False temporarily so both source and Employee Versions
are real. No permission bypass, DocPerm/schema change, logger patch, commit or
financial voucher is requested. All tab tables, Error Logs, Employee contents
and whole GL/SLE hashes are audited. No files or employee user IDs are made.

A submitted legacy source without a snapshot cannot be made through the new
supported submission API. The default legacy check is only an unpersisted
controller before_cancel probe; it is not a real legacy submission/cancellation
gate. No extension bypass, snapshot backfill or scan of private legacy data is
used. Exceptions propagate after the same cleanup as successful execution.
"""

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot


PROMOTION = "Employee Promotion"
TRANSFER = "Employee Transfer"
SNAPSHOT = "jf_history_snapshot"
CONFIG = {PROMOTION: ("promotion_date", "promotion_details", "EmployeePromotionMixin"),
          TRANSFER: ("transfer_date", "transfer_details", "EmployeeTransferMixin")}
WATCH = {"company", "department", "designation", "branch", "status", "date_of_joining",
         "relieving_date", "user_id"}
STATE_FIELDS = WATCH | {"ctc", "employment_type", "grade", "reports_to"}
HISTORY_FIELDS = ("name", "parent", "parenttype", "parentfield", "idx", "branch",
                  "department", "designation", "from_date", "to_date")
TAMPER = "人事追溯快照只能由服务器生成，不能修改"
CHANGED = "员工或精确工作历史已有后续变更，不能自动取消本单"
LEGACY = "旧人事单据缺少可靠追溯快照，请先人工核对迁移"


def _serialize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError("Unsupported native oracle value: " + type(value).__name__)


def _normal(value):
    return json.loads(json.dumps(value, default=_serialize, sort_keys=True, ensure_ascii=False))


def _digest(value):
    return hashlib.sha256(json.dumps(value, default=_serialize, sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _tab_counts(tables=None):
    """All tab tables, including nontransactional logs; no table is invented."""
    if tables is None:
        rows = frappe.db.sql("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                             "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE 'tab%' "
                             "ORDER BY TABLE_NAME", as_dict=True)
        tables = {row.TABLE_NAME: row.ENGINE for row in rows}
    counts = {}
    for table in tables:
        quoted = "`" + table.replace("`", "``") + "`"
        counts[table] = frappe.db.sql("SELECT COUNT(*) FROM " + quoted)[0][0]
    return tables, counts


def _employee_contents(name=None, exclude=None):
    """Full DB contents, including child metadata, for isolation/rollback audit."""
    where, values = "", ()
    if name:
        where, values = " WHERE name=%s", (name,)
    elif exclude:
        where, values = " WHERE name!=%s", (exclude,)
    result = {"Employee": frappe.db.sql("SELECT * FROM `tabEmployee`" + where + " ORDER BY name",
                                        values, as_dict=True)}
    for field in frappe.get_meta("Employee").get_table_fields():
        table = "`tab" + field.options.replace("`", "``") + "`"
        condition, params = " WHERE parenttype=%s", ("Employee",)
        if name:
            condition, params = condition + " AND parent=%s", (*params, name)
        elif exclude:
            condition, params = condition + " AND parent!=%s", (*params, exclude)
        result[field.fieldname] = frappe.db.sql("SELECT * FROM " + table + condition + " ORDER BY name",
                                               params, as_dict=True)
    return result


def _state(name):
    document = frappe.get_doc("Employee", name)
    fields = {field: document.get(field) for field in STATE_FIELDS
              if document.meta.get_field(field)}
    history = [{field: row.get(field) for field in HISTORY_FIELDS}
               for row in document.internal_work_history]
    assert len({row["name"] for row in history}) == len(history)
    assert all(row["parent"] == name and row["parenttype"] == "Employee"
               and row["parentfield"] == "internal_work_history" for row in history)
    return _normal({"fields": fields, "history": history})


def _native_history(before, after, changed, effective_date):
    """Independent oracle for HRMS append/update_to_date submission behavior."""
    old, new = before["history"], after["history"]
    assert len({row["name"] for row in new}) == len(new), "原生历史 ID 重复"
    if old:
        assert len(new) == len(old) + 1 and [row["name"] for row in new[:-1]] == [row["name"] for row in old]
    else:
        assert len(new) == 2, "首次办理必须同时产生原始任职历史和本次变更历史"
        baseline = new[0]
        assert baseline["idx"] == 1 and baseline["from_date"] == before["fields"]["date_of_joining"]
        assert baseline["to_date"] == add_days(effective_date, -1)
        assert all((baseline[field] or None) == (before["fields"][field] or None)
                   for field in ("branch", "department", "designation"))
    for index, row in enumerate(old):
        expected = dict(row)
        if not row["to_date"]:
            expected["to_date"] = add_days(new[index + 1]["from_date"], -1)
        assert new[index] == expected, "原生提交改变了旧历史 ID、非日期内容或已有结束日期"
    added = new[-1]
    assert added["idx"] == len(new) and added["from_date"] == str(effective_date)
    assert not added["to_date"]
    for field in ("branch", "department", "designation"):
        assert (added[field] or None) == changed.get(field), "新增历史不是实际所改岗位字段"
    assert added["name"] not in {row["name"] for row in old}
    return [row["name"] for row in new if row["name"] not in {previous["name"] for previous in old}]


def _snapshot(document, before, after, added, state="submitted"):
    payload = json.loads(document.get(SNAPSHOT))
    date_field, detail_field, _ = CONFIG[document.doctype]
    watched = WATCH | {row.fieldname for row in document.get(detail_field)}
    if document.doctype == PROMOTION:
        watched |= {"ctc"}
    assert payload["version"] == 1 and payload["state"] == state
    for section, expected in (("before", before), ("after", after)):
        assert set(payload[section]["fields"]) == watched
        assert payload[section]["fields"] == {field: expected["fields"][field] for field in watched}
        assert payload[section]["history"] == expected["history"]
    source = payload["source"]
    assert (source["doctype"], source["name"], source["employee"], source["company"], source["date"]) == (
        document.doctype, document.name, document.employee, before["fields"]["company"],
        str(document.get(date_field)))
    assert source["details"] == [{"name": row.name, "fieldname": row.fieldname,
                                  "current": "" if row.current is None else str(row.current),
                                  "new": "" if row.new is None else str(row.new)}
                                 for row in document.get(detail_field)]
    changed = sorted(field for field in watched if before["fields"][field] != after["fields"][field])
    assert payload["changed_fields"] == changed and payload["added_history_ids"] == added
    old = {row["name"]: row for row in before["history"]}
    dates = [{"name": row["name"], "before": old[row["name"]]["to_date"], "after": row["to_date"]}
             for row in after["history"] if row["name"] in old and
             old[row["name"]]["to_date"] != row["to_date"]]
    assert payload["to_date_changes"] == dates
    return payload


def _versions(doctype, name):
    return {row.name: json.loads(row.data) for row in frappe.get_all("Version",
            filters={"ref_doctype": doctype, "docname": name}, fields=["name", "data"])}


def _callbacks():
    return {field: list(getattr(frappe.db, field)._functions) for field in
            ("before_commit", "after_commit", "before_rollback", "after_rollback")}


def _restore_callbacks(previous):
    # Savepoint rollback does not discard transaction callbacks. Preserve the
    # caller's original queue and drop only callbacks from this experiment.
    for field, functions in previous.items():
        manager = getattr(frappe.db, field)
        manager.reset()
        for function in functions:
            manager.add(function)


def smoke(company, other_company=None):
    """Real roles/permissions/actions; runtime gate_count counts semantic gates."""
    from frappe.client import get as get_document_api
    from frappe.model import get_permitted_fields

    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb", "当前原生验收 helper 的全表审计使用 MariaDB"
    if not other_company:
        other_company = frappe.db.get_value("Company", {"name": ["!=", company]}, "name")
    for name in (company, other_company):
        if not name or not frappe.db.exists("Company", name):
            frappe.throw("人事验收需要两个已存在且不同的公司")
    assert company != other_company
    gender = frappe.db.get_value("Gender", {}, "name")
    currency = frappe.db.get_value("Company", company, "default_currency")
    assert gender and currency and frappe.db.exists("Role", "HR Manager")
    extensions = frappe.get_hooks("extend_doctype_class")
    for doctype, (_, _, mixin) in CONFIG.items():
        installed = extensions.get(doctype, [])
        installed = [installed] if isinstance(installed, str) else installed
        assert "jifeng_erp.personnel_changes." + mixin in installed, "人事控制器扩展尚未迁移"
        field = frappe.get_meta(doctype).get_field(SNAPSHOT)
        assert field and field.fieldtype == "Long Text" and all(field.get(flag) for flag in
                ("hidden", "read_only", "no_copy", "allow_on_submit")), "人事追溯快照字段尚未迁移"
        for hook, expected in (("has_permission", "personnel_change_has_permission"),
                               ("permission_query_conditions", "promotion_query_conditions" if doctype == PROMOTION
                                else "transfer_query_conditions")):
            installed = frappe.get_hooks(hook).get(doctype, [])
            installed = [installed] if isinstance(installed, str) else installed
            assert "jifeng_erp.personnel_changes." + expected in installed, "人事来源读取/列表权限守卫尚未安装"
    employee_rpc = frappe.override_whitelisted_method("hrms.hr.utils.get_employee_field_property")
    assert employee_rpc == "jifeng_erp.personnel_ui.get_employee_field_property", "原生员工属性 RPC 权限覆盖尚未安装"
    assert all(hasattr(getattr(frappe.db, field), "_functions") for field in
               ("before_commit", "after_commit", "before_rollback", "after_rollback"))
    ledger_before, employee_before = _ledger_snapshot(), _digest(_employee_contents())
    tables, counts_before = _tab_counts()
    for doctype in ("Employee", "Employee Internal Work History", "Department", "Designation", "User",
                    "Has Role", "User Permission", PROMOTION, TRANSFER, "Employee Property History",
                    "Version", "Series"):
        assert tables["tab" + doctype] == "InnoDB", "人事验收写入对象必须支持事务回滚"
    logs_before = set(frappe.get_all("Error Log", pluck="name"))
    original_user, original_test = frappe.session.user, frappe.in_test
    callbacks_before = _callbacks()
    suffix = frappe.generate_hash(length=10)
    prefix, point = "JF-QA-HRCHANGE-" + suffix, "jf_hrchange_" + suffix
    objects, records, users, checks, evidence = [], set(), [], [], {
        "roles": {"local": ["HR Manager"], "cross_company": ["HR Manager"]},
        "legacy_limit": "only unpersisted controller probe; no real submitted legacy source created or cancelled",
        "unsupported": "create_new_employee_id is explicitly rejected; no new-ID reversal claimed",
        "version_mode": "actual submit/cancel temporarily use in_test=False; fixture creation uses True",
    }
    current_gate, other_hash, primary = "begin", None, None
    _begin_rollback_only(point)

    def gate(label):
        nonlocal current_gate
        current_gate = label

    def passed():
        checks.append(current_gate)
        assert _ledger_snapshot() == ledger_before, "人事主档操作改变了 whole GL/SLE 内容"
        if primary:
            assert _digest(_employee_contents(exclude=primary.name)) == other_hash, "其他员工或其子表内容发生变化"
        assert set(frappe.get_all("Error Log", pluck="name")) == logs_before, "直接 Doc 操作产生了 Error Log"

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **values):
        document = remember(frappe.get_doc({"doctype": doctype, **values}))
        document.insert()
        records.add((doctype, document.name))
        return document

    def permit(user, doctype, names):
        frappe.set_user("Administrator")
        for name in names:
            insert("User Permission", user=user, allow=doctype, for_value=name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user)

    def change(doctype, employee=None, when=None, revised_ctc=None, **values):
        date_field, detail_field, _ = CONFIG[doctype]
        selected = values.pop("details", {"designation": designations[1].name})
        fields = {"employee": employee or primary.name, "company": company,
                  date_field: when or promotion_date, detail_field: [
                      {"property": field.title(), "fieldname": field, "current": prefix + "-forged-current",
                       "new": value} for field, value in selected.items()], **values}
        if doctype == PROMOTION:
            fields.update(current_ctc=777, revised_ctc=15000 if revised_ctc is None else revised_ctc)
        return insert(doctype, **fields)

    def native(document, action):
        source_versions = set(_versions(document.doctype, document.name))
        employee_versions = set(_versions("Employee", document.employee))
        old_status = int(document.docstatus)
        previous_test = frappe.in_test
        try:
            frappe.in_test = False
            getattr(document, action)()
        finally:
            frappe.in_test = previous_test
        document.reload()
        source_new = {name: data for name, data in _versions(document.doctype, document.name).items()
                      if name not in source_versions}
        employee_new = {name: data for name, data in _versions("Employee", document.employee).items()
                        if name not in employee_versions}
        assert source_new and employee_new, "真实原生操作未产生来源和员工 Version"
        assert any(["docstatus", old_status, int(document.docstatus)] in data.get("changed", [])
                   for data in source_new.values())
        assert any(SNAPSHOT in {row[0] for row in data.get("changed", [])} for data in source_new.values())
        changed_fields = {row[0] for data in employee_new.values() for row in data.get("changed", [])}
        assert set(json.loads(document.get(SNAPSHOT))["changed_fields"]) <= changed_fields, "员工 Version 未记录实际字段变更"
        records.update(("Version", name) for name in (*source_new, *employee_new))
        evidence.setdefault("native_versions", []).append({"source_type": document.doctype,
            "source": document.name, "action": action, "source_versions": sorted(source_new),
            "employee_versions": sorted(employee_new), "employee_changed_fields": sorted({
                row[0] for data in employee_new.values() for row in data.get("changed", [])}),
            "added_history_ids": sorted({row[1]["name"] for data in employee_new.values()
                for row in data.get("added", []) if row[0] == "internal_work_history"}),
            "removed_history_ids": sorted({row[1]["name"] for data in employee_new.values()
                for row in data.get("removed", []) if row[0] == "internal_work_history"})})
        return document

    def reject(operation, error, message=None):
        nested = "jf_hrchange_reject_" + frappe.generate_hash(length=8)
        ledger, staff = _ledger_snapshot(), _digest(_employee_contents())
        logs, callbacks = set(frappe.get_all("Error Log", pluck="name")), _callbacks()
        frappe.db.savepoint(nested)
        try:
            try:
                operation()
            except error as exception:
                if message:
                    assert message in str(exception), "错误并非预期的人事来源守卫"
                actual = type(exception).__name__
            else:
                raise AssertionError("验收预期拒绝，但操作成功")
        finally:
            try:
                frappe.db.rollback(save_point=nested)
            finally:
                _restore_callbacks(callbacks)
        assert _ledger_snapshot() == ledger and _digest(_employee_contents()) == staff
        assert set(frappe.get_all("Error Log", pluck="name")) == logs, "拒绝产生了不可回滚 Error Log"
        evidence.setdefault("rejections", []).append({"gate": current_gate, "exception_type": actual})
        return actual

    def safe_cancel(document):
        source = frappe.get_doc(document.doctype, document.name)
        staff = _digest(_employee_contents())
        try:
            source.cancel()
        finally:
            assert _digest(_employee_contents()) == staff, "被拒绝的反审已修改员工或历史"

    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        gate("native_hr_manager_fixture_permissions_and_independent_employee_history_hashes")
        departments = [insert("Department", department_name=prefix + "-D" + str(index), company=owner)
                       for index, owner in enumerate((company, company, other_company))]
        designations = [insert("Designation", designation_name=prefix + "-JOB" + str(index)) for index in range(3)]
        joined, old_start = add_days(today(), -90), add_days(today(), -45)
        promotion_date, transfer_date = add_days(today(), -10), add_days(today(), -5)
        base_history = [{"department": departments[0].name, "designation": designations[0].name,
                         "from_date": joined, "to_date": add_days(old_start, -1)},
                        {"department": departments[0].name, "designation": designations[0].name,
                         "from_date": old_start}]
        common = {"company": company, "gender": gender, "date_of_birth": "1990-01-01",
                  "date_of_joining": joined, "status": "Active", "department": departments[0].name,
                  "designation": designations[0].name, "ctc": 0, "salary_currency": currency,
                  "create_user_automatically": 0, "create_user_permission": 0}
        primary = insert("Employee", first_name=prefix + "-primary", employee_number=prefix + "-A",
                         internal_work_history=base_history, **common)
        peer_history = [dict(row) for row in base_history]
        peer_history[-1]["to_date"] = add_days(promotion_date, -1)
        peer_history.extend([{"designation": designations[1].name, "from_date": promotion_date,
                              "to_date": add_days(transfer_date, -1)},
                             {"department": departments[1].name, "from_date": transfer_date}])
        peer = insert("Employee", first_name=prefix + "-peer", employee_number=prefix + "-B",
                      internal_work_history=peer_history, **common)
        empty_employee = insert("Employee", first_name=prefix + "-first-change", employee_number=prefix + "-C",
                                **common)
        assert not empty_employee.internal_work_history
        actors = {}
        for kind, companies in (("local", [company]), ("cross_company", [company, other_company])):
            user = insert("User", email=f"{prefix.lower()}-{kind}@example.invalid", first_name="人事原生验收",
                          send_welcome_email=0, roles=[{"role": "HR Manager"}])
            users.append(user.name)
            actors[kind] = user.name
            permit(user.name, "Company", companies)
            permit(user.name, "Employee", [primary.name, empty_employee.name])
        initial = _state(primary.name)
        assert initial["fields"]["ctc"] == 0 and len(initial["history"]) == 2
        other_hash = _digest(_employee_contents(exclude=primary.name))
        peer_hash = _digest(_employee_contents(name=peer.name))
        evidence.update(other_employee_contents_hash=other_hash, colliding_peer_contents_hash=peer_hash,
                        collision={"designation": designations[1].name, "promotion_date": promotion_date,
                                   "department": departments[1].name, "transfer_date": transfer_date})
        frappe.set_user(actors["local"])
        assert frappe.get_doc("Employee", primary.name).has_permission("read")
        assert frappe.get_doc("Employee", primary.name).has_permission("write")
        assert not frappe.get_doc("Employee", peer.name).has_permission("read")
        assert not frappe.get_doc("Company", other_company).has_permission("select")
        for doctype in CONFIG:
            assert all(frappe.has_permission(doctype, permission) for permission in ("create", "submit", "cancel"))
        field_evidence = {}
        employee_meta = frappe.get_meta("Employee")
        history_table = employee_meta.get_field("internal_work_history")
        assert history_table and history_table.fieldtype == "Table" and history_table.options == "Employee Internal Work History"
        for permission in ("read", "write"):
            employee_fields = set(get_permitted_fields("Employee", permission_type=permission, ignore_virtual=True))
            history_fields = set(get_permitted_fields("Employee Internal Work History", parenttype="Employee",
                                                      permission_type=permission, ignore_virtual=True))
            assert (WATCH | {"ctc"}) <= employee_fields
            assert "internal_work_history" not in employee_fields, "原生物理列 API 不应返回 Table 字段"
            table_levels = set(employee_meta.get_permlevel_access(permission_type=permission))
            table_level = history_table.permlevel or 0
            assert table_level in table_levels, "实际 HR Manager 无原生父表字段权限"
            assert set(HISTORY_FIELDS[5:]) <= history_fields
            field_evidence[permission] = {"employee": sorted((WATCH | {"ctc"}) & employee_fields),
                "history_table": {"fieldname": history_table.fieldname, "fieldtype": history_table.fieldtype,
                                  "options": history_table.options, "permlevel": table_level,
                                  "native_permlevels": sorted(table_levels), "permitted": True,
                                  "column_api_contains_table": "internal_work_history" in employee_fields},
                "history_child": sorted(set(HISTORY_FIELDS[5:]) & history_fields)}
        evidence["native_hr_manager_field_permissions"] = field_evidence
        evidence["field_denial_limit"] = "native positive read/write observed; field-level denial is static-model coverage; no permlevels/DocPerm changed"
        passed()

        gate("promotion_corrects_forged_current_and_zero_ctc_then_records_exact_native_delta_versions")
        promotion = change(PROMOTION)
        assert promotion.current_ctc == 777 and promotion.promotion_details[0].current == prefix + "-forged-current"
        native(promotion, "submit")
        promoted = _state(primary.name)
        assert (promoted["fields"]["designation"], flt(promoted["fields"]["ctc"])) == (designations[1].name, 15000)
        assert promotion.current_ctc == 0 and promotion.promotion_details[0].current == designations[0].name
        added_promotion = _native_history(initial, promoted, {"designation": designations[1].name}, promotion_date)
        records.update(("Employee Internal Work History", name) for name in added_promotion)
        _snapshot(promotion, initial, promoted, added_promotion)
        assert any(row["designation"] == designations[1].name and row["from_date"] == promotion_date
                   for row in _state(peer.name)["history"])
        passed()

        gate("native_source_reads_snapshot_lists_and_employee_rpc_respect_real_employee_permissions")
        promotion.check_permission("read")
        rows = frappe.get_list(PROMOTION, filters={"name": promotion.name}, fields=["name", SNAPSHOT])
        assert len(rows) == 1 and rows[0].name == promotion.name and rows[0].get(SNAPSHOT) == promotion.get(SNAPSHOT)
        assert get_document_api(PROMOTION, promotion.name)[SNAPSHOT] == promotion.get(SNAPSHOT)
        for field, expected in (("designation", designations[1].name), ("ctc", 15000)):
            result = frappe.call(employee_rpc, employee=primary.name, fieldname=field)
            definition = frappe.get_meta("Employee").get_field(field)
            assert result == {"value": expected, "datatype": definition.fieldtype,
                              "label": definition.label, "options": definition.options}
        reject(lambda: frappe.call(employee_rpc, employee=peer.name, fieldname="designation"), frappe.PermissionError)
        evidence["native_employee_property_rpc"] = employee_rpc
        passed()

        gate("new_client_snapshot_and_submitted_snapshot_or_intent_tampering_are_rejected")
        for doctype in CONFIG:
            reject(lambda doctype=doctype: change(doctype, **{SNAPSHOT: '{"version":1}'}), frappe.ValidationError, TAMPER)
        def tamper_snapshot():
            source = frappe.get_doc(PROMOTION, promotion.name)
            source.set(SNAPSHOT, source.get(SNAPSHOT) + " ")
            source.save(ignore_version=False)
        reject(tamper_snapshot, frappe.ValidationError, TAMPER)
        def tamper_intent():
            source = frappe.get_doc(PROMOTION, promotion.name)
            source.revised_ctc = 99999
            source.save(ignore_version=False)
        reject(tamper_intent, frappe.ValidationError, "人事单据来源或已审核变更内容")
        assert frappe.db.get_value(PROMOTION, promotion.name, "docstatus") == 1 and _state(primary.name) == promoted
        passed()

        gate("transfer_after_promotion_preserves_native_current_fields_precise_history_and_versions")
        transfer = change(TRANSFER, when=transfer_date, details={"department": departments[1].name})
        native(transfer, "submit")
        transferred = _state(primary.name)
        assert transferred["fields"]["department"] == departments[1].name
        assert transferred["fields"]["designation"] == designations[1].name and transferred["fields"]["ctc"] == 15000
        assert transfer.transfer_details[0].current == departments[0].name
        added_transfer = _native_history(promoted, transferred, {"department": departments[1].name}, transfer_date)
        records.update(("Employee Internal Work History", name) for name in added_transfer)
        _snapshot(transfer, promoted, transferred, added_transfer)
        passed()

        gate("out_of_order_promotion_cancellation_rejects_without_touching_employee_or_peer_history")
        reject(lambda: safe_cancel(promotion), frappe.ValidationError, CHANGED)
        assert _state(primary.name) == transferred and frappe.db.get_value(PROMOTION, promotion.name, "docstatus") == 1
        passed()
        gate("reverse_transfer_cancellation_removes_only_its_added_id_and_restores_old_to_date")
        native(transfer, "cancel")
        assert _state(primary.name) == promoted and not frappe.db.exists("Employee Internal Work History", added_transfer[0])
        _snapshot(transfer, promoted, transferred, added_transfer, "cancelled")
        passed()
        gate("reverse_promotion_cancellation_restores_zero_ctc_exact_original_history_and_ends")
        promotion = frappe.get_doc(PROMOTION, promotion.name)
        native(promotion, "cancel")
        assert _state(primary.name) == initial and not frappe.db.exists("Employee Internal Work History", added_promotion[0])
        _snapshot(promotion, initial, promoted, added_promotion, "cancelled")
        passed()

        gate("later_employee_property_or_precise_history_changes_block_automatic_cancellation")
        later = change(PROMOTION)
        native(later, "submit")
        for mode in ("property", "history"):
            def subsequent_change(mode=mode):
                employee = frappe.get_doc("Employee", primary.name)
                if mode == "property":
                    employee.designation = designations[2].name
                else:
                    employee.internal_work_history[-1].to_date = add_days(today(), -1)
                employee.save(ignore_version=False)
                safe_cancel(later)
            reject(subsequent_change, frappe.ValidationError, CHANGED)
        native(frappe.get_doc(PROMOTION, later.name), "cancel")
        assert _state(primary.name) == initial
        passed()

        gate("native_future_promotion_and_transfer_dates_reject_without_employee_changes")
        for doctype in CONFIG:
            source = change(doctype, when=add_days(today(), 1))
            reject(lambda source=source: source.submit(), frappe.DocstatusTransitionError)
            assert frappe.db.get_value(doctype, source.name, "docstatus") == 0
        assert _state(primary.name) == initial
        passed()
        gate("create_new_employee_id_is_explicitly_rejected_without_new_employee_or_relief")
        source = change(TRANSFER, create_new_employee_id=1)
        reject(lambda: source.submit(), frappe.ValidationError, "暂不支持创建新员工编号")
        assert not frappe.db.get_value(TRANSFER, source.name, "new_employee_id") and _state(primary.name) == initial
        passed()

        gate("native_employee_user_permission_hides_peer_from_promotion_and_transfer_actions")
        for doctype in CONFIG:
            reject(lambda doctype=doctype: change(doctype, employee=peer.name).submit(), frappe.PermissionError)
        assert _digest(_employee_contents(name=peer.name)) == peer_hash
        passed()
        gate("native_company_permission_rejects_transfer_to_unpermitted_target_company")
        reject(lambda: change(TRANSFER, when=transfer_date, new_company=other_company,
                              details={"department": departments[2].name}).submit(), frappe.PermissionError)
        assert _state(primary.name) == initial
        passed()

        gate("authorized_same_id_cross_company_transfer_records_join_date_and_precise_history")
        frappe.set_user(actors["cross_company"])
        assert all(frappe.get_doc("Company", name).has_permission("select") for name in (company, other_company))
        cross = change(TRANSFER, when=transfer_date, new_company=other_company,
                       details={"department": departments[2].name})
        native(cross, "submit")
        relocated = _state(primary.name)
        assert (relocated["fields"]["company"], relocated["fields"]["department"],
                relocated["fields"]["date_of_joining"]) == (other_company, departments[2].name, transfer_date)
        assert not cross.new_employee_id and cross.employee == primary.name
        added_cross = _native_history(initial, relocated, {"department": departments[2].name}, transfer_date)
        records.update(("Employee Internal Work History", name) for name in added_cross)
        _snapshot(cross, initial, relocated, added_cross)
        cross.check_permission("read")
        rows = frappe.get_list(TRANSFER, filters={"name": cross.name}, fields=["name", SNAPSHOT])
        assert len(rows) == 1 and rows[0].get(SNAPSHOT) == cross.get(SNAPSHOT)
        assert get_document_api(TRANSFER, cross.name)[SNAPSHOT] == cross.get(SNAPSHOT)
        assert frappe.call(employee_rpc, employee=primary.name, fieldname="department")["value"] == departments[2].name
        passed()
        gate("cross_company_employee_becomes_hidden_to_local_manager_and_cannot_be_cancelled")
        frappe.set_user(actors["local"])
        assert not frappe.get_doc("Employee", primary.name).has_permission("read")
        for source in (promotion, cross):
            assert not frappe.get_doc(source.doctype, source.name).has_permission("read"), "失去员工读取权限后来源快照仍可读"
            assert not frappe.get_list(source.doctype, filters={"name": source.name}, pluck="name")
            reject(lambda source=source: get_document_api(source.doctype, source.name), frappe.PermissionError)
        reject(lambda: frappe.call(employee_rpc, employee=primary.name, fieldname="department"), frappe.PermissionError)
        reject(lambda: safe_cancel(cross), frappe.PermissionError)
        assert _state(primary.name) == relocated and frappe.db.get_value(TRANSFER, cross.name, "docstatus") == 1
        passed()
        gate("authorized_cross_company_cancel_restores_original_company_join_date_and_history")
        frappe.set_user(actors["cross_company"])
        native(frappe.get_doc(TRANSFER, cross.name), "cancel")
        assert _state(primary.name) == initial
        _snapshot(frappe.get_doc(TRANSFER, cross.name), initial, relocated, added_cross, "cancelled")
        frappe.set_user(actors["local"])
        assert frappe.get_doc(PROMOTION, promotion.name).has_permission("read")
        assert frappe.get_list(PROMOTION, filters={"name": promotion.name}, pluck="name") == [promotion.name]
        # The cancelled Transfer retains its genuine new_company=B link. Native
        # Company permission still hides this source from the A-only manager,
        # even after Employee A becomes readable again; the custom hook grants
        # nothing beyond the native Company/Employee permissions.
        assert not frappe.get_doc(TRANSFER, cross.name).has_permission("read")
        assert not frappe.get_list(TRANSFER, filters={"name": cross.name}, pluck="name")
        assert frappe.call(employee_rpc, employee=primary.name, fieldname="department")["value"] == departments[0].name
        frappe.set_user(actors["cross_company"])
        assert frappe.get_doc(TRANSFER, cross.name).has_permission("read")
        assert frappe.get_list(TRANSFER, filters={"name": cross.name}, pluck="name") == [cross.name]
        passed()

        # Deliberately unpersisted; no fake submitted docstatus or old native
        # controller is used. This checks the installed missing-snapshot guard
        # and is evidence only, not a persisted legacy business gate.
        gate("unpersisted_missing_snapshot_controller_probe_not_a_real_legacy_submission")
        for doctype in CONFIG:
            source = frappe.new_doc(doctype)
            source.employee, source.company = primary.name, company
            assert source.is_new() and not source.get(SNAPSHOT) and int(source.docstatus) == 0
            actual = reject(lambda source=source: source.run_method("before_cancel"), frappe.ValidationError, LEGACY)
            evidence.setdefault("unpersisted_legacy_controller_probe", {})[doctype] = actual
        gate("native_versions_trace_sources_employee_and_exact_added_removed_history_ids")
        for source, added in ((promotion, added_promotion), (transfer, added_transfer), (cross, added_cross)):
            entries = [row for row in evidence["native_versions"] if row["source"] == source.name]
            assert {row["action"] for row in entries} == {"submit", "cancel"}
            assert next(row for row in entries if row["action"] == "submit")["added_history_ids"] == sorted(added)
            assert next(row for row in entries if row["action"] == "cancel")["removed_history_ids"] == sorted(added)
        assert not _versions("Employee", peer.name), "其他员工新增了版本或被修改"
        assert _digest(_employee_contents(exclude=primary.name)) == other_hash
        assert _state(primary.name) == initial
        passed()

        gate("first_personnel_change_removes_both_precise_generated_history_ids_with_native_versions")
        # The first employee's completed action phase protected C as an untouched
        # peer. Now freeze a separate isolation boundary around C; this includes
        # A's full completed contents and the colliding B history, rather than
        # excluding both action employees from one broad hash.
        completed_primary = primary.name
        primary = empty_employee
        other_hash = _digest(_employee_contents(exclude=primary.name))
        evidence["first_change_isolation"] = {"active_employee": primary.name,
            "completed_employee_protected": completed_primary, "other_employee_contents_hash": other_hash}
        empty_initial = _state(primary.name)
        assert not empty_initial["history"]
        first_change = change(PROMOTION)
        native(first_change, "submit")
        first_changed = _state(primary.name)
        first_added = _native_history(empty_initial, first_changed, {"designation": designations[1].name}, promotion_date)
        assert len(first_added) == 2
        records.update(("Employee Internal Work History", name) for name in first_added)
        _snapshot(first_change, empty_initial, first_changed, first_added)
        assert _digest(_employee_contents(exclude=primary.name)) == other_hash
        native(first_change, "cancel")
        assert _state(primary.name) == empty_initial
        assert all(not frappe.db.exists("Employee Internal Work History", name) for name in first_added)
        _snapshot(first_change, empty_initial, first_changed, first_added, "cancelled")
        entries = [row for row in evidence["native_versions"] if row["source"] == first_change.name]
        assert next(row for row in entries if row["action"] == "submit")["added_history_ids"] == sorted(first_added)
        assert next(row for row in entries if row["action"] == "cancel")["removed_history_ids"] == sorted(first_added)
        assert _digest(_employee_contents(name=peer.name)) == peer_hash
        passed()
        evidence["native_counts_before_rollback"] = {
            doctype: {"before": counts_before["tab" + doctype], "during": frappe.db.count(doctype)}
            for doctype in (PROMOTION, TRANSFER, "Employee Property History", "Employee",
                            "Employee Internal Work History", "Version", "Email Queue", "Notification Log", "Error Log")}
    except Exception as exception:
        exception.add_note("native personnel change smoke failed at gate: " + current_gate)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            employees = [name for doctype, name in records if doctype == "Employee"]
            if employees:
                for field in frappe.get_meta("Employee").get_table_fields():
                    records.update((field.options, name) for name in frappe.get_all(field.options,
                        filters={"parenttype": "Employee", "parent": ["in", employees]}, pluck="name"))
            names = list({name for _, name in records})
            if names:
                records.update(("Version", name) for name in frappe.get_all("Version",
                               filters={"docname": ["in", names]}, pluck="name"))
        finally:
            try:
                _end_rollback_only(point)
            finally:
                _restore_callbacks(callbacks_before)
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records), "回滚后残留合成员工/来源/权限/Version"
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before, "直接 Doc 验收新增了 Error Log"
            assert _tab_counts(tables)[1] == counts_before, "回滚后全 tab 表精确计数不同"
            assert _digest(_employee_contents()) == employee_before, "回滚后 Employee 和全部子表内容不同"
            assert _ledger_snapshot() == ledger_before, "回滚后 whole GL/SLE 内容不同"
    checks.extend(("rollback_all_synthetic_sources_employees_permissions_versions_and_all_tab_counts",
                   "rollback_whole_employee_child_contents_gl_sle_hashes_and_zero_error_logs"))
    return {"passed": True, "checks": checks, "gate_count": len(checks), "business_data": "rolled_back",
            "record_counts": dict(Counter(doctype for doctype, _ in records)), "evidence": evidence,
            "whole_ledger_hashes_before": ledger_before, "whole_ledger_hashes_after": _ledger_snapshot(),
            "employee_contents_hash_before": employee_before, "employee_contents_hash_after": _digest(_employee_contents()),
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
            "table_engines": tables, "error_logs_created": 0,
            "legacy_persisted_cancellation_verified": False,
            "execution": "direct Python, one invocation, serial rollback experiment"}
