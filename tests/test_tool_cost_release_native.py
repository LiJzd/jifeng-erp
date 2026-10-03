"""Meaningful native-helper failure gates; only fake SQL and local temporary files.

No Frappe connection, server, browser or external transfer is made. Linux-only
filesystem tests exercise the production permission/fsync/tar/gzip behavior.
"""

from contextlib import contextmanager
from datetime import datetime
import copy
import gzip
import importlib.util
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


N = load("tool_cost_native_test", ROOT / "deploy/erp-test/tool_cost_release_native.py")
M = load("tool_cost_audit_fixture", ROOT / "tests/test_tool_cost_release_audit.py")
A = M.A
NOW = datetime(2026, 10, 3, 1, 0, 1)
REVIEWED = {
    name + "_sha256": "a" * 64 for name in ("native", "audit", "plan", "manifest")
}


class Attr(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def sequence():
    return {
        "next_not_cached_value": 7,
        "minimum_value": 1,
        "maximum_value": 9223372036854775806,
        "start_value": 1,
        "increment": 1,
        "cache_size": 0,
        "cycle_option": 0,
        "cycle_count": 0,
    }


def target(root):
    return N.Target.owned_clone(
        site="jifeng-tool-cost-release-qa-unit.localhost",
        database="jf_tc_release_unit",
        bench=root / "bench",
        root=root / "release",
        uid=0,
    )


def definition():
    return {
        "passed": True,
        "schemas": M.schemas(),
        "migration_hash": dict(A.MIGRATION_HASH),
        "source_files": 100,
        "hook_count": 33,
    }


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql):
        self.connection.events.append(sql)
        if self.connection.fail_lock and sql.startswith("LOCK TABLES"):
            raise RuntimeError("synthetic lock timeout")

    def fetchone(self):
        return (self.connection.database, self.connection.identity)

    def close(self):
        self.connection.events.append("cursor.close")


class Connection:
    def __init__(self, database, identity, events):
        self.database, self.identity, self.events = database, identity, events
        self.fail_lock = False

    def cursor(self):
        return Cursor(self)

    def select_db(self, database):
        self.database = database

    def rollback(self):
        self.events.append("read.rollback")

    def close(self):
        self.events.append("connection.close")


class FakeDB:
    def __init__(self, target):
        self.target, self.events = target, []
        self.host = "local-only"
        self._conn = Connection(target.database, 11, self.events)
        self.locker = Connection(target.database, 12, self.events)
        reference = M.fixture()
        self.inventory, self.columns, self.indexes, self.rows = [], {}, {}, {}
        for name in reference["baseline_names"]:
            self.inventory.append(
                {
                    "TABLE_NAME": name,
                    "ENGINE": "MyISAM" if name in A.MYISAM else "InnoDB",
                    "TABLE_COLLATION": "utf8mb4_unicode_ci",
                    "ROW_FORMAT": "Dynamic",
                    "TABLE_TYPE": "SEQUENCE" if name in A.SEQUENCES else "BASE TABLE",
                }
            )
            fields = (
                reference["columns"][name]
                if name in A.META
                else sorted(N.SEQUENCE_FIELDS)
                if name in A.SEQUENCES
                else ["name"]
            )
            self.columns[name] = [
                {
                    "Field": field,
                    "Type": "varchar(140)",
                    "Privileges": "select,insert,update,references",
                }
                for field in fields
            ]
            self.indexes[name] = []
            self.rows[name] = [sequence()] if name in A.SEQUENCES else []
        self.known = set()
        self.commits = 0

    def sql(self, sql, as_dict=True):
        self.events.append(sql)
        if sql.startswith("SELECT DATABASE()"):
            return [
                {
                    "database_name": self.target.database,
                    "connection_id": self._conn.identity,
                }
            ]
        if "FROM information_schema.TABLES" in sql:
            return copy.deepcopy(self.inventory)
        name = sql.split(chr(96))[1]
        if sql.startswith("SHOW FULL COLUMNS"):
            return copy.deepcopy(self.columns[name])
        if sql.startswith("SHOW INDEX"):
            return copy.deepcopy(self.indexes[name])
        if sql.startswith("SELECT *"):
            return copy.deepcopy(self.rows[name])
        raise AssertionError("unexpected fake SQL")

    def get_connection(self):
        return self.locker

    def connect(self):
        pass

    def exists(self, doctype, name):
        return (doctype, name) in self.known

    def rollback(self):
        self._conn.rollback()

    def commit(self):
        self.commits += 1


