import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def install():
    fields = {doctype: [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "item_code", "no_copy": 1,
    }] for doctype in (
        "Subcontracting Order Item", "Subcontracting Receipt Item", "Material Request Item", "Purchase Order Item",
        "Purchase Receipt Item", "Purchase Invoice Item", "Stock Entry Detail",
    )}
    fields["Sales Order Item"] = [
        {"fieldname": "jf_make_flow", "fieldtype": "Check", "label": "生成集沣流程单",
         "insert_after": "description", "default": "1", "in_list_view": 1, "allow_on_submit": 1},
        {"fieldname": "jf_requirements", "fieldtype": "Small Text", "label": "集沣定制/工艺要求",
         "insert_after": "jf_make_flow", "allow_on_submit": 1},
        {"fieldname": "jf_packaging", "fieldtype": "Small Text", "label": "集沣包装要求",
         "insert_after": "jf_requirements", "allow_on_submit": 1},
        {"fieldname": "jf_requirements_attachment", "fieldtype": "Attach", "label": "集沣要求附件",
         "insert_after": "jf_packaging", "allow_on_submit": 1},
    ]
    for doctype in ("Delivery Note Item", "Sales Invoice Item"):
        fields[doctype] = [{
            "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
            "options": "Jifeng Flow Order", "insert_after": "so_detail", "read_only": 1, "no_copy": 1,
            "description": "由精确销售来源明细继承，不改变原生价格、数量或库存估价。",
        }]
    fields["Work Order"] = [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "sales_order", "in_list_view": 1,
    }]
    fields["Job Card"] = [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "work_order", "read_only": 1,
        "no_copy": 1, "in_list_view": 1,
    }, {
        "fieldname": "jf_department", "fieldtype": "Link", "label": "生产部门",
        "options": "Department", "insert_after": "operation", "read_only": 1,
        "no_copy": 1, "in_list_view": 1,
    }]
    for doctype in ("Workstation", "Operation", "Work Order Operation"):
        fields[doctype] = [{
            "fieldname": "jf_department", "fieldtype": "Link", "label": "生产部门",
            "options": "Department", "in_list_view": 1,
            "insert_after": {"Workstation": "workstation_name", "Operation": "workstation",
                             "Work Order Operation": "workstation"}[doctype],
            "description": "工单工序可单独指定；未填写时先取工作站，再取工序默认部门。",
        }]
    fields["Production Plan Item"] = [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "item_code", "in_list_view": 1,
    }]
    fields["Production Plan"] = [
        {"fieldname": "jf_instruction_section", "fieldtype": "Section Break",
         "label": "集沣生产指令", "insert_after": "posting_date"},
        {"fieldname": "jf_instruction_no", "fieldtype": "Data", "label": "原指令编号",
         "insert_after": "jf_instruction_section"},
        {"fieldname": "jf_instruction_date", "fieldtype": "Date", "label": "指令日期",
         "default": "Today", "insert_after": "jf_instruction_no"},
        {"fieldname": "jf_instruction_weight", "fieldtype": "Float", "label": "指令重量",
         "insert_after": "jf_instruction_date", "non_negative": 1},
        {"fieldname": "jf_instruction_weight_uom", "fieldtype": "Link", "label": "重量单位",
         "options": "UOM", "insert_after": "jf_instruction_weight"},
        {"fieldname": "jf_instruction_column", "fieldtype": "Column Break",
         "insert_after": "jf_instruction_weight_uom"},
        {"fieldname": "jf_instruction_currency", "fieldtype": "Link", "label": "指令币种",
         "options": "Currency", "read_only": 1, "insert_after": "jf_instruction_column"},
        {"fieldname": "jf_instruction_amount", "fieldtype": "Currency", "label": "指令参考金额",
         "options": "jf_instruction_currency", "insert_after": "jf_instruction_currency",
         "non_negative": 1, "description": "用于指令记录和打印，不生成库存估价或财务凭证。"},
        {"fieldname": "jf_instruction_remark", "fieldtype": "Small Text", "label": "指令备注",
         "insert_after": "jf_instruction_amount"},
    ]
    fields["Stock Entry"] = [{
        "fieldname": "jf_material_issue_flow", "fieldtype": "Link", "label": "按单配料流程单",
        "options": "Jifeng Flow Order", "insert_after": "work_order", "read_only": 1,
        "no_copy": 1,
    }]
    fields["Journal Entry"] = [{
        "fieldname": "jf_expense_voucher", "fieldtype": "Link", "label": "集沣费用登记",
        "options": "Jifeng Expense Voucher", "insert_after": "user_remark",
        "read_only": 1, "no_copy": 1, "unique": 1,
    }]
    for doctype, details in (("Employee Promotion", "promotion_details"),
                              ("Employee Transfer", "transfer_details")):
        fields[doctype] = [{
            "fieldname": "jf_history_snapshot", "fieldtype": "Long Text",
            "label": "集沣人事变更追溯", "insert_after": details,
            "hidden": 1, "read_only": 1, "no_copy": 1, "print_hide": 1,
            "allow_on_submit": 1,
            "description": "服务器记录本员工精确原值和工作历史，用于核对后续变更及安全取消。",
        }]
    fields["Employee Separation"] = [
        {"fieldname": "jf_exit_section", "fieldtype": "Section Break",
         "label": "离职办理", "insert_after": "boarding_status"},
        {"fieldname": "jf_last_working_day", "fieldtype": "Date", "label": "实际最后工作日",
         "insert_after": "jf_exit_section", "allow_on_submit": 1,
         "description": "填写员工实际最后工作日，完成交接后点击办理离职。"},
        {"fieldname": "jf_reason_for_leaving", "fieldtype": "Small Text", "label": "离职原因",
         "insert_after": "jf_last_working_day", "allow_on_submit": 1},
        {"fieldname": "jf_exit_status", "fieldtype": "Select", "label": "离职办理状态",
         "insert_after": "jf_reason_for_leaving", "options": "待办理\n已办理\n已撤销",
         "default": "待办理", "read_only": 1, "no_copy": 1, "in_list_view": 1,
         "allow_on_submit": 1},
        {"fieldname": "jf_exit_snapshot", "fieldtype": "Long Text", "label": "离职办理追溯",
         "insert_after": "jf_exit_status", "hidden": 1, "read_only": 1, "no_copy": 1,
         "print_hide": 1, "allow_on_submit": 1,
         "description": "服务器记录员工原值与办理结果，用于核对后续变更及安全撤销。"},
    ]
    create_custom_fields(fields, update=True)
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    make_property_setter("Production Plan", None, "track_changes", 1, "Check", for_doctype=True)
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
    from frappe.permissions import setup_custom_perms

    for role in ("Manufacturing User", "Manufacturing Manager"):
        # Preserve site-specific rules. Custom DocPerm defaults read to 1, so
        # explicitly disable it when installing a new selector-only rule.
        if any(row.role == role for row in frappe.get_meta("Department").permissions):
            continue
        setup_custom_perms("Department")
        frappe.get_doc({
            "doctype": "Custom DocPerm", "parent": "Department", "parenttype": "DocType",
            "parentfield": "permissions", "role": role, "permlevel": 0,
            "if_owner": 0, "read": 0, "select": 1,
        }).insert(ignore_permissions=True)
        validate_permissions_for_doctype("Department")

    _install_wage_source_permissions()
    _install_wage_reversal_permissions()
    _install_separation_permissions()


