"""Real image decoding plus scoped File/transaction models; no live upload."""

import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import unittest
from uuid import uuid4

from PIL import Image

import test_inventory_archive as fixture


class NativeFile(fixture.NativeDocument):
    def insert(self):
        env = self.environment
        self.check_permission("create")
        if env.fail_file:
            raise ValueError("native File failure")
        self.name = "FILE-" + uuid4().hex
        self.file_url = "/private/files/" + hashlib.sha256(self.content).hexdigest()[-6:] + "-" + self.file_name
        self["__islocal"] = 0
        self.modified = "2026-10-15T00:00:00"
        env.documents[("File", self.name)] = deepcopy(dict(self))
        env.blobs[self.file_url] = self.content
        env.frappe.db.after_rollback.add(lambda: env.blobs.pop(self.file_url, None))
        env.saves.append("File")
        if env.fail_after_file:
            env.fail_profile = True
        return self

    def is_downloadable(self):
        return self.name not in self.environment.download_denials

    @property
    def unique_url(self):
        return self.file_url + "?fid=" + self.name


class TestInventoryArchiveImages(unittest.TestCase):
    def setUp(self):
        self.env = fixture.TestInventoryArchive()
        self.env.setUp()
        self.addCleanup(self.env.doCleanups)
        env = self.env
        self.module, self.images = env.module, env.images
        env.fail_file, env.fail_after_file = False, False
        env.blobs, env.download_denials = {}, set()
        native_new, native_get = env.new_doc, env.get_doc
        env.frappe.new_doc.side_effect = lambda doctype: NativeFile(dict(native_new(doctype))) if doctype == "File" else native_new(doctype)
        env.frappe.get_doc.side_effect = lambda doctype, name, **kw: NativeFile(dict(native_get(doctype, name, **kw))) if doctype == "File" else native_get(doctype, name, **kw)

    def upload(self, file_name="picture.png", color="blue", format="PNG"):
        content = BytesIO()
        Image.new("RGB", (3, 2), color).save(content, format=format)
        return {"file_name": file_name, "content": base64.b64encode(content.getvalue()).decode("ascii")}

    def payload(self, new=False):
        value = self.env.creation_payload() if new else self.env.existing_payload()
        value.update(image_upload=self.upload(), image_change_request_id=str(uuid4()))
        return value

    def file_ids(self):
        return {name for (doctype, name) in self.env.documents if doctype == "File"}

    def test_new_archive_saves_item_profile_and_private_image_with_one_receipt(self):
        payload = self.payload(new=True)
        saved = self.module.save_archive(payload)
        self.assertEqual(saved["image"]["state"], "已上传")
        self.assertEqual(saved["image_change_request_id"], payload["image_change_request_id"])
        self.assertEqual(len(self.file_ids()), 1)
        attachment = self.env.documents["File", next(iter(self.file_ids()))]
        self.assertEqual((attachment["is_private"], attachment["attached_to_doctype"],
                          attachment["attached_to_name"], attachment["attached_to_field"]),
                         (1, self.module.PROFILE, saved["name"], "image"))
        self.assertNotIn("image", self.env.documents["Item", saved["item_code"]])
        counts = (len(self.env.saves), len(self.env.blobs), self.file_ids())
        self.assertEqual(self.module.save_archive(deepcopy(payload))["name"], saved["name"])
        self.assertEqual((len(self.env.saves), len(self.env.blobs), self.file_ids()), counts)
        resolved = self.module.resolve_creation(payload["request_id"], "物料")["archive"]
        self.assertEqual(resolved["image_change_request_id"], payload["image_change_request_id"])
        for replacement in (self.upload(color="red"), self.upload(file_name="other.png")):
            with self.assertRaises(ValueError):
                self.module.save_archive({**payload, "image_upload": replacement})

    def test_replace_and_clear_keep_shared_item_and_old_attachments(self):
        before = deepcopy(self.env.item)
        saved = self.module.save_archive(self.payload())
        previous = self.file_ids()
        replacement = self.module.save_archive({**self.payload(), "image_upload": self.upload(color="red")})
        self.assertEqual(len(self.file_ids()), 2)
        cleared = self.module.save_archive(self.env.existing_payload(profile={"image": None}, image_change_request_id=str(uuid4())))
        self.assertEqual(cleared["image"]["state"], "未上传")
        self.assertIsNone(cleared["profile"]["image"])
        self.assertNotEqual(cleared["image_change_request_id"], saved["image_change_request_id"])
        self.assertTrue(previous.issubset(self.file_ids()))
        self.assertEqual(len(self.env.blobs), 2)
        self.assertEqual(self.env.documents["Item", "BUSINESS-ITEM"], before)
        self.assertEqual(replacement["image"]["state"], "已上传")

    def test_native_file_and_later_profile_failure_roll_back_new_business_rows_and_blob(self):
        for failure in ("fail_file", "fail_after_file"):
            setattr(self.env, failure, True)
            before = deepcopy(self.env.documents)
            with self.assertRaises(ValueError):
                self.module.save_archive(self.payload(new=True))
            self.assertEqual(self.env.documents, before)
            self.assertEqual(self.env.blobs, {})
            self.assertEqual(self.env.frappe.db.after_rollback._functions, [])
            self.assertIsNone(self.images._CHANGING.get())
            self.env.fail_profile = False
            setattr(self.env, failure, False)

    def test_upload_role_field_company_and_versions_are_checked_before_file_insert(self):
        payload = self.payload()
        for doctype, permission in ((self.module.PROFILE, "write"), ("Item", "read"), ("File", "create")):
            self.env.denials.add((doctype, permission))
            with self.assertRaises(PermissionError):
                self.module.save_archive(payload)
            self.assertEqual(self.file_ids(), set())
            self.env.denials.clear()
        for doctype, permission, field in ((self.module.PROFILE, "read", "image"),
                                          (self.module.PROFILE, "write", "image"),
                                          (self.module.PROFILE, "read", self.images.RECEIPT),
                                          ("File", "read", "attached_to_name")):
            self.env.hidden[(doctype, permission)] = {field}
            with self.assertRaises(PermissionError):
                self.module.save_archive(payload)
            self.assertEqual(self.file_ids(), set())
            self.env.hidden.clear()
        self.env.denied_companies.add("JF")
        with self.assertRaises(PermissionError):
            self.module.save_archive(payload)
        self.env.denied_companies.clear()
        with self.assertRaises(RuntimeError):
            self.module.save_archive({**payload, "item_version": "old"})
        self.assertEqual(self.file_ids(), set())

    def test_real_decoder_rejects_chinese_names_non_images_and_unbounded_data(self):
        valid = self.upload()
        bad = [dict(valid, file_name=name) for name in ("图片.png", "\U00020000.png", "\u3007.png", "\u2f00.png",
                                                      "../a.png", "a\\b.png", "a%20.png", " a.png", "a.svg", "a.png\x00")]
        bad += [dict(valid, content=value) for value in ("", "not base64", valid["content"] + "=", True)]
        bad += [dict(valid, file_name="mismatch.jpg"), dict(valid, content=base64.b64encode(b"not an image").decode()),
                {**valid, "unexpected": "field"}]
        for value in bad:
            with self.subTest(file_name=value.get("file_name")), self.assertRaises(ValueError):
                self.images.prepare_upload(value)
        self.env.maximum_image_bytes = 4
        with self.assertRaises(ValueError):
            self.images.prepare_upload(valid)
        self.assertEqual(self.file_ids(), set())

    def test_file_scope_read_download_and_field_permissions_guard_preview(self):
        saved = self.module.save_archive(self.payload())
        file_id = next(iter(self.file_ids()))
        attachment = self.env.documents["File", file_id]
        for key, changed in (("attached_to_name", "OTHER"), ("attached_to_field", "notes"),
                             ("attached_to_doctype", "Item"), ("is_private", 0)):
            old = attachment[key]
            attachment[key] = changed
            self.assertEqual(self.module.read_archive("PROFILE", "物料")["image"]["url"], "")
            attachment[key] = old
        self.env.denied_names.add(file_id)
        result = self.module.read_archive("PROFILE", "物料")
        self.assertIsNone(result["profile"]["image"])
        self.assertEqual(result["image"]["url"], "")
        self.env.denied_names.clear()
        self.env.download_denials.add(file_id)
        self.assertEqual(self.module.read_archive("PROFILE", "物料")["image"]["url"], "")
        self.env.download_denials.clear()
        self.assertEqual(self.module.read_archive("PROFILE", "物料")["image"], saved["image"])
        self.env.hidden[(self.module.PROFILE, "read")] = {"image"}
        result = self.module.read_archive("PROFILE", "物料")
        self.assertNotIn("image", result)
        self.assertNotIn("image_change_request_id", result)

    def test_native_receipt_cannot_be_forged_and_copy_never_reuses_private_binding(self):
        saved = self.module.save_archive(self.payload())
        document = self.env.get_doc(self.module.PROFILE, "PROFILE")
        document.image_change_request_id = str(uuid4())
        with self.assertRaises(ValueError):
            document.save()
        document = self.env.get_doc(self.module.PROFILE, "PROFILE")
        document.image = None
        document.save()
        self.assertIsNone(self.env.documents[self.module.PROFILE, "PROFILE"].get(self.images.RECEIPT))
        self.env.documents[self.module.PROFILE, "PROFILE"]["image"] = saved["profile"]["image"]
        copied = self.module.init_archive("物料", copy_from="PROFILE")
        self.assertIsNone(copied["profile"]["image"])
        self.assertIsNone(copied["image_change_request_id"])

    def test_unknown_or_conflicting_picture_parameters_never_save(self):
        for changes in ({"image_change_request_id": None}, {"profile": {"image": None}},
                        {"image_upload": None, "image_change_request_id": str(uuid4())}):
            payload = {**self.payload(), **changes}
            with self.assertRaises(ValueError):
                self.module.save_archive(payload)
        self.assertEqual(self.file_ids(), set())

    def test_masked_profile_or_file_fields_do_not_expose_images_or_enable_upload(self):
        self.module.save_archive(self.payload())
        for doctype, name in ((self.module.PROFILE, "image"), ("File", "file_url")):
            field = self.env.metas[doctype].get_field(name)
            field.mask = 1
            result = self.module.read_archive("PROFILE", "物料")
            if doctype == self.module.PROFILE:
                self.assertNotIn("image", result)
            else:
                self.assertEqual(result["image"]["url"], "")
                self.assertFalse(result["image_options"]["can_choose"])
            field.mask = 0

    def test_replacing_an_unavailable_picture_saves_all_fields_once(self):
        self.env.profile["image"] = "/private/files/missing-old.png"
        self.assertEqual(self.module.read_archive("PROFILE", "物料")["image"]["state"], "附件不可用")
        payload = self.payload()
        payload["profile"] = {"display_name": "补图后的名称"}
        saved = self.module.save_archive(payload)
        self.assertEqual(saved["profile"]["display_name"], "补图后的名称")
        self.assertEqual(saved["image"]["state"], "已上传")
        self.assertEqual(self.env.saves.count(self.module.PROFILE), 1)


if __name__ == "__main__":
    unittest.main()
