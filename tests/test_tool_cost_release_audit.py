"""Failure gates for the protected two-table release; no SQL or network.

The compressed static fixture contains full raw metadata/indexes from the
independent native reference, the 932 prior physical names and the OLD Git hooks.
It contains no business rows or credentials. It is not generated from a target
installation or from the audit implementation. Snapshot counters below are
synthetic collector projections; the native fixture proves defaults and layout.
"""

import base64
import ast
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tool_cost_release_audit", ROOT / "deploy/erp-test/tool_cost_release_audit.py"
)
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)
START = datetime(2026, 10, 3, 1)
MID = datetime(2026, 10, 3, 1, 0, 1)
END = datetime(2026, 10, 3, 1, 0, 2)
# Native reference: tool-cost-release-native-reference-20261003-r1.
# Physical names: tool-cost-native-install-20261003-r1/before.json.
FIXTURE_SHA256 = "da1f1a78410cf2f0b3335fbba59c98eb60dacee81e9b81c81588920a1b9d78f8"
FIXTURE_ZLIB_BASE64 = (
    "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"
    "OFi2Q7uD8sDAHrv6/wEvVHkm"
)


def fixture():
    payload = zlib.decompress(base64.b64decode(FIXTURE_ZLIB_BASE64))
    assert hashlib.sha256(payload).hexdigest() == FIXTURE_SHA256
    return json.loads(payload)


def schemas():
    return {
        name: json.loads((ROOT / path.removeprefix("app/")).read_text(encoding="utf-8"))
        for name, path in A.SCHEMA_PATHS.items()
    }


def model():
    reference = fixture()
    installed = copy.deepcopy(reference["installed"])
    before = {"tables": {}, "metadata": {}, "sequences": sorted(A.SEQUENCES)}
    for name in reference["baseline_names"]:
        before["tables"][name] = {
            "engine": "MyISAM" if name in A.MYISAM else "InnoDB",
            "rows": 1 if name in A.SEQUENCES else 0,
            "sha": A.fingerprint(["protected", name]),
            "schema": A.fingerprint(["schema", name]),
            "indexes": A.digest_rows([])
            if name in A.SEQUENCES
            else A.fingerprint(["index", name]),
        }
    for table in A.META:
        before["tables"][table].update(rows=0, sha=A.digest_rows([]))
        before["metadata"][table] = {
            "columns": reference["columns"][table],
            "outside": {"rows": 0, "sha": A.digest_rows([])},
            "parents": {name: [] for name in A.TARGETS},
        }
    after = copy.deepcopy(before)
    for target, metadata in installed.items():
        for section in (
            "doctype",
            "fields",
            "permissions",
            "actions",
            "links",
            "states",
        ):
            rows = [metadata[section]] if section == "doctype" else metadata[section]
            for row in rows:
                row["creation"] = MID
                row["modified"] = MID
                row["owner"] = "Administrator"
                row["modified_by"] = "Administrator"
                row["docstatus"] = 0
        for table, section in A.META.items():
            rows = [metadata["doctype"]] if section is None else metadata[section]
            after["metadata"][table]["parents"][target] = rows
    for table in A.META:
        rows = [
            row
            for owned in after["metadata"][table]["parents"].values()
            for row in owned
        ]
        after["tables"][table].update(rows=len(rows), sha=A.digest_rows(rows))
    for table, layout in A.APPROVED_NEW_TABLES.items():
        after["tables"][table] = {
            "engine": "InnoDB",
            "rows": 0,
            "sha": A.digest_rows([]),
            **layout,
        }
    return before, after, schemas(), installed


def verify(arguments):
    return A.verify_snapshot_delta(
        *arguments, START, END, "Administrator", dict(A.MIGRATION_HASH)
    )