def _install_wage_source_permissions():
    """Allow HR supervisors to verify wage sources without production edits."""
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
    from frappe.permissions import setup_custom_perms

    for doctype, readable in (
        ("Job Card", True), ("Work Order", True), ("Item", True),
        ("Jifeng Flow Order", True), ("Operation", False), ("UOM", False),
    ):
        rules = frappe.get_meta(doctype).permissions
        # A site's explicit HR rule takes precedence, including an intentional
        # denial. Existing shared read/select rules need no extra permission.
        if any(row.role == "HR Manager" for row in rules):
            continue
        if any(row.role == "All" and (row.read if readable else row.read or row.select) for row in rules):
            continue
        setup_custom_perms(doctype)
        frappe.get_doc({
            "doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType",
            "parentfield": "permissions", "role": "HR Manager", "permlevel": 0,
            "if_owner": 0, "read": int(readable), "select": 1,
            "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0,
            "amend": 0, "report": 0, "export": 0, "share": 0, "print": 0,
        }).insert(ignore_permissions=True)
        validate_permissions_for_doctype(doctype)


def _install_wage_reversal_permissions():
    """Give default HR supervisors a native reversal path for additional wages."""
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
    from frappe.permissions import setup_custom_perms
    from jifeng_erp.wage_permissions import default_wage_permissions

    doctype = "Additional Salary"
    custom = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["*"], limit_page_length=0)
    if custom:
        fields = ("role", "permlevel", *[field.fieldname for field in frappe.get_meta("DocPerm").fields
                                          if field.fieldtype == "Check"])
        native = frappe.get_all("DocPerm", filters={"parent": doctype}, fields=["*"], limit_page_length=0)
        ess = frappe.get_doc("User Type", "Employee Self Service") if frappe.db.exists(
            "User Type", "Employee Self Service") else None
        salary_field = frappe.get_meta("Salary Detail").get_field("additional_salary") if frappe.db.exists(
            "DocType", "Salary Detail") else None
        # HRMS creates an exact native clone plus ESS selection permissions from
        # Salary Slip -> Salary Detail.additional_salary. This is not a manual
        # site policy. Any meaningful deviation still prevents installation.
        ess_generated = bool(ess and ess.role == "Employee Self Service"
            and any(row.document_type == "Salary Slip" for row in ess.user_doctypes)
            and any(row.document_type == doctype for row in ess.select_doctypes)
            and salary_field and salary_field.options == doctype)
        if not default_wage_permissions(native, custom, ess_generated=ess_generated, fields=fields):
            return
    setup_custom_perms(doctype)
    names = frappe.get_all("Custom DocPerm", filters={
        "parent": doctype, "role": "HR Manager", "permlevel": 0,
    }, pluck="name", limit_page_length=0)
    changed = False
    for name in names:
        rule = frappe.get_doc("Custom DocPerm", name)
        if not rule.submit or (rule.cancel and rule.delete):
            continue
        rule.cancel, rule.delete = 1, 1
        rule.save(ignore_permissions=True)
        changed = True
    validate_permissions_for_doctype(doctype)
    if changed:
        frappe.get_doc("DocType", doctype).add_comment(
            "Info", "集沣工资分配安装：核对原生权限及 HRMS 员工自助选择权限后，默认 HR Manager 补充附加薪资取消和草稿删除权限；原生已提交状态与来源链接校验保留。有实质差异的站点自定义权限不覆盖。",
        )


