import frappe
from frappe.model.document import Document

from jifeng_erp.jushuitan import record_key


class JushuitanSettings(Document):
    def validate(self):
        if self.enabled:
            for field in ("app_key", "merchant_id", "company", "item_group", "stock_uom"):
                if not self.get(field):
                    frappe.throw(f"启用同步前请填写：{self.meta.get_label(field)}")
            if not self.get_password("app_secret", raise_exception=False) or not self.get_password("access_token", raise_exception=False):
                frappe.throw("启用同步前请配置应用密钥和商家授权 Token")
        if not 1 <= int(self.max_pages or 20) <= 20:
            frappe.throw("每轮请求页数必须在 1 到 20 之间")
        scope = record_key(self.environment, f"{self.app_key}|{self.merchant_id}|{self.company}")
        if self.sync_scope and self.sync_scope != scope:
            self.order_cursor, self.sku_watermark, self.sku_window_end, self.sku_page = "1", None, None, 1
            self.sku_window_begin = None
            self.last_result = "商家、应用、环境或公司已变化，同步游标重置；历史数据仍保留。"
        self.sync_scope = scope
        for host in (self.image_hosts or "").splitlines():
            if host.strip() and ("/" in host or ":" in host or "*" in host):
                frappe.throw("图片域名应只填写精确主机名，不含协议、路径、端口或通配符")
