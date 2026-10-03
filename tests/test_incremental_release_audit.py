"""Isolated pure proofs: no Frappe, SQL, network or deployment processes."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "incremental_audit", ROOT / "deploy/erp-test/incremental_audit.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
START = datetime(2026, 10, 3, 10, 0)
END = START + timedelta(minutes=2)
ACTOR = "Administrator"


def source_fixture():
    commit = audit.NEW
    pack = "app/.git/objects/pack/pack-" + "a" * 40
    contents = {
        "app/.git/HEAD": (commit + "\n").encode(),
        "app/.git/shallow": (commit + "\n").encode(),
        "app/.git/refs/tags/jifeng-release-pinned": (commit + "\n").encode(),
        "app/.git/config": b"[core]\nrepositoryformatversion = 0\nbare = false\n",
        pack + ".pack": b"pinned pack fixture",
        pack + ".idx": b"pinned pack index fixture",
    }
    for path in audit.SCHEMA_PATHS.values():
        contents[path] = (ROOT / path.removeprefix("app/")).read_bytes()
    hooks = []
    for index in range(audit.HOOK_COUNT):
        suffix = ".css" if index < audit.CSS_COUNT else ".js"
        relative = (
            ("css/" if index < audit.CSS_COUNT else "js/")
            + "resource_"
            + str(index)
            + suffix
        )
        content = ("public resource " + str(index)).encode()
        contents["app/jifeng_erp/public/" + relative] = content
        hooks.append(
            "/assets/jifeng_erp/"
            + relative
            + "?v="
            + hashlib.sha256(content).hexdigest()[:16]
        )
    plain_hooks = [url.split("?v=")[0] for url in hooks]
    contents["app/jifeng_erp/hooks.py"] = (
        "app_include_css = "
        + repr(plain_hooks[: audit.CSS_COUNT])
        + "\napp_include_js = "
        + repr(plain_hooks[audit.CSS_COUNT :])
        + "\ndoctype_js = "
        + repr(audit.DOCTYPE_JS)
        + "\n"
    ).encode()
    contents["app/jifeng_erp/public/js/jifeng_sales_order.js"] = (
        b"window.JifengOrderCustomerPicker = class JifengOrderCustomerPicker {};"
    )
    manifest = {"commit": commit, "files": [], "hooks": hooks}
    inventory = {"app": {"kind": "directory", "mode": 0o755}}
    for path, content in sorted(contents.items()):
        manifest["files"].append(
            {
                "path": path,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": 0o644,
            }
        )
        for parent in PurePosixPath(path).parents:
            if parent.as_posix() != ".":
                inventory[parent.as_posix()] = {"kind": "directory", "mode": 0o755}
        inventory[path] = {"kind": "file", "mode": 0o644, "content": content}
    return manifest, inventory


def index_row(name, field, unique):
    return {
        "Table": "tab" + name,
        "Non_unique": int(not unique),
        "Key_name": "PRIMARY" if field == "name" else field,
        "Seq_in_index": 1,
        "Column_name": field,
        "Collation": "A",
        "Sub_part": None,
        "Packed": None,
        "Null": "" if field == "name" else "YES",
        "Index_type": "BTREE",
        "Comment": "",
        "Index_comment": "",
        "Ignored": "NO",
    }


def metadata_columns(schemas, parentfield):
    columns = set(audit.TECHNICAL)
    if parentfield is None:
        columns |= set(audit.HEADER_DEFAULTS) | {
            "migration_hash",
            "editable_grid",
            "allow_rename",
            "custom",
            "_comments",
        }
        for source in schemas.values():
            columns |= set(source) - {
                "doctype",
                "fields",
                "field_order",
                "permissions",
                "actions",
                "links",
                "states",
            }
    else:
        columns |= audit.CHILD_IDENTITY
        for source in schemas.values():
            for row in source.get(parentfield, []):
                columns |= set(row)
        if parentfield == "fields":
            columns |= {
                "fieldname",
                "label",
                "options",
                "description",
                "permlevel",
                "mask",
                "unique",
            }
        if parentfield == "permissions":
            columns |= {
                "role",
                "permlevel",
                "if_owner",
                "delete",
                "export",
                "share",
                "submit",
                "print",
                "email",
                "impersonate",
            }
    return sorted(columns)


def full_row(declared, columns, name, parentfield, index):
    row = dict.fromkeys(columns, None)
    row.update(
        {
            "name": name,
            "owner": ACTOR,
            "modified_by": ACTOR,
            "creation": START,
            "modified": START,
            "docstatus": 0,
            "idx": index,
        }
    )
    row.update({key: value for key, value in declared.items() if key in columns})
    if parentfield is None:
        row.update(
            {
                key: value
                for key, value in audit.HEADER_DEFAULTS.items()
                if key not in declared
            }
        )
    return row


def delta_fixture():
    manifest, inventory = source_fixture()
    source = audit.verify_source_manifest(manifest, inventory)
    schemas, migrations = source["schemas"], source["migration_hash"]
    installed, metadata = {}, {}
    for table, parentfield in audit.META.items():
        metadata[table] = {
            "columns": metadata_columns(schemas, parentfield),
            "outside": {
                "rows": 10,
                "sha256": audit.fingerprint([table, "unchanged outside"]),
            },
            "new_parents": {name: [] for name in audit.TARGETS},
            "targets": {},
        }
    for name, schema in schemas.items():
        header = full_row(schema, metadata["tabDocType"]["columns"], name, None, 0)
        header["migration_hash"] = migrations[name]
        actual = {
            "doctype": header,
            "indexes": sorted(
                [index_row(name, "name", True), index_row(name, "modified", False)]
                + [
                    index_row(name, field["fieldname"], True)
                    for field in schema["fields"]
                    if field.get("unique")
                ],
                key=lambda row: (row["Key_name"], row["Seq_in_index"]),
            ),
        }
        for table, parentfield in audit.META.items():
            if parentfield is None:
                continue
            actual[parentfield] = []
            for idx, declared in enumerate(schema.get(parentfield, []), 1):
                row = full_row(
                    declared,
                    metadata[table]["columns"],
                    name + ":" + parentfield + ":" + str(idx),
                    parentfield,
                    idx,
                )
                row.update(parent=name, parenttype="DocType", parentfield=parentfield)
                actual[parentfield].append(row)
        installed[name] = actual
    patch = {}
    for idx, (field, changes) in enumerate(patch.items(), 1):
        row = full_row(
            {
                "fieldname": field,
                "label": "unchanged label",
                "options": "unchanged options",
                "description": "unchanged description",
            },
            metadata["tabDocField"]["columns"],
            "exact-old-" + field,
            "fields",
            idx,
        )
        row.update(
            parent="Jifeng Customer Type",
            parenttype="DocType",
            parentfield="fields",
            creation=START - timedelta(days=3),
            modified=START - timedelta(days=1),
        )
        row.update({key: value["old"] for key, value in changes.items()})
        metadata["tabDocField"]["targets"][field] = row
    names = (
        set(audit.META)
        | audit.MYISAM
        | {
            "__Auth",
            "tabCustomer",
            "tabSales Order",
            "tabSales Order Item",
            "tabAddress",
            "tabContact",
            "__UserSettings",
            "__default",
        }
    )
    names |= {
        "fixture_table_" + str(index)
        for index in range(audit.BEFORE_TABLES - len(names))
    }
    tables = {
        name: {
            "engine": "MyISAM" if name in audit.MYISAM else "InnoDB",
            "rows": 3,
            "content_sha256": audit.fingerprint([name, "unchanged"]),
            "schema_sha256": audit.fingerprint([name, "columns"]),
            "indexes_sha256": audit.fingerprint([name, "indexes"]),
        }
        for name in names
    }
    before = {"tables": tables, "metadata": metadata}
    for table, partition in metadata.items():
        tables[table]["rows"] = partition["outside"]["rows"] + len(partition["targets"])
    after = deepcopy(before)
    for field, changes in patch.items():
        row = after["metadata"]["tabDocField"]["targets"][field]
        row.update({key: value["new"] for key, value in changes.items()})
        row.update(modified=START, modified_by=ACTOR)
    for table, parentfield in audit.META.items():
        partition = after["metadata"][table]
        for name in audit.TARGETS:
            rows = (
                [installed[name]["doctype"]]
                if parentfield is None
                else installed[name][parentfield]
            )
            partition["new_parents"][name] = deepcopy(rows)
        owned = sum(len(rows) for rows in partition["new_parents"].values()) + len(
            partition["targets"]
        )
        after["tables"][table]["rows"] = partition["outside"]["rows"] + owned
        if owned != len(partition["targets"]) or table == "tabDocField":
            after["tables"][table]["content_sha256"] = audit.fingerprint(partition)
    for name in audit.TARGETS:
        after["tables"]["tab" + name] = {
            "engine": "InnoDB",
            "rows": 0,
            "content_sha256": audit.fingerprint([]),
            "schema_sha256": audit.fingerprint([name, "new physical columns"]),
            "indexes_sha256": audit.fingerprint(installed[name]["indexes"]),
        }
    return {
        "before": before,
        "after": after,
        "docfield_changes": patch,
        "schemas": schemas,
        "installed": installed,
        "window_start": START,
        "window_end": END,
        "actor": ACTOR,
        "migration_hash": migrations,
    }


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.inventory = source_fixture()

    def verify(self):
        return audit.verify_source_manifest(self.manifest, self.inventory)

    def test_success_and_no_mutation(self):
        previous = deepcopy((self.manifest, self.inventory))
        result = self.verify()
        self.assertTrue(result["passed"])
        self.assertEqual(result["hook_count"], 28)
        self.assertIn("Sales Order", result["doctype_js"])
        for name, path in audit.SCHEMA_PATHS.items():
            self.assertEqual(
                result["migration_hash"][name],
                hashlib.md5(
                    self.inventory[path]["content"], usedforsecurity=False
                ).hexdigest(),
            )
        self.assertEqual(previous, (self.manifest, self.inventory))

    def test_candidate_byte_changed(self):
        self.inventory[audit.SCHEMA_PATHS[audit.TARGETS[0]]]["content"] += b" "
        with self.assertRaisesRegex(audit.AuditError, "source_content_changed"):
            self.verify()

    def test_missing_or_extra_file_or_directory(self):
        for extra, kind in (("app/extra.txt", "file"), ("app/extra", "directory")):
            with self.subTest(kind=kind):
                inventory = deepcopy(self.inventory)
                inventory[extra] = {"kind": kind, "mode": 0o644}
                with self.assertRaises(audit.AuditError):
                    audit.verify_source_manifest(self.manifest, inventory)
        self.inventory.pop("app/.git/HEAD")
        with self.assertRaises(audit.AuditError):
            self.verify()

    def test_mode_and_symlink(self):
        for path, key, value in (
            ("app/.git/HEAD", "mode", 0o600),
            ("app/.git/HEAD", "kind", "symlink"),
            ("app", "mode", 0o777),
        ):
            with self.subTest(path=path, key=key):
                inventory = deepcopy(self.inventory)
                inventory[path][key] = value
                with self.assertRaises(audit.AuditError):
                    audit.verify_source_manifest(self.manifest, inventory)

    def test_hook_version_and_count(self):
        self.manifest["hooks"][0] = (
            self.manifest["hooks"][0].split("?v=")[0] + "?v=" + "0" * 16
        )
        with self.assertRaisesRegex(audit.AuditError, "hook_fingerprint"):
            self.verify()
        self.manifest["hooks"].pop()
        with self.assertRaisesRegex(audit.AuditError, "exact_28_hooks"):
            self.verify()

    def test_unhashable_hook_rejected(self):
        self.manifest["hooks"][0] = {}
        with self.assertRaises(audit.AuditError):
            self.verify()

    def test_hook_list_must_match_verified_source(self):
        self.manifest["hooks"][0], self.manifest["hooks"][1] = (
            self.manifest["hooks"][1],
            self.manifest["hooks"][0],
        )
        with self.assertRaisesRegex(audit.AuditError, "exact_source_hook_list"):
            self.verify()

    def test_remote_config_even_with_manifest_rehashed(self):
        content = b'[remote "origin"]\nurl = forbidden\n'
        self.inventory["app/.git/config"]["content"] = content
        entry = next(
            item for item in self.manifest["files"] if item["path"] == "app/.git/config"
        )
        entry.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        with self.assertRaisesRegex(audit.AuditError, "git_remote_or_include"):
            self.verify()

    def test_source_traversal(self):
        self.manifest["files"][0]["path"] = "app/../private"
        with self.assertRaises(audit.AuditError):
            self.verify()

    def test_previous_release_and_wrong_doctype_script_rejected(self):
        self.manifest["commit"] = audit.OLD
        with self.assertRaisesRegex(audit.AuditError, "new_release_commit_required"):
            self.verify()
        self.manifest["commit"] = audit.NEW
        path = "app/jifeng_erp/hooks.py"
        content = self.inventory[path]["content"].replace(
            b"public/js/jifeng_sales_order.js", b"public/js/old_order.js"
        )
        self.inventory[path]["content"] = content
        entry = next(item for item in self.manifest["files"] if item["path"] == path)
        entry.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        with self.assertRaisesRegex(audit.AuditError, "sales_order_doctype_js"):
            self.verify()


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "old_commit": audit.OLD,
            "new_commit": audit.NEW,
            "source_files": 532,
            "new_table_layout": deepcopy(audit.APPROVED_NEW_TABLES),
        }

    def test_fixed_batch_and_reference_do_not_mutate_input(self):
        previous = deepcopy(self.plan)
        self.assertTrue(audit.verify_release_plan(self.plan)["passed"])
        self.assertEqual(self.plan, previous)

    def test_previous_release_count_and_unapproved_layout_rejected(self):
        for key, value in (
            ("new_commit", audit.OLD),
            ("source_files", 505),
            ("source_files", True),
        ):
            with self.subTest(key=key, value=value):
                plan = deepcopy(self.plan)
                plan[key] = value
                with self.assertRaises(audit.AuditError):
                    audit.verify_release_plan(plan)
        self.plan["new_table_layout"]["tabJifeng Plating Color"]["schema_sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(audit.AuditError, "approved_native_layout"):
            audit.verify_release_plan(self.plan)

    def test_arbitrary_target_and_source_path_rejected(self):
        self.plan["source_path"] = "/elsewhere"
        with self.assertRaises(audit.AuditError):
            audit.verify_release_plan(self.plan)
        self.plan.pop("source_path")
        self.plan["new_table_layout"]["tabOperation"] = self.plan[
            "new_table_layout"
        ].pop("tabJifeng Order Operation")
        with self.assertRaises(audit.AuditError):
            audit.verify_release_plan(self.plan)


class SnapshotAuditTests(unittest.TestCase):
    def setUp(self):
        self.dto = delta_fixture()

    def verify(self):
        return audit.verify_snapshot_delta(**self.dto)

    def reject(self, expression=None):
        with self.assertRaisesRegex(audit.AuditError, expression or "."):
            self.verify()

    def test_exact_batch_success_and_inputs_unchanged(self):
        previous = deepcopy(self.dto)
        result = self.verify()
        self.assertEqual(result["before_tables"], 929)
        self.assertEqual(result["after_tables"], 931)
        self.assertEqual(self.dto, previous)

    def test_global_missing_even_if_count_preserved(self):
        for snapshot in ("before", "after"):
            tables = self.dto[snapshot]["tables"]
            tables["fake_replacement"] = tables.pop("__global_search")
        self.reject("protected_physical_table_missing")

    def test_old_auth_and_native_business_mutations(self):
        baseline = deepcopy(self.dto)
        for table in (
            "__Auth",
            "__default",
            "tabCustomer",
            "tabSales Order Item",
            "tabContact",
            "tabAddress",
        ):
            for column in ("rows", "content_sha256", "schema_sha256", "indexes_sha256"):
                with self.subTest(table=table, column=column):
                    self.dto = deepcopy(baseline)
                    self.dto["after"]["tables"][table][column] = (
                        9 if column == "rows" else "0" * 64
                    )
                    self.reject("protected_table_changed")

    def test_engine_whitelist_exact(self):
        self.dto["after"]["tables"]["__default"]["engine"] = "MyISAM"
        self.reject("exact_three_myisam")

    def test_wrong_added_table_even_same_count(self):
        tables = self.dto["after"]["tables"]
        tables["tabJifeng Plating Color Extra"] = tables.pop("tabJifeng Plating Color")
        self.reject("exact_added_tables")

    def test_nonempty_new_table_and_false_empty_hash(self):
        table = self.dto["after"]["tables"]["tabJifeng Plating Color"]
        table["rows"] = 1
        self.reject("empty_new_table")
        table["rows"], table["content_sha256"] = 0, "0" * 64
        self.reject("empty_new_table")

    def test_missing_hash_and_extra_proof_key(self):
        table = self.dto["after"]["tables"]["__Auth"]
        table["unknown"] = "unused"
        self.reject("exact_keys")
        table.pop("unknown")
        table.pop("content_sha256")
        self.reject("exact_keys")

    def test_outside_metadata_change(self):
        self.dto["after"]["metadata"]["tabDocPerm"]["outside"]["sha256"] = "0" * 64
        self.reject("outside_metadata_changed")

    def test_existing_metadata_schema_index_change(self):
        self.dto["after"]["tables"]["tabDocField"]["indexes_sha256"] = "0" * 64
        self.reject("existing_metadata_schema_or_index_changed")

    def test_metadata_rowcount_partition(self):
        self.dto["after"]["tables"]["tabDocType"]["rows"] += 1
        self.reject("metadata_partition_count")

    def test_existing_docfield_cannot_be_excluded_or_patched(self):
        self.dto["docfield_changes"] = {
            "value": {"description": {"old": "x", "new": "y"}}
        }
        self.reject("exact_keys:docfield_changes")
        self.dto = delta_fixture()
        self.dto["after"]["metadata"]["tabDocField"]["targets"]["value"] = {}
        self.reject("exact_keys:tabDocField")

    def test_existing_docfield_outside_changed(self):
        self.dto["after"]["metadata"]["tabDocField"]["outside"]["sha256"] = "0" * 64
        self.reject("outside_metadata_changed")

    def test_missing_full_column(self):
        self.dto["after"]["metadata"]["tabDocField"]["new_parents"][audit.TARGETS[0]][
            0
        ].pop("owner")
        self.reject("exact_keys")

    def test_new_parent_wrong_scope(self):
        row = self.dto["after"]["metadata"]["tabDocField"]["new_parents"][
            audit.TARGETS[0]
        ][0]
        row["parent"] = "Jifeng Plating Color Extra"
        self.reject("exact_new_parent_scope")

    def test_undeclared_permission_grant(self):
        row = self.dto["installed"][audit.TARGETS[0]]["permissions"][-1]
        row["delete"] = 1
        self.reject("undeclared_metadata_property")

    def test_field_order_and_property(self):
        fields = self.dto["installed"][audit.TARGETS[0]]["fields"]
        fields[0], fields[1] = fields[1], fields[0]
        self.reject("new_child_identity")
        fields[0], fields[1] = fields[1], fields[0]
        fields[0]["options"] = "Customer"
        self.reject("new_declared_property")

    def test_physical_unique_required_and_bound_to_table(self):
        indexes = self.dto["installed"][audit.TARGETS[0]]["indexes"]
        indexes[:] = [row for row in indexes if row["Column_name"] != "request_id"]
        self.reject("exact_unique_indexes")

    def test_prefix_unique_index_and_bool_sequence(self):
        baseline = deepcopy(self.dto)
        for key, value in (("Sub_part", 20), ("Seq_in_index", True)):
            with self.subTest(key=key):
                self.dto = deepcopy(baseline)
                row = next(
                    row
                    for row in self.dto["installed"][audit.TARGETS[0]]["indexes"]
                    if row["Column_name"] == "request_id"
                )
                row[key] = value
                self.reject()

    def test_unstable_index_column_rejected(self):
        self.dto["installed"][audit.TARGETS[0]]["indexes"][0]["Cardinality"] = 0
        self.reject("index_columns")

    def test_index_proof_changed(self):
        self.dto["after"]["tables"]["tabJifeng Plating Color"]["indexes_sha256"] = (
            "0" * 64
        )
        self.reject("installed_index_proof")

    def test_installed_fullrows_must_match_snapshot(self):
        self.dto["after"]["metadata"]["tabDocField"]["new_parents"][audit.TARGETS[0]][
            0
        ]["description"] = "PRIVATE-SENTINEL"
        self.reject("installed_full_rows_differ")

    def test_audit_actor_and_timestamp_exact(self):
        row = self.dto["after"]["metadata"]["tabDocField"]["new_parents"][
            audit.TARGETS[0]
        ][0]
        row["modified_by"] = "unapproved actor"
        self.dto["installed"][audit.TARGETS[0]]["fields"][0]["modified_by"] = (
            "unapproved actor"
        )
        self.reject("new_metadata_audit")
        row["modified_by"], row["modified"] = ACTOR, END + timedelta(seconds=1)
        self.dto["installed"][audit.TARGETS[0]]["fields"][0].update(
            modified_by=ACTOR, modified=END + timedelta(seconds=1)
        )
        self.reject("new_metadata_audit")

    def test_native_wire_datetime_supported(self):
        self.dto = audit._wire(self.dto)
        self.assertTrue(self.verify()["passed"])

    def test_new_metadata_status_and_migration_md5(self):
        for key, value, rule in (
            ("docstatus", 1, "new_metadata_status"),
            ("migration_hash", "0" * 32, "migration_hash"),
        ):
            with self.subTest(key=key):
                self.dto = delta_fixture()
                self.dto["installed"][audit.TARGETS[0]]["doctype"][key] = value
                self.dto["after"]["metadata"]["tabDocType"]["new_parents"][
                    audit.TARGETS[0]
                ][0][key] = value
                self.reject(rule)

    def test_error_does_not_expose_values(self):
        self.dto["installed"][audit.TARGETS[0]]["permissions"][-1]["delete"] = (
            "PRIVATE-SENTINEL"
        )
        with self.assertRaises(audit.AuditError) as captured:
            self.verify()
        self.assertNotIn("PRIVATE-SENTINEL", str(captured.exception))

    def test_fingerprint_exact_decimal_and_binary(self):
        values = [Decimal("1.00"), b"\x00\xff", START, timedelta(microseconds=1)]
        self.assertEqual(
            audit.fingerprint(values), audit.fingerprint(audit._wire(values))
        )
        self.assertNotEqual(
            audit.fingerprint(Decimal("1.00")), audit.fingerprint(Decimal("1.0"))
        )


if __name__ == "__main__":
    unittest.main()