def _install_separation_permissions():
    """Default HR supervisors use native safe Separation submit/save/cancel."""
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
    from frappe.permissions import setup_custom_perms
    from jifeng_erp.separation_permissions import default_separation_permissions

    doctype = "Employee Separation"
    native = frappe.get_all("DocPerm", filters={"parent": doctype}, fields=["*"], limit_page_length=0)
    custom = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["*"], limit_page_length=0)
    fields = ("role", "permlevel", *[field.fieldname for field in frappe.get_meta("DocPerm").fields
                                      if field.fieldtype == "Check"])
    if custom and not default_separation_permissions(native, custom, fields=fields):
        return  # Explicit site decisions, including partial grants/denials, win.
    # Enable this default extension only where the complete safe controller chain
    # is installed; native HRMS cancellation force-deletes project/tasks.
    hooks = frappe.get_hooks("extend_doctype_class")
    for target, extension in ((doctype, "EmployeeSeparationMixin"), ("Employee", "SeparationEmployeeMixin"),
                              ("Project", "SeparationProjectMixin")):
        installed = hooks.get(target, [])
        installed = [installed] if isinstance(installed, str) else installed
        if "jifeng_erp.personnel_separation." + extension not in installed:
            frappe.throw("离职原生权限安装需要完整的安全员工、来源和交接项目控制器")
    setup_custom_perms(doctype)  # Native clone preserves every original rule.
    names = frappe.get_all("Custom DocPerm", filters={
        "parent": doctype, "role": "HR Manager", "permlevel": 0, "if_owner": 0,
    }, pluck="name", limit_page_length=0)
    changed = False
    for name in names:
        rule = frappe.get_doc("Custom DocPerm", name)
        if rule.submit and rule.cancel:
            continue
        rule.submit, rule.cancel = 1, 1
        rule.save(ignore_permissions=True)
        changed = True
    validate_permissions_for_doctype(doctype)
    if changed:
        frappe.get_doc("DocType", doctype).add_comment("Info",
            "集沣离职办理安装：核对完整原生权限克隆及安全控制器后，默认 HR Manager 补充正式离职来源的提交、取消权限；不新增删除权限，不改变 HR User、其他角色及站点实质自定义权限。已办理须先撤销，交接项目任务通过原生保存取消并保留。")


