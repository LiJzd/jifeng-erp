"""Native Product Analysis + Personnel Roster, in one rollback-only experiment.

After migration, run once in an exclusive business-write window. Do not use
``bench execute`` (its exception fallback can invoke a failing function twice)::

    cd /home/jifengtest/jifeng-dev/bench
    ./env/bin/python - <<'PY'
    import json
    import frappe
    from jifeng_erp.catalog_analysis_validation import smoke
    frappe.init(site="jifeng.localhost", sites_path="sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(json.dumps(smoke(company="Jifeng ERP UI Validation",
                               item_group="Products", stock_uom="Nos"),
                         ensure_ascii=False, default=str))
    finally:
        frappe.destroy()
    PY

Only synthetic master/catalog records and remote URL File metadata are made.
No bytes, native financial vouchers, schema/DocPerm changes, force flags or
commits are requested. Report exceptions and assertions propagate after audit.
"""

from collections import Counter
import json
from unittest.mock import patch

import frappe
from frappe.utils import add_days, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot


PRODUCT_REPORT = "Jifeng Product Analysis"
PERSONNEL_REPORT = "Jifeng Personnel Roster"
PROFILE = "Jifeng Product Profile"
SUPPLEMENT = "Jifeng Employee Supplement"
DIMENSIONS = {
    "品牌": ("brand",), "类别": ("major_category", "minor_category"), "材质": ("material",),
    "来源": ("source",), "设计": ("designer",), "打版": ("pattern_maker",),
    "建模": ("modeler",), "风格": ("style",),
}
EMPLOYEE_FIELDS = ("name", "employee_number", "employee_name", "company", "department", "designation",
                   "status", "date_of_joining", "relieving_date")
SUPPLEMENT_FIELDS = ("name", "employee", "company", "term_type", "wage_method")
ROLES = {"stock": ("Stock Manager",), "production": ("Manufacturing Manager", "Manufacturing User"),
         "hr_user": ("HR User",), "hr_manager": ("HR Manager",), "selector": ("Desk User",)}
AUDIT_TYPES = ("Item", "Item Default", "UOM Conversion Detail", PROFILE, "Jifeng Material Line",
               "Jifeng Operation Line", "Employee", "Employee Education", "Employee External Work History",
               "Employee Internal Work History", SUPPLEMENT, "Department", "Designation", "User", "Has Role",
               "User Permission", "Version", "File", "Communication", "Notification Log", "Email Queue",
               "DocShare", "Error Log", "Activity Log", "Access Log", "GL Entry", "Stock Ledger Entry")


def _counts():
    return {doctype: frappe.db.count(doctype) for doctype in AUDIT_TYPES if frappe.db.table_exists(doctype)}


def _field_levels(doctype, fields):
    meta = frappe.get_meta(doctype)
    levels = {}
    for field in fields:
        if field == "name":
            levels[field] = 0  # Native default identity field.
            continue
        definition = meta.get_field(field)
        if not definition:
            frappe.throw("报表所需原生字段不存在，请核对已安装版本")
        levels[field] = int(definition.permlevel or 0)
    return levels


