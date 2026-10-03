"""Pure collector-contract tests; no SQL, Frappe, processes, or network."""

import copy
from datetime import datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "salary_release_audit", ROOT / "deploy/erp-test/salary_release_audit.py"
)
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)
OLD_TIME = datetime(2026, 10, 1, 1)
START = datetime(2026, 10, 3, 1)
MID = datetime(2026, 10, 3, 1, 0, 1)
END = datetime(2026, 10, 3, 1, 0, 2)


def index_rows(target, uniques):
    result = []
    for key, field, unique in [
        ("PRIMARY", "name", True),
        ("modified", "modified", False),
        *[(field, field, True) for field in sorted(uniques)],
    ]:
        result.append(
            {
                "Table": "tab" + target,
                "Non_unique": int(not unique),
                "Key_name": key,
                "Seq_in_index": 1,
                "Column_name": field,
                "Collation": "A",
                "Sub_part": None,
                "Packed": None,
                "Null": "" if key == "PRIMARY" else "YES",
                "Index_type": "BTREE",
                "Comment": "",
                "Index_comment": "",
                "Ignored": "NO",
            }
        )
    return sorted(result, key=lambda row: (row["Key_name"], row["Seq_in_index"]))


def source_schemas():
    return {
        name: json.loads((ROOT / path.removeprefix("app/")).read_text(encoding="utf-8"))
        for name, path in A.SCHEMA_PATHS.items()
    }