class FakeFrappe:
    def __init__(self, target):
        self.local = Attr(site=target.site)
        self.conf = Attr(
            db_name=target.database,
            db_host="local-only",
            maintenance_mode=1,
            pause_scheduler=1,
            disable_scheduler=1,
            mute_emails=1,
        )
        self.db = FakeDB(target)
        self.calls = []
        self.fail_at = None

    def reload_doc(self, module, kind, name, force):
        self.calls.append((module, kind, name, force))
        if self.fail_at == name:
            raise RuntimeError("synthetic ambiguous native failure")

    def init(self, site, sites_path):
        self.local.site = site

    def connect(self):
        pass

    def set_user(self, user):
        self.calls.append(("user", user))

    def destroy(self):
        self.db._conn.identity += 2


def allow_preconditions(fr):
    fr.db.known.update(
        ("Role", role)
        for role in N.AUTOMATIC_ROLES
        | {"Accounts User", "Accounts Manager", "System Manager"}
    )
    fr.db.known.add(("Module Def", "Jifeng ERP"))
    fr.db.known.update(
        ("DocType", name)
        for name in (
            "Company",
            "Currency",
            "Jifeng Expense Voucher",
            "Journal Entry",
            "DocType",
        )
    )


class CollectorTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.gettempdir()).resolve() / "tool-cost-model"
        self.target = target(root)
        self.fr = FakeFrappe(self.target)

    def test_actual_engine_columns_rows_indexes_and_sequence_values_are_measured(self):
        value = N.collect_snapshot(self.fr, A, self.target)
        A.verify_baseline(value.snapshot)
        self.assertEqual(set(value.sequence_rows), A.SEQUENCES)
        self.assertEqual(
            value.sequence_rows["crm_note_id_seq"][0]["next_not_cached_value"], 7
        )
        self.assertEqual(
            value.snapshot["tables"]["crm_note_id_seq"]["sha"],
            A.digest_rows([sequence()]),
        )
        self.assertEqual(
            value.snapshot["tables"]["tabCompany"]["schema"],
            A.digest_rows(self.fr.db.columns["tabCompany"]),
        )
        self.assertIn("Privileges", self.fr.db.columns["tabCompany"][0])
        self.assertEqual(set(value.snapshot), {"tables", "metadata", "sequences"})

    def test_actual_sequence_type_engine_and_full_row_omissions_fail(self):
        for defect in ("sequence", "engine", "view", "row", "column"):
            with self.subTest(defect=defect):
                fr = FakeFrappe(self.target)
                if defect == "sequence":
                    next(
                        row
                        for row in fr.db.inventory
                        if row["TABLE_NAME"] == "crm_note_id_seq"
                    )["TABLE_TYPE"] = "BASE TABLE"
                elif defect == "engine":
                    next(
                        row
                        for row in fr.db.inventory
                        if row["TABLE_NAME"] == "tabError Log"
                    )["ENGINE"] = "InnoDB"
                elif defect == "view":
                    next(
                        row
                        for row in fr.db.inventory
                        if row["TABLE_NAME"] == "tabCompany"
                    )["TABLE_TYPE"] = "VIEW"
                elif defect == "row":
                    fr.db.rows["crm_note_id_seq"][0].pop("cycle_count")
                else:
                    fr.db.columns["tabCompany"] = []
                with self.assertRaises(N.NativeError):
                    N.collect_snapshot(fr, A, self.target)

    def test_complete_index_projection_is_required_and_cardinality_is_excluded(self):
        index = {
            "Table": "tabCompany",
            "Key_name": "PRIMARY",
            "Non_unique": 0,
            "Seq_in_index": 1,
            "Column_name": "name",
            "Collation": "A",
            "Sub_part": None,
            "Null": "",
            "Index_type": "BTREE",
            "Comment": "",
            "Index_comment": "",
            "Ignored": "NO",
            "Cardinality": 12345,
            "Packed": None,
        }
        self.fr.db.indexes["tabCompany"] = [index]
        value = N.collect_snapshot(self.fr, A, self.target)
        expected = [{key: index[key] for key in A.INDEX_KEYS}]
        self.assertEqual(
            value.snapshot["tables"]["tabCompany"]["indexes"], A.digest_rows(expected)
        )
        self.fr.db.indexes["tabCompany"][0].pop("Ignored")
        with self.assertRaisesRegex(N.NativeError, "complete_native_indexes"):
            N.collect_snapshot(self.fr, A, self.target)

    def test_read_locks_span_body_then_unlock_and_close_even_on_failure(self):
        with N.protected_reads(self.fr, A, self.target):
            self.fr.db.events.append("native-backup-and-validation")
        events = self.fr.db.events
        lock = next(
            index
            for index, value in enumerate(events)
            if value.startswith("LOCK TABLES")
        )
        self.assertLess(lock, events.index("native-backup-and-validation"))
        self.assertLess(
            events.index("native-backup-and-validation"), events.index("UNLOCK TABLES")
        )
        self.assertEqual(events[-1], "connection.close")
        self.fr.db.events.clear()
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with N.protected_reads(self.fr, A, self.target):
                raise RuntimeError("synthetic")
        self.assertIn("UNLOCK TABLES", self.fr.db.events)
        self.assertEqual(self.fr.db.events[-1], "connection.close")

    def test_same_connection_wrong_site_and_transparent_reconnect_fail(self):
        with patch.object(self.fr.db, "get_connection", return_value=self.fr.db._conn):
            with self.assertRaisesRegex(N.NativeError, "independent_lock_connection"):
                with N.protected_reads(self.fr, A, self.target):
                    pass
        self.fr.local.site = "wrong.localhost"
        with self.assertRaisesRegex(N.NativeError, "site_database_identity"):
            with N.protected_reads(self.fr, A, self.target):
                pass
        self.fr.local.site = self.target.site
        with self.assertRaisesRegex(N.NativeError, "primary_connection_changed"):
            with N.protected_reads(self.fr, A, self.target):
                self.fr.db._conn = Connection(
                    self.target.database, 99, self.fr.db.events
                )

    def test_fresh_collection_really_requires_a_new_connection(self):
        self.fr.destroy = lambda: None
        with self.assertRaisesRegex(N.NativeError, "fresh_connection_required"):
            N.fresh_collection(self.fr, A, self.target)

    def test_child_then_parent_once_no_existing_reload_or_role_creation(self):
        allow_preconditions(self.fr)
        attempts = []
        result = N.apply_new_doctypes(
            self.fr,
            A,
            M.model()[0],
            definition(),
            guard=lambda: None,
            record_attempt=attempts.append,
        )
        self.assertEqual(attempts, list(reversed(A.TARGETS)))
        self.assertEqual(
            self.fr.calls,
            [
                ("jifeng_erp", "doctype", "jifeng_tool_cost_allocation_item", True),
                ("jifeng_erp", "doctype", "jifeng_tool_cost_allocation", True),
            ],
        )
        self.assertEqual(result["existing_doctypes_reloaded"], [])
        self.assertEqual(self.fr.db.commits, 0)

    def test_missing_role_existing_target_or_changed_schema_prevents_any_reload(self):
        for defect in ("role", "existing", "schema", "old_drift"):
            with self.subTest(defect=defect):
                fr = FakeFrappe(self.target)
                allow_preconditions(fr)
                before, candidate = M.model()[0], definition()
                if defect == "role":
                    fr.db.known.remove(("Role", "Accounts Manager"))
                elif defect == "existing":
                    fr.db.known.add(("DocType", A.TARGETS[0]))
                elif defect == "schema":
                    candidate["schemas"][A.TARGETS[0]]["permissions"][0]["share"] = 1
                else:
                    before["metadata"]["tabDocType"]["parents"][A.TARGETS[0]] = [{}]
                with self.assertRaises((N.NativeError, A.AuditError)):
                    N.apply_new_doctypes(
                        fr,
                        A,
                        before,
                        candidate,
                        guard=lambda: None,
                        record_attempt=lambda _: None,
                    )
                self.assertEqual(fr.calls, [])

    def test_ambiguous_parent_reload_failure_is_not_retried_committed_or_rolled_back(
        self,
    ):
        allow_preconditions(self.fr)
        self.fr.fail_at = "jifeng_tool_cost_allocation"
        attempts = []
        with self.assertRaisesRegex(RuntimeError, "synthetic ambiguous"):
            N.apply_new_doctypes(
                self.fr,
                A,
                M.model()[0],
                definition(),
                guard=lambda: None,
                record_attempt=attempts.append,
            )
        self.assertEqual(len(self.fr.calls), 2)
        self.assertEqual(attempts, list(reversed(A.TARGETS)))
        self.assertEqual(self.fr.db.commits, 0)
        # There is one pre-write rollback only, never after a possible mutation.
        self.assertEqual(self.fr.db.events.count("read.rollback"), 1)

    def test_production_and_clone_scope_cannot_be_arbitrarily_overridden(self):
        with self.assertRaisesRegex(N.NativeError, "fixed_production_target"):
            N.Target(site="arbitrary.localhost")
        with self.assertRaisesRegex(N.NativeError, "owned_clone_scope"):
            N.Target.owned_clone(
                site=N.SITE,
                database=N.DATABASE,
                bench=self.target.bench,
                root=self.target.root,
                uid=0,
            )

    def test_cache_requires_exact_app_assets_deny_hooks_and_parent_script(self):
        class Meta(Attr):
            def get_field(self, name):
                return self["fields"].get(name)

        class Cache:
            def __init__(self, forms):
                self.forms = forms
                self.deleted = []

            def delete_value(self, key):
                self.deleted.append(key)

            def get_value(self, key):
                return self.forms[key.removeprefix("doctype_form_meta::")]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = A.TARGETS[0]
            script = 'frappe.ui.form.on("Jifeng Tool Cost Allocation", {});'
            path = root / (A.SCHEMA_PATHS[parent][:-5] + ".js")
            path.parent.mkdir(parents=True)
            path.write_text(script, encoding="utf-8")
            candidate = definition()
            forms = {
                name: Meta(
                    fields={row["fieldname"]: Attr(row) for row in schema["fields"]},
                    __js=script if name == parent else "",
                )
                for name, schema in candidate["schemas"].items()
            }
            hooks = copy.deepcopy(A.NEW_HOOKS)
            hooks.update(
                app_include_css=["css-" + str(i) for i in range(12)],
                app_include_js=["js-" + str(i) for i in range(21)],
            )
            self.fr.client_cache = Cache(forms)
            self.fr.clear_cache = lambda **kwargs: self.fr.calls.append(
                ("clear", kwargs)
            )

            def get_hooks(name, app_name):
                self.assertEqual(app_name, "jifeng_erp")
                return hooks[name]

            self.fr.get_hooks = get_hooks
            paths = Attr(root=root, read=lambda item: item.read_bytes())
            manifest = {"hooks": hooks["app_include_css"] + hooks["app_include_js"]}
            result = N.verify_cache(
                self.fr, A, candidate, manifest, paths, get_form_meta=forms.__getitem__
            )
            self.assertEqual(result["effective_hooks"], 33)
            self.assertIn("app_hooks", self.fr.client_cache.deleted)
            for defect in ("asset", "deny", "share", "script", "private_flag"):
                original_hooks, original_forms = (
                    copy.deepcopy(hooks),
                    copy.deepcopy(forms),
                )
                if defect == "asset":
                    hooks["app_include_js"].pop()
                elif defect == "deny":
                    hooks["has_permission"][parent] = "wrong.callback"
                elif defect == "share":
                    hooks["doc_events"]["DocShare"]["validate"] = "unsafe.extra"
                elif defect == "script":
                    forms[parent]["__js"] = "old cached script"
                else:
                    forms[parent]["fields"]["source_snapshot"]["hidden"] = 0
                with self.subTest(defect=defect), self.assertRaises(N.NativeError):
                    N.verify_cache(
                        self.fr,
                        A,
                        candidate,
                        manifest,
                        paths,
                        get_form_meta=forms.__getitem__,
                    )
                hooks.clear()
                hooks.update(original_hooks)
                forms.clear()
                forms.update(original_forms)