_PROCUREMENT_SOURCES = {
    "Purchase Order": (("material_request_item", "Material Request Item", "material_request"),),
    "Purchase Receipt": (("purchase_order_item", "Purchase Order Item", "purchase_order"),),
    "Purchase Invoice": (
        ("pr_detail", "Purchase Receipt Item", "purchase_receipt"),
        ("po_detail", "Purchase Order Item", "purchase_order"),
    ),
    "Subcontracting Receipt": (
        ("subcontracting_order_item", "Subcontracting Order Item", "subcontracting_order"),
    ),
}

_RETURN_SOURCES = {
    "Purchase Receipt": ("purchase_receipt_item", "Purchase Receipt Item"),
    "Purchase Invoice": ("purchase_invoice_item", "Purchase Invoice Item"),
    "Subcontracting Receipt": ("subcontracting_receipt_item", "Subcontracting Receipt Item"),
}


def _propagate_procurement_flow(doc):
    sources = _PROCUREMENT_SOURCES.get(doc.doctype, ())
    if doc.get("is_return") and doc.doctype in _RETURN_SOURCES:
        source_field, source_doctype = _RETURN_SOURCES[doc.doctype]
        sources += ((source_field, source_doctype, "return_against"),)
    for row in doc.get("items") or []:
        source_flows = set()
        for source_field, source_doctype, parent_field in sources:
            source_name = row.get(source_field)
            if not source_name:
                continue
            source = frappe.db.get_value(
                source_doctype, source_name, ["parent", "item_code", "jf_flow_order"], as_dict=True,
            )
            if not source:
                frappe.throw("采购来源明细不存在，请重新生成单据")
            parent_name = row.get(parent_field) or doc.get(parent_field)
            if parent_field == "return_against" and not parent_name:
                frappe.throw("退回来源单据不存在")
            if parent_name and source.parent != parent_name:
                frappe.throw("采购来源单据与来源明细不一致")
            if source.item_code != row.item_code:
                frappe.throw("采购明细商品与来源明细不一致")
            if source.jf_flow_order:
                source_flows.add(source.jf_flow_order)
        if len(source_flows) > 1:
            frappe.throw("采购来源明细关联了不同流程单")
        if source_flows:
            flow_name = source_flows.pop()
            if row.get("jf_flow_order") and row.jf_flow_order != flow_name:
                frappe.throw("采购明细流程单与来源单据不一致")
            row.jf_flow_order = flow_name


def _propagate_subcontracting_stock_flow(doc):
    if not doc.get("subcontracting_order"):
        return
    for row in doc.get("items") or []:
        if not row.get("sco_rm_detail"):
            continue
        supplied = frappe.db.get_value(
            "Subcontracting Order Supplied Item", row.sco_rm_detail,
            ["parent", "reference_name", "main_item_code", "rm_item_code"], as_dict=True,
        )
        if not supplied or supplied.parent != doc.subcontracting_order:
            frappe.throw("委外发料来源明细与委外订单不一致")
        if supplied.rm_item_code != row.item_code or (
            row.get("subcontracted_item") and supplied.main_item_code != row.subcontracted_item
        ):
            frappe.throw("委外发料物料与来源明细不一致")
        if not supplied.reference_name:
            continue
        source = frappe.db.get_value(
            "Subcontracting Order Item", supplied.reference_name,
            ["parent", "item_code", "jf_flow_order"], as_dict=True,
        )
        if not source or source.parent != doc.subcontracting_order or source.item_code != supplied.main_item_code:
            frappe.throw("委外发料成品与委外订单明细不一致")
        if source.jf_flow_order:
            if row.get("jf_flow_order") and row.jf_flow_order != source.jf_flow_order:
                frappe.throw("委外发料流程单与来源单据不一致")
            row.jf_flow_order = source.jf_flow_order


