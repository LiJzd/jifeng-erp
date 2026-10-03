"""One-use Frappe phases for the shared A/B test site's incremental release.

Called from the pinned image with an explicit old or candidate source mount.
Only apply modifies SQL: two new empty DocTypes and their own metadata.
No full migrate, fixture import, data seed, backup cleanup or automatic retry.
"""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

SITE = "jifeng-erp-test.localhost"
DATABASE = "jifeng_erp_test"
BENCH = Path("/home/frappe/frappe-bench")
ROOT = Path("/opt/source-release")
PRIVATE = Path("/opt/source-release-backup")
OLD = "4d71c354d3f3f281d21d3d16b496bb6e5434e655"
NEW = "18e5835cfda4171ec691d5e701c8f42018b5448c"
NATIVE_PINS = {
    "frappe/utils/backups.py": "04647dc5d8abc6f7b7a2e59086949405c3de1f5ae70c120a4a75dc58a2064a76",
    "frappe/database/mariadb/database.py": "0eaae7d449c560dcd0fa6ef56686bcfe2eb10a474f21c62c282617cf3c3fb3b9",
    "frappe/database/__init__.py": "c78ea57b3fee7ca4299ece3e384dd62fa6e655e8d9c6e42f31307db4c20b7b29",
}
SEQUENCES = {
    "bisect_nodes_id_seq",
    "crm_note_id_seq",
    "ledger_health_id_seq",
    "prospect_opportunity_id_seq",
    "pwa_notification_id_seq",
    "web_form_list_column_id_seq",
}
# Loaded only from the SHA-pinned plan; audit also checks the independent native
# reference. Never derive expected physical layouts from this external database.
APPROVED_NEW_TABLES = {}


def require(condition, rule):
    if not condition:
        raise RuntimeError(rule)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quote(value):
    return "`" + value.replace("`", "``") + "`"


def load_audit(expected_sha):
    path = ROOT / "incoming/incremental_audit.py"
    require(
        stat.S_ISREG(path.lstat().st_mode) and sha(path) == expected_sha,
        "audit_source_pin",
    )
    spec = importlib.util.spec_from_file_location("incremental_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_release_plan(expected_sha):
    path = ROOT / "native-release-plan.json"
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and (info.st_uid, info.st_gid) == (1000, 1000)
        and stat.S_IMODE(info.st_mode) == 0o644
        and sha(path) == expected_sha,
        "release_plan_file_pin",
    )
    plan = json.loads(path.read_bytes())
    audit.verify_release_plan(plan)
    return plan


def verify_changes(changes):
    require(
        isinstance(changes, dict)
        and set(changes) == {"old_commit", "new_commit", "doctype", "changes"}
        and changes["old_commit"] == OLD
        and changes["new_commit"] == NEW
        and changes["doctype"] is None
        and changes["changes"] == {},
        "zero_existing_metadata_changes",
    )


def verify_candidate_metadata(manifest, files, definition):
    require(
        manifest["commit"] == NEW
        and manifest["source_files"] == release_plan["source_files"]
        and len(files) == release_plan["source_files"]
        and definition["source_files"] == release_plan["source_files"],
        "candidate_release_identity",
    )
    require(
        manifest.get("verified_doctype_js")
        == {
            "Sales Order": {
                "path": audit.DOCTYPE_JS["Sales Order"],
                "sha256": definition["doctype_js"]["Sales Order"]["sha256"],
            }
        },
        "candidate_sales_order_source_pin",
    )


def apply_new_doctypes():
    require(
        tuple(audit.TARGETS) == ("Jifeng Plating Color", "Jifeng Order Operation")
        and audit.PATCHES == {},
        "fixed_new_doctype_scope",
    )
    for target in audit.TARGETS:
        require(load_release_plan(plan_sha256) == release_plan, "release_plan_changed")
        candidate()
        slug = target.lower().replace(" ", "_")
        frappe.reload_doc("jifeng_erp", "doctype", slug, force=True)


