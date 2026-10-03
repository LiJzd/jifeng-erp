"""Pure audits for the 18e5835 -> 2ca4358 ERP test release.

No filesystem, SQL, process or network operations occur here. The caller must
collect every physical table and exact SQL partitions from one protected
snapshot. Hashes supplied by that collector prove comparisons, not provenance.
Use schemas returned from verify_source_manifest, never reconstructed expected
definitions from the installed metadata being checked.
"""

import ast
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import PurePosixPath
import re


OLD = "18e5835cfda4171ec691d5e701c8f42018b5448c"
NEW = "2ca4358398cf750da31584fc31d3afaef48b30b6"
TARGETS = (
    "Jifeng Wage Entry",
    "Jifeng Wage Allocation Employee",
    "Jifeng Salary History Rule",
)
EXISTING_TARGETS = TARGETS[:2]
NEW_TARGET = TARGETS[2]
NEW_TABLES = {"tab" + NEW_TARGET}
BEFORE_TABLES, AFTER_TABLES = 931, 932
HOOK_COUNT, CSS_COUNT, JS_COUNT = 31, 11, 20
DOCTYPE_JS = {
    "Sales Order": "public/js/jifeng_sales_order.js",
    "Production Plan": "public/js/jifeng_production_instruction.js",
    "Work Order": "public/js/jifeng_work_order.js",
    "Employee Promotion": "public/js/jifeng_personnel_change.js",
    "Employee Transfer": "public/js/jifeng_personnel_change.js",
    "Employee Separation": "public/js/jifeng_employee_separation.js",
}
NATIVE_FORM_JS = {
    name: "app/jifeng_erp/jifeng_erp/doctype/"
    + name.lower().replace(" ", "_")
    + "/"
    + name.lower().replace(" ", "_")
    + ".js"
    for name in ("Jifeng Wage Entry", "Jifeng Wage Allocation", NEW_TARGET)
}
PAGE_JS = {
    "jifeng-wage-workbench": "app/jifeng_erp/jifeng_erp/page/jifeng_wage_workbench/jifeng_wage_workbench.js"
}
NEW_DATA_FIELDS = ("piece_rate_reference", "piece_rate_reference_version")
NEW_FIELDS = {
    EXISTING_TARGETS[0]: NEW_DATA_FIELDS,
    EXISTING_TARGETS[1]: ("call_history_rate", *NEW_DATA_FIELDS),
}
UNIQUE_FIELDS = {
    EXISTING_TARGETS[0]: {"workbench_request_key"},
    EXISTING_TARGETS[1]: set(),
    NEW_TARGET: {"binding_key"},
}
SEQUENCES = {
    "bisect_nodes_id_seq",
    "crm_note_id_seq",
    "ledger_health_id_seq",
    "prospect_opportunity_id_seq",
    "pwa_notification_id_seq",
    "web_form_list_column_id_seq",
}
# Independent native measurement, never inferred from the target database.
APPROVED_NEW_TABLES = {
    "tabJifeng Salary History Rule": {
        "schema_sha256": "afff7480ed7a7e43ccd41e55c541f157484ee0805f798de12570d0ff75861d86",
        "indexes_sha256": "524594c65ff95ddd2aca7341611ac1e6b0e4b86fc0b108307227eba16f9d4c40",
    }
}
MYISAM = {"tabData Import Log", "tabError Log", "__global_search"}
META = {
    "tabDocType": None,
    "tabDocField": "fields",
    "tabDocPerm": "permissions",
    "tabDocType Action": "actions",
    "tabDocType Link": "links",
    "tabDocType State": "states",
}
PATCHES = {}
SCHEMA_PATHS = {
    name: "app/jifeng_erp/jifeng_erp/doctype/"
    + name.lower().replace(" ", "_")
    + "/"
    + name.lower().replace(" ", "_")
    + ".json"
    for name in TARGETS
}
TABLE_KEYS = {"engine", "rows", "content_sha256", "schema_sha256", "indexes_sha256"}
INDEX_REQUIRED = {
    "Table",
    "Non_unique",
    "Key_name",
    "Seq_in_index",
    "Column_name",
    "Collation",
    "Sub_part",
    "Packed",
    "Null",
    "Index_type",
    "Comment",
    "Index_comment",
}
INDEX_OPTIONAL = {"Ignored", "Visible", "Expression"}
TECHNICAL = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
CHILD_IDENTITY = {"parent", "parenttype", "parentfield"}
# In-import skips Document._set_defaults. The only nonempty, undeclared header
# values in the isolated native SQL reference are these four SQL defaults.
# DocPerm's interactive defaults must NOT grant undeclared delete/export/etc.
HEADER_DEFAULTS = {
    "sort_field": "creation",
    "sort_order": "DESC",
    "engine": "InnoDB",
    "row_format": "Dynamic",
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
MD5 = re.compile(r"[0-9a-f]{32}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class AuditError(ValueError):
    """Rule and public location only; never include row values or byte contents."""


def _require(condition, rule, location=""):
    if not condition:
        raise AuditError(rule + (":" + location if location else ""))


def _keys(value, expected, location):
    _require(
        isinstance(value, dict) and set(value) == set(expected), "exact_keys", location
    )


def _count(value, location):
    _require(type(value) is int and value >= 0, "nonnegative_integer", location)


def _hash(value, location):
    _require(
        isinstance(value, str) and SHA.fullmatch(value), "sha256_required", location
    )


def _wire(value):
    if isinstance(value, (datetime, date, time)):
        return {"type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, timedelta):
        return {
            "type": "timedelta",
            "value": [value.days, value.seconds, value.microseconds],
        }
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value), "string_keys_required")
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    _require(
        value is None or type(value) in (str, int, bool, float),
        "unsupported_proof_value",
    )
    return value


def fingerprint(value):
    """Canonical projection hash shared with the trusted collector; row order matters.

    The collector must sort SQL rows by their true primary key and indexes by
    Key_name/Seq_in_index. Empty-table content is fingerprint([]).
    """
    data = json.dumps(
        _wire(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(data).hexdigest()


def _path(value):
    _require(
        isinstance(value, str) and value.startswith("app/") and "\\" not in value,
        "relative_source_path",
    )
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and path.as_posix() == value
        and not any(
            part
            in {
                ".",
                "..",
                "",
                "output",
                "sites",
                "logs",
                "__pycache__",
                "node_modules",
                ".env",
            }
            for part in path.parts
        ),
        "source_path_scope",
    )
    return path


def verify_release_plan(plan):
    """Pin this batch and the separately measured isolated native SQL reference."""
    _keys(
        plan,
        {"old_commit", "new_commit", "source_files", "new_table_layout"},
        "release_plan",
    )
    _require(
        plan["old_commit"] == OLD and plan["new_commit"] == NEW, "release_plan_commits"
    )
    _require(
        type(plan["source_files"]) is int and plan["source_files"] > 0,
        "new_release_source_files",
    )
    _keys(plan["new_table_layout"], NEW_TABLES, "new_table_layout")
    for name, layout in plan["new_table_layout"].items():
        _keys(layout, {"schema_sha256", "indexes_sha256"}, name)
        for value in layout.values():
            _hash(value, "approved_layout_hash")
    _require(plan["new_table_layout"] == APPROVED_NEW_TABLES, "approved_native_layout")
    return {"passed": True, "source_files": plan["source_files"]}


def verify_source_manifest(manifest, observed_inventory):
    """Check {commit, files:[path,bytes,sha256,mode], hooks:[31 URLs]}.

    Inventory includes app and all descendants: directories {kind,mode}, files
    {kind,mode,content:bytes}. Modes and SHA are measured, never defaulted. The
    caller handles read-only bind identity; this function cannot inspect mounts.
    """
    _keys(manifest, {"commit", "files", "hooks"}, "source_manifest")
    _require(
        manifest["commit"] == NEW,
        "new_release_commit_required",
    )
    _require(
        isinstance(manifest["files"], list) and isinstance(observed_inventory, dict),
        "inventory_required",
    )
    expected, directories = {}, {"app"}
    for item in manifest["files"]:
        _keys(item, {"path", "bytes", "sha256", "mode"}, "source_file")
        path = _path(item["path"])
        _require(item["path"] not in expected, "duplicate_source_path")
        _count(item["bytes"], "source_bytes")
        _hash(item["sha256"], "source_file")
        _require(
            type(item["mode"]) is int and item["mode"] == 0o644, "source_file_mode"
        )
        expected[item["path"]] = item
        directories.update(
            parent.as_posix() for parent in path.parents if parent.as_posix() != "."
        )
    # Minimal shallow Git packages keep these two empty directories. They have
    # no file entries; only their exact names and ordinary directory modes pass.
    directories.update(
        set(observed_inventory) & {"app/.git/objects/info", "app/.git/refs/heads"}
    )
    _require(
        set(observed_inventory) == set(expected) | directories, "exact_inventory_set"
    )
    for path, observed in observed_inventory.items():
        if path in directories:
            _keys(observed, {"kind", "mode"}, "source_directory")
            _require(
                observed["kind"] == "directory"
                and type(observed["mode"]) is int
                and observed["mode"] == 0o755,
                "source_directory_mode_or_symlink",
            )
        else:
            _keys(observed, {"kind", "mode", "content"}, "source_file")
            _require(
                observed["kind"] == "file"
                and type(observed["mode"]) is int
                and observed["mode"] == expected[path]["mode"],
                "source_file_mode_or_symlink",
            )
            _require(isinstance(observed["content"], bytes), "source_bytes_required")
            _require(
                len(observed["content"]) == expected[path]["bytes"]
                and hashlib.sha256(observed["content"]).hexdigest()
                == expected[path]["sha256"],
                "source_content_changed",
            )
    git = "app/.git/"
    fixed = {
        git + "HEAD",
        git + "config",
        git + "shallow",
        git + "refs/tags/jifeng-release-pinned",
    }
    _require(fixed <= set(expected), "minimal_git_missing")
    commit = manifest["commit"]
    try:
        git_refs = [
            observed_inventory[path]["content"].decode("ascii").strip()
            for path in (
                git + "HEAD",
                git + "shallow",
                git + "refs/tags/jifeng-release-pinned",
            )
        ]
        config = observed_inventory[git + "config"]["content"].decode("utf-8")
    except UnicodeError:
        raise AuditError("git_encoding") from None
    _require(all(ref == commit for ref in git_refs), "git_commit_not_exact")
    _require(
        not re.search(r"(?im)^\s*\[\s*(remote|include|includeif)\b", config),
        "git_remote_or_include",
    )
    packs = {
        path[:-5]
        for path in expected
        if re.fullmatch(r"app/\.git/objects/pack/pack-[0-9a-f]{40}\.pack", path)
    }
    _require(
        len(packs) == 1 and all(path + ".idx" in expected for path in packs),
        "minimal_git_pack_missing",
    )
    git_allowed = (
        fixed
        | {git + "index"}
        | {base + ext for base in packs for ext in (".pack", ".idx", ".rev")}
    )
    _require(
        {path for path in expected if path.startswith(git)} <= git_allowed,
        "minimal_git_extra_file",
    )
    hooks = manifest["hooks"]
    _require(
        isinstance(hooks, list)
        and len(hooks) == HOOK_COUNT
        and all(isinstance(url, str) for url in hooks)
        and len(set(hooks)) == HOOK_COUNT,
        "exact_31_hooks",
    )
    for url in hooks:
        _require(
            isinstance(url, str)
            and re.fullmatch(r"/assets/jifeng_erp/[^?]+\?v=[0-9a-f]{16}", url),
            "hook_url",
        )
        plain, version = url.split("?v=")
        path = "app/jifeng_erp/public/" + plain.removeprefix("/assets/jifeng_erp/")
        _require(
            path in expected and expected[path]["sha256"][:16] == version,
            "hook_fingerprint",
        )
    hooks_path = "app/jifeng_erp/hooks.py"
    _require(hooks_path in expected, "source_hooks_missing")
    try:
        tree = ast.parse(observed_inventory[hooks_path]["content"])
        declared, doctype_js = {}, None
        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id
                        in {
                            "app_include_css",
                            "app_include_js",
                        }
                        and isinstance(statement.value, ast.List)
                    ):
                        _require(
                            target.id not in declared, "source_hooks_duplicate_list"
                        )
                        declared[target.id] = ast.literal_eval(statement.value)
                    if isinstance(target, ast.Name) and target.id == "doctype_js":
                        _require(doctype_js is None, "source_doctype_js_duplicate")
                        doctype_js = ast.literal_eval(statement.value)
    except (SyntaxError, ValueError, UnicodeError):
        raise AuditError("source_hooks_literal_lists") from None
    _keys(declared, {"app_include_css", "app_include_js"}, "source_hooks")
    _require(
        len(declared["app_include_css"]) == CSS_COUNT
        and len(declared["app_include_js"]) == JS_COUNT,
        "exact_11_css_20_js",
    )
    _require(
        all(
            isinstance(path, str) and path.endswith(".css")
            for path in declared["app_include_css"]
        )
        and all(
            isinstance(path, str) and path.endswith(".js")
            for path in declared["app_include_js"]
        ),
        "source_hook_asset_types",
    )
    _require(
        declared["app_include_css"] + declared["app_include_js"]
        == [url.split("?v=")[0] for url in hooks],
        "exact_source_hook_list",
    )
    _require(
        isinstance(doctype_js, dict) and doctype_js == DOCTYPE_JS,
        "exact_doctype_js_hooks",
    )
    form_scripts = {}
    for name, relative in DOCTYPE_JS.items():
        path = "app/jifeng_erp/" + relative
        _require(
            path in expected and expected[path]["bytes"] > 0,
            "doctype_js_source_missing",
            name,
        )
        form_scripts[name] = {
            "path": path,
            "bytes": expected[path]["bytes"],
            "sha256": expected[path]["sha256"],
        }
    schemas, hashes, migrations = {}, {}, {}
    for name, path in SCHEMA_PATHS.items():
        _require(path in expected, "target_schema_missing", name)
        try:
            schemas[name] = json.loads(observed_inventory[path]["content"])
        except (ValueError, UnicodeError):
            raise AuditError("target_schema_json") from None
        hashes[name] = expected[path]["sha256"]
        migrations[name] = hashlib.md5(
            observed_inventory[path]["content"], usedforsecurity=False
        ).hexdigest()
    native_form_js, page_js = {}, {}
    for collection, paths in ((native_form_js, NATIVE_FORM_JS), (page_js, PAGE_JS)):
        for name, path in paths.items():
            _require(
                path in expected and expected[path]["bytes"] > 0,
                "native_script_source_missing",
                name,
            )
            collection[name] = {
                "path": path,
                "bytes": expected[path]["bytes"],
                "sha256": expected[path]["sha256"],
            }
    return {
        "passed": True,
        "native_form_js": native_form_js,
        "page_js": page_js,
        "source_files": len(expected),
        "hook_count": HOOK_COUNT,
        "doctype_js": form_scripts,
        "schemas": schemas,
        "schema_sha256": hashes,
        "migration_hash": migrations,
    }


def _time(value):
    if isinstance(value, dict):
        _keys(value, {"type", "value"}, "sql_datetime")
        _require(value["type"] == "datetime", "naive_sql_datetime_required")
        value = value["value"]
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise AuditError("naive_sql_datetime_required") from None
    _require(parsed.tzinfo is None, "naive_sql_datetime_required")
    return parsed


def _indexes(name, indexes, unique_fields):
    _require(isinstance(indexes, list) and bool(indexes), "index_proof_required", name)
    groups = {}
    for row in indexes:
        _require(
            isinstance(row, dict)
            and INDEX_REQUIRED <= set(row) <= INDEX_REQUIRED | INDEX_OPTIONAL,
            "index_columns",
            name,
        )
        _require(
            row["Table"] == "tab" + name
            and row["Index_type"] == "BTREE"
            and row.get("Ignored", "NO") == "NO"
            and row.get("Visible", "YES") == "YES"
            and row.get("Expression") in (None, ""),
            "index_shape",
            name,
        )
        _require(
            type(row["Non_unique"]) is int and row["Non_unique"] in (0, 1),
            "index_unique_flag",
            name,
        )
        _require(
            isinstance(row["Key_name"], str)
            and bool(row["Key_name"])
            and isinstance(row["Column_name"], str)
            and bool(row["Column_name"])
            and type(row["Seq_in_index"]) is int,
            "index_identity",
            name,
        )
        groups.setdefault(row["Key_name"], []).append(row)
    found = set()
    for key, rows in groups.items():
        _require(
            [row["Seq_in_index"] for row in rows] == list(range(1, len(rows) + 1)),
            "index_sequence",
            name,
        )
        _require(
            len({row["Non_unique"] for row in rows}) == 1, "index_unique_flag", name
        )
        if rows[0]["Non_unique"] == 0:
            _require(
                len(rows) == 1 and rows[0]["Sub_part"] is None,
                "single_full_unique_index",
                name,
            )
            field = rows[0]["Column_name"]
            _require(
                key == ("PRIMARY" if field == "name" else field) and field not in found,
                "unique_index_identity",
                name,
            )
            found.add(field)
    _require(found == {"name", *unique_fields}, "exact_unique_indexes", name)


def _definition_row(row, declared, parentfield, location):
    _require(isinstance(row, dict), "raw_metadata_row", location)
    skipped = TECHNICAL | (
        {
            "doctype",
            "fields",
            "field_order",
            "permissions",
            "actions",
            "links",
            "states",
        }
        if parentfield is None
        else CHILD_IDENTITY
    )
    for key, value in declared.items():
        if key not in skipped:
            _require(
                key in row and row[key] == value, "new_declared_property", location
            )
    for key, value in row.items():
        if (
            key in skipped
            or key in declared
            or (key == "migration_hash" and parentfield is None)
        ):
            continue
        default = HEADER_DEFAULTS.get(key) if parentfield is None else None
        _require(
            value == default if default is not None else value in (None, "", 0),
            "undeclared_metadata_property",
            location,
        )


def verify_new_definitions(schemas, installed):
    """Installed DTO: doctype (full SQL row), five full SQL child lists, indexes.

    Lists are ordered by idx; source JSON comes from verify_source_manifest.
    Indexes are SHOW INDEX full rows minus unstable Cardinality, sorted by
    Key_name/Seq_in_index. SQL identity/audit fields are checked by delta below.
    """
    _keys(schemas, TARGETS, "schemas")
    _keys(installed, TARGETS, "installed")
    for name in TARGETS:
        source, actual = schemas[name], installed[name]
        _require(
            isinstance(source, dict)
            and source.get("doctype") == "DocType"
            and source.get("name") == name,
            "schema_identity",
            name,
        )
        if name == NEW_TARGET:
            _require(
                source.get("permissions")
                == [
                    {"role": "HR User", "read": 1, "select": 1},
                    {
                        "role": "HR Manager",
                        "read": 1,
                        "select": 1,
                        "create": 1,
                        "write": 1,
                        "submit": 1,
                    },
                    {
                        "role": "System Manager",
                        "read": 1,
                        "select": 1,
                        "create": 1,
                        "write": 1,
                        "submit": 1,
                    },
                ],
                "fixed_new_rule_permission_scope",
                name,
            )
        _keys(
            actual,
            {
                "doctype",
                "fields",
                "permissions",
                "actions",
                "links",
                "states",
                "indexes",
            },
            name,
        )
        fields = source.get("fields")
        _require(
            isinstance(fields, list)
            and all(
                isinstance(field, dict)
                and isinstance(field.get("fieldname"), str)
                and field["fieldname"]
                for field in fields
            ),
            "source_fields",
            name,
        )
        order = [field["fieldname"] for field in fields]
        _require(
            len(set(order)) == len(order) and source.get("field_order") == order,
            "exact_field_order",
            name,
        )
        _require(
            isinstance(actual["doctype"], dict)
            and actual["doctype"].get("name") == name,
            "installed_doctype_identity",
            name,
        )
        _definition_row(actual["doctype"], source, None, name)
        for parentfield in ("fields", "permissions", "actions", "links", "states"):
            expected, rows = source.get(parentfield, []), actual[parentfield]
            _require(
                isinstance(expected, list)
                and isinstance(rows, list)
                and len(rows) == len(expected),
                "exact_new_metadata_count",
                name,
            )
            for idx, (row, declared) in enumerate(zip(rows, expected), 1):
                _require(
                    isinstance(declared, dict)
                    and isinstance(row, dict)
                    and row.get("parent") == name
                    and row.get("parenttype") == "DocType"
                    and row.get("parentfield") == parentfield
                    and type(row.get("idx")) is int
                    and row["idx"] == idx,
                    "new_child_identity",
                    name,
                )
                _definition_row(row, declared, parentfield, name)
        uniques = {field["fieldname"] for field in fields if field.get("unique")}
        expected = UNIQUE_FIELDS[name]
        _require(uniques == expected, "exact_unique_fields", name)
        _indexes(name, actual["indexes"], uniques)
    return {"passed": True, "new_doctypes": list(TARGETS)}


COLUMN_KEYS = {
    "Field",
    "Type",
    "Collation",
    "Null",
    "Key",
    "Default",
    "Extra",
    "Comment",
}
STRUCTURE_KEYS = {"ENGINE", "TABLE_COLLATION", "ROW_FORMAT", "TABLE_TYPE"}
BUSINESS_KEYS = {
    "structure",
    "columns",
    "indexes",
    "projected_old_columns",
    "projected_old_rows_sha256",
    "new_columns_nonnull",
}


def _business(value, tables):
    _keys(value, {"tab" + name for name in EXISTING_TARGETS}, "existing_business")
    for name, proof in value.items():
        _keys(proof, BUSINESS_KEYS, name)
        _keys(proof["structure"], STRUCTURE_KEYS, name)
        _require(
            proof["structure"]["ENGINE"] == "InnoDB"
            and proof["structure"]["TABLE_TYPE"] == "BASE TABLE",
            "business_table_structure",
            name,
        )
        columns = proof["columns"]
        _require(isinstance(columns, list) and bool(columns), "business_columns", name)
        for column in columns:
            _keys(column, COLUMN_KEYS, name)
        fields = [column["Field"] for column in columns]
        _require(
            all(isinstance(field, str) and bool(field) for field in fields)
            and len(fields) == len(set(fields)),
            "business_column_identity",
            name,
        )
        projection = proof["projected_old_columns"]
        _require(
            isinstance(projection, list)
            and bool(projection)
            and all(isinstance(field, str) for field in projection)
            and len(projection) == len(set(projection))
            and set(projection) <= set(fields),
            "business_projection_columns",
            name,
        )
        _hash(proof["projected_old_rows_sha256"], name)
        _require(
            isinstance(proof["new_columns_nonnull"], dict), "new_column_counts", name
        )
        for count in proof["new_columns_nonnull"].values():
            _count(count, name)
        _indexes(
            name.removeprefix("tab"),
            proof["indexes"],
            UNIQUE_FIELDS[name.removeprefix("tab")],
        )
        _require(
            tables[name]["schema_sha256"]
            == fingerprint({"structure": proof["structure"], "columns": columns})
            and tables[name]["indexes_sha256"] == fingerprint(proof["indexes"]),
            "business_raw_proof_mismatch",
            name,
        )


def _snapshot(value, count):
    _keys(value, {"tables", "metadata", "existing_business", "sequences"}, "snapshot")
    tables = value["tables"]
    _require(isinstance(tables, dict) and len(tables) == count, "physical_table_count")
    _require(
        {
            "__Auth",
            "__global_search",
            *META,
            *SEQUENCES,
            *("tab" + name for name in EXISTING_TARGETS),
        }
        <= set(tables),
        "protected_physical_table_missing",
    )
    sequences = value["sequences"]
    _require(
        isinstance(sequences, list) and sequences == sorted(SEQUENCES),
        "exact_six_sequences",
    )
    _require(
        {
            name
            for name, row in tables.items()
            if isinstance(row, dict) and row.get("engine") != "InnoDB"
        }
        == MYISAM,
        "exact_three_myisam",
    )
    for name, row in tables.items():
        _keys(row, TABLE_KEYS, "table")
        _require(
            row["engine"] == ("MyISAM" if name in MYISAM else "InnoDB"),
            "table_engine",
            name,
        )
        _count(row["rows"], "table_rows")
        for field in TABLE_KEYS - {"engine", "rows"}:
            _hash(row[field], "table_proof")
    _keys(value["metadata"], META, "metadata")
    for table, partition in value["metadata"].items():
        _keys(partition, {"columns", "outside", "new_parents", "targets"}, table)
        columns = partition["columns"]
        required = TECHNICAL | (
            {"migration_hash"} if table == "tabDocType" else CHILD_IDENTITY
        )
        if table == "tabDocField":
            required |= {"fieldname", "label", "options", "description"}
        _require(
            isinstance(columns, list)
            and all(isinstance(column, str) for column in columns)
            and len(set(columns)) == len(columns)
            and required <= set(columns),
            "metadata_columns",
            table,
        )
        _keys(partition["outside"], {"rows", "sha256"}, table)
        _count(partition["outside"]["rows"], table)
        _hash(partition["outside"]["sha256"], table)
        _keys(partition["new_parents"], TARGETS, table)
        _keys(partition["targets"], (), table)
        owned = []
        for parent, rows in partition["new_parents"].items():
            _require(isinstance(rows, list), "metadata_rows", table)
            for row in rows:
                _keys(row, columns, table)
                _require(
                    (row["name"] if table == "tabDocType" else row.get("parent"))
                    == parent,
                    "exact_parent_scope",
                    table,
                )
            owned.extend(rows)
        names = [row["name"] for row in owned]
        _require(
            all(isinstance(name, str) and bool(name) for name in names)
            and len(names) == len(set(names)),
            "unique_metadata_primary_keys",
            table,
        )
        _require(
            tables[table]["rows"] == partition["outside"]["rows"] + len(owned),
            "metadata_partition_count",
            table,
        )
    _business(value["existing_business"], tables)
    if count == BEFORE_TABLES:
        for table in META:
            _require(
                not value["metadata"][table]["new_parents"][NEW_TARGET],
                "new_metadata_must_be_absent",
                table,
            )
        for target in EXISTING_TARGETS:
            _require(
                len(value["metadata"]["tabDocType"]["new_parents"][target]) == 1,
                "existing_header_count",
                target,
            )
            rows = value["metadata"]["tabDocField"]["new_parents"][target]
            fields = [row["fieldname"] for row in rows]
            _require(
                len(fields) == len(set(fields))
                and not set(NEW_FIELDS[target]) & set(fields),
                "new_docfields_must_be_absent",
                target,
            )
            name = "tab" + target
            proof = value["existing_business"][name]
            columns = [column["Field"] for column in proof["columns"]]
            _require(
                not set(NEW_DATA_FIELDS) & set(columns)
                and proof["new_columns_nonnull"] == {},
                "new_columns_must_be_absent",
                name,
            )
            _require(
                proof["projected_old_columns"] == columns
                and proof["projected_old_rows_sha256"]
                == tables[name]["content_sha256"],
                "baseline_full_column_projection",
                name,
            )


def verify_baseline(before, schemas):
    """Reject all existing-source drift before the first metadata/DDL write.

    Candidate source changes only add the fixed fields. Original audit times,
    names, and migration hashes remain the protected server oracle's values.
    This gate deliberately rejects rather than resets external customizations.
    """
    _snapshot(before, BEFORE_TABLES)
    _keys(schemas, TARGETS, "schemas")
    for target in EXISTING_TARGETS:
        source = schemas[target]
        _require(
            isinstance(source, dict)
            and source.get("name") == target
            and source.get("doctype") == "DocType",
            "source_identity",
            target,
        )
        fields = source.get("fields")
        _require(
            isinstance(fields, list)
            and all(
                isinstance(field, dict) and isinstance(field.get("fieldname"), str)
                for field in fields
            ),
            "source_fields",
            target,
        )
        names = [field["fieldname"] for field in fields]
        _require(
            source.get("field_order") == names and len(names) == len(set(names)),
            "source_field_order",
            target,
        )
        _require(
            set(NEW_FIELDS[target]) <= set(names),
            "candidate_added_fields_present",
            target,
        )
        for field in fields:
            if field["fieldname"] in NEW_DATA_FIELDS:
                _require(
                    field.get("fieldtype") == "Data"
                    and field.get("read_only") == 1
                    and field.get("no_copy") == 1
                    and not field.get("reqd")
                    and not field.get("unique")
                    and field.get("default") in (None, ""),
                    "candidate_nullable_readonly_data_fields",
                    target,
                )
            elif field["fieldname"] == "call_history_rate":
                _require(
                    target == EXISTING_TARGETS[1]
                    and field.get("fieldtype") == "Button",
                    "candidate_button_metadata_only",
                    target,
                )
        for table, parentfield in META.items():
            rows = before["metadata"][table]["new_parents"][target]
            expected = [source] if parentfield is None else source.get(parentfield, [])
            if parentfield == "fields":
                expected = [
                    field
                    for field in expected
                    if field["fieldname"] not in NEW_FIELDS[target]
                ]
            _require(
                isinstance(expected, list) and len(rows) == len(expected),
                "baseline_source_count",
                table,
            )
            for index, (row, declared) in enumerate(zip(rows, expected), 1):
                _definition_row(row, declared, parentfield, target)
                _require(
                    type(row["docstatus"]) is int
                    and row["docstatus"] == 0
                    and type(row["idx"]) is int
                    and row["idx"] == (0 if parentfield is None else index),
                    "baseline_source_order",
                    target,
                )
                if parentfield is not None:
                    _require(
                        row["parenttype"] == "DocType"
                        and row["parentfield"] == parentfield,
                        "baseline_source_child_identity",
                        target,
                    )
    return {
        "passed": True,
        "existing_doctypes": list(EXISTING_TARGETS),
        "source_drift_absent": True,
    }


def _audit_new(row, start, end, actor, parentfield, index, location):
    _require(
        row.get("owner") == actor
        and row.get("modified_by") == actor
        and start <= _time(row.get("creation")) <= _time(row.get("modified")) <= end,
        "new_metadata_audit_fields",
        location,
    )
    _require(
        type(row.get("docstatus")) is int
        and row["docstatus"] == 0
        and type(row.get("idx")) is int
        and row["idx"] == (0 if parentfield is None else index),
        "new_metadata_status",
        location,
    )


def _existing_metadata(before, after, schemas, start, end, actor, migration_hash):
    for target in EXISTING_TARGETS:
        left = before["metadata"]["tabDocType"]["new_parents"][target]
        right = after["metadata"]["tabDocType"]["new_parents"][target]
        _require(len(left) == len(right) == 1, "existing_header_count", target)
        previous, current = left[0], right[0]
        _require(
            all(
                previous[key] == current[key]
                for key in previous
                if key not in {"modified", "modified_by", "migration_hash"}
            ),
            "existing_header_property_changed",
            target,
        )
        _require(
            current["migration_hash"] == migration_hash[target]
            and current["modified_by"] == actor
            and start <= _time(current["modified"]) <= end
            and _time(current["modified"]) >= _time(previous["modified"]),
            "existing_header_audit_fields",
            target,
        )
        previous_fields = before["metadata"]["tabDocField"]["new_parents"][target]
        current_fields = after["metadata"]["tabDocField"]["new_parents"][target]
        prior = {row["fieldname"]: row for row in previous_fields}
        current = {row["fieldname"]: row for row in current_fields}
        _require(
            len(prior) == len(previous_fields) and len(current) == len(current_fields),
            "unique_docfields",
            target,
        )
        _require(
            set(current) == set(prior) | set(NEW_FIELDS[target])
            and not (set(prior) & set(NEW_FIELDS[target])),
            "exact_added_docfields",
            target,
        )
        order = {
            field: index
            for index, field in enumerate(schemas[target]["field_order"], 1)
        }
        _require(set(current) == set(order), "candidate_field_identity", target)
        for field, previous in prior.items():
            row = current[field]
            _require(
                all(previous[key] == row[key] for key in previous if key != "idx")
                and row["idx"] == order[field],
                "existing_docfield_property_changed",
                target,
            )
        for field in NEW_FIELDS[target]:
            _audit_new(
                current[field], start, end, actor, "fields", order[field], target
            )
        for table, parentfield in META.items():
            if parentfield in (None, "fields"):
                continue
            _require(
                before["metadata"][table]["new_parents"][target]
                == after["metadata"][table]["new_parents"][target],
                "existing_metadata_child_changed",
                table,
            )


def _business_delta(before, after):
    for name, previous in before["existing_business"].items():
        current = after["existing_business"][name]
        original_columns = [column["Field"] for column in previous["columns"]]
        _require(
            not set(NEW_DATA_FIELDS) & set(original_columns)
            and previous["projected_old_columns"] == original_columns
            and current["projected_old_columns"] == original_columns
            and previous["projected_old_rows_sha256"]
            == before["tables"][name]["content_sha256"]
            and current["projected_old_rows_sha256"]
            == previous["projected_old_rows_sha256"],
            "old_business_rows_changed",
            name,
        )
        _require(
            previous["new_columns_nonnull"] == {}
            and current["new_columns_nonnull"]
            == {field: 0 for field in NEW_DATA_FIELDS},
            "new_data_columns_not_null",
            name,
        )
        expected_columns = previous["columns"] + [
            {
                "Field": field,
                "Type": "varchar(140)",
                "Collation": previous["structure"]["TABLE_COLLATION"],
                "Null": "YES",
                "Key": "",
                "Default": None,
                "Extra": "",
                "Comment": "",
            }
            for field in NEW_DATA_FIELDS
        ]
        _require(
            previous["structure"] == current["structure"]
            and current["columns"] == expected_columns,
            "exact_nullable_data_columns_only",
            name,
        )
        _require(
            previous["indexes"] == current["indexes"],
            "old_business_indexes_changed",
            name,
        )
        _require(
            all(
                before["tables"][name][key] == after["tables"][name][key]
                for key in ("engine", "rows", "indexes_sha256")
            ),
            "old_business_table_changed",
            name,
        )


def verify_snapshot_delta(
    before,
    after,
    docfield_changes,
    schemas,
    installed,
    window_start,
    window_end,
    actor,
    migration_hash,
):
    """Verify a trusted collector's exact protected server 931 -> 932 delta.

    Collect every sequence value and three MyISAM tables from protected reads.
    Existing business rows are projected on the *before* physical columns and
    sorted by their original primary key. Metadata rows retain every SQL column.
    All hashes are supplied by the pinned collector; this pure function neither
    establishes SQL provenance nor accepts a local whole-site oracle.
    """
    start, end = _time(window_start), _time(window_end)
    _require(start <= end and actor == "Administrator", "audit_window_or_actor")
    _keys(docfield_changes, (), "no_undeclared_docfield_patches")
    _keys(migration_hash, TARGETS, "migration_hashes")
    for value in migration_hash.values():
        _require(
            isinstance(value, str) and MD5.fullmatch(value), "migration_md5_required"
        )
    verify_new_definitions(schemas, installed)
    verify_baseline(before, schemas)
    _snapshot(after, AFTER_TABLES)
    _require(
        not NEW_TABLES & set(before["tables"])
        and set(after["tables"]) == set(before["tables"]) | NEW_TABLES,
        "exact_added_tables",
    )
    for name in NEW_TABLES:
        proof = after["tables"][name]
        _require(
            proof["rows"] == 0 and proof["content_sha256"] == fingerprint([]),
            "empty_new_table",
            name,
        )
        _keys(APPROVED_NEW_TABLES, NEW_TABLES, "approved_native_layout_missing")
        _require(
            all(
                proof[key] == expected
                for key, expected in APPROVED_NEW_TABLES[name].items()
            ),
            "approved_native_layout",
            name,
        )
        _require(
            proof["indexes_sha256"] == fingerprint(installed[NEW_TARGET]["indexes"]),
            "installed_index_proof",
            name,
        )
    for table, prior in before["tables"].items():
        current = after["tables"][table]
        if table in {"tab" + name for name in EXISTING_TARGETS}:
            continue
        if table not in META:
            _require(prior == current, "protected_table_changed", table)
            continue
        _require(
            all(
                prior[key] == current[key]
                for key in ("engine", "schema_sha256", "indexes_sha256")
            ),
            "existing_metadata_schema_or_index_changed",
            table,
        )
        left, right = before["metadata"][table], after["metadata"][table]
        _require(
            left["columns"] == right["columns"] and left["outside"] == right["outside"],
            "outside_metadata_changed",
            table,
        )
        _require(
            not left["new_parents"][NEW_TARGET], "new_parent_already_present", table
        )
        for target in TARGETS:
            rows = right["new_parents"][target]
            actual = (
                [installed[target]["doctype"]]
                if META[table] is None
                else installed[target][META[table]]
            )
            _require(rows == actual, "installed_full_rows_differ", table)
        if all(
            left["new_parents"][target] == right["new_parents"][target]
            for target in TARGETS
        ):
            _require(prior == current, "unchanged_metadata_content_changed", table)
        for index, row in enumerate(right["new_parents"][NEW_TARGET], 1):
            _audit_new(row, start, end, actor, META[table], index, table)
            if META[table] is None:
                _require(
                    row["migration_hash"] == migration_hash[NEW_TARGET],
                    "migration_hash",
                    table,
                )
    _existing_metadata(before, after, schemas, start, end, actor, migration_hash)
    _business_delta(before, after)
    return {
        "passed": True,
        "protected_objects": BEFORE_TABLES,
        "total_objects": AFTER_TABLES,
        "new_empty_doctypes": [NEW_TARGET],
        "updated_doctypes": list(EXISTING_TARGETS),
        "new_nullable_data_columns": 4,
        "old_permissions_unchanged": True,
    }