def validate_flow_links(doc, method=None):
    if doc.doctype == "Job Card":
        work_order = frappe.get_doc("Work Order", doc.work_order)
        work_order.check_permission("read")
        source_flow = work_order.get("jf_flow_order")
        if doc.get("jf_flow_order") and doc.jf_flow_order != source_flow:
            frappe.throw("报工单流程单与生产工单来源不一致")
        if work_order.company != doc.company or work_order.production_item != doc.production_item:
            frappe.throw("报工单与生产工单的公司、商品不一致")
        if source_flow:
            flow = frappe.get_doc("Jifeng Flow Order", source_flow)
            flow.check_permission("read")
            if flow.company != doc.company or flow.item_code != doc.production_item:
                frappe.throw("报工单与流程单的公司、商品不一致")
        doc.jf_flow_order = source_flow
        return
    if doc.doctype in _PROCUREMENT_SOURCES:
        _propagate_procurement_flow(doc)
    if doc.doctype == "Stock Entry":
        if doc.get("jf_material_issue_flow"):
            from jifeng_erp.material_issue import validate_material_issue

            validate_material_issue(doc)
        _propagate_subcontracting_stock_flow(doc)
    if doc.doctype == "Work Order":
        from jifeng_erp.production_instruction import validate_work_order

        validate_work_order(doc)
        return
    work_flow = None
    if doc.doctype == "Stock Entry" and doc.get("work_order"):
        work_order = frappe.get_doc("Work Order", doc.work_order)
        if work_order.get("jf_flow_order"):
            work_flow = frappe.get_doc("Jifeng Flow Order", work_order.jf_flow_order)
            work_flow.check_permission("read")
            if work_order.company != doc.company or work_flow.company != doc.company:
                frappe.throw("生产工单、库存凭证与流程单公司不一致")
            if work_order.production_item != work_flow.item_code:
                frappe.throw("生产工单商品与流程单商品不一致")
            valid_items = {row.item_code for row in work_flow.materials} | {work_flow.item_code}
            for row in doc.get("items") or []:
                if row.get("jf_flow_order") and row.jf_flow_order != work_flow.name:
                    frappe.throw("生产工单库存明细不能关联其他流程单")
                if row.item_code in valid_items:
                    row.jf_flow_order = work_flow.name
    for row in doc.get("items") or []:
        if not row.get("jf_flow_order"):
            continue
        if (doc.doctype == "Subcontracting Receipt" and doc.get("is_return")
                and float(row.get("process_loss_qty") or 0) != 0):
            frappe.throw("关联流程单的委外退货只能退回实物，工艺损耗须为 0；原收货损耗保留。"
                         "若需冲回损耗，请先处理或更正原收货凭证。")
        flow = work_flow if work_flow and row.jf_flow_order == work_flow.name else frappe.get_doc(
            "Jifeng Flow Order", row.jf_flow_order
        )
        flow.check_permission("read")
        if flow.company != doc.company:
            frappe.throw("流程单与业务凭证公司不一致")
        if doc.doctype in ("Subcontracting Order", "Subcontracting Receipt") and flow.item_code != row.item_code:
            frappe.throw("委外单据商品与流程单商品不一致")
        if doc.doctype in (
            "Stock Entry", "Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice",
        ):
            valid_items = {m.item_code for m in flow.materials} | {flow.item_code}
            if row.item_code not in valid_items:
                frappe.throw("凭证物料不在流程单物料分析中，请先补齐分析")
