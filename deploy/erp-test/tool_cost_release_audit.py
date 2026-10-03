"""Pure checks for two new tool-cost DocTypes after the 2ca4358 release.

The caller collects full SQL rows and all physical objects in one protected
snapshot, including sequence values and MyISAM tables. This module performs no
filesystem, SQL, process or network operations. Collector provenance, backups,
read-only mounts and transaction boundaries remain the caller's responsibility.
New physical layout hashes come from an independent native clone, not the
database being installed. No existing fields or permissions may change.
Metadata and hooks are frozen at ae371b9; a repaired business commit must be
passed explicitly as expected_commit to both package and plan verification.
"""

import ast
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import PurePosixPath
import re


OLD = "2ca4358398cf750da31584fc31d3afaef48b30b6"
NEW = "5b49bacde6fcc088e08b118103fee496524ec99f"
TARGETS = ("Jifeng Tool Cost Allocation", "Jifeng Tool Cost Allocation Item")
NEW_TABLES = {"tab" + name for name in TARGETS}
BEFORE_TABLES, AFTER_TABLES = 932, 934
HOOK_COUNT, CSS_COUNT, JS_COUNT = 33, 12, 21
BASELINE_NAMES_SHA256 = (
    "d5c34a923c45de34dd9654b375c348ff01f42bbfbdd64693652208a2b9790fa6"
)
APPROVED_NEW_TABLES = {
    "tabJifeng Tool Cost Allocation": {
        "schema": "70dff60e5a9843521c912d63a4e6aa6c245061d16ba63802b97fedd9c32ac3cb",
        "indexes": "b3f035dc5c36d1cb375f88a82e14def49c533158aaf235dbb34ab2f9b2497707",
    },
    "tabJifeng Tool Cost Allocation Item": {
        "schema": "8a052c4ad6c53f0162e81b960f09c6b6fe0301ea39b285e900fe5a0fcee50fad",
        "indexes": "1a347d185b11794d683704e62984dc733034d0c7b70ef7a7ec200fbe07dae2bd",
    },
}
# Empty-install oracle: output/tool-cost-native-install-20261003-r1/after-fresh.json
# file SHA256 39f346286441e994cfa6533d6ffd78a9b4501d413deb38a72c8624c0d79ff8dc.
SCHEMA_SHA256 = {
    TARGETS[0]: "e053d795910a3fee870fcc3961a6185efb526ef9aeb75869eb2fb9c51f5ccef3",
    TARGETS[1]: "a03bde7ff9e3ae014652275c064dee9d2c2b24dab37aa1f943e2f399276c337f",
}
DEFINITION_SHA256 = {
    TARGETS[0]: "73c95078c67c6ddd8465fa56c77cac09b7c14c77bf07911ea23e923e94b381e3",
    TARGETS[1]: "12c3ba87075cc39c43f618be0c98bc60d3afb3ee25c7b91a233463ac09eebdc0",
}
MIGRATION_HASH = {
    TARGETS[0]: "ff5915d6985cd7ae45db8ebd0b423b32",
    TARGETS[1]: "ca5161186457f1f3530b077e9382ac8b",
}
HOOK_SOURCE_SHA256 = {
    OLD: "a133f27cb677ee6fdeb528de573574c9308a549f6687bd49999e510d5384949a",
    NEW: "9f6505578438831b8acbb3a8e1944491c470a048bef2cb2f18a16253e8b05de8",
}
SCHEMA_PATHS = {
    name: "app/jifeng_erp/jifeng_erp/doctype/"
    + name.lower().replace(" ", "_")
    + "/"
    + name.lower().replace(" ", "_")
    + ".json"
    for name in TARGETS
}
META = {
    "tabDocType": None,
    "tabDocField": "fields",
    "tabDocPerm": "permissions",
    "tabDocType Action": "actions",
    "tabDocType Link": "links",
    "tabDocType State": "states",
}
SEQUENCES = {
    "bisect_nodes_id_seq",
    "crm_note_id_seq",
    "ledger_health_id_seq",
    "prospect_opportunity_id_seq",
    "pwa_notification_id_seq",
    "web_form_list_column_id_seq",
}
MYISAM = {"tabData Import Log", "tabError Log", "__global_search"}
TABLE_KEYS = {"engine", "rows", "sha", "schema", "indexes"}
TECHNICAL = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
CHILD_IDENTITY = {"parent", "parenttype", "parentfield"}
HEADER_DEFAULTS = {
    "sort_field": "creation",
    "sort_order": "DESC",
    "engine": "InnoDB",
    "row_format": "Dynamic",
}
INDEX_KEYS = {
    "Key_name",
    "Non_unique",
    "Seq_in_index",
    "Column_name",
    "Collation",
    "Sub_part",
    "Null",
    "Index_type",
    "Comment",
    "Index_comment",
    "Ignored",
}
UNIQUE_FIELDS = {TARGETS[0]: {"request_id", "active_source_key"}, TARGETS[1]: set()}
NEW_CSS = "/assets/jifeng_erp/css/jifeng_tool_costs.css"
NEW_JS = "/assets/jifeng_erp/js/jifeng_tool_costs.js"
ARCHIVE_JS = "/assets/jifeng_erp/js/jifeng_tool_archive.js"
NEW_HOOKS = {
    "has_permission": {
        TARGETS[0]: "jifeng_erp.tool_cost_sources.tool_cost_allocation_has_permission",
        TARGETS[
            1
        ]: "jifeng_erp.tool_cost_sources.tool_cost_allocation_item_has_permission",
    },
    "permission_query_conditions": {
        TARGETS[
            0
        ]: "jifeng_erp.tool_cost_sources.tool_cost_allocation_query_conditions",
        TARGETS[
            1
        ]: "jifeng_erp.tool_cost_sources.tool_cost_allocation_item_query_conditions",
    },
    "doc_events": {
        "DocShare": {
            "before_validate": "jifeng_erp.tool_cost_sources.prevent_tool_cost_docshare"
        },
    },
}
# Filled only from the separate native SQL reference's complete column sets and
# normalized rows. Empty/default SQL representations are normalized, not omitted.
NATIVE_METADATA_SHA256 = {
    TARGETS[0]: "84c8d6d6f8a07296c64e1fe37e24d3028793702fcb3b3eda512a9ea5c23bf8ce",
    TARGETS[1]: "57c9684defffb8bec997572ad0207b3027e412f10f21174b2076f06c0afcd3a4",
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class AuditError(ValueError):
    """Only a public rule/location is exposed; never include row values."""


def _require(condition, rule, location=""):
    if not condition:
        raise AuditError(rule + (":" + location if location else ""))


def _keys(value, expected, location):
    _require(
        isinstance(value, dict) and set(value) == set(expected), "exact_keys", location
    )


def _hash(value, location):
    _require(
        isinstance(value, str) and SHA.fullmatch(value), "sha256_required", location
    )


def _count(value, location):
    _require(type(value) is int and value >= 0, "nonnegative_integer", location)


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


def _encode(value):
    return json.dumps(
        _wire(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value):
    """Canonical value hash; sequence/list order matters."""
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def digest_rows(rows):
    """Existing native collector hash: sorted canonical row encodings."""
    _require(isinstance(rows, list), "row_list_required")
    return fingerprint(sorted(_encode(row) for row in rows))


def _path(value):
    _require(
        isinstance(value, str) and value.startswith("app/") and "\\" not in value,
        "relative_source_path",
    )
    path = PurePosixPath(value)
    _require(
        path.as_posix() == value
        and not path.is_absolute()
        and not set(path.parts)
        & {".", "..", "output", "sites", "logs", "__pycache__", "node_modules", ".env"},
        "source_path_scope",
    )
    return path


def verify_release_plan(plan, *, expected_commit=NEW):
    """Pin the business commit, object delta and independent native layouts."""
    _keys(
        plan,
        {"old_commit", "new_commit", "source_files", "new_table_layout"},
        "release_plan",
    )
    _require(
        isinstance(expected_commit, str)
        and COMMIT.fullmatch(expected_commit)
        and expected_commit != OLD,
        "pinned_candidate_commit",
    )
    _require(
        plan["old_commit"] == OLD and plan["new_commit"] == expected_commit,
        "release_plan_commits",
    )
    _count(plan["source_files"], "source_files")
    _require(plan["source_files"] > 0, "source_files_required")
    _require(plan["new_table_layout"] == APPROVED_NEW_TABLES, "approved_native_layout")
    return {"passed": True, "source_files": plan["source_files"]}


def _literal_assignment(tree, name):
    matches = [
        statement
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        )
        and not isinstance(statement.value, ast.Call)
    ]
    _require(len(matches) == 1, "literal_hook_declaration", name)
    try:
        return matches[0], ast.literal_eval(matches[0].value)
    except ValueError:
        raise AuditError("literal_hook_declaration:" + name) from None


def verify_hooks(previous_source, current_source, hook_urls, assets):
    """Only two global assets, four deny hooks and one scoped share hook are new.

    assets maps package paths to their measured SHA256. hook_urls is the actual
    app-specific Frappe result, CSS first then JS, with content fingerprints.
    Neither parsing nor comparison executes hooks.py.
    """
    for commit, content in ((OLD, previous_source), (NEW, current_source)):
        _require(
            isinstance(content, bytes)
            and hashlib.sha256(content).hexdigest() == HOOK_SOURCE_SHA256[commit],
            "frozen_hook_source",
            commit,
        )
    try:
        old_tree, new_tree = ast.parse(previous_source), ast.parse(current_source)
    except (SyntaxError, UnicodeError):
        raise AuditError("hook_source_syntax") from None
    declared = {}
    for name, addition in (("app_include_css", NEW_CSS), ("app_include_js", NEW_JS)):
        old_statement, previous = _literal_assignment(old_tree, name)
        new_statement, current = _literal_assignment(new_tree, name)
        _require(
            isinstance(previous, list)
            and isinstance(current, list)
            and all(isinstance(item, str) for item in current)
            and len(current) == len(set(current))
            and current.count(addition) == 1
            and [item for item in current if item != addition] == previous,
            "exact_asset_hook_addition",
            name,
        )
        declared[name] = current
        new_statement.value = old_statement.value
    _require(
        len(declared["app_include_css"]) == CSS_COUNT
        and len(declared["app_include_js"]) == JS_COUNT,
        "exact_12_css_21_js",
    )
    _require(
        declared["app_include_js"].index(NEW_JS)
        < declared["app_include_js"].index(ARCHIVE_JS),
        "tool_cost_before_archive",
    )
    for name, additions in NEW_HOOKS.items():
        old_statement, previous = _literal_assignment(old_tree, name)
        new_statement, current = _literal_assignment(new_tree, name)
        _require(
            isinstance(previous, dict)
            and isinstance(current, dict)
            and not set(additions) & set(previous)
            and current == {**previous, **additions},
            "exact_permission_hook_addition",
            name,
        )
        new_statement.value = old_statement.value
    _require(
        ast.dump(old_tree, include_attributes=False)
        == ast.dump(new_tree, include_attributes=False),
        "unrelated_hook_drift",
    )
    paths = declared["app_include_css"] + declared["app_include_js"]
    _require(
        isinstance(hook_urls, list)
        and len(hook_urls) == HOOK_COUNT
        and all(isinstance(url, str) for url in hook_urls)
        and len(set(hook_urls)) == HOOK_COUNT,
        "exact_33_hook_urls",
    )
    _require(isinstance(assets, dict), "asset_hash_map")
    for expected, url in zip(paths, hook_urls):
        _require(
            isinstance(url, str)
            and re.fullmatch(re.escape(expected) + r"\?v=[0-9a-f]{16}", url),
            "hook_url_or_order",
        )
        path = "app/jifeng_erp/public/" + expected.removeprefix("/assets/jifeng_erp/")
        _require(path in assets, "hook_asset_missing")
        _hash(assets[path], "hook_asset_hash")
        _require(url.rsplit("?v=", 1)[1] == assets[path][:16], "hook_fingerprint")
    return {"passed": True, "hook_count": HOOK_COUNT}


def verify_source_manifest(
    manifest, observed_inventory, previous_hooks_source, *, expected_commit=NEW
):
    """Verify a trusted Git package's complete file/directory inventory.

    manifest={commit,files:[{path,bytes,sha256,mode}],hooks:[33 versioned URLs]}.
    inventory[path]={kind,mode,content:bytes} for files, {kind,mode} for dirs.
    No extra or missing file, symlink, executable mode or private tree is allowed.
    Git object/commit verification and bind identity are separate caller gates.
    """
    _keys(manifest, {"commit", "files", "hooks"}, "source_manifest")
    _require(
        isinstance(expected_commit, str)
        and COMMIT.fullmatch(expected_commit)
        and expected_commit != OLD,
        "pinned_candidate_commit",
    )
    _require(manifest["commit"] == expected_commit, "new_release_commit_required")
    _require(
        isinstance(manifest["files"], list)
        and bool(manifest["files"])
        and isinstance(observed_inventory, dict),
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
            content = observed["content"]
            _require(isinstance(content, bytes), "source_bytes_required")
            _require(
                len(content) == expected[path]["bytes"]
                and hashlib.sha256(content).hexdigest() == expected[path]["sha256"],
                "source_content_changed",
            )
    fixed = {
        "app/.git/HEAD",
        "app/.git/config",
        "app/.git/shallow",
        "app/.git/refs/tags/jifeng-release-pinned",
    }
    _require(fixed <= set(expected), "minimal_git_missing")
    for path in fixed - {"app/.git/config"}:
        _require(
            observed_inventory[path]["content"] == (expected_commit + "\n").encode(),
            "git_commit_not_exact",
        )
    try:
        config = observed_inventory["app/.git/config"]["content"].decode("utf-8")
    except UnicodeError:
        raise AuditError("git_encoding") from None
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
    allowed = (
        fixed
        | {"app/.git/index"}
        | {base + ext for base in packs for ext in (".pack", ".idx", ".rev")}
    )
    _require(
        {path for path in expected if path.startswith("app/.git/")} <= allowed,
        "minimal_git_extra_file",
    )
    hooks_path = "app/jifeng_erp/hooks.py"
    _require(hooks_path in expected, "source_hooks_missing")
    verify_hooks(
        previous_hooks_source,
        observed_inventory[hooks_path]["content"],
        manifest["hooks"],
        {path: item["sha256"] for path, item in expected.items()},
    )
    schemas, migration = {}, {}
    for name, path in SCHEMA_PATHS.items():
        _require(
            path in expected and expected[path]["sha256"] == SCHEMA_SHA256[name],
            "frozen_schema_source",
            name,
        )
        content = observed_inventory[path]["content"]
        try:
            schemas[name] = json.loads(content)
        except (ValueError, UnicodeError):
            raise AuditError("schema_json") from None
        migration[name] = hashlib.md5(content, usedforsecurity=False).hexdigest()
    native_js = SCHEMA_PATHS[TARGETS[0]][:-5] + ".js"
    _require(
        native_js in expected and expected[native_js]["bytes"] > 0,
        "native_form_script_missing",
    )
    for path in (
        "app/jifeng_erp/tool_cost_sources.py",
        "app/jifeng_erp/tool_cost_validation.py",
        *(schema[:-5] + ".py" for schema in SCHEMA_PATHS.values()),
    ):
        _require(
            path in expected and expected[path]["bytes"] > 0,
            "native_controller_source_missing",
        )
    for schema in SCHEMA_PATHS.values():
        _require(
            str(PurePosixPath(schema).parent / "__init__.py") in expected,
            "native_controller_package_missing",
        )
    _require(migration == MIGRATION_HASH, "frozen_migration_hash")
    return {
        "passed": True,
        "source_files": len(expected),
        "hook_count": HOOK_COUNT,
        "schemas": schemas,
        "migration_hash": migration,
    }


def metadata_signature(installed):
    """Reference signature retains every nontechnical SQL column, including nulls."""
    result = {}
    for name in TARGETS:
        actual = installed[name]
        result[name] = {}
        for section in (
            "doctype",
            "fields",
            "permissions",
            "actions",
            "links",
            "states",
        ):
            skipped = TECHNICAL | (
                {"migration_hash"} if section == "doctype" else CHILD_IDENTITY
            )
            rows = [actual[section]] if section == "doctype" else actual[section]
            result[name][section] = [
                {
                    key: None if value in (None, "", 0) else value
                    for key, value in row.items()
                    if key not in skipped
                }
                for row in rows
            ]
    return {name: fingerprint(result[name]) for name in TARGETS}


def _definition_row(row, declared, parentfield, location):
    _require(isinstance(row, dict), "raw_metadata_row", location)
    skipped = TECHNICAL | (
        CHILD_IDENTITY
        if parentfield
        else {
            "doctype",
            "fields",
            "field_order",
            "permissions",
            "actions",
            "links",
            "states",
        }
    )
    for key, value in declared.items():
        if key not in skipped:
            _require(
                key in row and type(row[key]) is type(value) and row[key] == value,
                "new_declared_property",
                location,
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


def _indexes(name, indexes):
    _require(isinstance(indexes, list) and bool(indexes), "index_proof_required", name)
    groups = {}
    for row in indexes:
        _keys(row, INDEX_KEYS, "index")
        _require(
            row["Index_type"] == "BTREE"
            and row["Ignored"] == "NO"
            and type(row["Non_unique"]) is int
            and row["Non_unique"] in (0, 1)
            and isinstance(row["Key_name"], str)
            and bool(row["Key_name"])
            and isinstance(row["Column_name"], str)
            and bool(row["Column_name"])
            and type(row["Seq_in_index"]) is int,
            "index_shape",
            name,
        )
        groups.setdefault(row["Key_name"], []).append(row)
    found = set()
    for key, rows in groups.items():
        rows = sorted(rows, key=lambda row: row["Seq_in_index"])
        _require(
            [row["Seq_in_index"] for row in rows] == list(range(1, len(rows) + 1))
            and len({row["Non_unique"] for row in rows}) == 1,
            "index_sequence",
            name,
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
    _require(found == {"name", *UNIQUE_FIELDS[name]}, "exact_unique_indexes", name)
    _require(
        digest_rows(indexes) == APPROVED_NEW_TABLES["tab" + name]["indexes"],
        "approved_native_indexes",
        name,
    )


def verify_new_definitions(schemas, installed):
    """Frozen JSON declarations plus independently measured full native defaults."""
    _keys(schemas, TARGETS, "schemas")
    _keys(installed, TARGETS, "installed")
    _keys(NATIVE_METADATA_SHA256, TARGETS, "native_metadata_reference_missing")
    for name in TARGETS:
        source, actual = schemas[name], installed[name]
        _require(
            fingerprint(source) == DEFINITION_SHA256[name],
            "frozen_schema_definition",
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
        _require(
            isinstance(actual["doctype"], dict)
            and actual["doctype"].get("name") == name,
            "installed_doctype_identity",
            name,
        )
        _definition_row(actual["doctype"], source, None, name)
        for section in ("fields", "permissions", "actions", "links", "states"):
            expected, rows = source.get(section, []), actual[section]
            _require(
                isinstance(rows, list) and len(rows) == len(expected),
                "exact_new_metadata_count",
                name,
            )
            for index, (row, declared) in enumerate(zip(rows, expected), 1):
                _require(
                    isinstance(row, dict)
                    and row.get("parent") == name
                    and row.get("parenttype") == "DocType"
                    and row.get("parentfield") == section
                    and type(row.get("idx")) is int
                    and row["idx"] == index,
                    "new_child_identity",
                    name,
                )
                _definition_row(row, declared, section, name)
        _indexes(name, actual["indexes"])
    _require(
        metadata_signature(installed) == NATIVE_METADATA_SHA256,
        "independent_native_metadata_reference",
    )
    return {"passed": True, "new_doctypes": list(TARGETS)}


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


def _snapshot(value, after=False):
    _keys(value, {"tables", "metadata", "sequences"}, "snapshot")
    tables = value["tables"]
    _require(
        isinstance(tables, dict)
        and len(tables) == (AFTER_TABLES if after else BEFORE_TABLES),
        "physical_table_count",
    )
    _require(
        all(isinstance(name, str) and name for name in tables), "physical_object_names"
    )
    names = sorted(set(tables) - (NEW_TABLES if after else set()))
    _require(
        fingerprint(names) == BASELINE_NAMES_SHA256, "exact_baseline_physical_names"
    )
    _require(value["sequences"] == sorted(SEQUENCES), "exact_six_sequences")
    _require(
        {
            name
            for name, row in tables.items()
            if isinstance(row, dict) and row.get("engine") == "MyISAM"
        }
        == MYISAM,
        "exact_three_myisam",
    )
    for name, row in tables.items():
        _keys(row, TABLE_KEYS, "physical_object")
        _require(
            row["engine"] == ("MyISAM" if name in MYISAM else "InnoDB"),
            "table_engine",
            name,
        )
        _count(row["rows"], "physical_rows")
        for field in ("sha", "schema", "indexes"):
            _hash(row[field], "physical_proof")
        if name in SEQUENCES:
            _require(
                row["rows"] == 1 and row["indexes"] == digest_rows([]),
                "sequence_physical_shape",
                name,
            )
    _keys(value["metadata"], META, "metadata")
    for table, partition in value["metadata"].items():
        _keys(partition, {"columns", "outside", "parents"}, table)
        columns = partition["columns"]
        required = TECHNICAL | (
            {"migration_hash"} if table == "tabDocType" else CHILD_IDENTITY
        )
        _require(
            isinstance(columns, list)
            and all(isinstance(column, str) and column for column in columns)
            and len(columns) == len(set(columns))
            and required <= set(columns),
            "metadata_columns",
            table,
        )
        _keys(partition["outside"], {"rows", "sha"}, table)
        _count(partition["outside"]["rows"], "outside_rows")
        _hash(partition["outside"]["sha"], "outside_rows")
        _keys(partition["parents"], TARGETS, table)
        owned = []
        for parent, rows in partition["parents"].items():
            _require(isinstance(rows, list), "metadata_rows", table)
            _require(after or not rows, "new_metadata_must_be_absent", table)
            for row in rows:
                _keys(row, columns, table)
                _require(
                    (row["name"] if table == "tabDocType" else row["parent"]) == parent,
                    "exact_parent_scope",
                    table,
                )
            owned.extend(rows)
        primary_keys = [row["name"] for row in owned]
        _require(
            all(isinstance(name, str) and name for name in primary_keys)
            and len(primary_keys) == len(set(primary_keys)),
            "unique_metadata_primary_keys",
            table,
        )
        _require(
            tables[table]["rows"] == partition["outside"]["rows"] + len(owned),
            "metadata_partition_count",
            table,
        )
        if not owned:
            _require(
                tables[table]["sha"] == partition["outside"]["sha"],
                "metadata_partition_hash",
                table,
            )


def verify_baseline(before):
    """Fail before any write unless all 932 objects and empty target partitions exist."""
    _snapshot(before)
    _require(not NEW_TABLES & set(before["tables"]), "new_tables_must_be_absent")
    return {"passed": True, "protected_objects": BEFORE_TABLES}


def verify_snapshot_delta(
    before, after, schemas, installed, window_start, window_end, actor, migration_hash
):
    """Accept only empty parent/child creation; all old physical data is protected.

    snapshot={tables:{name:{engine,rows,sha,schema,indexes}},sequences:[six names],
      metadata:{six tables:{columns:[all SQL columns],outside:{rows,sha},
        parents:{two names:[full raw SQL rows]}}}}.
    All sha/schema/indexes use digest_rows. Native SHOW INDEX projection uses
    INDEX_KEYS; SHOW FULL COLUMNS uses all returned columns, including Privileges.
    """
    start, end = _time(window_start), _time(window_end)
    _require(start <= end and actor == "Administrator", "audit_window_or_actor")
    _require(migration_hash == MIGRATION_HASH, "frozen_migration_hash")
    verify_new_definitions(schemas, installed)
    verify_baseline(before)
    _snapshot(after, after=True)
    _require(
        not NEW_TABLES & set(before["tables"])
        and set(after["tables"]) == set(before["tables"]) | NEW_TABLES,
        "exact_added_tables",
    )
    for name in NEW_TABLES:
        current = after["tables"][name]
        _require(
            current["rows"] == 0 and current["sha"] == digest_rows([]),
            "empty_new_table",
            name,
        )
        _require(
            all(
                current[key] == expected
                for key, expected in APPROVED_NEW_TABLES[name].items()
            ),
            "approved_native_layout",
            name,
        )
    for table, prior in before["tables"].items():
        current = after["tables"][table]
        if table not in META:
            _require(current == prior, "protected_table_changed", table)
            continue
        _require(
            all(current[key] == prior[key] for key in ("engine", "schema", "indexes")),
            "existing_metadata_schema_or_index_changed",
            table,
        )
        left, right = before["metadata"][table], after["metadata"][table]
        _require(
            left["columns"] == right["columns"] and left["outside"] == right["outside"],
            "outside_metadata_changed",
            table,
        )
        for target in TARGETS:
            rows = right["parents"][target]
            actual = (
                [installed[target]["doctype"]]
                if META[table] is None
                else installed[target][META[table]]
            )
            _require(rows == actual, "installed_full_rows_differ", table)
            for index, row in enumerate(rows, 1):
                _require(
                    row["owner"] == actor
                    and row["modified_by"] == actor
                    and type(row["docstatus"]) is int
                    and row["docstatus"] == 0
                    and type(row["idx"]) is int
                    and row["idx"] == (0 if META[table] is None else index),
                    "new_metadata_audit_identity",
                    table,
                )
                _require(
                    start <= _time(row["creation"]) <= _time(row["modified"]) <= end,
                    "new_metadata_audit_window",
                    table,
                )
                if META[table] is None:
                    _require(
                        row["migration_hash"] == migration_hash[target],
                        "migration_hash",
                        table,
                    )
        if not any(right["parents"].values()):
            _require(current == prior, "unchanged_metadata_content_changed", table)
        else:
            _require(
                current["sha"] != prior["sha"], "stale_metadata_content_proof", table
            )
    return {
        "passed": True,
        "protected_objects": BEFORE_TABLES,
        "total_objects": AFTER_TABLES,
        "new_empty_doctypes": list(TARGETS),
        "existing_fields_changed": 0,
        "old_permissions_unchanged": True,
        "sequences_unchanged": True,
    }