@unittest.skipUnless(os.name == "posix", "production filesystem gates require POSIX")
class FileAndPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = target(Path(self.temp.name))
        self.target.root.mkdir()
        for name in (N.CONTROL_NAME, N.PRIVATE_NAME):
            (self.target.root / name).mkdir(mode=0o700)
        (self.target.root / N.PRIVATE_NAME / "audit").mkdir(mode=0o700)
        self.paths = N.Paths(self.target)
        self.site = self.target.bench / "sites" / self.target.site
        for folder in ("public", "private"):
            (self.site / folder / "files").mkdir(parents=True)
        (self.site / "public/files/a.txt").write_bytes(b"public fixture")
        (self.site / "private/files/b.txt").write_bytes(b"private fixture")
        self.config = b'{"db_name":"jf_tc_release_unit","maintenance_mode":1}\n'
        (self.site / "site_config.json").write_bytes(self.config)
        self.fr = FakeFrappe(self.target)
        self.before = N.collect_snapshot(self.fr, A, self.target)
        self.runner = N.Runner(self.fr, A, self.paths, REVIEWED, clock=lambda: NOW)
        self.parts = None

    def backup(self):
        directory = self.paths.private / "native-backup"
        directory.mkdir(mode=0o700)
        sql = []
        for name in self.before.snapshot["tables"]:
            quoted = N.quote(name)
            if name in A.SEQUENCES:
                sql.extend(
                    [
                        "CREATE SEQUENCE "
                        + quoted
                        + " start with 1 minvalue 1 maxvalue 9223372036854775806 increment by 1 nocache nocycle ENGINE=InnoDB;\n",
                        "DO SETVAL(" + quoted + ", 7, 0);\n",
                    ]
                )
            else:
                sql.append("CREATE TABLE " + quoted + " (\n  name varchar(140)\n);\n")
        parts = {
            "database": directory / "database.sql.gz",
            "configuration": directory / "configuration.json",
        }
        with gzip.open(parts["database"], "wt", encoding="utf-8") as stream:
            stream.write("".join(sql))
        parts["configuration"].write_bytes(self.config)
        for folder in ("public", "private"):
            path = directory / (folder + ".tgz")
            with tarfile.open(path, "w:gz") as archive:
                source = self.site / folder / "files"
                archive.add(source, arcname=self.target.site + "/" + folder + "/files")
            parts[folder + "-files"] = path
        self.parts = parts
        return parts

    def validated_backup(self):
        parts = self.backup()
        trees = {
            folder: N.file_tree(self.site / folder / "files")
            for folder in ("public", "private")
        }
        inventory = N.validate_backups(
            parts, self.paths, self.before, self.config, trees
        )
        return inventory

    @contextmanager
    def mocked_runtime(self):
        with (
            patch.object(N, "runtime"),
            patch.object(N, "candidate", return_value=(definition(), {"hooks": []})),
            patch.object(N, "connect"),
        ):
            yield

    def test_directories_traversal_symlinks_and_wrong_modes_fail(self):
        with self.assertRaisesRegex(N.NativeError, "fixed_release_directories"):
            N.Paths(self.target, control=self.paths.private)
        with self.assertRaisesRegex(N.NativeError, "release_path_scope"):
            self.paths.check(self.paths.private / ".." / ".." / "escape")
        link = self.paths.private / "link"
        link.symlink_to(self.paths.control, target_is_directory=True)
        with self.assertRaisesRegex(N.NativeError, "release_symlink"):
            self.paths.check(link, directory=True)
        os.chmod(self.paths.control, 0o755)
        with self.assertRaisesRegex(N.NativeError, "release_path_mode"):
            N.Paths(self.target)

    def test_four_parts_full_objects_sequence_values_tar_content_and_config_pass(self):
        inventory = self.validated_backup()
        self.assertEqual(len(inventory), 4)
        before_sha = self.paths.record("before.json", self.before.snapshot, A)
        extra_sha = self.paths.record("before-extra.json", self.before.extra(), A)
        receipt = {
            "identity": self.runner.identity,
            "before_sha256": before_sha,
            "parts": inventory,
            "physical_objects": 932,
            "myisam_locked_through_validation": True,
            "before_extra_sha256": extra_sha,
        }
        N.check_backup_receipt(self.paths, receipt, self.runner.identity, before_sha)
        self.assertTrue(all(part["bytes"] > 0 for part in inventory))

    def test_missing_sequence_state_wrong_value_or_missing_physical_object_fails(self):
        parts = self.backup()
        data = gzip.decompress(parts["database"].read_bytes()).decode()
        for defect in (
            "state_missing",
            "state_wrong",
            "object_missing",
            "fake_create_in_data",
        ):
            changed = data
            if defect == "state_missing":
                changed = changed.replace(
                    "DO SETVAL(" + N.quote("crm_note_id_seq") + ", 7, 0);\n", ""
                )
            elif defect == "state_wrong":
                changed = changed.replace(
                    "DO SETVAL(" + N.quote("crm_note_id_seq") + ", 7, 0);",
                    "DO SETVAL(" + N.quote("crm_note_id_seq") + ", 8, 0);",
                )
            else:
                changed = changed.replace(
                    "CREATE TABLE "
                    + N.quote("tabCompany")
                    + " (\n  name varchar(140)\n);\n",
                    "",
                )
                if defect == "fake_create_in_data":
                    changed += (
                        "INSERT INTO dummy VALUES ('CREATE TABLE "
                        + N.quote("tabCompany")
                        + " (');\n"
                    )
            with gzip.open(parts["database"], "wt", encoding="utf-8") as stream:
                stream.write(changed)
            with self.subTest(defect=defect), self.assertRaises(N.NativeError):
                N.validate_sql_backup(
                    parts["database"], self.before.snapshot, self.before.sequence_rows
                )

    def test_gzip_crc_missing_files_extra_members_and_links_fail(self):
        parts = self.backup()
        path = parts["public-files"]
        tree = N.file_tree(self.site / "public/files")
        original = path.read_bytes()
        path.write_bytes(original[:-8])
        with self.assertRaises((EOFError, gzip.BadGzipFile)):
            N.validate_tar_backup(path, tree, self.site / "public/files")
        for kind in ("missing", "extra", "symlink", "traversal"):
            with tarfile.open(path, "w:gz") as archive:
                prefix = self.target.site + "/public/files"
                directory = tarfile.TarInfo(prefix)
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                if kind != "missing":
                    item = tarfile.TarInfo(
                        prefix + ("/../escape" if kind == "traversal" else "/extra")
                    )
                    item.type = (
                        tarfile.SYMTYPE if kind == "symlink" else tarfile.REGTYPE
                    )
                    item.linkname = "/etc/passwd"
                    item.size = 0
                    archive.addfile(item, io.BytesIO())
            with self.subTest(kind=kind), self.assertRaises(N.NativeError):
                N.validate_tar_backup(path, tree, self.site / "public/files")
        path.write_bytes(original)

    def test_backup_receipt_binding_extra_files_changed_sha_and_mode_fail(self):
        inventory = self.validated_backup()
        before_sha = self.paths.record("before.json", self.before.snapshot, A)
        extra_sha = self.paths.record("before-extra.json", self.before.extra(), A)
        base = {
            "identity": self.runner.identity,
            "before_sha256": before_sha,
            "parts": inventory,
            "physical_objects": 932,
            "myisam_locked_through_validation": True,
            "before_extra_sha256": extra_sha,
        }
        for kind in ("identity", "before", "missing", "hash"):
            receipt = copy.deepcopy(base)
            if kind == "identity":
                receipt["identity"]["new_commit"] = N.OLD
            elif kind == "before":
                receipt["before_sha256"] = "b" * 64
            elif kind == "missing":
                receipt["parts"].pop()
            else:
                receipt["parts"][0]["sha256"] = "b" * 64
            with self.subTest(kind=kind), self.assertRaises(N.NativeError):
                N.check_backup_receipt(
                    self.paths, receipt, self.runner.identity, before_sha
                )
        path = self.paths.private / "native-backup" / inventory[0]["name"]
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(N.NativeError, "release_path_mode"):
            N.check_backup_receipt(self.paths, base, self.runner.identity, before_sha)

    def test_source_config_changed_during_backup_and_incomplete_parts_fail(self):
        parts = self.backup()
        parts["configuration"].write_bytes(b"changed configuration")
        trees = {
            folder: N.file_tree(self.site / folder / "files")
            for folder in ("public", "private")
        }
        with self.assertRaisesRegex(N.NativeError, "backup_original_config"):
            N.validate_backups(parts, self.paths, self.before, self.config, trees)
        parts.pop("private-files")
        with self.assertRaisesRegex(N.NativeError, "four_unique_backup_parts"):
            N.validate_backups(parts, self.paths, self.before, self.config, trees)

    def test_used_or_interrupted_phase_never_runs_runtime_again(self):
        self.runner.claim("apply")
        with patch.object(N, "runtime") as runtime:
            with self.assertRaises(FileExistsError):
                N.Runner(self.fr, A, self.paths, REVIEWED, clock=lambda: NOW).run(
                    "apply"
                )
            runtime.assert_not_called()
        # The first runner may consume its claim once, then a failed prerequisite
        # publishes failure only and keeps the durable started marker.
        with self.mocked_runtime():
            result = self.runner.run("apply")
        self.assertFalse(result["passed"])
        self.assertFalse((self.paths.audit_directory / "apply-completed.json").exists())
        with self.assertRaises(FileExistsError):
            self.runner.run("apply")

    def test_failure_before_connect_keeps_marker_and_has_no_success_receipt(self):
        with (
            patch.object(N, "runtime", side_effect=N.NativeError("source_pin_failed")),
            patch.object(N, "connect") as connect,
        ):
            result = self.runner.run("before")
        self.assertFalse(result["passed"])
        self.assertTrue((self.paths.control / "before-started.json").exists())
        self.assertFalse(
            (self.paths.audit_directory / "before-completed.json").exists()
        )
        connect.assert_not_called()

    def test_commit_alone_cannot_unlock_verify_or_cache(self):
        self.paths.record(
            "apply-committed.json",
            {"committed": True, "identity": self.runner.identity},
            A,
        )
        self.paths.record(
            "before-completed.json",
            {"passed": False, "phase": "before", "identity": self.runner.identity},
            A,
        )
        with self.mocked_runtime():
            result = self.runner.run("verify", "c" * 64)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "successful_previous_phase_required")
        self.assertFalse(
            (self.paths.audit_directory / "verify-completed.json").exists()
        )

    def test_all_four_phases_follow_success_chain_and_preserve_old_objects(self):
        self.runner.backup_factory = lambda _fr, _paths: self.backup()
        with self.mocked_runtime():
            before_result = self.runner.run("before")
        self.assertTrue(before_result["passed"], before_result.get("reason"))
        before_sha = before_result["proof_sha256"]
        baseline = self.paths.proof("before.json", before_sha)
        _old, native_after, _schemas, installed = M.model()
        after = copy.deepcopy(baseline)
        for table in A.META:
            after["metadata"][table] = native_after["metadata"][table]
            after["tables"][table]["rows"] = native_after["tables"][table]["rows"]
            after["tables"][table]["sha"] = native_after["tables"][table]["sha"]
        for table in A.NEW_TABLES:
            after["tables"][table] = native_after["tables"][table]
        post = N.Collection(
            after, installed, self.before.sequence_rows, self.before.inventory
        )
        pre = N.Collection(
            baseline, {}, self.before.sequence_rows, self.before.inventory
        )
        allow_preconditions(self.fr)
        with (
            self.mocked_runtime(),
            patch.object(N, "fresh_collection", side_effect=[pre, post]),
        ):
            applied = self.runner.run("apply", before_sha)
        self.assertTrue(applied["passed"], applied.get("reason"))
        self.assertEqual(self.fr.db.commits, 1)
        self.assertEqual(applied["apply"]["attempted"], list(reversed(A.TARGETS)))
        with (
            self.mocked_runtime(),
            patch.object(N, "fresh_collection", return_value=post),
        ):
            verified = self.runner.run("verify", before_sha)
        self.assertTrue(verified["passed"], verified.get("reason"))
        with (
            self.mocked_runtime(),
            patch.object(N, "fresh_collection", return_value=post),
            patch.object(N, "verify_cache", return_value={"effective_hooks": 33}),
        ):
            cached = self.runner.run("cache", before_sha)
        self.assertTrue(cached["passed"], cached.get("reason"))
        self.assertEqual(cached["delta"]["existing_fields_changed"], 0)
        for phase in N.PHASES:
            self.assertTrue((self.paths.control / (phase + "-started.json")).exists())
            self.assertTrue(
                (self.paths.audit_directory / (phase + "-completed.json")).exists()
            )


if __name__ == "__main__":
    unittest.main()