def _tree(result, documents, dimension):
    """Independent exact source/count oracle, without counting expanded rows twice."""
    fields = DIMENSIONS[dimension]
    expected = {}
    for document in documents:
        key = tuple(None if document.get(field) is None or not str(document.get(field)).strip()
                    else str(document.get(field)) for field in fields)
        expected.setdefault(key, []).append(document)
    headers, leaves, current = {}, {}, None
    for row in result["result"]:
        assert isinstance(row, dict)
        key = tuple(row.get(field) for field in fields)
        if row.get("is_group"):
            assert row.get("indent") == 0 and key not in headers and key in expected
            sources = expected[key]
            inactive = sum(bool(doc.inactive) for doc in sources)
            assert (row["record_count"], row["active_count"], row["inactive_count"]) == (
                len(sources), len(sources) - inactive, inactive)
            headers[key], current = row, key
        else:
            assert row.get("indent") == 1 and key == current and row["document"] not in leaves
            source = next(doc for doc in expected[key] if doc.name == row["document"])
            assert (row["company"], row["item_code"], row["status"]) == (
                source.company, source.item_code, "停用" if source.inactive else "在用")
            assert not row.get("record_count") and not row.get("active_count") and not row.get("inactive_count")
            leaves[row["document"]] = row
    assert set(headers) == set(expected) and set(leaves) == {doc.name for doc in documents}
    assert sum(row["record_count"] for row in headers.values()) == len(documents)
    assert all(sum(tuple(row.get(field) for field in fields) == key for row in leaves.values()) ==
               header["record_count"] for key, header in headers.items())
    forbidden = {"image", "image_url", "image_status", "weight", "silver_weight", "stone_weight",
                 "list_price", "rate", "blank_price", "material_unit_cost", "processing_unit_cost"}
    assert all(column["fieldtype"] not in ("Currency", "Float") and column["fieldname"] not in forbidden
               for column in result["columns"])
    assert all(not forbidden.intersection(row) for row in result["result"])
    return headers, leaves


def _roster(result, employees, employee_fields, supplement_fields=()):
    allowed = {"employee" if field == "name" else field for field in employee_fields}
    allowed.update(set(supplement_fields).intersection(("term_type", "wage_method")))
    whitelist = {"employee", *EMPLOYEE_FIELDS[1:], "term_type", "wage_method"}
    columns = {column["fieldname"] for column in result["columns"]}
    assert columns <= whitelist, "人员报告列超出隐私白名单"
    rows = {row["employee"]: row for row in result["result"]}
    assert set(rows) == {document.name for document in employees} and len(rows) == len(result["result"])
    for document in employees:
        row = rows[document.name]
        assert set(row) <= whitelist, "人员报告值超出隐私白名单"
        # A standard report may keep a blank column for a hidden field. The
        # actual field value must remain absent/blank after native filtering.
        assert all(row.get(field) in (None, "") for field in set(row) - allowed), "人员报告回填了原生隐藏字段"
        for field in set(employee_fields) - {"name"}:
            actual, expected = row.get(field), document.get(field)
            if field in ("date_of_joining", "relieving_date"):
                actual, expected = str(actual or ""), str(expected or "")
            assert actual == expected
    assert not {"identity_number", "identity_image", "cell_number", "bank_ac_no", "bank_name",
                "bank_account_name", "personal_email", "company_email", "date_of_birth"}.intersection(columns)
    return rows