def decode(value):
    if isinstance(value, dict) and set(value) == {"type", "value"}:
        kind, data = value["type"], value["value"]
        if kind in {"datetime", "date", "time"}:
            return {"datetime": datetime, "date": date, "time": time}[
                kind
            ].fromisoformat(data)
        if kind == "decimal":
            return Decimal(data)
        if kind == "bytes":
            return bytes.fromhex(data)
        if kind == "timedelta":
            return timedelta(days=data[0], seconds=data[1], microseconds=data[2])
        raise RuntimeError("unknown_evidence_type")
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def record(name, value):
    path = PRIVATE / "audit" / name
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(
            audit._wire(value),
            stream,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return sha(path)


def read_proof(name, expected_sha=None):
    path = PRIVATE / "audit" / name
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 1000
        and stat.S_IMODE(info.st_mode) == 0o600,
        "private_proof_identity",
    )
    require(expected_sha is None or sha(path) == expected_sha, "private_proof_sha")
    return decode(json.loads(path.read_bytes()))


def candidate():
    source = ROOT / "app"
    observed = {}
    for path in [source, *source.rglob("*")]:
        info = path.lstat()
        require((info.st_uid, info.st_gid) == (1000, 1000), "candidate_source_owner")
        relative = path.relative_to(ROOT).as_posix()
        if stat.S_ISDIR(info.st_mode):
            observed[relative] = {
                "kind": "directory",
                "mode": stat.S_IMODE(info.st_mode),
            }
        else:
            require(stat.S_ISREG(info.st_mode), "candidate_source_not_regular")
            observed[relative] = {
                "kind": "file",
                "mode": stat.S_IMODE(info.st_mode),
                "content": path.read_bytes(),
            }
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    require(
        manifest["commit"] == NEW
        and manifest["source_files"] == release_plan["source_files"],
        "candidate_release_identity",
    )
    files = json.loads((ROOT / "source-files.json").read_bytes())
    require(
        isinstance(files, list) and len(files) == release_plan["source_files"],
        "candidate_source_file_count",
    )
    definition = audit.verify_source_manifest(
        {
            "commit": NEW,
            "files": [dict(row, mode=0o644) for row in files],
            "hooks": manifest["verified_direct_hook_paths"],
        },
        observed,
    )
    verify_candidate_metadata(manifest, files, definition)
    return definition


def runtime(expected):
    require(os.geteuid() == 1000, "native_runtime_uid")
    actual = (
        subprocess.check_output(
            ["git", "-C", str(BENCH / "apps/jifeng_erp"), "rev-parse", "HEAD"]
        )
        .decode()
        .strip()
    )
    require(actual == expected, "native_runtime_source")
    require(
        os.readlink(BENCH / "sites/assets") == "../assets"
        and os.readlink(BENCH / "assets/jifeng_erp")
        == "../apps/jifeng_erp/jifeng_erp/public",
        "native_asset_links",
    )
    for relative, expected_sha in NATIVE_PINS.items():
        require(
            sha(BENCH / "apps/frappe" / relative) == expected_sha,
            "native_backup_implementation_pin",
        )


@contextmanager
def protected_reads():
    """Hold all three MyISAM READ locks until the optional full dump completes."""
    connection, cursor = None, None
    try:
        connection = frappe.db.get_connection()
        require(connection is not frappe.db._conn, "independent_lock_connection")
        connection.select_db(DATABASE)
        cursor = connection.cursor()
        cursor.execute("SET SESSION lock_wait_timeout=10")
        cursor.execute("SELECT CONNECTION_ID()")
        identity = cursor.fetchone()[0]
        cursor.execute(
            "LOCK TABLES "
            + ", ".join(quote(table) + " READ" for table in sorted(audit.MYISAM))
        )
        frappe.db._conn.rollback()
        main = frappe.db._conn.cursor()
        try:
            main.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            main.execute("SET TRANSACTION READ ONLY")
            main.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        finally:
            main.close()
        yield
        cursor.execute("SELECT CONNECTION_ID()")
        require(cursor.fetchone()[0] == identity, "read_lock_connection_changed")
    finally:
        try:
            if frappe.db._conn is not None:
                frappe.db._conn.rollback()
        finally:
            try:
                if cursor is not None:
                    cursor.execute("UNLOCK TABLES")
            finally:
                try:
                    if cursor is not None:
                        cursor.close()
                finally:
                    if connection is not None:
                        connection.close()


