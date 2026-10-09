import frappe
from frappe.model.document import Document


IMAGE_METADATA_FIELDS = ("source_image_candidates", "archived_image_source")


class JifengSKUMapping(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        self._validate_image_metadata(previous)
        if previous and self.item_code != previous.item_code:
            # Relinking must never turn a manually managed Item into a sync target.
            self.managed_item = 0
            self.archived_image = None
            self.source_image_candidates = None
            self.archived_image_source = None
        elif previous and not self.flags.from_sync:
            self.managed_item = previous.managed_item

    def _validate_image_metadata(self, previous):
        changed = [field for field in IMAGE_METADATA_FIELDS
                   if (self.get(field) or "") != (previous.get(field) or "" if previous else "")]
        if not changed:
            return
        # Incoming document flags are caller controlled. Only an internal,
        # request-local scope can authorize writes to image provenance.
        scope = getattr(frappe.flags, "jifeng_image_metadata_write", None)
        if (not isinstance(scope, dict) or not self.name
                or scope.get("mapping_name") != self.name
                or not isinstance(scope.get("fields"), (list, tuple))
                or any(field not in scope["fields"] for field in changed)):
            frappe.throw("图片来源追溯字段只能由商品同步或图片归档更新。")
        from jifeng_erp.images import validate_source_image_candidates, valid_image_source_url

        try:
            if "source_image_candidates" in changed:
                validate_source_image_candidates(self, self.source_image_candidates)
            if ("archived_image_source" in changed and self.archived_image_source
                    and not valid_image_source_url(self.archived_image_source)):
                raise ValueError("归档图片来源地址无效。")
        except ValueError as error:
            frappe.throw(str(error))