def smoke(company, item_group, stock_uom):
    """One experiment; semantic gates aggregate repeated dimensions/role loops."""
    from frappe.desk.query_report import run
    from frappe.model import get_permitted_fields

    frappe.only_for("System Manager")
    for doctype, name in (("Company", company), ("Item Group", item_group), ("UOM", stock_uom)):
        if not frappe.db.exists(doctype, name):
            frappe.throw("请提供已存在的验收公司、明细商品分组及单位")
    other_company = frappe.db.get_value("Company", {"name": ["!=", company]}, "name")
    gender = frappe.db.get_value("Gender", {}, "name")
    if not other_company or not gender or frappe.db.get_value("Item Group", item_group, "is_group"):
        frappe.throw("验收需要另一既有公司、原生性别及明细商品分组")
    for report in (PRODUCT_REPORT, PERSONNEL_REPORT):
        if not frappe.db.exists("Report", report):
            frappe.throw("请先迁移产品分析和人员花名册报告")
        definition = frappe.get_doc("Report", report)
        assert not definition.disabled and not definition.prepared_report
    meta = frappe.get_meta(PROFILE)
    assert meta.autoname == "field:item_code" and meta.get_field("item_code").unique
    assert all(meta.get_field(field).fieldtype == "Data" for fields in DIMENSIONS.values() for field in fields)
    levels = {"Employee": _field_levels("Employee", EMPLOYEE_FIELDS),
              SUPPLEMENT: _field_levels(SUPPLEMENT, SUPPLEMENT_FIELDS)}
    before, counts = _ledger_snapshot(), _counts()
    suffix = frappe.generate_hash(length=10)
    prefix, point = "JF-QA-ANALYSIS-" + suffix, "jf_analysis_" + suffix
    original_user, original_test = frappe.session.user, frappe.in_test
    objects, records, users, urls, checks, evidence = [], set(), [], [], [], {
        "native_field_permlevels": levels,
        "product_schema": "native unique item_code; duplicate rejected; multiple profiles per Item not claimed",
        "design_dimensions": "stored Data text; no inferred Employee relationship",
    }
    current_gate = "begin"
    _begin_rollback_only(point)

    def gate(label):
        nonlocal current_gate
        current_gate = label

    def passed():
        checks.append(current_gate)

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

    def report(name, **filters):
        ledger = _ledger_snapshot()
        result = run(name, filters={"company": company, **filters}, ignore_prepared_report=True,
                     is_tree=name == PRODUCT_REPORT)
        assert not result.get("prepared_report") and _ledger_snapshot() == ledger
        return result

    def reject(operation, errors):
        nested = "jf_analysis_reject_" + frappe.generate_hash(length=8)
        ledger = _ledger_snapshot()
        frappe.db.savepoint(nested)
        try:
            try:
                operation()
            except errors as exception:
                actual = type(exception).__name__
            else:
                raise AssertionError("验收预期原生拒绝，但操作成功")
        finally:
            frappe.db.rollback(save_point=nested)
        assert _ledger_snapshot() == ledger
        return actual

    def product_actors():
        for kind in ("stock", "production"):
            frappe.set_user(actors[kind])
            yield kind

    def hr_fields(kind):
        user = actors[kind]
        permitted = get_permitted_fields("Employee", user=user, permission_type="read", ignore_virtual=True)
        employee_fields = [field for field in EMPLOYEE_FIELDS if field in permitted]
        supplement_fields = []
        if frappe.has_permission(SUPPLEMENT, "read"):
            permitted = get_permitted_fields(SUPPLEMENT, user=user, permission_type="read", ignore_virtual=True)
            supplement_fields = [field for field in SUPPLEMENT_FIELDS if field in permitted]
        evidence.setdefault("native_readable_fields", {})[kind] = {
            "Employee": employee_fields, SUPPLEMENT: supplement_fields}
        return employee_fields, supplement_fields

    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        gate("native_synthetic_masters_and_role_permissions")
        departments = [insert("Department", department_name=prefix + "-D" + str(index), company=owner)
                       for index, owner in enumerate((company, company, other_company))]
        designations = [insert("Designation", designation_name=prefix + "-JOB" + str(index)) for index in range(2)]
        employees = []
        for index, status in enumerate(("Active", "Inactive", "Suspended", "Left", "Active", "Active")):
            employees.append(insert("Employee", first_name=prefix + "-E" + str(index),
                employee_number=prefix + str(index) if index != 5 else None,
                company=company, gender=gender, date_of_birth="1990-01-01", date_of_joining=add_days(today(), -45),
                relieving_date=add_days(today(), -1) if status == "Left" else None, status=status,
                department=departments[index % 2].name if index != 5 else None,
                designation=designations[index % 2].name if index != 5 else None,
                create_user_automatically=0, cell_number="13800000000", bank_ac_no=prefix + "-private-bank"))
        foreign_employee = insert("Employee", first_name=prefix + "-foreign", company=other_company, gender=gender,
            date_of_birth="1990-01-01", date_of_joining=add_days(today(), -45), status="Active",
            department=departments[2].name, designation=designations[0].name, create_user_automatically=0)
        visible_supplement = insert(SUPPLEMENT, employee=employees[0].name, term_type="长期", wage_method="计时",
                                    identity_number=prefix + "-private-id", bank_account_name=prefix + "-private-name")
        hidden_supplement = insert(SUPPLEMENT, employee=employees[4].name, term_type="临时", wage_method="计件")
        items = [insert("Item", item_code=prefix + "-I" + str(index), item_name="分析验收 " + str(index),
                        item_group=item_group, stock_uom=stock_uom) for index in range(6)]
        image_url = "https://" + prefix.lower() + ".example.invalid/metadata-only.png"
        urls.append(image_url)
        items[0].image = image_url
        items[0].save()
        values = ((None, "A/B", "C", 0), ("未填写", "A", "B/C", 0),
                  ("共组", "组", "X", 0), ("共组", "组", "X", 1))
        profiles = [insert(PROFILE, item_code=items[index].name, company=company, brand=brand,
            major_category=major, minor_category=minor, inactive=inactive, material="金属" if index < 2 else "未填写",
            source="自制" if index < 2 else "采购", designer="设计甲" if index < 2 else None,
            pattern_maker="打版甲" if index != 1 else "未填写", modeler="建模甲" if index != 2 else None,
            style="简约" if index < 2 else "复古", list_price=11 + index, weight=3.25 + index,
            silver_weight=0.5, stone_weight=0.25) for index, (brand, major, minor, inactive) in enumerate(values)]
        foreign_profile = insert(PROFILE, item_code=items[5].name, company=other_company, brand="外部档案")
        profiles[0].list_price = 25
        profiles[0].save(ignore_version=False)
        employees[0].employee_number = prefix + "-UPDATED"
        employees[0].save(ignore_version=False)
        assert frappe.db.exists("Version", {"ref_doctype": PROFILE, "docname": profiles[0].name})
        if frappe.get_meta("Employee").track_changes:
            assert frappe.db.exists("Version", {"ref_doctype": "Employee", "docname": employees[0].name})
        actors = {}
        for kind, roles in ROLES.items():
            user = insert("User", email=f"{prefix.lower()}-{kind}@example.invalid", first_name="分析原生验收",
                          send_welcome_email=0, roles=[{"role": role} for role in roles])
            users.append(user.name)
            actors[kind] = user.name
            permit(user.name, "Company", [company])
            if kind in ("stock", "production"):
                permit(user.name, "Item", [item.name for item in items[:5]])
            elif kind in ("hr_user", "hr_manager"):
                permit(user.name, "Employee", [employee.name for employee in employees])
        for kind, user in actors.items():
            frappe.set_user(user)
            evidence.setdefault("native_permissions", {})[kind] = {doctype: {
                permission: bool(frappe.has_permission(doctype, permission)) for permission in ("read", "select", "report")}
                for doctype in ("Item", PROFILE, "Employee", SUPPLEMENT, "Company")}
        assert not evidence["native_permissions"]["selector"]["Item"]["read"]
        assert evidence["native_permissions"]["selector"]["Item"]["select"]
        assert not evidence["native_permissions"]["selector"][PROFILE]["read"]
        assert not evidence["native_permissions"]["hr_user"][SUPPLEMENT]["read"]
        assert evidence["native_permissions"]["hr_manager"][SUPPLEMENT]["read"]
        passed()

        gate("native_unique_item_profile_rejects_duplicate")
        frappe.set_user("Administrator")
        def duplicate():
            document = remember(frappe.copy_doc(profiles[0]))
            document.item_code = items[0].name
            document.insert()
        evidence["duplicate_profile_error"] = reject(duplicate, (frappe.DuplicateEntryError, frappe.UniqueValidationError))
        assert len(frappe.get_all(PROFILE, filters={"item_code": items[0].name}, pluck="name")) == 1
        passed()

        gate("eight_native_product_dimensions_tree_sources_counts_and_no_money_weight_image")
        with patch("jifeng_erp.catalog_reports._readable_image", side_effect=AssertionError("分析报表请求了图片")) as image_guard:
            for kind in product_actors():
                for dimension in DIMENSIONS:
                    result = report(PRODUCT_REPORT, dimension=dimension)
                    _tree(result, profiles, dimension)
                evidence.setdefault("product_dimensions_verified", {})[kind] = list(DIMENSIONS)
            image_guard.assert_not_called()
        passed()
        gate("product_category_separator_collisions_and_empty_literal_values_are_distinct")
        for _ in product_actors():
            categories, _ = _tree(report(PRODUCT_REPORT, dimension="类别"), profiles, "类别")
            assert ("A/B", "C") in categories and ("A", "B/C") in categories
            brands, _ = _tree(report(PRODUCT_REPORT, dimension="品牌"), profiles, "品牌")
            assert (None,) in brands and ("未填写",) in brands
            assert brands[(None,)]["empty_note"] == "品牌未填写" and not brands[("未填写",)]["empty_note"]
            assert (brands[("共组",)]["record_count"], brands[("共组",)]["active_count"],
                    brands[("共组",)]["inactive_count"]) == (2, 1, 1)
        passed()
        gate("product_native_status_exact_item_and_item_without_profile_filters")
        for _ in product_actors():
            for status, expected in (("在用", profiles[:3]), ("停用", profiles[3:])):
                _tree(report(PRODUCT_REPORT, status=status), expected, "品牌")
            _tree(report(PRODUCT_REPORT, item_code=items[0].name), profiles[:1], "品牌")
            _tree(report(PRODUCT_REPORT, item_code=items[4].name), [], "品牌")
        passed()
        gate("product_company_user_permissions_exclude_foreign_sources_and_filters")
        for kind in product_actors():
            assert not frappe.get_doc(PROFILE, foreign_profile.name).has_permission("read")
            evidence.setdefault("product_foreign_company_error", {})[kind] = reject(
                lambda: report(PRODUCT_REPORT, company=other_company), frappe.PermissionError)
        passed()
        gate("product_item_user_permissions_restrict_counts_and_hidden_item_filters")
        for kind in ("stock", "production"):
            frappe.set_user("Administrator")
            for permission in frappe.get_all("User Permission", filters={"user": actors[kind], "allow": "Item"},
                                              fields=["name", "for_value"]):
                if permission.for_value != items[0].name:
                    frappe.delete_doc("User Permission", permission.name)
            frappe.clear_cache(user=actors[kind])
            frappe.set_user(actors[kind])
            _tree(report(PRODUCT_REPORT), profiles[:1], "品牌")
            reject(lambda: report(PRODUCT_REPORT, item_code=items[1].name), frappe.PermissionError)
        passed()
        gate("pure_item_select_is_insufficient_for_product_archive_report")
        frappe.set_user(actors["selector"])
        reject(lambda: report(PRODUCT_REPORT), frappe.PermissionError)
        passed()

        gate("native_hr_user_and_manager_roster_field_permissions_four_statuses_and_empty_fields")
        for kind in ("hr_user", "hr_manager"):
            frappe.set_user(actors[kind])
            employee_fields, supplement_fields = hr_fields(kind)
            assert "name" in employee_fields
            result = report(PERSONNEL_REPORT)
            rows = _roster(result, employees, employee_fields, supplement_fields)
            assert {row["status"] for row in rows.values()} == {"Active", "Inactive", "Suspended", "Left"}
            for marker in ("13800000000", prefix + "-private-bank", prefix + "-private-id", prefix + "-private-name"):
                assert marker not in json.dumps(result, default=str)
            assert str(rows[employees[3].name]["relieving_date"]) == str(employees[3].relieving_date)
            assert rows[employees[1].name]["relieving_date"] is None
            assert all(not rows[employees[5].name].get(field) for field in ("employee_number", "department", "designation"))
            if kind == "hr_user":
                assert "term_type" not in rows[employees[0].name] and "wage_method" not in rows[employees[0].name]
        passed()
        gate("personnel_department_designation_and_status_filters_follow_native_records")
        for kind in ("hr_user", "hr_manager"):
            frappe.set_user(actors[kind])
            employee_fields, supplement_fields = hr_fields(kind)
            for filters in ({"department": departments[0].name}, {"designation": designations[1].name},
                            *[{"status": status} for status in ("Active", "Inactive", "Suspended", "Left")]):
                expected = [employee for employee in employees if all(employee.get(field) == value
                                                                       for field, value in filters.items())]
                _roster(report(PERSONNEL_REPORT, **filters), expected, employee_fields, supplement_fields)
        passed()
        gate("personnel_supplement_readable_hidden_and_missing_are_independent_from_employee_read")
        frappe.set_user(actors["hr_manager"])
        employee_fields, supplement_fields = hr_fields("hr_manager")
        rows = _roster(report(PERSONNEL_REPORT), employees, employee_fields, supplement_fields)
        assert (rows[employees[0].name]["term_type"], rows[employees[0].name]["wage_method"]) == ("长期", "计时")
        permit(actors["hr_manager"], SUPPLEMENT, [visible_supplement.name])
        frappe.set_user(actors["hr_manager"])
        assert not frappe.get_doc(SUPPLEMENT, hidden_supplement.name).has_permission("read")
        assert frappe.get_doc("Employee", employees[4].name).has_permission("read")
        rows = _roster(report(PERSONNEL_REPORT), employees, employee_fields, supplement_fields)
        assert tuple(rows[employees[4].name].get(field) for field in ("term_type", "wage_method")) == (
            rows[employees[5].name].get("term_type"), rows[employees[5].name].get("wage_method")) == (None, None)
        assert rows[employees[0].name]["term_type"] == "长期"
        passed()
        gate("personnel_company_employee_and_foreign_department_native_user_permissions")
        for kind in ("hr_user", "hr_manager"):
            frappe.set_user(actors[kind])
            assert not frappe.get_doc("Employee", foreign_employee.name).has_permission("read")
            reject(lambda: report(PERSONNEL_REPORT, company=other_company), frappe.PermissionError)
            selected = frappe.get_doc("Department", departments[2].name)
            error = frappe.ValidationError if selected.has_permission("select") else frappe.PermissionError
            evidence.setdefault("personnel_foreign_department_error", {})[kind] = reject(
                lambda: report(PERSONNEL_REPORT, department=departments[2].name), error)
            frappe.set_user("Administrator")
            for permission in frappe.get_all("User Permission", filters={"user": actors[kind], "allow": "Employee"},
                                              fields=["name", "for_value"]):
                if permission.for_value != employees[0].name:
                    frappe.delete_doc("User Permission", permission.name)
            frappe.clear_cache(user=actors[kind])
            frappe.set_user(actors[kind])
            _roster(report(PERSONNEL_REPORT), employees[:1], *hr_fields(kind))
        passed()
        gate("employee_selection_only_and_guest_cannot_read_personnel_or_product_reports")
        frappe.set_user(actors["stock"])
        assert frappe.has_permission("Employee", "select") and not frappe.has_permission("Employee", "read")
        reject(lambda: report(PERSONNEL_REPORT), frappe.PermissionError)
        frappe.set_user("Guest")
        for name in (PRODUCT_REPORT, PERSONNEL_REPORT):
            reject(lambda name=name: report(name), frappe.PermissionError)
        passed()
        assert _ledger_snapshot() == before, "主档/报告参考资料改变了 whole GL/SLE hash"
    except Exception as exception:
        exception.add_note("catalog/personnel smoke failed at gate: " + current_gate)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            if urls:
                records.update(("File", name) for name in frappe.get_all("File", filters={"file_url": ["in", urls]},
                                                                        pluck="name"))
            names = list({name for _, name in records})
            if names:
                records.update(("Version", name) for name in frappe.get_all("Version", filters={"docname": ["in", names]},
                                                                           pluck="name"))
        finally:
            try:
                _end_rollback_only(point)
            finally:
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records), "回滚后残留 QA 对象/Version/File"
            assert _counts() == counts, "回滚后主档/档案/用户权限/辅助表记录数有差异"
            assert _ledger_snapshot() == before, "回滚后 whole GL/SLE hash 有差异"
    checks.extend(("rollback_all_tracked_masters_catalogs_permissions_versions_and_file_aliases",
                   "rollback_whole_gl_sle_hashes_and_all_touched_table_counts"))
    return {"passed": True, "checks": checks, "gate_count": len(checks), "business_data": "rolled_back",
            "record_counts": dict(Counter(doctype for doctype, _ in records)), "evidence": evidence,
            "whole_ledger_hashes_before": before, "whole_ledger_hashes_after": _ledger_snapshot(),
            "table_counts_before": counts, "table_counts_after": _counts(),
            "images": "remote URL metadata only; report makes no image permission or download request",
            "execution": "direct Python, one invocation, serial rollback experiment"}
