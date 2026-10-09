"""Extend the native ordinary-production progress with real loss issues only."""

from collections import defaultdict

from frappe.utils import flt

from jifeng_erp.production_loss import (
    _loss_documents, _qty, get_booked_loss_for_job_card, get_booked_loss_for_work_order,
    is_loss_entry, prepare_loss_stock_reservations,
)


class ProductionLossWorkOrderMixin:
    def _process_loss_qty(self):
        native = super()._process_loss_qty()
        if self.get("track_semi_finished_goods") or not self.get("name") or self.is_new():
            return native
        return flt(native) + float(get_booked_loss_for_work_order(self.name))

    def update_consumed_qty_for_required_items(self):
        super().update_consumed_qty_for_required_items()
        if self.get("track_semi_finished_goods") or not self.get("name") or self.is_new():
            return
        additional = defaultdict(float)
        for doc in _loss_documents(work_order=self.name):
            for row in doc.items:
                additional[row.item_code] += float(_qty(row.transfer_qty))
        warehouse = self.get("wip_warehouse")
        if self.get("skip_transfer") and not self.get("from_wip_warehouse"):
            warehouse = None
        for item in self.required_items:
            if not additional[item.item_code]:
                continue
            consumed = flt(item.consumed_qty) + additional[item.item_code]
            item.db_set("consumed_qty", consumed, update_modified=False)
            if self.get("reserve_stock"):
                self.update_consumed_qty_in_stock_reservation(item, consumed, warehouse or item.source_warehouse)


class ProductionLossJobCardMixin:
    def get_consumed_process_loss(self):
        native = super().get_consumed_process_loss()
        if (self.get("track_semi_finished_goods") or self.get("is_subcontracted")
                or not self.get("name") or self.is_new()):
            return native
        return flt(native) + float(get_booked_loss_for_job_card(self.name))


class ProductionLossStockEntryMixin:
    def update_stock_ledger(self, *args, **kwargs):
        if is_loss_entry(self):
            prepare_loss_stock_reservations(self)
        return super().update_stock_ledger(*args, **kwargs)