def model():
    schemas = source_schemas()
    hashes = {
        name: hashlib.md5(
            (ROOT / A.SCHEMA_PATHS[name].removeprefix("app/")).read_bytes(),
            usedforsecurity=False,
        ).hexdigest()
        for name in A.TARGETS
    }
    installed = {name: {} for name in A.TARGETS}
    metadata = {}
    for table, parentfield in A.META.items():
        skipped = {
            "doctype",
            "fields",
            "field_order",
            "permissions",
            "actions",
            "links",
            "states",
        }
        required = A.TECHNICAL | (
            {"migration_hash"} if parentfield is None else A.CHILD_IDENTITY
        )
        if parentfield == "fields":
            required |= {"fieldname", "label", "options", "description"}
        declared = [
            schema if parentfield is None else item
            for schema in schemas.values()
            for item in (
                [schema] if parentfield is None else schema.get(parentfield, [])
            )
        ]
        columns = sorted(
            required | {key for item in declared for key in item if key not in skipped}
        )
        if parentfield is None:
            columns = sorted(set(columns) | set(A.HEADER_DEFAULTS))
        if parentfield == "permissions":
            columns = sorted(
                set(columns)
                | {
                    "create",
                    "write",
                    "submit",
                    "cancel",
                    "delete",
                    "export",
                    "print",
                    "email",
                    "report",
                    "share",
                }
            )
        parents = {}
        for target, schema in schemas.items():
            expected = [schema] if parentfield is None else schema.get(parentfield, [])
            rows = []
            for number, item in enumerate(expected, 1):
                row = {key: None for key in columns}
                if parentfield is None:
                    row.update(A.HEADER_DEFAULTS)
                if parentfield == "permissions":
                    row.update(
                        {
                            key: 0
                            for key in columns
                            if key
                            in {
                                "create",
                                "write",
                                "submit",
                                "cancel",
                                "delete",
                                "export",
                                "print",
                                "email",
                                "report",
                                "share",
                            }
                        }
                    )
                row.update(
                    {key: value for key, value in item.items() if key not in skipped}
                )
                fresh = target == A.NEW_TARGET or (
                    parentfield == "fields"
                    and item["fieldname"] in A.NEW_FIELDS[target]
                )
                row.update(
                    name=target
                    if parentfield is None
                    else target
                    + ":"
                    + (
                        item.get("fieldname")
                        or ((item.get("role") or parentfield) + ":" + str(number))
                    ),
                    owner="Administrator",
                    creation=MID if fresh else OLD_TIME,
                    modified=MID if fresh or parentfield is None else OLD_TIME,
                    modified_by="Administrator",
                    docstatus=0,
                    idx=0 if parentfield is None else number,
                )
                if parentfield is None:
                    row["migration_hash"] = hashes[target]
                else:
                    row.update(
                        parent=target, parenttype="DocType", parentfield=parentfield
                    )
                rows.append(row)
            parents[target] = rows
            installed[target]["doctype" if parentfield is None else parentfield] = (
                rows[0] if parentfield is None else rows
            )
        metadata[table] = {
            "columns": columns,
            "outside": {"rows": 0, "sha256": A.fingerprint([])},
            "new_parents": parents,
            "targets": {},
        }
    for target in A.TARGETS:
        installed[target]["indexes"] = index_rows(target, A.UNIQUE_FIELDS[target])
    for row in installed[A.NEW_TARGET]["indexes"]:
        if row["Key_name"] == "modified":
            row["Key_name"] = row["Column_name"] = "creation"
    installed[A.NEW_TARGET]["indexes"].sort(
        key=lambda row: (row["Key_name"], row["Seq_in_index"])
    )
    after = {
        "tables": {},
        "metadata": metadata,
        "existing_business": {},
        "sequences": sorted(A.SEQUENCES),
    }
    for target in A.EXISTING_TARGETS:
        columns = [
            {
                "Field": field,
                "Type": "varchar(140)",
                "Collation": "utf8mb4_unicode_ci",
                "Null": "NO" if field == "name" else "YES",
                "Key": "PRI" if field == "name" else "",
                "Default": None,
                "Extra": "",
                "Comment": "",
            }
            for field in [
                "name",
                "modified",
                *sorted(A.UNIQUE_FIELDS[target]),
                "old_amount",
            ]
        ]
        rows = [{column["Field"]: None for column in columns}]
        rows[0].update(
            name="protected:1", modified=OLD_TIME, old_amount=Decimal("120.00")
        )
        structure = {
            "ENGINE": "InnoDB",
            "TABLE_COLLATION": "utf8mb4_unicode_ci",
            "ROW_FORMAT": "Dynamic",
            "TABLE_TYPE": "BASE TABLE",
        }
        added = [dict(columns[-1], Field=field) for field in A.NEW_DATA_FIELDS]
        proof = {
            "structure": structure,
            "columns": columns + added,
            "indexes": installed[target]["indexes"],
            "projected_old_columns": [column["Field"] for column in columns],
            "projected_old_rows_sha256": A.fingerprint(rows),
            "new_columns_nonnull": {field: 0 for field in A.NEW_DATA_FIELDS},
        }
        name = "tab" + target
        after["existing_business"][name] = proof
        after["tables"][name] = {
            "engine": "InnoDB",
            "rows": 1,
            "content_sha256": A.fingerprint(
                [dict(rows[0], **{field: None for field in A.NEW_DATA_FIELDS})]
            ),
            "schema_sha256": A.fingerprint(
                {"structure": structure, "columns": proof["columns"]}
            ),
            "indexes_sha256": A.fingerprint(proof["indexes"]),
        }
    for table, partition in metadata.items():
        rows = sorted(
            [row for items in partition["new_parents"].values() for row in items],
            key=lambda row: row["name"],
        )
        after["tables"][table] = {
            "engine": "InnoDB",
            "rows": len(rows),
            "content_sha256": A.fingerprint(rows),
            "schema_sha256": A.fingerprint(partition["columns"]),
            "indexes_sha256": A.fingerprint([]),
        }
    regular = {
        "__Auth",
        "__global_search",
        "tabGL Entry",
        "tabStock Ledger Entry",
        "tabPayment Ledger Entry",
        "tabCustom DocPerm",
        "tabUser Permission",
        "tabSeries",
        *A.SEQUENCES,
        *A.MYISAM,
    }
    for table in regular:
        after["tables"][table] = {
            "engine": "MyISAM" if table in A.MYISAM else "InnoDB",
            "rows": 1,
            "content_sha256": A.fingerprint([{"value": 17}]),
            "schema_sha256": A.fingerprint([]),
            "indexes_sha256": A.fingerprint([]),
        }
    rule = "tab" + A.NEW_TARGET
    after["tables"][rule] = {
        "engine": "InnoDB",
        "rows": 0,
        "content_sha256": A.fingerprint([]),
        "schema_sha256": "afff7480ed7a7e43ccd41e55c541f157484ee0805f798de12570d0ff75861d86",
        "indexes_sha256": A.fingerprint(installed[A.NEW_TARGET]["indexes"]),
    }
    for number in range(A.AFTER_TABLES - len(after["tables"])):
        after["tables"]["tabProtected fixture " + str(number)] = {
            "engine": "InnoDB",
            "rows": 0,
            "content_sha256": A.fingerprint([]),
            "schema_sha256": A.fingerprint([]),
            "indexes_sha256": A.fingerprint([]),
        }
    before = copy.deepcopy(after)
    del before["tables"][rule]
    for table, parentfield in A.META.items():
        partition = before["metadata"][table]
        partition["new_parents"][A.NEW_TARGET] = []
        for target in A.EXISTING_TARGETS:
            if parentfield is None:
                partition["new_parents"][target][0].update(
                    modified=OLD_TIME, migration_hash="a" * 32
                )
            elif parentfield == "fields":
                partition["new_parents"][target] = [
                    row
                    for row in partition["new_parents"][target]
                    if row["fieldname"] not in A.NEW_FIELDS[target]
                ]
                for number, row in enumerate(partition["new_parents"][target], 1):
                    row["idx"] = number
        rows = sorted(
            [row for items in partition["new_parents"].values() for row in items],
            key=lambda row: row["name"],
        )
        before["tables"][table].update(
            rows=len(rows), content_sha256=A.fingerprint(rows)
        )
    for table, proof in before["existing_business"].items():
        proof["columns"] = proof["columns"][:-2]
        proof["new_columns_nonnull"] = {}
        before["tables"][table].update(
            content_sha256=proof["projected_old_rows_sha256"],
            schema_sha256=A.fingerprint(
                {"structure": proof["structure"], "columns": proof["columns"]}
            ),
        )
    layout = {
        rule: {
            key: after["tables"][rule][key]
            for key in ("schema_sha256", "indexes_sha256")
        }
    }
    return before, after, schemas, installed, hashes, layout


