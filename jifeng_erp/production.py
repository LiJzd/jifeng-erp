"""Permission-aware production reports; operation quantities are never added as finished goods."""

import frappe
from frappe.utils import cint, date_diff, flt, get_datetime, getdate, today


STATUS_LABELS = {"Draft": "草稿", "Not Started": "未投产", "In Process": "生产中",
                 "Stock Reserved": "库存已预留", "Completed": "已完成", "Stopped": "已停止",
                 "Closed": "已关闭", "Open": "待开始", "Work In Progress": "报工中",
                 "Partially Transferred": "部分发料", "Material Transferred": "已发料",
                 "On Hold": "暂停", "Submitted": "已提交"}


def report_scope(filters, doctype):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission(doctype, "read"):
        frappe.throw("没有生产单据读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    if not frappe.get_list("Company", filters={"name": filters.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    return filters, start, end


def overdue_days(end_date, remaining, inactive=False):
    if not end_date or remaining <= 0 or inactive:
        return 0
    return max(date_diff(today(), end_date), 0)


def work_order_progress(filters=None):
    filters, start, end = report_scope(filters, "Work Order")
    query = {"company": filters.company, "docstatus": ["<", 2],
             "creation": ["between", [f"{start} 00:00:00", f"{end} 23:59:59.999999"]]}
    for field in ("production_item", "status", "sales_order"):
        if filters.get(field):
            query[field] = filters[field]
    if filters.flow_order:
        query["jf_flow_order"] = filters.flow_order
    rows = frappe.get_list("Work Order", filters=query, fields=[
        "name", "jf_flow_order", "sales_order", "production_item", "item_name", "stock_uom",
        "qty", "produced_qty", "process_loss_qty", "status", "docstatus", "creation",
        "planned_start_date", "planned_end_date", "actual_start_date", "actual_end_date",
    ], order_by="planned_start_date asc, name asc", limit_page_length=0)
    result = []
    for row in rows:
        if cint(filters.only_loss) and flt(row.process_loss_qty) <= 0:
            continue
        remaining = max(flt(row.qty) - flt(row.produced_qty) - flt(row.process_loss_qty), 0)
        overdue = overdue_days(row.planned_end_date, remaining, row.docstatus == 0 or row.status in ("Stopped", "Closed"))
        if cint(filters.only_overdue) and not overdue:
            continue
        result.append({**row, "remaining_qty": remaining, "overdue_days": overdue,
                       "status_label": STATUS_LABELS.get(row.status, row.status),
                       "approval": "已提交" if row.docstatus == 1 else "草稿",
                       "progress_percent": min(100, (flt(row.produced_qty) + flt(row.process_loss_qty)) / flt(row.qty) * 100) if row.qty else 0})
    columns = [
        column("name", "生产工单", "Link", "Work Order", 170),
        column("jf_flow_order", "流程单", "Link", "Jifeng Flow Order", 160),
        column("sales_order", "销售订单", "Link", "Sales Order", 155),
        column("production_item", "商品编号", "Link", "Item", 150),
        column("item_name", "商品名称", width=150), column("stock_uom", "单位", "Link", "UOM", 80),
        column("approval", "审核状态", width=95), column("status_label", "工单状态", width=130),
        column("qty", "计划数量", "Float"), column("produced_qty", "完工入库数量", "Float", width=125),
        column("process_loss_qty", "入库凭证损耗", "Float", width=125), column("remaining_qty", "尚未完工数量", "Float", width=125),
        column("progress_percent", "结清进度 %", "Percent"),
        column("planned_start_date", "计划开始", "Datetime", width=160),
        column("planned_end_date", "计划结束", "Datetime", width=160),
        column("actual_end_date", "实际结束", "Datetime", width=160), column("overdue_days", "逾期天数", "Int"),
    ]
    return columns, result


def job_card_progress(filters=None, date_basis="posting_date"):
    filters, start, end = report_scope(filters, "Job Card")
    if not frappe.has_permission("Work Order", "read"):
        frappe.throw("没有生产工单读取权限", frappe.PermissionError)
    if filters.department and not frappe.get_list("Department", filters={"name": filters.department,
                                                  "company": filters.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查询该生产部门的权限", frappe.PermissionError)
    if filters.department and cint(filters.only_unassigned):
        frappe.throw("指定生产部门和仅未指定部门不能同时选择")
    work_filters = {"company": filters.company, "docstatus": 1}
    if date_basis == "work_order":
        work_filters["creation"] = ["between", [f"{start} 00:00:00", f"{end} 23:59:59.999999"]]
    elif date_basis != "posting_date":
        frappe.throw("报工日期口径无效")
    if filters.work_order:
        work_filters["name"] = filters.work_order
    if filters.flow_order:
        work_filters["jf_flow_order"] = filters.flow_order
    parents = frappe.get_list("Work Order", filters=work_filters,
                              fields=["name", "jf_flow_order", "qty", "status"], limit_page_length=0)
    sources = {}
    for parent in parents:
        work = frappe.get_doc("Work Order", parent.name)
        work.check_permission("read")
        sources[parent.name] = {"flow_order": parent.jf_flow_order, "qty": flt(parent.qty),
                                "status": parent.status, "operations": {
                                    operation.name: operation for operation in work.get("operations") or []}}
    rows = []
    if sources:
        query = {"company": filters.company, "docstatus": ["<", 2], "work_order": ["in", list(sources)]}
        if date_basis == "posting_date":
            query["posting_date"] = ["between", [str(start), str(end)]]
        for field in ("production_item", "operation", "workstation", "status"):
            if field == "operation" and date_basis == "work_order":
                continue  # A corrective card has its own operation; select its source below.
            if filters.get(field):
                query[field] = filters[field]
        if filters.department:
            query["jf_department"] = filters.department
        elif cint(filters.only_unassigned):
            query["jf_department"] = ["is", "not set"]
        if filters.approval == "已提交":
            query["docstatus"] = 1
        elif filters.approval == "草稿":
            query["docstatus"] = 0
        elif filters.approval not in (None, "", "全部"):
            frappe.throw("审核状态无效")
        rows = frappe.get_list("Job Card", filters=query, fields=[
            "name", "work_order", "production_item", "operation", "workstation", "stock_uom", "jf_department",
            "posting_date", "for_quantity", "total_completed_qty", "process_loss_qty", "status", "docstatus",
            "pending_qty", "operation_id", "is_corrective_job_card", "for_operation",
            "total_time_in_mins", "expected_start_date", "expected_end_date", "actual_start_date", "actual_end_date",
        ], order_by="posting_date asc, name asc", limit_page_length=0)
    result = []
    for row in rows:
        parent = sources[row.work_order]
        corrective = cint(row.is_corrective_job_card)
        effective = max(flt(row.for_quantity) - flt(row.pending_qty), 0)
        source_operation = row.for_operation if corrective else row.operation
        source_status = "未关联工单工序"
        planned_qty = 0 if corrective else effective
        if row.operation_id:
            operation = parent["operations"].get(row.operation_id)
            if not operation or operation.operation != source_operation:
                frappe.throw("报工单 {0} 的来源工序与生产工单不一致，请先核对".format(row.name))
            source_operation, planned_qty, source_status = operation.operation, parent["qty"], "已关联工单工序"
        if date_basis == "work_order" and filters.operation and source_operation != filters.operation:
            continue
        if cint(filters.only_loss) and flt(row.process_loss_qty) <= 0:
            continue
        remaining = max(effective - flt(row.total_completed_qty) - flt(row.process_loss_qty), 0)
        overdue = overdue_days(row.expected_end_date, remaining, parent["status"] in ("Stopped", "Closed"))
        if cint(filters.only_overdue) and not overdue:
            continue
        result.append({**row, "jf_flow_order": parent["flow_order"], "remaining_qty": remaining,
                       "effective_qty": effective, "source_operation": source_operation,
                       "source_planned_qty": planned_qty, "source_status": source_status,
                       "work_order_status": parent["status"],
                       "card_type": "返工纠正" if corrective else "正常报工",
                       "status_label": STATUS_LABELS.get(row.status, row.status),
                       "approved_qty": row.total_completed_qty if row.docstatus == 1 else 0,
                       "draft_qty": row.total_completed_qty if row.docstatus == 0 else 0,
                       "approved_loss_qty": row.process_loss_qty if row.docstatus == 1 else 0,
                       "draft_loss_qty": row.process_loss_qty if row.docstatus == 0 else 0,
                       "hours": 0 if corrective else flt(row.total_time_in_mins) / 60,
                       "corrective_hours": flt(row.total_time_in_mins) / 60 if corrective else 0,
                       "overdue_days": overdue,
                       "approval": "已提交" if row.docstatus == 1 else "草稿"})
    columns = [
        column("name", "报工单", "Link", "Job Card", 155),
        column("jf_flow_order", "流程单", "Link", "Jifeng Flow Order", 160),
        column("work_order", "生产工单", "Link", "Work Order", 170),
        column("posting_date", "报工日期", "Date"), column("production_item", "商品编号", "Link", "Item", 150),
        column("operation", "工序", "Link", "Operation", 130), column("workstation", "工作站", "Link", "Workstation", 140),
        column("jf_department", "生产部门", "Link", "Department", 150),
        column("stock_uom", "单位", "Link", "UOM", 80), column("approval", "审核状态", width=95),
        column("status_label", "报工状态", width=130), column("card_type", "报工类型", width=110),
        column("source_status", "工序来源", width=145),
        column("for_quantity", "原派工量（含转交）", "Float", width=155),
        column("pending_qty", "转交待派工", "Float", width=125), column("effective_qty", "本卡任务数量", "Float", width=125),
        column("approved_qty", "已审核完成", "Float"), column("draft_qty", "待审核完成", "Float"),
        column("approved_loss_qty", "已审核损耗", "Float"), column("draft_loss_qty", "待审核损耗", "Float"),
        column("remaining_qty", "本卡尚未报完", "Float", width=130), column("hours", "正常工时", "Float"),
        column("corrective_hours", "纠正工时", "Float"),
        column("expected_end_date", "计划结束", "Datetime", width=160), column("overdue_days", "逾期天数", "Int"),
    ]
    return columns, result


def department_progress(filters=None):
    filters = frappe._dict(filters or {})
    # Select the work-order creation batch, including every readable card in it.
    # Filtering overdue cards first loses completed continuations and their quantities.
    _, rows = job_card_progress({**filters, "only_overdue": 0, "only_loss": 0}, date_basis="work_order")
    groups = {}
    numeric = ("for_quantity", "approved_qty", "draft_qty", "approved_loss_qty", "draft_loss_qty", "remaining_qty", "hours")
    for row in rows:
        corrective = cint(row["is_corrective_job_card"])
        linked = bool(row["operation_id"])
        source_kind = "linked" if linked else "legacy_corrective" if corrective else "legacy_normal"
        key = (row.get("jf_department") or "", row["source_operation"] or "", row["stock_uom"], source_kind)
        if key not in groups:
            groups[key] = {"jf_department": key[0], "department_label": key[0] or "未指定部门",
                           "operation": key[1], "stock_uom": key[2], "work_orders": set(),
                           "source_status": row["source_status"], "sources": {},
                           "job_card_count": 0, "corrective_job_card_count": 0, "corrective_hours": 0,
                           "source_operation_count": 0, "overdue_count": 0, **dict.fromkeys(numeric, 0)}
        group = groups[key]
        group["work_orders"].add(row["work_order"])
        if corrective:
            group["corrective_job_card_count"] += 1
            group["corrective_hours"] += flt(row["corrective_hours"])
            continue
        group["job_card_count"] += 1
        # Exact child-row identity distinguishes repeated operations with the same
        # name. An old card without that identity remains its own effective task.
        source_key = (row["work_order"], row["operation_id"] or row["name"])
        if source_key not in group["sources"]:
            group["sources"][source_key] = {"planned_qty": flt(row["source_planned_qty"]),
                                           "settled_qty": 0, "end_dates": [], "status": row["work_order_status"]}
        source = group["sources"][source_key]
        source["settled_qty"] += sum(flt(row[field]) for field in
                                     ("approved_qty", "draft_qty", "approved_loss_qty", "draft_loss_qty"))
        if row["expected_end_date"]:
            source["end_dates"].append(row["expected_end_date"])
        for field in ("approved_qty", "draft_qty", "approved_loss_qty", "draft_loss_qty", "hours"):
            group[field] += flt(row[field])
    result = []
    for key in sorted(groups, key=lambda value: tuple(part or "" for part in value)):
        row = groups[key]
        sources = row.pop("sources")
        row["source_operation_count"] = len(sources)
        for source in sources.values():
            remaining = max(source["planned_qty"] - source["settled_qty"], 0)
            row["for_quantity"] += source["planned_qty"]
            row["remaining_qty"] += remaining
            earliest = min(source["end_dates"], key=get_datetime) if source["end_dates"] else None
            row["overdue_count"] += int(overdue_days(earliest, remaining, source["status"] in ("Stopped", "Closed")) > 0)
        row["work_order_count"] = len(row.pop("work_orders"))
        row["approved_percent"] = min(100, (row["approved_qty"] + row["approved_loss_qty"]) / row["for_quantity"] * 100) if row["for_quantity"] else 0
        if cint(filters.only_overdue) and not row["overdue_count"]:
            continue
        if cint(filters.only_loss) and not (row["approved_loss_qty"] or row["draft_loss_qty"]):
            continue
        result.append(row)
    columns = [column("department_label", "生产部门", width=170),
               column("operation", "工序", "Link", "Operation", 140), column("stock_uom", "单位", "Link", "UOM", 80),
               column("source_status", "工序来源", width=145),
               column("work_order_count", "工单数", "Int"), column("source_operation_count", "工序任务数", "Int"),
               column("job_card_count", "正常报工卡数", "Int", width=135), column("for_quantity", "工序计划数量", "Float", width=140),
               column("approved_qty", "已审核完成", "Float"), column("approved_loss_qty", "已审核损耗", "Float"),
               column("draft_qty", "待审核完成", "Float"), column("draft_loss_qty", "待审核损耗", "Float"),
               column("remaining_qty", "尚未报完", "Float"), column("approved_percent", "审核结清 %", "Percent"),
               column("hours", "正常工时", "Float"), column("corrective_hours", "纠正工时", "Float"),
               column("corrective_job_card_count", "纠正卡数", "Int"), column("overdue_count", "逾期工序数", "Int", width=135)]
    return columns, result


def column(fieldname, label, fieldtype="Data", options=None, width=110):
    return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "options": options, "width": width}
