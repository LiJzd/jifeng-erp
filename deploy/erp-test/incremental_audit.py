"""Pure audits for the 5aa4b3b -> 4d71c35 ERP test release.

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


TARGETS = ("Jifeng Customer Profile", "Jifeng Order Dictionary")
NEW_TABLES = {"tab" + name for name in TARGETS}
MYISAM = {"tabData Import Log", "tabError Log", "__global_search"}
META = {
    "tabDocType": None,
    "tabDocField": "fields",
    "tabDocPerm": "permissions",
    "tabDocType Action": "actions",
    "tabDocType Link": "links",
    "tabDocType State": "states",
}
PATCHES = {"dictionary_type": {"label", "options"}, "value": {"description"}}
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


def verify_source_manifest(manifest, observed_inventory):
    """Check {commit, files:[path,bytes,sha256,mode], hooks:[25 URLs]}.

    Inventory includes app and all descendants: directories {kind,mode}, files
    {kind,mode,content:bytes}. Modes and SHA are measured, never defaulted. The
    caller handles read-only bind identity; this function cannot inspect mounts.
    """
    _keys(manifest, {"commit", "files", "hooks"}, "source_manifest")
    _require(
        isinstance(manifest["commit"], str) and COMMIT.fullmatch(manifest["commit"]),
        "commit_required",
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
        and len(hooks) == 25
        and all(isinstance(url, str) for url in hooks)
        and len(set(hooks)) == 25,
        "exact_25_hooks",
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
        declared = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and isinstance(
                statement.value, ast.List
            ):
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "app_include_css",
                        "app_include_js",
                    }:
                        _require(
                            target.id not in declared, "source_hooks_duplicate_list"
                        )
                        declared[target.id] = ast.literal_eval(statement.value)
    except (SyntaxError, ValueError, UnicodeError):
        raise AuditError("source_hooks_literal_lists") from None
    _keys(declared, {"app_include_css", "app_include_js"}, "source_hooks")
    _require(
        declared["app_include_css"] + declared["app_include_js"]
        == [url.split("?v=")[0] for url in hooks],
        "exact_source_hook_list",
    )
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
    return {
        "passed": True,
        "source_files": len(expected),
        "hook_count": 25,
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
        expected = (
            {"request_id", "binding_key"} if name == TARGETS[0] else {"request_id"}
        )
        _require(uniques == expected, "exact_unique_fields", name)
        _indexes(name, actual["indexes"], uniques)
    return {"passed": True, "new_doctypes": list(TARGETS)}


def _snapshot(value, count):
    _keys(value, {"tables", "metadata"}, "snapshot")
    tables = value["tables"]
    _require(isinstance(tables, dict) and len(tables) == count, "physical_table_count")
    _require(
        {"__Auth", "__global_search", *META} <= set(tables),
        "protected_physical_table_missing",
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
            row["engine"] == ("MyISAM" if name in MYISAM else "InnoDB"), "table_engine"
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
        _keys(partition["targets"], PATCHES if table == "tabDocField" else (), table)
        owned = []
        for parent, rows in partition["new_parents"].items():
            _require(isinstance(rows, list), "metadata_rows", table)
            for row in rows:
                _keys(row, columns, table)
                _require(
                    (row["name"] if table == "tabDocType" else row.get("parent"))
                    == parent,
                    "exact_new_parent_scope",
                    table,
                )
            owned.extend(rows)
        for field, row in partition["targets"].items():
            _keys(row, columns, table)
            _require(
                row.get("parent") == "Jifeng Customer Type"
                and row.get("fieldname") == field,
                "exact_docfield_identity",
                table,
            )
            owned.append(row)
        names = [row["name"] for row in owned]
        _require(
            all(isinstance(name, str) and name for name in names)
            and len(names) == len(set(names)),
            "unique_metadata_primary_keys",
            table,
        )
        _require(
            tables[table]["rows"] == partition["outside"]["rows"] + len(owned),
            "metadata_partition_count",
            table,
        )


def _new_metadata(after, schemas, installed, migration_hash, start, end, actor):
    for table, parentfield in META.items():
        for name in TARGETS:
            rows = after["metadata"][table]["new_parents"][name]
            expected = (
                [schemas[name]]
                if parentfield is None
                else schemas[name].get(parentfield, [])
            )
            installed_rows = (
                [installed[name]["doctype"]]
                if parentfield is None
                else installed[name][parentfield]
            )
            _require(rows == installed_rows, "installed_full_rows_differ", table)
            _require(len(rows) == len(expected), "exact_new_metadata_count", table)
            for index, row in enumerate(rows, 1):
                _require(
                    row.get("owner") == actor
                    and row.get("modified_by") == actor
                    and start
                    <= _time(row.get("creation"))
                    <= _time(row.get("modified"))
                    <= end,
                    "new_metadata_audit_fields",
                    table,
                )
                _require(
                    type(row.get("docstatus")) is int
                    and row["docstatus"] == 0
                    and type(row.get("idx")) is int
                    and row["idx"] == (0 if parentfield is None else index),
                    "new_metadata_status",
                    table,
                )
                if parentfield is None:
                    _require(
                        row["migration_hash"] == migration_hash[name],
                        "migration_hash",
                        table,
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
    """Verify exactly 927 -> 929 physical tables and this batch's metadata delta.

    Metadata outside hashes exclude only the two new parent names and the exact
    two old DocField primary keys. Every owned row contains all SQL columns.
    Callers provide stable column/index hashes and sorted full-row content hashes.
    No input is modified; rejection never exposes row values.
    """
    start, end = _time(window_start), _time(window_end)
    _require(
        start <= end and isinstance(actor, str) and bool(actor), "audit_window_or_actor"
    )
    _keys(migration_hash, TARGETS, "migration_hashes")
    for value in migration_hash.values():
        _require(
            isinstance(value, str) and MD5.fullmatch(value), "migration_md5_required"
        )
    verify_new_definitions(schemas, installed)
    _snapshot(before, 927)
    _snapshot(after, 929)
    _require(
        not NEW_TABLES & set(before["tables"])
        and set(after["tables"]) == set(before["tables"]) | NEW_TABLES,
        "exact_added_tables",
    )
    for name in NEW_TABLES:
        table = after["tables"][name]
        _require(
            table["rows"] == 0 and table["content_sha256"] == fingerprint([]),
            "empty_new_table",
            name,
        )
        _require(
            table["indexes_sha256"]
            == fingerprint(installed[name.removeprefix("tab")]["indexes"]),
            "installed_index_proof",
            name,
        )
    for table, prior in before["tables"].items():
        current = after["tables"][table]
        if table not in META:
            _require(prior == current, "protected_table_changed", table)
        else:
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
                left["columns"] == right["columns"]
                and left["outside"] == right["outside"],
                "outside_metadata_changed",
                table,
            )
            _require(
                all(not rows for rows in left["new_parents"].values()),
                "new_parent_already_present",
                table,
            )
            if prior["rows"] == current["rows"] and left["targets"] == right["targets"]:
                _require(
                    prior["content_sha256"] == current["content_sha256"],
                    "unchanged_metadata_content_changed",
                    table,
                )
    _keys(docfield_changes, PATCHES, "docfield_changes")
    for field, allowed in PATCHES.items():
        _keys(docfield_changes[field], allowed, "docfield_patch")
        previous = before["metadata"]["tabDocField"]["targets"][field]
        current = after["metadata"]["tabDocField"]["targets"][field]
        for key, patch in docfield_changes[field].items():
            _keys(patch, {"old", "new"}, "docfield_value")
            _require(
                previous.get(key) == patch["old"] and current.get(key) == patch["new"],
                "exact_docfield_patch",
                field,
            )
        _require(
            all(
                previous[key] == current[key]
                for key in previous
                if key not in allowed | {"modified", "modified_by"}
            ),
            "other_docfield_property_changed",
            field,
        )
        _require(
            start <= _time(current["modified"]) <= end
            and _time(current["modified"]) >= _time(previous["modified"])
            and current["modified_by"] == actor,
            "docfield_audit_fields",
            field,
        )
    _new_metadata(after, schemas, installed, migration_hash, start, end, actor)
    return {
        "passed": True,
        "before_tables": 927,
        "after_tables": 929,
        "new_empty_tables": sorted(NEW_TABLES),
        "myisam": sorted(MYISAM),
    }