class SalaryReleaseAuditTests(unittest.TestCase):
    def setUp(self):
        (
            self.before,
            self.after,
            self.schemas,
            self.installed,
            self.hashes,
            self.layout,
        ) = model()

    def run_audit(self):
        return A.verify_snapshot_delta(
            self.before,
            self.after,
            {},
            self.schemas,
            self.installed,
            START,
            END,
            "Administrator",
            self.hashes,
        )

    def reject(self, edit):
        edit()
        with self.assertRaises(A.AuditError):
            self.run_audit()

    def test_exact_scope_and_input_immutability(self):
        original = copy.deepcopy(
            (self.before, self.after, self.schemas, self.installed)
        )
        self.assertTrue(self.run_audit()["passed"])
        self.assertEqual(
            original, (self.before, self.after, self.schemas, self.installed)
        )
        self.assertEqual(
            (A.BEFORE_TABLES, A.AFTER_TABLES, len(A.TARGETS), len(A.NEW_TABLES)),
            (931, 932, 3, 1),
        )
        self.assertEqual(
            A.APPROVED_NEW_TABLES,
            {
                "tabJifeng Salary History Rule": {
                    "schema_sha256": "afff7480ed7a7e43ccd41e55c541f157484ee0805f798de12570d0ff75861d86",
                    "indexes_sha256": "524594c65ff95ddd2aca7341611ac1e6b0e4b86fc0b108307227eba16f9d4c40",
                }
            },
        )

    def test_old_business_value_changed_with_same_count(self):
        self.reject(
            lambda: self.after["existing_business"][
                "tab" + A.EXISTING_TARGETS[0]
            ].update(projected_old_rows_sha256="f" * 64)
        )

    def test_projection_uses_before_columns_and_hash(self):
        for field in ("projected_old_columns", "projected_old_rows_sha256"):
            with self.subTest(field=field):
                self.setUp()
                value = ["name"] if field.endswith("columns") else "f" * 64
                self.reject(
                    lambda: self.before["existing_business"][
                        "tab" + A.EXISTING_TARGETS[0]
                    ].update({field: value})
                )

    def test_new_reference_populated_on_existing_row(self):
        self.reject(
            lambda: self.after["existing_business"]["tab" + A.EXISTING_TARGETS[1]][
                "new_columns_nonnull"
            ].update(piece_rate_reference=1)
        )

    def test_old_column_definition_changed_even_with_matching_hash(self):
        name = "tab" + A.EXISTING_TARGETS[0]

        def edit():
            self.after["existing_business"][name]["columns"][0]["Type"] = "varchar(255)"
            proof = self.after["existing_business"][name]
            self.after["tables"][name]["schema_sha256"] = A.fingerprint(
                {"structure": proof["structure"], "columns": proof["columns"]}
            )

        self.reject(edit)

    def test_new_data_column_type_default_and_nullability(self):
        for field, value in (
            ("Type", "text"),
            ("Null", "NO"),
            ("Default", "0"),
            ("Collation", "utf8mb4_bin"),
            ("Key", "UNI"),
        ):
            with self.subTest(field=field):
                self.setUp()
                name = "tab" + A.EXISTING_TARGETS[0]

                def edit():
                    proof = self.after["existing_business"][name]
                    proof["columns"][-1][field] = value
                    self.after["tables"][name]["schema_sha256"] = A.fingerprint(
                        {"structure": proof["structure"], "columns": proof["columns"]}
                    )

                self.reject(edit)

    def test_index_added_or_definition_changed(self):
        name = "tab" + A.EXISTING_TARGETS[1]

        def edit():
            proof = self.after["existing_business"][name]
            proof["indexes"][1]["Index_comment"] = "changed"
            self.after["tables"][name]["indexes_sha256"] = A.fingerprint(
                proof["indexes"]
            )

        self.reject(edit)

    def test_permissions_old_row_identity_and_audit_values_preserved(self):
        for field, value in (
            ("name", "recreated"),
            ("modified", MID),
            ("export", 1),
            ("role", "Other Role"),
        ):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.after["metadata"]["tabDocPerm"]["new_parents"][
                        A.EXISTING_TARGETS[0]
                    ][0].update({field: value})
                )

    def test_new_rule_default_permissions_do_not_grant_export_print_email(self):
        for field in ("export", "print", "email", "cancel", "delete", "share"):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.installed[A.NEW_TARGET]["permissions"][0].update(
                        {field: 1}
                    )
                )

    def test_source_cannot_expand_this_batch_rule_permissions(self):
        self.schemas[A.NEW_TARGET]["permissions"][0]["export"] = 1
        self.installed[A.NEW_TARGET]["permissions"][0]["export"] = 1
        with self.assertRaises(A.AuditError):
            self.run_audit()

    def test_old_docfields_only_exact_idx_changes(self):
        for field, value in (
            ("name", "new-id"),
            ("creation", MID),
            ("modified", MID),
            ("label", "modified label"),
            ("idx", 999),
            ("parent", "other"),
        ):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.after["metadata"]["tabDocField"]["new_parents"][
                        A.EXISTING_TARGETS[0]
                    ][0].update({field: value})
                )

    def test_old_header_has_no_broad_mutation_exemption(self):
        for field, value in (
            ("owner", "other"),
            ("creation", MID),
            ("modified", datetime(2026, 10, 4)),
            ("migration_hash", "f" * 32),
            ("allow_import", 0),
        ):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.after["metadata"]["tabDocType"]["new_parents"][
                        A.EXISTING_TARGETS[0]
                    ][0].update({field: value})
                )

    def test_metadata_outside_targets_and_unknown_partition_rejected(self):
        self.reject(
            lambda: self.after["metadata"]["tabDocField"]["outside"].update(
                sha256="f" * 64
            )
        )
        self.setUp()
        self.reject(lambda: self.after["metadata"].update({"tabUnseen Metadata": {}}))

    def test_all_original_objects_including_sequence_values_protected(self):
        for name in (
            "__Auth",
            "tabGL Entry",
            "tabStock Ledger Entry",
            "tabPayment Ledger Entry",
            "tabCustom DocPerm",
            "tabUser Permission",
            "tabSeries",
            *sorted(A.SEQUENCES),
            *sorted(A.MYISAM),
        ):
            with self.subTest(table=name):
                self.setUp()
                self.reject(
                    lambda: self.after["tables"][name].update(content_sha256="f" * 64)
                )

    def test_actual_sequence_inventory_missing_duplicate_extra(self):
        for value in (
            sorted(A.SEQUENCES)[:-1],
            sorted(A.SEQUENCES) + [sorted(A.SEQUENCES)[0]],
            sorted(A.SEQUENCES) + ["other_seq"],
        ):
            with self.subTest(value=value):
                self.setUp()
                self.reject(lambda: self.after.update(sequences=value))

    def test_exact_myisam_inventory_and_physical_object_set(self):
        self.reject(
            lambda: self.after["tables"]["tabError Log"].update(engine="InnoDB")
        )
        self.setUp()

        def edit():
            self.after["tables"]["tabExtra"] = self.after["tables"].pop(
                "tabProtected fixture 0"
            )

        self.reject(edit)

    def test_rule_not_empty_or_wrong_native_layout(self):
        for field, value in (
            ("rows", 1),
            ("content_sha256", "f" * 64),
            ("schema_sha256", "f" * 64),
            ("indexes_sha256", "f" * 64),
        ):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.after["tables"]["tab" + A.NEW_TARGET].update(
                        {field: value}
                    )
                )

    def test_new_docfields_missing_extra_duplicate_wrong_parent(self):
        for mutate in (
            lambda rows: rows.pop(),
            lambda rows: rows.append(copy.deepcopy(rows[-1])),
            lambda rows: rows[-1].update(parent="other"),
        ):
            with self.subTest(mutate=mutate):
                self.setUp()
                self.reject(lambda: mutate(self.installed[A.NEW_TARGET]["fields"]))

    def test_new_row_audit_identity_window_and_unknown_properties(self):
        for field, value in (
            ("owner", "other"),
            ("creation", OLD_TIME),
            ("docstatus", 1),
            ("idx", 900),
            ("extra_unapproved", 1),
        ):
            with self.subTest(field=field):
                self.setUp()
                self.reject(
                    lambda: self.installed[A.NEW_TARGET]["fields"][0].update(
                        {field: value}
                    )
                )

    def test_false_boolean_counts_and_unseen_metadata_column(self):
        self.reject(
            lambda: self.after["existing_business"]["tab" + A.EXISTING_TARGETS[0]][
                "new_columns_nonnull"
            ].update(piece_rate_reference=True)
        )
        self.setUp()
        self.reject(
            lambda: self.after["metadata"]["tabDocField"]["columns"].append(
                "unknown_missing_column"
            )
        )

    def test_approved_layout_cannot_be_inferred_from_database_or_plan(self):
        plan = {
            "old_commit": A.OLD,
            "new_commit": A.NEW,
            "source_files": 700,
            "new_table_layout": copy.deepcopy(self.layout),
        }
        self.assertTrue(A.verify_release_plan(plan)["passed"])
        plan["new_table_layout"]["tab" + A.NEW_TARGET]["schema_sha256"] = "f" * 64
        with self.assertRaises(A.AuditError):
            A.verify_release_plan(plan)

    def test_no_arbitrary_patches_or_nonadministrator_actor(self):
        with self.assertRaises(A.AuditError):
            A.verify_snapshot_delta(
                self.before,
                self.after,
                {"other": {}},
                self.schemas,
                self.installed,
                START,
                END,
                "Administrator",
                self.hashes,
            )
        with self.assertRaises(A.AuditError):
            A.verify_snapshot_delta(
                self.before,
                self.after,
                {},
                self.schemas,
                self.installed,
                START,
                END,
                "other",
                self.hashes,
            )

    def test_baseline_all_targets_checked_before_mutation(self):
        original = copy.deepcopy(self.before)
        self.assertTrue(A.verify_baseline(self.before, self.schemas)["passed"])
        self.assertEqual(self.before, original)
        for table, parentfield, field, value in (
            ("tabDocType", None, "allow_import", 1),
            ("tabDocField", "fields", "label", "external customization"),
            ("tabDocPerm", "permissions", "export", 1),
        ):
            with self.subTest(table=table):
                self.setUp()
                target = (
                    A.EXISTING_TARGETS[0]
                    if parentfield == "permissions"
                    else A.EXISTING_TARGETS[1]
                )
                self.before["metadata"][table]["new_parents"][target][0][field] = value
                with self.assertRaises(A.AuditError):
                    A.verify_baseline(self.before, self.schemas)


