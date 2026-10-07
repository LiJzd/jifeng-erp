"""Wake the existing native valuation scheduler after linked stock commits."""

import frappe


_METHOD = "erpnext.stock.doctype.repost_item_valuation.repost_item_valuation."
_STOCK_TYPES = {"Stock Entry", "Subcontracting Receipt", "Delivery Note", "Sales Invoice"}


def _enqueue_native_reposting():
    # Stock is already committed. A scheduling failure must never tell callers
    # that their stock submission failed, or invite them to repeat it.
    try:
        from frappe.utils.scheduler import is_scheduler_disabled

        if frappe.conf.get("maintenance_mode") or is_scheduler_disabled():
            return
        parallel = frappe.db.get_single_value("Stock Reposting Settings", "enable_parallel_reposting")
        method = _METHOD + ("run_parallel_reposting" if parallel else "repost_entries")
        name = frappe.db.get_value("Scheduled Job Type", {"method": method}, "name")
        if not name:
            raise RuntimeError("missing_native_reposting_schedule")
        job = frappe.get_doc("Scheduled Job Type", name)
        if job.get("stopped"):
            return
        if job.get("method") != method or job.get("server_script"):
            raise RuntimeError("unexpected_native_reposting_schedule")
        # Native enqueue owns rq_job_id, queue, deduplication and worker entry.
        # The native repost method still checks Stock Reposting Settings' window.
        job.enqueue(force=True)
    except Exception as exc:
        try:
            frappe.log_error(title="流程库存重过账未能立即排队",
                message=f"{type(exc).__name__}：原生库存任务调度失败，原库存操作已提交；请核对调度器与队列，勿重复提交。")
        except Exception:
            # Preserve the committed operation even when the normal log sink is
            # unavailable. The unchanged native hourly schedule remains active.
            pass


def schedule_flow_stock_reposting(doc, method=None):
    """An internal doc event, never a client Repost permission or direct runner."""
    if doc.doctype not in _STOCK_TYPES or (method, doc.docstatus) not in {("on_submit", 1), ("on_cancel", 2)}:
        return
    if doc.doctype == "Sales Invoice" and not doc.get("update_stock"):
        return
    if not doc.get("jf_material_issue_flow") and not any(row.get("jf_flow_order") for row in doc.get("items") or []):
        return
    # Register per transaction, with no request-wide flag that could survive a
    # rollback. Native SJT deduplication handles multiple linked stock documents.
    frappe.db.after_commit.add(_enqueue_native_reposting)