def collect():
    inventory = [
        dict(row)
        for row in frappe.db.sql(
            "SELECT TABLE_NAME,ENGINE,TABLE_COLLATION,ROW_FORMAT,TABLE_TYPE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME",
            as_dict=True,
        )
    ]
    require(
        {row["TABLE_NAME"] for row in inventory if row["ENGINE"] != "InnoDB"}
        == audit.MYISAM,
        "physical_engine_inventory",
    )
    require(
        {row["TABLE_NAME"] for row in inventory if row["TABLE_TYPE"] == "SEQUENCE"}
        == SEQUENCES,
        "physical_sequence_inventory",
    )
    tables, metadata, raw_indexes = {}, {}, {}
    for row in inventory:
        name = row.pop("TABLE_NAME")
        require(
            row["TABLE_TYPE"] == ("SEQUENCE" if name in SEQUENCES else "BASE TABLE"),
            "base_table_or_known_sequence",
        )
        columns = [
            dict(item)
            for item in frappe.db.sql(
                "SHOW FULL COLUMNS FROM " + quote(name), as_dict=True
            )
        ]
        for column in columns:
            column.pop("Privileges", None)
        indexes = [
            dict(item)
            for item in frappe.db.sql("SHOW INDEX FROM " + quote(name), as_dict=True)
        ]
        for index in indexes:
            index.pop("Cardinality", None)
        indexes.sort(key=lambda index: (index["Key_name"], index["Seq_in_index"]))
        primary = [
            index["Column_name"] for index in indexes if index["Key_name"] == "PRIMARY"
        ]
        order = primary or [column["Field"] for column in columns]
        rows = [
            dict(item)
            for item in frappe.db.sql(
                "SELECT * FROM "
                + quote(name)
                + " ORDER BY "
                + ",".join(map(quote, order)),
                as_dict=True,
            )
        ]
        tables[name] = {
            "engine": row["ENGINE"],
            "rows": len(rows),
            "content_sha256": audit.fingerprint(rows),
            "schema_sha256": audit.fingerprint({"structure": row, "columns": columns}),
            "indexes_sha256": audit.fingerprint(indexes),
        }
        if name in APPROVED_NEW_TABLES:
            require(
                all(
                    tables[name][key] == expected
                    for key, expected in APPROVED_NEW_TABLES[name].items()
                ),
                "approved_physical_new_schema:" + name,
            )
        if name in audit.META:
            identity = "name" if name == "tabDocType" else "parent"
            parents = {
                parent: sorted(
                    [item for item in rows if item[identity] == parent],
                    key=lambda item: (item.get("idx", 0), item["name"]),
                )
                for parent in audit.TARGETS
            }
            targets = {}
            owned_names = {
                item["name"] for items in parents.values() for item in items
            } | {item["name"] for item in targets.values()}
            outside = [item for item in rows if item["name"] not in owned_names]
            metadata[name] = {
                "columns": [column["Field"] for column in columns],
                "new_parents": parents,
                "targets": targets,
                "outside": {"rows": len(outside), "sha256": audit.fingerprint(outside)},
            }
        if name in audit.NEW_TABLES:
            raw_indexes[name] = indexes
    installed = {}
    if set(audit.NEW_TABLES) <= set(tables):
        for target in audit.TARGETS:
            require(
                len(metadata["tabDocType"]["new_parents"][target]) == 1,
                "installed_parent_exact",
            )
            installed[target] = {
                "doctype": metadata["tabDocType"]["new_parents"][target][0],
                "indexes": raw_indexes["tab" + target],
            }
            for table, child in audit.META.items():
                if child:
                    installed[target][child] = metadata[table]["new_parents"][target]
    return {"tables": tables, "metadata": metadata}, installed