def source_package():
    """Small complete inventory model for the independently pinned collector."""
    paths = {
        "app/jifeng_erp/hooks.py",
        *A.SCHEMA_PATHS.values(),
        *A.NATIVE_FORM_JS.values(),
        *A.PAGE_JS.values(),
        *("app/jifeng_erp/" + path for path in A.DOCTYPE_JS.values()),
    }
    hooks_source = (ROOT / "jifeng_erp/hooks.py").read_bytes()
    tree = A.ast.parse(hooks_source)
    asset_paths = {}
    for statement in tree.body:
        if isinstance(statement, A.ast.Assign) and isinstance(
            statement.value, A.ast.List
        ):
            for target in statement.targets:
                if isinstance(target, A.ast.Name) and target.id in {
                    "app_include_css",
                    "app_include_js",
                }:
                    asset_paths[target.id] = A.ast.literal_eval(statement.value)
    urls = asset_paths["app_include_css"] + asset_paths["app_include_js"]
    paths.update(
        "app/jifeng_erp/public/" + path.removeprefix("/assets/jifeng_erp/")
        for path in urls
    )
    contents = {path: (ROOT / path.removeprefix("app/")).read_bytes() for path in paths}
    git = "app/.git/"
    contents.update(
        {
            git + "HEAD": (A.NEW + "\n").encode(),
            git + "shallow": (A.NEW + "\n").encode(),
            git + "refs/tags/jifeng-release-pinned": (A.NEW + "\n").encode(),
            git + "config": b"[core]\n repositoryformatversion = 0\n",
            git + "objects/pack/pack-" + "a" * 40 + ".pack": b"model pack",
            git + "objects/pack/pack-" + "a" * 40 + ".idx": b"model index",
        }
    )
    files = [
        {
            "path": path,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": 0o644,
        }
        for path, content in sorted(contents.items())
    ]
    inventory = {
        path: {"kind": "file", "mode": 0o644, "content": content}
        for path, content in contents.items()
    }
    for path in contents:
        for parent in A.PurePosixPath(path).parents:
            if parent.as_posix() != ".":
                inventory[parent.as_posix()] = {"kind": "directory", "mode": 0o755}
    hooks = [
        url
        + "?v="
        + hashlib.sha256(
            contents["app/jifeng_erp/public/" + url.removeprefix("/assets/jifeng_erp/")]
        ).hexdigest()[:16]
        for url in urls
    ]
    return {"commit": A.NEW, "files": files, "hooks": hooks}, inventory


class SourcePackageAuditTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.inventory = source_package()

    def check(self):
        return A.verify_source_manifest(self.manifest, self.inventory)

    def test_assets_forms_page_and_raw_source_pins(self):
        result = self.check()
        self.assertEqual(result["hook_count"], 31)
        self.assertEqual(len(self.manifest["hooks"]), A.CSS_COUNT + A.JS_COUNT)
        self.assertEqual(set(result["native_form_js"]), set(A.NATIVE_FORM_JS))
        self.assertEqual(set(result["page_js"]), {"jifeng-wage-workbench"})
        self.assertEqual(set(result["doctype_js"]), set(A.DOCTYPE_JS))
        for category, paths in (
            ("native_form_js", A.NATIVE_FORM_JS),
            ("page_js", A.PAGE_JS),
        ):
            for name, path in paths.items():
                content = self.inventory[path]["content"]
                self.assertEqual(
                    result[category][name],
                    {
                        "path": path,
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    },
                )
        self.assertEqual(result["schemas"], source_schemas())

    def test_missing_native_form_or_page_source_is_rejected(self):
        for path in [*A.NATIVE_FORM_JS.values(), *A.PAGE_JS.values()]:
            with self.subTest(path=path):
                self.setUp()
                # Remove all inventory-only parent directories that became empty.
                del self.inventory[path]
                self.manifest["files"] = [
                    item for item in self.manifest["files"] if item["path"] != path
                ]
                used = {
                    parent.as_posix()
                    for item in self.manifest["files"]
                    for parent in A.PurePosixPath(item["path"]).parents
                    if parent.as_posix() != "."
                }
                self.inventory = {
                    name: item
                    for name, item in self.inventory.items()
                    if item["kind"] == "file" or name in used
                }
                with self.assertRaises(A.AuditError):
                    self.check()

    def test_no_stale_content_hook_or_count(self):
        for mutation in (
            lambda: self.manifest["hooks"].pop(),
            lambda: self.manifest["hooks"].__setitem__(
                0, self.manifest["hooks"][0][:-16] + "0" * 16
            ),
            lambda: self.inventory[A.PAGE_JS["jifeng-wage-workbench"]].update(
                content=b"stale page"
            ),
        ):
            with self.subTest(mutation=mutation):
                self.setUp()
                mutation()
                with self.assertRaises(A.AuditError):
                    self.check()

    def test_manifest_wrong_commit_duplicate_file_extra_credentials_or_symlink(self):
        for mutation in (
            lambda: self.manifest.update(commit=A.OLD),
            lambda: self.manifest["files"].append(
                copy.deepcopy(self.manifest["files"][0])
            ),
            lambda: self.manifest["files"][0].update(path="app/output/credentials.env"),
            lambda: self.inventory["app"].update(kind="symlink"),
            lambda: self.inventory["app/jifeng_erp/hooks.py"].update(mode=0o666),
        ):
            with self.subTest(mutation=mutation):
                self.setUp()
                mutation()
                with self.assertRaises(A.AuditError):
                    self.check()

    def test_wrong_git_ref_or_unlisted_inventory_file(self):
        self.inventory["app/.git/HEAD"]["content"] = (A.OLD + "\n").encode()
        with self.assertRaises(A.AuditError):
            self.check()
        self.setUp()
        self.inventory["app/unlisted.py"] = {
            "kind": "file",
            "mode": 0o644,
            "content": b"unexpected",
        }
        with self.assertRaises(A.AuditError):
            self.check()


if __name__ == "__main__":
    unittest.main()
