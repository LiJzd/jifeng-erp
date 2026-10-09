"""Checkpointed, one-way Jushuitan ingestion. Only sync entrypoints are public."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, now_datetime

from jifeng_erp.jushuitan import Client, JushuitanError, order_snapshot, record_key, safe_image_url
from jifeng_erp.images import (IMAGE_JOB_TIMEOUT, _require_image_metadata_fields,
                               image_metadata_write, source_image_candidates_from_sku)
from jifeng_erp.workflow import BLOCKED_STATUSES


def client(settings):
    return Client(settings.app_key, settings.get_password("app_secret"),
                  settings.get_password("access_token"), settings.environment)


@frappe.whitelist()
def enqueue_sync():
    frappe.only_for("System Manager")
    settings = frappe.get_single("Jushuitan Settings")
    settings.validate()
    if not settings.enabled:
        frappe.throw("请先启用聚水潭同步")
    frappe.enqueue("jifeng_erp.sync.run_sync", queue="long", timeout=1800, enqueue_after_commit=True)
    return "同步任务已排队，可在同步记录中查看结果"


@frappe.whitelist()
def test_connection():
    frappe.only_for("System Manager")
    settings = frappe.get_single("Jushuitan Settings")
    settings.validate()
    try:
        data = client(settings).orders(settings.order_cursor or 1)
    except JushuitanError as exc:
        frappe.throw(str(exc))
    for row in data.get("orders") or []:
        if str(row.get("co_id")) != str(settings.merchant_id):
            frappe.throw("授权商家与配置的 co_id 不一致")
    count = len(data.get("orders") or [])
    return {"message": "订单查询成功" if count else "订单查询成功，但空结果尚不能校验商家数据覆盖", "returned_orders": count}


@frappe.whitelist(methods=["POST"])
def refresh_authorization():
    frappe.only_for("System Manager")
    with frappe.cache.lock("jifeng:jushuitan:sync", timeout=120, blocking_timeout=1):
        settings = frappe.get_single("Jushuitan Settings")
        if settings.app_type != "自有商城":
            frappe.throw("官方刷新接口仅适用于自有商城应用；服务商应用需重新授权")
        if not settings.token_expires_at or get_datetime(settings.token_expires_at) <= now_datetime():
            frappe.throw("请先核实授权到期时间；已过期 Token 需要重新授权")
        token = settings.get_password("refresh_token", raise_exception=False)
        if not token:
            frappe.throw("请配置 Refresh Token")
        try:
            data = client(settings).refresh(token)
        except JushuitanError as exc:
            frappe.throw(str(exc))
        settings.access_token, settings.refresh_token = data["access_token"], data["refresh_token"]
        settings.token_expires_at = now_datetime() + timedelta(seconds=int(data["expires_in"]))
        settings.save()
        return "授权已刷新并加密保存"


@frappe.whitelist(methods=["POST"])
def acquire_initial_authorization():
    """Explicitly acquire credentials for a self-owned Jushuitan app."""
    frappe.only_for("System Manager")
    with frappe.cache.lock("jifeng:jushuitan:sync", timeout=120, blocking_timeout=1):
        settings = frappe.get_single("Jushuitan Settings")
        if settings.app_type != "自有商城":
            frappe.throw("初始授权接口仅适用于自有商城应用；服务商应用需按官方流程授权")
        app_secret = settings.get_password("app_secret", raise_exception=False)
        if not settings.app_key or not app_secret:
            frappe.throw("请先配置 App Key 和 App Secret")
        try:
            data = Client(settings.app_key, app_secret, None, settings.environment).get_initial_token()
        except JushuitanError as exc:
            frappe.throw(str(exc))
        # Persist only a complete, validated response. Password fields are encrypted
        # by Frappe when the singleton is saved; credentials never enter messages.
        settings.access_token = data["access_token"]
        settings.refresh_token = data["refresh_token"]
        settings.token_expires_at = now_datetime() + timedelta(seconds=int(data["expires_in"]))
        settings.save()
        return "初始授权已加密保存；后续请在到期前手动刷新"


def scheduled_sync():
    settings = frappe.get_single("Jushuitan Settings")
    if settings.enabled and settings.automatic_sync:
        frappe.enqueue("jifeng_erp.sync.run_sync", queue="long", timeout=1800)


def run_sync():
    # Redis lock prevents overlapping manual/scheduled workers from moving cursors.
    with frappe.cache.lock("jifeng:jushuitan:sync", timeout=2100, blocking_timeout=1):
        settings = frappe.get_single("Jushuitan Settings")
        if not settings.enabled:
            return
        settings.validate()
        api = client(settings)
        log = frappe.get_doc({"doctype": "Jifeng Sync Run", "status": "运行中", "started_at": now_datetime()}).insert(ignore_permissions=True)
        frappe.db.commit()
        try:
            sync_orders(settings, api, log)
            sync_skus(settings, api, log)
            log.status, log.message = "完成", "本轮处理完成；已保存分页进度，下轮继续增量同步。"
        except Exception as exc:
            frappe.db.rollback()
            log.reload()
            log.status = "失败"
            log.message = str(exc) if isinstance(exc, JushuitanError) else f"同步失败（{type(exc).__name__}），检查配置、商品映射与主数据。已提交页进度保留。"
        log.finished_at = now_datetime()
        log.save(ignore_permissions=True)
        frappe.db.set_single_value("Jushuitan Settings", "last_result", f"{log.name}：{log.status}。{log.message}")
        frappe.db.commit()


def _page_list(data, key):
    rows = data.get(key)
    if not isinstance(rows, list) or not isinstance(data.get("has_next"), bool):
        raise JushuitanError(f"查询响应缺少 {key} 列表，未推进同步进度")
    return rows


def sync_orders(settings, api, log):
    cursor = int(settings.order_cursor or 1)
    for _ in range(int(settings.max_pages or 20)):
        data = api.orders(cursor)
        raw_orders = _page_list(data, "orders")
        if not raw_orders:
            break
        snapshots = [order_snapshot(r) for r in raw_orders]
        for snapshot in snapshots:
            if snapshot["merchant_id"] != str(settings.merchant_id):
                raise JushuitanError("订单商家编号与配置不一致，未推进同步进度")
        sku_ids = sorted({r["sku_id"] for s in snapshots for r in s["items"]})
        missing = [sku for sku in sku_ids if not frappe.db.exists("Jifeng SKU Mapping", record_key(settings.sync_scope, sku))]
        for offset in range(0, len(missing), 20):
            for sku in _page_list(api.skus(sku_ids=missing[offset:offset + 20]), "datas"):
                upsert_sku(settings, sku)
        for snapshot in snapshots:
            upsert_order(settings, snapshot)
        next_cursor = max(int(s["source_cursor"]) for s in snapshots)
        if next_cursor < cursor:
            raise JushuitanError("订单游标倒退，未推进同步进度")
        if next_cursor == cursor and data.get("has_next"):
            raise JushuitanError("订单游标未推进但仍有下一页，需要核实接口边界；未跳过数据")
        settings.order_cursor = str(next_cursor)
        settings.db_set("order_cursor", str(next_cursor))
        log.order_count = (log.order_count or 0) + len(snapshots)
        log.save(ignore_permissions=True)
        frappe.db.commit()
        if next_cursor == cursor or not data.get("has_next"):
            break
        cursor = next_cursor


def sync_skus(settings, api, log):
    target = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None) - timedelta(minutes=2)
    watermark = get_datetime(settings.sku_watermark or settings.sku_begin or (target - timedelta(days=7)))
    if watermark >= target:
        return
    begin = get_datetime(settings.sku_window_begin) if settings.sku_window_begin else (watermark - timedelta(minutes=2) if settings.sku_watermark else watermark)
    end = get_datetime(settings.sku_window_end) if settings.sku_window_end else min(watermark + timedelta(days=6), target)
    page = int(settings.sku_page or 1)
    for _ in range(int(settings.max_pages or 20)):
        data = api.skus(begin=str(begin.replace(microsecond=0)), end=str(end.replace(microsecond=0)), page=page)
        rows = _page_list(data, "datas")
        for raw in rows:
            upsert_sku(settings, raw)
        log.sku_count = (log.sku_count or 0) + len(rows)
        if data.get("has_next"):
            if not rows:
                raise JushuitanError("商品接口空页仍标记下一页，停止推进")
            page += 1
            values = {"sku_window_begin": begin, "sku_window_end": end, "sku_page": page}
        else:
            values = {"sku_watermark": end, "sku_window_begin": None, "sku_window_end": None, "sku_page": 1}
        for field, value in values.items():
            settings.db_set(field, value)
        log.save(ignore_permissions=True)
        frappe.db.commit()
        if not data.get("has_next"):
            if end >= target:
                break
            begin, end, page = end - timedelta(minutes=2), min(end + timedelta(days=6), target), 1


def upsert_sku(settings, raw, *, image_origin="sku"):
    _require_image_metadata_fields(frappe)
    sku_id = str(raw.get("sku_id") or "")
    if not sku_id:
        raise JushuitanError("商品响应缺少 sku_id")
    key = record_key(settings.sync_scope, sku_id)
    existing = frappe.db.exists("Jifeng SKU Mapping", key)
    mapping = frappe.get_doc("Jifeng SKU Mapping", key) if existing else frappe.new_doc("Jifeng SKU Mapping")
    old_supplier_sku_id = mapping.supplier_sku_id
    old_source_specification = mapping.source_specification
    raw_image_present = "pic_big" in raw or "pic" in raw
    raw_image = raw.get("pic_big") or raw.get("pic")
    image = safe_image_url(raw_image)
    image_changed = raw_image_present and (
        image != (mapping.source_image or "") or bool(raw_image and not image)
    )
    if not existing:
        # Never silently take over an existing manually maintained Item.
        item_code = sku_id if not frappe.db.exists("Item", sku_id) else "JST-" + key[:20]
        source_uom = str(raw.get("unit") or settings.stock_uom)
        if not frappe.db.exists("UOM", source_uom):
            frappe.get_doc({"doctype": "UOM", "uom_name": source_uom}).insert(ignore_permissions=True)
        item = frappe.get_doc({
            "doctype": "Item", "item_code": item_code, "item_name": raw.get("name") or sku_id,
            "item_group": settings.item_group, "stock_uom": source_uom,
            "description": frappe.utils.escape_html(str(raw.get("properties_value") or raw.get("name") or sku_id)),
            # External URLs are only source metadata. The Item image is set by
            # archive_image after host, format, size, and content checks pass.
            "image": "", "is_stock_item": 1,
        }).insert(ignore_permissions=True)
        mapping.update({"sync_key": key, "scope": settings.sync_scope, "sku_id": sku_id,
                        "item_code": item.name, "managed_item": 1})
    elif mapping.managed_item:
        item = frappe.get_doc("Item", mapping.item_code)
        item.item_name = raw.get("name") or item.item_name
        if image_changed and isinstance(item.image, str) and item.image.startswith(("http://", "https://")):
            # Never leave a remote URL on Item; retain validated local images
            # until a replacement has been downloaded and checked.
            item.image = ""
        item.save(ignore_permissions=True)
    incoming_supplier_sku_id = raw.get("supplier_sku_id")
    incoming_specification = raw.get("properties_value")
    if incoming_supplier_sku_id not in (None, ""):
        mapping.supplier_sku_id = str(incoming_supplier_sku_id)
    mapping.supplier_name = raw.get("supplier_name") or mapping.supplier_name
    mapping.source_modified = raw.get("modified") or mapping.source_modified
    candidate_fields = []
    if raw_image_present and image_origin == "sku":
        candidates = source_image_candidates_from_sku(mapping, raw)
        if mapping.source_image_candidates != candidates:
            mapping.source_image_candidates = candidates
            candidate_fields.append("source_image_candidates")
    if (candidate_fields and not image_changed and mapping.image_sync_status == "失败"
            and (mapping.image_sync_error or "").startswith((
                "图片服务器返回 HTTP 403，已停止下载。", "图片服务器返回 HTTP 404，已停止下载。"))):
        # A newly observed real candidate can repair a previously missing main
        # image during normal sync. Unchanged failures are not re-enqueued.
        mapping.image_sync_status = "待处理"
        mapping.image_sync_error = ""
        mapping.image_sync_attempted_at = now_datetime()
    if image_changed:
        mapping.source_image = image or None
        # Keep the last successful attachment as the ownership baseline while
        # a replacement is pending or fails. The worker replaces it only if
        # the Item still points at that attachment.
        if image:
            mapping.image_sync_status = "待处理"
        elif raw_image:
            mapping.image_sync_status = "失败"
        else:
            mapping.image_sync_status = "未配置"
        mapping.image_sync_error = "" if image or not raw_image else "源图片链接无效，未下载。"
        mapping.image_sync_attempted_at = now_datetime() if image else None
    elif not mapping.source_image:
        mapping.image_sync_status = "未配置"
        mapping.image_sync_error = ""
    elif mapping.archived_image:
        if mapping.image_sync_status in (None, "", "未配置"):
            # Backfill legacy state only when it was never recorded. A
            # pending/failed current source must keep that state even if an
            # older archived image is retained as the ownership baseline.
            mapping.image_sync_status = "已完成"
            mapping.image_sync_error = ""
        elif mapping.image_sync_status == "已完成":
            mapping.image_sync_error = ""
    elif mapping.image_sync_status in (None, "", "未配置"):
        # Legacy mappings have no image state yet; let the worker process them once.
        mapping.image_sync_status = "待处理"
        mapping.image_sync_attempted_at = mapping.image_sync_attempted_at or now_datetime()

    # Existing mappings use their previous source values as baselines. On old
    # records without a specification baseline, only fill blank profile fields.
    old_supplier_baseline = old_supplier_sku_id
    old_specification_baseline = old_source_specification
    profile_name = frappe.db.exists("Jifeng Product Profile", {"item_code": mapping.item_code})
    if profile_name:
        profile = frappe.get_doc("Jifeng Product Profile", profile_name)
        if profile.company != settings.company:
            raise JushuitanError("商品档案公司与当前同步公司不一致，已停止更新。")
        profile_changed = False
        if incoming_supplier_sku_id not in (None, ""):
            profile_changed |= _sync_profile_source_value(profile, "supplier_product_code", old_supplier_baseline,
                                                          incoming_supplier_sku_id)
        if incoming_specification not in (None, ""):
            profile_changed |= _sync_profile_source_value(profile, "specification", old_specification_baseline,
                                                          incoming_specification)
        if profile_changed:
            profile.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "Jifeng Product Profile", "item_code": mapping.item_code,
                        "company": settings.company, "supplier_product_code": incoming_supplier_sku_id,
                        "specification": str(incoming_specification or "")}).insert(ignore_permissions=True)
    # Empty/omitted source values are not authoritative. In particular, the
    # order fallback only has a partial SKU payload, and platform snapshots can
    # also temporarily omit a specification. Keep the last known baseline so
    # a later complete SKU sync can safely distinguish source-owned from
    # manually edited profile values.
    if incoming_specification not in (None, ""):
        mapping.source_specification = str(incoming_specification)
    mapping.flags.from_sync = True
    with image_metadata_write(mapping.name if existing else key, candidate_fields, frappe_context=frappe):
        mapping.save(ignore_permissions=True)
    if image_changed and not image:
        return mapping.item_code
    if mapping.source_image and mapping.image_sync_status == "待处理":
        if not settings.image_hosts:
            mapping.image_sync_status = "失败"
            mapping.image_sync_error = "未配置图片允许域名，未下载。"
            mapping.save(ignore_permissions=True)
            return mapping.item_code
        frappe.enqueue("jifeng_erp.images.archive_image", mapping_name=mapping.name,
                       queue="long", timeout=IMAGE_JOB_TIMEOUT, enqueue_after_commit=True)
    return mapping.item_code


def _sync_profile_source_value(profile, fieldname, previous_source, incoming_source):
    """Update only blank or still source-owned profile values."""
    current = profile.get(fieldname)
    previous_source = str(previous_source or "")
    incoming_source = str(incoming_source or "")
    if not current or (previous_source and current == previous_source):
        if current != incoming_source:
            profile.set(fieldname, incoming_source)
            return True
    return False


def upsert_order(settings, snapshot):
    key = record_key(settings.sync_scope, snapshot["external_order_id"])
    exists = frappe.db.exists("Jifeng External Order", key)
    order = frappe.get_doc("Jifeng External Order", key, for_update=True) if exists else frappe.new_doc("Jifeng External Order")
    if exists and order.company != settings.company:
        raise JushuitanError("已有来源订单公司与当前同步公司不一致，停止更新")
    if exists and int(order.source_cursor or 0) > int(snapshot["source_cursor"]):
        return
    manual_requirements = {}
    for row in (getattr(order, "items", None) or []):
        line_id = str(row.get("external_line_id") or "")
        if not line_id or line_id in manual_requirements:
            raise JushuitanError("已有订单明细缺少唯一来源行号，停止同步以保留人工要求")
        manual_requirements[line_id] = {
            "jf_requirements": row.get("jf_requirements"),
            "jf_packaging": row.get("jf_packaging"),
        }
    order.update({"sync_key": key, "scope": settings.sync_scope, "company": settings.company})
    for field, value in snapshot.items():
        if field != "items":
            order.set(field, value)
    order.set("items", [])
    active_lines = set()
    for row in snapshot["items"]:
        sku_key = record_key(settings.sync_scope, row["sku_id"])
        item_code = frappe.db.get_value("Jifeng SKU Mapping", sku_key, "item_code")
        if not item_code:
            item_code = upsert_sku(settings, {"sku_id": row["sku_id"], "name": row["item_name"],
                                              "pic": row["image_url"]}, image_origin="order")
        flow_name = frappe.db.get_value("Jifeng Flow Order", {"source_key": record_key(key, row["external_line_id"])}, "name")
        active_lines.add(row["external_line_id"])
        if flow_name:
            flow = frappe.get_doc("Jifeng Flow Order", flow_name)
            changed = float(flow.source_qty or 0) != float(row["qty"]) or flow.item_code != item_code
            if changed or order.source_status in BLOCKED_STATUSES or row["source_status"] == "Cancelled":
                flow.source_changed = 1
                flow.source_change_note = "源商品、数量或取消/拆合单状态发生变化，请核对已开的采购与加工单。"
            flow.source_qty = row["qty"]
            flow.source_status = "Cancelled" if row["source_status"] == "Cancelled" else order.source_status
            flow.flags.from_sync = True
            flow.save(ignore_permissions=True)
        order.append("items", {
            **row, **manual_requirements.get(str(row["external_line_id"]), {}),
            "item_code": item_code, "flow_order": flow_name,
        })
    order.flags.from_sync = True
    order.save(ignore_permissions=True)
    for flow in frappe.get_all("Jifeng Flow Order", filters={"external_order": key}, fields=["name", "external_line_id"]):
        if flow.external_line_id not in active_lines:
            frappe.db.set_value("Jifeng Flow Order", flow.name, {"source_changed": 1, "source_status": "Cancelled", "source_change_note": "源订单已移除该明细，请人工核对。"})
    statuses = {s.strip() for s in (settings.flow_statuses or "").splitlines() if s.strip()}
    positive = all(float(r.qty or 0) > 0 for r in order.items if r.source_status != "Cancelled")
    if settings.auto_create_flows and positive and order.source_status in statuses and order.source_status not in BLOCKED_STATUSES:
        order.make_flows()