def snapshot():
    with protected_reads():
        return collect()


def backup(before):
    from frappe.utils.backups import BackupGenerator

    directory = PRIVATE / "native-backup"
    require(not directory.exists(), "backup_already_exists")
    directory.mkdir(mode=0o700)
    require(
        not frappe.get_system_settings("encrypt_backup"), "unexpected_backup_encryption"
    )
    generator = BackupGenerator(
        frappe.conf.db_name,
        frappe.conf.db_user or frappe.conf.db_name,
        frappe.conf.db_password,
        db_socket=frappe.conf.db_socket,
        db_host=frappe.conf.db_host,
        db_port=frappe.conf.db_port,
        db_type=frappe.conf.db_type or "mariadb",
        backup_path=str(directory),
        ignore_conf=True,
        compress_files=True,
        verbose=False,
    )
    require(
        not generator.backup_includes
        and not generator.backup_excludes
        and not generator.partial,
        "full_backup_scope",
    )
    # Direct get_backup never calls new_backup/delete_temp_backups or send_email.
    generator.get_backup(force=True, ignore_files=False)
    expected = {
        "database": Path(generator.backup_path_db),
        "configuration": Path(generator.backup_path_conf),
        "public-files": Path(generator.backup_path_files),
        "private-files": Path(generator.backup_path_private_files),
    }
    require(
        set(directory.iterdir()) == set(expected.values()), "four_backup_parts_exact"
    )
    inventory = []
    for part, path in sorted(expected.items()):
        require(
            path.parent == directory
            and stat.S_ISREG(path.lstat().st_mode)
            and path.stat().st_size > 0,
            "backup_regular_nonempty",
        )
        os.chmod(path, 0o600)
        if part == "database":
            names = set()
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    match = re.search(
                        r"CREATE (?:TABLE|SEQUENCE) `((?:``|[^`])+)`(?: \(|\s)", line
                    )
                    if match:
                        table = match.group(1).replace("``", "`")
                        require(table not in names, "backup_duplicate_table")
                        names.add(table)
            require(names == set(before["tables"]), "backup_all_929_physical_tables")
        elif part.endswith("files"):
            with tarfile.open(path, "r:gz") as archive:
                for member in archive:
                    if member.isfile():
                        with archive.extractfile(member) as stream:
                            while stream.read(1024 * 1024):
                                pass
        else:
            require(
                path.read_bytes()
                == (BENCH / "sites" / SITE / "site_config.json").read_bytes(),
                "backup_config_matches_site_file",
            )
        inventory.append(
            {
                "part": part,
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha(path),
            }
        )
    return inventory


def verify_delta(before, after, installed, definition, intent):
    require(
        intent.get("plan_sha256") == plan_sha256 and intent.get("commit") == NEW,
        "apply_receipt_release_identity",
    )
    return audit.verify_snapshot_delta(
        before,
        after,
        intent["changes"],
        definition["schemas"],
        installed,
        intent["start"],
        intent["end"],
        "Administrator",
        definition["migration_hash"],
    )