def package():
    reference = fixture()
    old = reference["old_hooks"].encode()
    # Git package bytes use LF, while the user's checkout can contain CRLF.
    current = (ROOT / "jifeng_erp/hooks.py").read_bytes().replace(b"\r\n", b"\n")
    tree = ast.parse(current)
    paths = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.List):
            if any(
                isinstance(target, ast.Name)
                and target.id in {"app_include_css", "app_include_js"}
                for target in statement.targets
            ):
                paths.extend(ast.literal_eval(statement.value))
    contents = {"app/jifeng_erp/hooks.py": current}
    for url in paths:
        path = "app/jifeng_erp/public/" + url.removeprefix("/assets/jifeng_erp/")
        contents[path] = (ROOT / path.removeprefix("app/")).read_bytes()
    for path in A.SCHEMA_PATHS.values():
        for controller in (path, path[:-5] + ".py"):
            contents[controller] = (ROOT / controller.removeprefix("app/")).read_bytes()
        init = str(Path(path).parent / "__init__.py").replace("\\", "/")
        contents[init] = b""
    native_js = A.SCHEMA_PATHS[A.TARGETS[0]][:-5] + ".js"
    contents[native_js] = (ROOT / native_js.removeprefix("app/")).read_bytes()
    for file in ("tool_cost_sources.py", "tool_cost_validation.py"):
        contents["app/jifeng_erp/" + file] = (ROOT / "jifeng_erp" / file).read_bytes()
    for path in (
        "app/.git/HEAD",
        "app/.git/shallow",
        "app/.git/refs/tags/jifeng-release-pinned",
    ):
        contents[path] = (A.NEW + "\n").encode()
    contents["app/.git/config"] = b"[core]\n    bare = false\n"
    # Pack identity/content must also be checked by the caller's git verifier.
    base = "app/.git/objects/pack/pack-" + "1" * 40
    contents[base + ".pack"] = b"static pack placeholder"
    contents[base + ".idx"] = b"static index placeholder"
    inventory = {"app": {"kind": "directory", "mode": 0o755}}
    entries = []
    for path, content in contents.items():
        entries.append(
            {
                "path": path,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": 0o644,
            }
        )
        inventory[path] = {"kind": "file", "mode": 0o644, "content": content}
        for parent in Path(path).parents:
            if str(parent) != ".":
                inventory[str(parent).replace("\\", "/")] = {
                    "kind": "directory",
                    "mode": 0o755,
                }
    hooks = [
        url
        + "?v="
        + hashlib.sha256(
            contents["app/jifeng_erp/public/" + url.removeprefix("/assets/jifeng_erp/")]
        ).hexdigest()[:16]
        for url in paths
    ]
    return {"commit": A.NEW, "files": entries, "hooks": hooks}, inventory, old


