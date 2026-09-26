from frappe.model.document import Document


class JifengSKUMapping(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and self.item_code != previous.item_code:
            # Relinking must never turn a manually managed Item into a sync target.
            self.managed_item = 0
            self.archived_image = None
        elif previous and not self.flags.from_sync:
            self.managed_item = previous.managed_item