def verify_cached_assets(definition):
    """Verify effective hooks and native FormMeta; this is not a GUI test."""
    for target in audit.TARGETS:
        frappe.clear_cache(doctype=target)
    frappe.client_cache.delete_value("app_hooks")
    key = "doctype_form_meta::Sales Order"
    frappe.client_cache.delete_value(key)
    css = [
        url
        for url in frappe.get_hooks("app_include_css")
        if url.startswith("/assets/jifeng_erp/")
    ]
    js = [
        url
        for url in frappe.get_hooks("app_include_js")
        if url.startswith("/assets/jifeng_erp/")
    ]
    expected = json.loads((ROOT / "manifest.json").read_bytes())[
        "verified_direct_hook_paths"
    ]
    require(
        len(css) == audit.CSS_COUNT
        and len(js) == audit.JS_COUNT
        and css + js == expected,
        "runtime_28_hooks",
    )
    path = definition["doctype_js"]["Sales Order"]["path"]
    source = ROOT / path
    require(
        sha(source) == definition["doctype_js"]["Sales Order"]["sha256"],
        "order_script_source_pin",
    )
    require(
        audit.DOCTYPE_JS["Sales Order"]
        in frappe.get_hooks("doctype_js", {}).get("Sales Order", []),
        "runtime_sales_order_doctype_js",
    )
    from frappe.desk.form.meta import get_meta

    script = source.read_bytes().decode("utf-8")
    meta_js = get_meta("Sales Order").get("__js") or ""
    cached = frappe.client_cache.get_value(key)
    require(
        bool(script)
        and script in meta_js
        and meta_js.count(
            "window.JifengOrderCustomerPicker = class JifengOrderCustomerPicker"
        )
        == 1
        and get_meta("Sales Order").get("__js") == meta_js
        and cached is not None
        and cached.get("__js") == meta_js,
        "native_sales_order_form_meta_current",
    )
    return {
        "effective_hooks": len(css) + len(js),
        "app_include_css": len(css),
        "app_include_js": len(js),
        "order_js_sha256": sha(source),
        "form_meta_js_sha256": hashlib.sha256(meta_js.encode("utf-8")).hexdigest(),
        "source_current": True,
        "cached_current": True,
        "gui_tested": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("preflight", "before", "apply", "verify", "cache")
    )
    parser.add_argument("--audit-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--before-sha")
    args = parser.parse_args()
    global audit, frappe, release_plan, plan_sha256, APPROVED_NEW_TABLES
    audit = load_audit(args.audit_sha)
    plan_sha256 = args.plan_sha256
    release_plan = load_release_plan(plan_sha256)
    APPROVED_NEW_TABLES = release_plan["new_table_layout"]
    runtime(OLD if args.mode == "before" else NEW)
    definition = candidate()
    if args.mode == "preflight":
        print(
            json.dumps(
                {
                    "passed": True,
                    "source_files": release_plan["source_files"],
                    "hook_count": audit.HOOK_COUNT,
                    "doctype_js": definition["doctype_js"],
                    "plan_sha256": plan_sha256,
                    "backup_implementation_verified": True,
                    "sql_connected": False,
                }
            )
        )
        return
    info = (PRIVATE / "audit").lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == 1000
        and stat.S_IMODE(info.st_mode) == 0o700,
        "private_audit_directory",
    )
    record(
        args.mode + "-started.json",
        {"mode": args.mode, "commit": NEW, "plan_sha256": plan_sha256},
    )
    os.chdir(BENCH / "sites")
    import frappe

    frappe.init(site=SITE, sites_path=str(BENCH / "sites"))
    frappe.connect()
    frappe.db.connect()
    result = {
        "passed": False,
        "mode": args.mode,
        "protected_table_count": audit.BEFORE_TABLES,
        "total_table_count": audit.BEFORE_TABLES
        if args.mode == "before"
        else audit.AFTER_TABLES,
        "plan_sha256": plan_sha256,
        "full_migrate": False,
        "automatic_retry": False,
    }
    capture = io.StringIO()
    try:
        require(
            frappe.local.site == SITE
            and frappe.conf.db_name == DATABASE
            and frappe.conf.maintenance_mode == 1,
            "site_database_maintenance_identity",
        )
        require(
            frappe.conf.db_host == "db" and frappe.db.host == "db",
            "database_host_identity",
        )
        frappe.set_user("Administrator")
        changes = json.loads((ROOT / "metadata-changes.json").read_bytes())
        verify_changes(changes)
        with redirect_stdout(capture), redirect_stderr(capture):
            if args.mode == "before":
                with protected_reads():
                    before, _ = collect()
                    require(
                        len(before["tables"]) == audit.BEFORE_TABLES
                        and not (audit.NEW_TABLES & set(before["tables"])),
                        "baseline_929_no_new_tables",
                    )
                    audit._snapshot(before, audit.BEFORE_TABLES)
                    for table in audit.META:
                        require(
                            all(
                                not rows
                                for rows in before["metadata"][table][
                                    "new_parents"
                                ].values()
                            ),
                            "new_metadata_absent",
                        )
                    result["proof_sha256"] = record("before.json", before)
                    result["backups"] = backup(before)
                    following, _ = collect()
                    require(following == before, "backup_changed_database")
                fresh, _ = snapshot()
                require(fresh == before, "fresh_database_changed_during_backup")
                record(
                    "backup-completed.json",
                    {
                        "parts": result["backups"],
                        "physical_tables": audit.BEFORE_TABLES,
                        "plan_sha256": plan_sha256,
                        "three_myisam_locked_through_dump": True,
                    },
                )
            else:
                require(args.before_sha, "baseline_sha_required")
                before = read_proof("before.json", args.before_sha)
                audit._snapshot(before, audit.BEFORE_TABLES)
                current, installed = snapshot()
                if args.mode == "apply":
                    require(current == before, "baseline_changed_before_apply")
                    from frappe.permissions import AUTOMATIC_ROLES
                    from frappe.utils import now_datetime

                    for schema in definition["schemas"].values():
                        require(
                            frappe.db.exists("Module Def", schema["module"]),
                            "module_must_already_exist",
                        )
                        require(
                            all(
                                frappe.db.exists("Role", role)
                                for role in {
                                    item["role"] for item in schema["permissions"]
                                }
                                | set(AUTOMATIC_ROLES)
                            ),
                            "roles_must_already_exist",
                        )
                    frappe.db.rollback()
                    start = now_datetime()
                    record(
                        "apply-intent.json",
                        {
                            "start": start,
                            "changes": changes["changes"],
                            "new_doctypes": list(audit.TARGETS),
                            "commit": NEW,
                            "plan_sha256": plan_sha256,
                        },
                    )
                    apply_new_doctypes()
                    frappe.db.commit()
                    end = now_datetime()
                    record(
                        "apply-committed.json",
                        {
                            "committed": True,
                            "start": start,
                            "end": end,
                            "changes": changes["changes"],
                            "commit": NEW,
                            "plan_sha256": plan_sha256,
                        },
                    )
                    current, installed = snapshot()
                intent = read_proof("apply-committed.json")
                result["delta"] = verify_delta(
                    before, current, installed, definition, intent
                )
                if args.mode == "cache":
                    with protected_reads():
                        result["cache"] = verify_cached_assets(definition)
                    fresh, _ = snapshot()
                    require(fresh == current, "cache_changed_database")
                result["proof_sha256"] = record("after-" + args.mode + ".json", current)
        result["passed"] = True
    except Exception as error:
        result.update(
            error_type=type(error).__name__,
            reason=str(error)
            if isinstance(error, (audit.AuditError, RuntimeError))
            else "native_phase_failed",
            outcome_requires_inspection=True,
        )
    finally:
        try:
            frappe.db._conn.rollback()
        finally:
            frappe.destroy()
    result["captured_output_sha256"] = hashlib.sha256(
        capture.getvalue().encode()
    ).hexdigest()
    record(args.mode + "-result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    require(result["passed"], "native_phase_failed_inspect_receipts")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