class ToolCostReleaseAuditTests(unittest.TestCase):
    def test_independent_native_reference_and_exact_empty_delta(self):
        arguments = model()
        self.assertEqual(A.metadata_signature(arguments[3]), A.NATIVE_METADATA_SHA256)
        result = verify(arguments)
        self.assertTrue(result["passed"])
        self.assertEqual(
            (result["protected_objects"], result["total_objects"]), (932, 934)
        )
        self.assertEqual(result["existing_fields_changed"], 0)

    def test_missing_replaced_or_extra_physical_object_is_rejected(self):
        for mutation in ("missing", "replacement", "extra"):
            with self.subTest(mutation=mutation):
                arguments = model()
                tables = arguments[1]["tables"]
                if mutation == "extra":
                    tables["tabUnexpected"] = copy.deepcopy(tables["tabCompany"])
                else:
                    tables.pop("tabGL Entry")
                    if mutation == "replacement":
                        tables["tabUnexpected"] = copy.deepcopy(tables["tabCompany"])
                with self.assertRaises(A.AuditError):
                    verify(arguments)

    def test_old_business_auth_permissions_and_sequences_cannot_drift(self):
        for table in (
            "tabGL Entry",
            "tabJifeng Mold",
            "__Auth",
            "tabCustom DocPerm",
            "tabUser Permission",
            "tabSeries",
            "bisect_nodes_id_seq",
            "tabError Log",
        ):
            with self.subTest(table=table):
                arguments = model()
                arguments[1]["tables"][table]["sha"] = "f" * 64
                with self.assertRaisesRegex(A.AuditError, "protected_table_changed"):
                    verify(arguments)

    def test_old_metadata_columns_indexes_rows_and_outside_permissions_are_protected(
        self,
    ):
        for kind in ("column", "index", "row", "count", "stale_full_hash"):
            with self.subTest(kind=kind):
                arguments = model()
                partition = arguments[1]["metadata"]["tabDocPerm"]
                if kind == "column":
                    partition["columns"].append("unsanctioned_permission")
                elif kind == "index":
                    arguments[1]["tables"]["tabDocPerm"]["indexes"] = "e" * 64
                elif kind == "row":
                    partition["outside"]["sha"] = "a" * 64
                elif kind == "count":
                    partition["outside"]["rows"] += 1
                else:
                    arguments[1]["tables"]["tabDocPerm"]["sha"] = arguments[0][
                        "tables"
                    ]["tabDocPerm"]["sha"]
                with self.assertRaises(A.AuditError):
                    verify(arguments)

    def test_new_doctypes_must_be_absent_before_install(self):
        arguments = model()
        row = arguments[3][A.TARGETS[0]]["doctype"]
        arguments[0]["metadata"]["tabDocType"]["parents"][A.TARGETS[0]] = [row]
        with self.assertRaisesRegex(A.AuditError, "new_metadata_must_be_absent"):
            verify(arguments)

    def test_new_tables_must_be_empty_with_independently_approved_layout(self):
        for table in A.NEW_TABLES:
            for field in ("rows", "sha", "schema", "indexes", "engine"):
                with self.subTest(table=table, field=field):
                    arguments = model()
                    arguments[1]["tables"][table][field] = (
                        1
                        if field == "rows"
                        else "MyISAM"
                        if field == "engine"
                        else "d" * 64
                    )
                    with self.assertRaises(A.AuditError):
                        verify(arguments)

    def test_request_and_active_binding_unique_indexes_cannot_be_weakened(self):
        for field in ("request_id", "active_source_key"):
            for kind in ("missing", "not_unique", "prefix"):
                with self.subTest(field=field, kind=kind):
                    arguments = model()
                    rows = arguments[3][A.TARGETS[0]]["indexes"]
                    row = next(row for row in rows if row["Column_name"] == field)
                    if kind == "missing":
                        rows.remove(row)
                    elif kind == "not_unique":
                        row["Non_unique"] = 1
                    else:
                        row["Sub_part"] = 32
                    with self.assertRaises(A.AuditError):
                        verify(arguments)

    def test_non_unique_index_drift_is_rejected_even_when_uniques_survive(self):
        arguments = model()
        rows = arguments[3][A.TARGETS[0]]["indexes"]
        next(row for row in rows if row["Non_unique"])["Column_name"] = (
            "expense_voucher"
        )
        with self.assertRaisesRegex(A.AuditError, "approved_native_indexes"):
            verify(arguments)

    def test_undeclared_roles_and_default_grants_are_forbidden(self):
        for right in (
            "delete",
            "share",
            "print",
            "export",
            "email",
            "report",
            "import",
            "amend",
            "if_owner",
        ):
            with self.subTest(right=right):
                arguments = model()
                arguments[3][A.TARGETS[0]]["permissions"][0][right] = 1
                with self.assertRaises(A.AuditError):
                    verify(arguments)
        arguments = model()
        arguments[3][A.TARGETS[1]]["permissions"].append(
            copy.deepcopy(arguments[3][A.TARGETS[0]]["permissions"][0])
        )
        with self.assertRaisesRegex(A.AuditError, "exact_new_metadata_count"):
            verify(arguments)

    def test_private_fields_remain_hidden_read_only_and_not_printed(self):
        for field in (
            "source_snapshot",
            "source_digest",
            "request_id",
            "request_digest",
            "expense_version",
            "journal_version",
            "active_source_key",
        ):
            for flag in ("hidden", "read_only", "print_hide", "no_copy"):
                with self.subTest(field=field, flag=flag):
                    arguments = model()
                    row = next(
                        row
                        for row in arguments[3][A.TARGETS[0]]["fields"]
                        if row["fieldname"] == field
                    )
                    row[flag] = 0
                    with self.assertRaisesRegex(A.AuditError, "new_declared_property"):
                        verify(arguments)

    def test_complete_raw_metadata_is_required_not_an_allowlisted_projection(self):
        arguments = model()
        arguments[3][A.TARGETS[0]]["doctype"].pop("allow_guest_to_view")
        with self.assertRaisesRegex(
            A.AuditError, "independent_native_metadata_reference"
        ):
            verify(arguments)

    def test_source_schema_permission_or_field_changes_are_rejected(self):
        for kind in ("permission", "existing_field", "extra_field"):
            arguments = model()
            schema = arguments[2][A.TARGETS[0]]
            if kind == "permission":
                schema["permissions"][0]["submit"] = 1
            elif kind == "existing_field":
                schema["fields"][0]["read_only"] = 1
            else:
                schema["fields"].append({"fieldname": "secret", "fieldtype": "Data"})
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(A.AuditError, "frozen_schema_definition"),
            ):
                verify(arguments)

    def test_audit_owner_window_migration_and_identity_must_match(self):
        for field, value in (
            ("owner", "Guest"),
            ("modified_by", "Guest"),
            ("idx", 3),
            ("docstatus", 1),
            ("creation", END.replace(day=4)),
            ("migration_hash", "0" * 32),
        ):
            with self.subTest(field=field):
                arguments = model()
                arguments[3][A.TARGETS[0]]["doctype"][field] = value
                with self.assertRaises(A.AuditError):
                    verify(arguments)

    def test_native_parent_partition_must_equal_collected_installed_metadata(self):
        arguments = model()
        arguments[1]["metadata"]["tabDocField"]["parents"][A.TARGETS[0]] = (
            copy.deepcopy(arguments[3][A.TARGETS[0]]["fields"])
        )
        arguments[1]["metadata"]["tabDocField"]["parents"][A.TARGETS[0]][0]["name"] = (
            "wrong identity"
        )
        with self.assertRaisesRegex(A.AuditError, "installed_full_rows_differ"):
            verify(arguments)

    def test_six_sequence_inventory_and_three_myisam_are_mandatory(self):
        for kind in ("sequence", "sequence_value", "myisam"):
            with self.subTest(kind=kind):
                arguments = model()
                if kind == "sequence":
                    arguments[1]["sequences"].pop()
                elif kind == "sequence_value":
                    arguments[1]["tables"]["crm_note_id_seq"]["rows"] = 0
                else:
                    arguments[1]["tables"]["tabError Log"]["engine"] = "InnoDB"
                with self.assertRaises(A.AuditError):
                    verify(arguments)

    def test_frozen_source_package_has_33_exact_assets_and_both_controllers(self):
        manifest, inventory, old = package()
        result = A.verify_source_manifest(manifest, inventory, old)
        self.assertTrue(result["passed"])
        self.assertEqual(result["hook_count"], 33)
        self.assertEqual(result["migration_hash"], A.MIGRATION_HASH)

    def test_source_resources_cannot_be_missing_duplicated_stale_or_reordered(self):
        for kind in ("missing", "duplicate", "stale", "order"):
            with self.subTest(kind=kind):
                manifest, inventory, old = package()
                if kind == "missing":
                    inventory.pop("app/jifeng_erp/public/js/jifeng_tool_costs.js")
                elif kind == "duplicate":
                    manifest["hooks"][-1] = manifest["hooks"][0]
                elif kind == "stale":
                    manifest["hooks"][0] = manifest["hooks"][0][:-16] + "0" * 16
                else:
                    positions = [
                        index
                        for index, url in enumerate(manifest["hooks"])
                        if "/jifeng_tool_costs.js?" in url
                        or "/jifeng_tool_archive.js?" in url
                    ]
                    first, second = positions
                    manifest["hooks"][first], manifest["hooks"][second] = (
                        manifest["hooks"][second],
                        manifest["hooks"][first],
                    )
                with self.assertRaises(A.AuditError):
                    A.verify_source_manifest(manifest, inventory, old)

    def test_hook_side_effects_guard_removal_and_unrelated_changes_are_rejected(self):
        for old_side in (False, True):
            manifest, inventory, old = package()
            if old_side:
                old += b"\nafter_install = 'unexpected'\n"
            else:
                path = "app/jifeng_erp/hooks.py"
                inventory[path]["content"] += b"\ndoc_events = {}\n"
                entry = next(row for row in manifest["files"] if row["path"] == path)
                entry["bytes"] = len(inventory[path]["content"])
                entry["sha256"] = hashlib.sha256(inventory[path]["content"]).hexdigest()
            with (
                self.subTest(old_side=old_side),
                self.assertRaisesRegex(A.AuditError, "frozen_hook_source"),
            ):
                A.verify_source_manifest(manifest, inventory, old)

    def test_source_inventory_and_permissions_cannot_be_extended_or_symlinked(self):
        for kind in ("extra_file", "symlink", "mode", "missing_controller", "remote"):
            with self.subTest(kind=kind):
                manifest, inventory, old = package()
                path = "app/jifeng_erp/tool_cost_sources.py"
                if kind == "extra_file":
                    inventory["app/private.txt"] = {
                        "kind": "file",
                        "mode": 0o644,
                        "content": b"private",
                    }
                elif kind == "symlink":
                    inventory[path]["kind"] = "symlink"
                elif kind == "mode":
                    inventory[path]["mode"] = 0o777
                elif kind == "missing_controller":
                    manifest["files"] = [
                        row for row in manifest["files"] if row["path"] != path
                    ]
                    inventory.pop(path)
                else:
                    path = "app/.git/config"
                    inventory[path]["content"] += (
                        b'[remote "origin"]\n    url = example.invalid\n'
                    )
                    entry = next(
                        row for row in manifest["files"] if row["path"] == path
                    )
                    entry["bytes"] = len(inventory[path]["content"])
                    entry["sha256"] = hashlib.sha256(
                        inventory[path]["content"]
                    ).hexdigest()
                with self.assertRaises(A.AuditError):
                    A.verify_source_manifest(manifest, inventory, old)

    def test_repaired_business_commit_must_be_explicitly_pinned(self):
        manifest, inventory, old = package()
        candidate = "f" * 40
        manifest["commit"] = candidate
        for path in (
            "app/.git/HEAD",
            "app/.git/shallow",
            "app/.git/refs/tags/jifeng-release-pinned",
        ):
            inventory[path]["content"] = (candidate + "\n").encode()
            entry = next(row for row in manifest["files"] if row["path"] == path)
            entry["sha256"] = hashlib.sha256(inventory[path]["content"]).hexdigest()
        with self.assertRaisesRegex(A.AuditError, "new_release_commit_required"):
            A.verify_source_manifest(manifest, inventory, old)
        self.assertTrue(
            A.verify_source_manifest(
                manifest, inventory, old, expected_commit=candidate
            )["passed"]
        )
        plan = {
            "old_commit": A.OLD,
            "new_commit": candidate,
            "source_files": len(manifest["files"]),
            "new_table_layout": copy.deepcopy(A.APPROVED_NEW_TABLES),
        }
        self.assertTrue(
            A.verify_release_plan(plan, expected_commit=candidate)["passed"]
        )
        plan["new_table_layout"]["tabJifeng Tool Cost Allocation"]["schema"] = "0" * 64
        with self.assertRaisesRegex(A.AuditError, "approved_native_layout"):
            A.verify_release_plan(plan, expected_commit=candidate)

    def test_errors_do_not_echo_sql_values(self):
        arguments = model()
        arguments[3][A.TARGETS[0]]["permissions"][0]["role"] = "private-role-value"
        with self.assertRaises(A.AuditError) as raised:
            verify(arguments)
        self.assertNotIn("private-role-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
