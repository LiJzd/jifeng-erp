"""One-use native phases for the tool-cost test release.

Production CLI targets are fixed. The host must stop all application writers and
attest Git objects/read-only mounts. This helper verifies package bytes, native
implementation pins, maintenance identity, full protected SQL snapshots and the
four backup parts. It never migrates the whole site, seeds data, grants roles,
restores SQL, deletes receipts, flushes Redis globally or retries a used phase.
"""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile

OLD = "2ca4358398cf750da31584fc31d3afaef48b30b6"
NEW = "5b49bacde6fcc088e08b118103fee496524ec99f"
SITE = "jifeng-erp-test.localhost"
DATABASE = "jifeng_erp_test"
BENCH = Path("/home/frappe/frappe-bench")
ROOT = Path("/opt/source-release")
CONTROL_NAME = "tool-cost-control-r1"
PRIVATE_NAME = "tool-cost-private-r1"
AUDIT_SHA256 = "518b8cf4918e46ab5a38c80f2c5f02b73f1ec5a332a36ae58a651f169c36ead0"
PHASES = ("before", "apply", "verify", "cache")
NATIVE_PINS = {
    "frappe/utils/backups.py": "04647dc5d8abc6f7b7a2e59086949405c3de1f5ae70c120a4a75dc58a2064a76",
    "frappe/database/mariadb/database.py": "0eaae7d449c560dcd0fa6ef56686bcfe2eb10a474f21c62c282617cf3c3fb3b9",
    "frappe/database/__init__.py": "c78ea57b3fee7ca4299ece3e384dd62fa6e655e8d9c6e42f31307db4c20b7b29",
    "frappe/modules/import_file.py": "d230781155b141cf364594392caa375009bcfd93c6bf52e16ec0f8f35586622b",
    "frappe/core/doctype/doctype/doctype.py": "9d54416a5754fdeecddfa62e6e7115f49b37012dcce09c0d77098c0f4af428c4",
    "frappe/model/sync.py": "be2e542b91bc7b9d22e162ef89004f24a60454c13629350237a06407c280b69b",
    "frappe/database/mariadb/schema.py": "7a0e8a45a931e5dc60c274389dedccf403234d89065724b724a5f4f664337f65",
    "frappe/database/schema.py": "08ef084d84f17a37af123a2d1591975936712277c15cef979047389ad7a7e2df",
}
AUTOMATIC_ROLES = {"Guest", "All", "Desk User", "Administrator"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
SEQUENCE_FIELDS = {
    "next_not_cached_value",
    "minimum_value",
    "maximum_value",
    "start_value",
    "increment",
    "cache_size",
    "cycle_option",
    "cycle_count",
}


class NativeError(RuntimeError):
    """Public rule only, never include native output, values or credentials."""


def require(condition, rule):
    if not condition:
        raise NativeError(rule)


@dataclass(frozen=True)
class Target:
    site: str = SITE
    database: str = DATABASE
    bench: Path = BENCH
    root: Path = ROOT
    uid: int = 1000
    clone: bool = False

    def __post_init__(self):
        if not self.clone:
            require(
                (self.site, self.database, self.bench, self.root, self.uid)
                == (SITE, DATABASE, BENCH, ROOT, 1000),
                "fixed_production_target",
            )
        else:
            require(
                self.site.startswith("jifeng-tool-cost-release-qa-")
                and self.site.endswith(".localhost")
                and re.fullmatch(r"jf_tc_release_[a-z0-9_]+", self.database)
                and self.bench.is_absolute()
                and self.root.is_absolute()
                and type(self.uid) is int
                and self.uid in (0, 1000),
                "owned_clone_scope",
            )

    @classmethod
    def owned_clone(cls, *, site, database, bench, root, uid):
        """Function-only override; ownership of the fresh clone is caller-attested."""
        return cls(site, database, Path(bench), Path(root), uid, True)


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def quote(value):
    require(isinstance(value, str) and value, "sql_identifier")
    mark = chr(96)
    return mark + value.replace(mark, mark * 2) + mark


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
        raise NativeError("unknown_evidence_type")
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def wire(value):
    if isinstance(value, (datetime, date, time)):
        return {"type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, timedelta):
        return {
            "type": "timedelta",
            "value": [value.days, value.seconds, value.microseconds],
        }
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def fsync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Paths:
    def __init__(self, target, control=None, private=None):
        self.target, self.root = target, target.root
        self.control = Path(control) if control else self.root / CONTROL_NAME
        self.private = Path(private) if private else self.root / PRIVATE_NAME
        require(
            self.control == self.root / CONTROL_NAME
            and self.private == self.root / PRIVATE_NAME,
            "fixed_release_directories",
        )
        for directory in (self.root, self.control, self.private):
            self.check(
                directory,
                directory=True,
                mode=0o700 if directory != self.root else None,
            )
        self.audit_directory = self.private / "audit"
        self.check(self.audit_directory, directory=True, mode=0o700)

    def check(self, path, *, directory=False, mode=None):
        path = Path(path)
        require(
            path.is_absolute()
            and ".." not in path.parts
            and (path == self.root or self.root in path.parents),
            "release_path_scope",
        )
        for component in (path, *path.parents):
            info = component.lstat()
            require(not stat.S_ISLNK(info.st_mode), "release_symlink")
            if component == self.root:
                break
        info = path.lstat()
        require(
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "regular_release_path",
        )
        require(info.st_uid == self.target.uid, "release_path_owner")
        require(mode is None or stat.S_IMODE(info.st_mode) == mode, "release_path_mode")
        return path

    def read(self, path, expected_sha=None, mode=0o644):
        path = self.check(path, mode=mode)
        if expected_sha is not None:
            require(
                isinstance(expected_sha, str) and SHA.fullmatch(expected_sha),
                "reviewed_sha256",
            )
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == self.target.uid
                and stat.S_IMODE(info.st_mode) == mode,
                "opened_file_identity",
            )
            data = stream.read()
        require(
            expected_sha is None or hashlib.sha256(data).hexdigest() == expected_sha,
            "pinned_file_changed",
        )
        return data

    def record(self, name, value, audit, *, control=False):
        require(re.fullmatch(r"[a-z0-9-]+[.]json", name), "receipt_name")
        directory = self.control if control else self.audit_directory
        self.check(directory, directory=True, mode=0o700)
        path = directory / name
        fd = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            data = (
                json.dumps(
                    wire(value),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(directory)
        return hashlib.sha256(data).hexdigest()

    def proof(self, name, expected_sha=None):
        return decode(
            json.loads(self.read(self.audit_directory / name, expected_sha, mode=0o600))
        )


@dataclass
class Collection:
    snapshot: dict
    installed: dict
    sequence_rows: dict
    inventory: list

    def extra(self):
        return {"sequence_rows": self.sequence_rows, "inventory": self.inventory}


def connection_identity(fr):
    rows = fr.db.sql(
        "SELECT DATABASE() AS database_name, CONNECTION_ID() AS connection_id",
        as_dict=True,
    )
    require(
        len(rows) == 1 and type(rows[0]["connection_id"]) is int, "connection_identity"
    )
    return rows[0]["database_name"], rows[0]["connection_id"]


def assert_site(fr, target):
    require(
        fr.local.site == target.site and fr.conf.db_name == target.database,
        "site_database_identity",
    )
    require(
        all(
            fr.conf.get(key) == 1
            for key in (
                "maintenance_mode",
                "pause_scheduler",
                "disable_scheduler",
                "mute_emails",
            )
        ),
        "quiescent_site_flags",
    )
    if not target.clone:
        require(
            fr.conf.db_host == "db" and fr.db.host == "db", "production_database_host"
        )
    require(
        connection_identity(fr)[0] == target.database, "connected_database_identity"
    )


def connect(fr, target):
    fr.init(site=target.site, sites_path=str(target.bench / "sites"))
    fr.connect()
    fr.db.connect()
    assert_site(fr, target)
    fr.set_user("Administrator")


@contextmanager
def protected_reads(fr, audit, target):
    """Independent three-MyISAM READ locks span the primary RR read and backup."""
    assert_site(fr, target)
    primary = fr.db._conn
    database, primary_id = connection_identity(fr)
    locker, cursor = None, None
    try:
        locker = fr.db.get_connection()
        require(locker is not primary, "independent_lock_connection")
        locker.select_db(target.database)
        cursor = locker.cursor()
        cursor.execute("SET SESSION lock_wait_timeout=10")
        cursor.execute("SELECT DATABASE(), CONNECTION_ID()")
        lock_database, lock_id = cursor.fetchone()
        require(lock_database == database and lock_id != primary_id, "locker_identity")
        cursor.execute(
            "LOCK TABLES "
            + ", ".join(quote(name) + " READ" for name in sorted(audit.MYISAM))
        )
        primary.rollback()
        main = primary.cursor()
        try:
            main.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            main.execute("SET TRANSACTION READ ONLY")
            main.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        finally:
            main.close()
        yield
        require(
            fr.db._conn is primary
            and connection_identity(fr) == (database, primary_id),
            "primary_connection_changed",
        )
        cursor.execute("SELECT DATABASE(), CONNECTION_ID()")
        require(cursor.fetchone() == (database, lock_id), "locker_connection_changed")
    finally:
        # This rollback only closes a read transaction; apply runs outside here.
        try:
            primary.rollback()
        finally:
            try:
                if cursor is not None:
                    cursor.execute("UNLOCK TABLES")
            finally:
                if cursor is not None:
                    cursor.close()
                if locker is not None:
                    locker.close()


def collect_snapshot(fr, audit, target):
    """All proofs originate from actual SELECTs, never guessed engines or values."""
    assert_site(fr, target)
    inventory = [
        dict(row)
        for row in fr.db.sql(
            "SELECT TABLE_NAME,ENGINE,TABLE_COLLATION,ROW_FORMAT,TABLE_TYPE "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME",
            as_dict=True,
        )
    ]
    names = [row["TABLE_NAME"] for row in inventory]
    require(len(names) == len(set(names)), "unique_physical_objects")
    require(
        {row["TABLE_NAME"] for row in inventory if row["ENGINE"] != "InnoDB"}
        == audit.MYISAM,
        "actual_three_myisam",
    )
    sequences = {
        row["TABLE_NAME"] for row in inventory if row["TABLE_TYPE"] == "SEQUENCE"
    }
    require(sequences == audit.SEQUENCES, "actual_six_sequences")
    tables, metadata, target_indexes, sequence_rows = {}, {}, {}, {}
    for physical in inventory:
        name = physical["TABLE_NAME"]
        require(
            physical["TABLE_TYPE"]
            == ("SEQUENCE" if name in sequences else "BASE TABLE"),
            "known_physical_object_type",
        )
        columns = [
            dict(row)
            for row in fr.db.sql("SHOW FULL COLUMNS FROM " + quote(name), as_dict=True)
        ]
        require(
            columns and len({row["Field"] for row in columns}) == len(columns),
            "complete_columns",
        )
        raw_indexes = fr.db.sql("SHOW INDEX FROM " + quote(name), as_dict=True)
        require(
            all(
                audit.INDEX_KEYS <= set(row) and row.get("Table", name) == name
                for row in raw_indexes
            ),
            "complete_native_indexes",
        )
        indexes = [{key: row[key] for key in audit.INDEX_KEYS} for row in raw_indexes]
        rows = [
            dict(row) for row in fr.db.sql("SELECT * FROM " + quote(name), as_dict=True)
        ]
        field_names = [row["Field"] for row in columns]
        require(all(set(row) == set(field_names) for row in rows), "complete_sql_rows")
        tables[name] = {
            "engine": physical["ENGINE"],
            "rows": len(rows),
            "sha": audit.digest_rows(rows),
            "schema": audit.digest_rows(columns),
            "indexes": audit.digest_rows(indexes),
        }
        if name in sequences:
            require(
                len(rows) == 1 and set(rows[0]) == SEQUENCE_FIELDS,
                "complete_sequence_state",
            )
            sequence_rows[name] = rows
        if name in audit.NEW_TABLES:
            target_indexes[name] = indexes
        if name in audit.META:
            identity = "name" if name == "tabDocType" else "parent"
            parents = {
                parent: sorted(
                    [row for row in rows if row[identity] == parent],
                    key=lambda row: (row["idx"], row["name"]),
                )
                for parent in audit.TARGETS
            }
            owned = {row["name"] for group in parents.values() for row in group}
            outside = [row for row in rows if row["name"] not in owned]
            metadata[name] = {
                "columns": field_names,
                "parents": parents,
                "outside": {"rows": len(outside), "sha": audit.digest_rows(outside)},
            }
    installed = {}
    if audit.NEW_TABLES <= set(tables) and all(
        len(metadata["tabDocType"]["parents"][name]) == 1 for name in audit.TARGETS
    ):
        for name in audit.TARGETS:
            installed[name] = {
                "doctype": metadata["tabDocType"]["parents"][name][0],
                "indexes": target_indexes["tab" + name],
                **{
                    section: metadata[table]["parents"][name]
                    for table, section in audit.META.items()
                    if section
                },
            }
    return Collection(
        {"tables": tables, "metadata": metadata, "sequences": sorted(sequences)},
        installed,
        sequence_rows,
        inventory,
    )


def fresh_collection(fr, audit, target):
    """Destroy the old connection and collect in a new protected read transaction."""
    previous = connection_identity(fr)[1]
    fr.destroy()
    connect(fr, target)
    require(connection_identity(fr)[1] != previous, "fresh_connection_required")
    with protected_reads(fr, audit, target):
        return collect_snapshot(fr, audit, target)


def file_tree(root):
    require(root.is_dir() and not root.is_symlink(), "backup_source_directory")
    result = {"": {"kind": "directory"}}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            result[relative] = {"kind": "directory"}
        else:
            require(stat.S_ISREG(info.st_mode), "backup_source_not_regular")
            result[relative] = {
                "kind": "file",
                "bytes": info.st_size,
                "sha256": sha(path),
            }
    return result


def validate_sql_backup(path, snapshot, sequence_rows):
    """Pinned MariaDB dump declaration grammar; never inspect INSERT strings."""
    tick = chr(96)
    identifier = tick + r"((?:" + tick * 2 + r"|[^" + tick + r"])+)" + tick
    prefix = r"^(?:/\*(?:M)?![0-9]{5,6}\s+)?"
    declaration = re.compile(
        prefix + r"CREATE (TABLE|SEQUENCE)\s+" + identifier + r"(\s.*)"
    )
    sequence_definition = re.compile(
        r"\s+start with (-?\d+) minvalue (-?\d+) maxvalue (-?\d+) increment by (-?\d+) "
        r"(nocache|cache \d+) (nocycle|cycle) ENGINE=InnoDB;(?:\s*\*/;?)?\s*\Z",
        re.I,
    )
    setval = re.compile(
        prefix
        + r"DO SETVAL\("
        + identifier
        + r",\s*(-?\d+),\s*(0)\);(?:\s*\*/;?)?\s*\Z",
        re.I,
    )
    objects, definitions, states = set(), {}, {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            match = declaration.match(line)
            if match:
                kind, name, tail = match.groups()
                name = name.replace(tick * 2, tick)
                require(name not in objects, "backup_duplicate_physical_object")
                objects.add(name)
                if kind == "SEQUENCE":
                    parts = sequence_definition.fullmatch(tail)
                    require(parts is not None, "known_sequence_dump_definition")
                    start, minimum, maximum, increment, cache, cycle = parts.groups()
                    definitions[name] = {
                        "start_value": int(start),
                        "minimum_value": int(minimum),
                        "maximum_value": int(maximum),
                        "increment": int(increment),
                        "cache_size": 0
                        if cache.lower() == "nocache"
                        else int(cache.split()[1]),
                        "cycle_option": int(cycle.lower() == "cycle"),
                    }
            elif line.startswith(("CREATE TABLE", "CREATE SEQUENCE")):
                require(False, "unknown_dump_declaration")
            match = setval.fullmatch(line.rstrip("\r\n"))
            if match:
                name, value, _mode = match.groups()
                name = name.replace(tick * 2, tick)
                require(name not in states, "backup_duplicate_sequence_state")
                states[name] = int(value)
            elif line.startswith("DO SETVAL("):
                require(False, "unknown_sequence_dump_state")
    require(objects == set(snapshot["tables"]), "backup_all_physical_objects")
    require(
        set(definitions)
        == set(states)
        == set(sequence_rows)
        == set(snapshot["sequences"]),
        "backup_all_sequence_states",
    )
    for name, rows in sequence_rows.items():
        require(
            len(rows) == 1 and set(rows[0]) == SEQUENCE_FIELDS,
            "backup_sequence_reference",
        )
        state = rows[0]
        require(
            all(definitions[name][key] == state[key] for key in definitions[name])
            and states[name] == state["next_not_cached_value"]
            and state["cycle_count"] == 0,
            "backup_sequence_values",
        )


def validate_tar_backup(path, tree, source):
    # tar EOF can precede gzip CRC/trailer, so validate the entire gzip first.
    with gzip.open(path, "rb") as stream:
        for _data in iter(lambda: stream.read(1024 * 1024), b""):
            pass
    prefixes = {
        PurePosixPath(str(source).replace("\\", "/").lstrip("/")),
        PurePosixPath(*source.parts[-3:]),
    }
    observed = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(
                not name.is_absolute() and ".." not in name.parts,
                "backup_tar_path_scope",
            )
            matches = [
                prefix
                for prefix in prefixes
                if name == prefix or prefix in name.parents
            ]
            require(len(matches) == 1, "backup_tar_source_scope")
            relative = name.relative_to(matches[0]).as_posix()
            relative = "" if relative == "." else relative
            require(relative not in observed, "backup_tar_duplicate_member")
            if member.isdir():
                observed[relative] = {"kind": "directory"}
            else:
                require(
                    member.isfile() and not member.issym() and not member.islnk(),
                    "backup_tar_regular_member",
                )
                digest, count = hashlib.sha256(), 0
                with archive.extractfile(member) as stream:
                    for data in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(data)
                        count += len(data)
                require(count == member.size, "backup_tar_member_size")
                observed[relative] = {
                    "kind": "file",
                    "bytes": count,
                    "sha256": digest.hexdigest(),
                }
    require(observed == tree, "backup_complete_source_tree")


def native_backup(fr, paths):
    from frappe.utils.backups import BackupGenerator

    directory = paths.private / "native-backup"
    require(not directory.exists(), "backup_already_started")
    directory.mkdir(mode=0o700)
    require(
        not fr.get_system_settings("encrypt_backup"), "backup_encryption_not_supported"
    )
    generator = BackupGenerator(
        fr.conf.db_name,
        fr.conf.db_user or fr.conf.db_name,
        fr.conf.db_password,
        db_socket=fr.conf.db_socket,
        db_host=fr.conf.db_host,
        db_port=fr.conf.db_port,
        db_type=fr.conf.db_type or "mariadb",
        backup_path=str(directory),
        ignore_conf=True,
        compress_files=True,
        verbose=False,
    )
    require(
        not generator.backup_includes
        and not generator.backup_excludes
        and not generator.partial,
        "full_native_backup_scope",
    )
    generator.get_backup(force=True, ignore_files=False)
    return {
        "database": Path(generator.backup_path_db),
        "configuration": Path(generator.backup_path_conf),
        "public-files": Path(generator.backup_path_files),
        "private-files": Path(generator.backup_path_private_files),
    }


def validate_backups(parts, paths, collection, config, trees):
    require(
        set(parts) == {"database", "configuration", "public-files", "private-files"}
        and len(set(parts.values())) == 4,
        "four_unique_backup_parts",
    )
    directory = paths.private / "native-backup"
    paths.check(directory, directory=True, mode=0o700)
    require(set(directory.iterdir()) == set(parts.values()), "four_backup_parts_exact")
    inventory = []
    for part, path in sorted(parts.items()):
        require(path.parent == directory, "backup_path_scope")
        paths.check(path)
        require(path.stat().st_size > 0, "nonempty_backup")
        os.chmod(path, 0o600)
        paths.check(path, mode=0o600)
        if part == "database":
            validate_sql_backup(path, collection.snapshot, collection.sequence_rows)
        elif part == "configuration":
            require(paths.read(path, mode=0o600) == config, "backup_original_config")
        else:
            folder = "public" if part == "public-files" else "private"
            source = paths.target.bench / "sites" / paths.target.site / folder / "files"
            validate_tar_backup(path, trees[folder], source)
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        inventory.append(
            {
                "part": part,
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha(path),
            }
        )
    fsync_directory(directory)
    return inventory


def check_backup_receipt(paths, receipt, identity, before_sha):
    require(
        set(receipt)
        == {
            "identity",
            "before_sha256",
            "parts",
            "physical_objects",
            "myisam_locked_through_validation",
            "before_extra_sha256",
        }
        and receipt["identity"] == identity
        and receipt["before_sha256"] == before_sha
        and receipt["physical_objects"] == 932
        and receipt["myisam_locked_through_validation"] is True,
        "backup_receipt_identity",
    )
    directory = paths.private / "native-backup"
    paths.check(directory, directory=True, mode=0o700)
    parts = receipt["parts"]
    require(
        isinstance(parts, list)
        and len(parts) == 4
        and {part["part"] for part in parts}
        == {"database", "configuration", "public-files", "private-files"},
        "backup_receipt_parts",
    )
    expected = set()
    for part in parts:
        require(
            set(part) == {"part", "name", "bytes", "sha256"}
            and isinstance(part["name"], str)
            and Path(part["name"]).name == part["name"],
            "backup_receipt_path",
        )
        path = directory / part["name"]
        require(path not in expected, "backup_receipt_duplicate_path")
        expected.add(path)
        paths.check(path, mode=0o600)
        require(
            path.stat().st_size == part["bytes"]
            and part["bytes"] > 0
            and sha(path) == part["sha256"],
            "backup_receipt_file_changed",
        )
    require(set(directory.iterdir()) == expected, "backup_receipt_inventory")
    paths.proof("before-extra.json", receipt["before_extra_sha256"])


def apply_new_doctypes(fr, audit, before, definition, *, guard, record_attempt):
    """Exactly child then parent; never reload or patch an existing DocType."""
    audit.verify_baseline(before)
    require(
        definition.get("passed") is True
        and definition["migration_hash"] == audit.MIGRATION_HASH
        and set(definition["schemas"]) == set(audit.TARGETS),
        "frozen_definition_required",
    )
    roles = set(AUTOMATIC_ROLES)
    links = set()
    for name in audit.TARGETS:
        source = definition["schemas"][name]
        require(
            audit.fingerprint(source) == audit.DEFINITION_SHA256[name],
            "frozen_candidate_schema",
        )
        require(not fr.db.exists("DocType", name), "new_doctype_already_exists")
        require(
            fr.db.exists("Module Def", source["module"]), "existing_module_required"
        )
        roles.update(row["role"] for row in source["permissions"])
        links.update(
            row["options"] for row in source["fields"] if row["fieldtype"] == "Link"
        )
    require(
        all(fr.db.exists("Role", name) for name in roles), "existing_roles_required"
    )
    require(
        all(fr.db.exists("DocType", name) for name in links - set(audit.TARGETS)),
        "existing_link_doctypes_required",
    )
    fr.db.rollback()  # Close pre-write read queries, not a rollback of a mutation.
    attempted = []
    for name in reversed(audit.TARGETS):
        guard()
        require(not fr.db.exists("DocType", name), "target_appeared_before_reload")
        record_attempt(name)
        attempted.append(name)
        fr.reload_doc(
            "jifeng_erp", "doctype", name.lower().replace(" ", "_"), force=True
        )
    return {"attempted": attempted, "existing_doctypes_reloaded": []}


def runtime(target, expected):
    require(os.geteuid() == target.uid, "native_runtime_uid")
    actual = (
        subprocess.check_output(
            ["git", "-C", str(target.bench / "apps/jifeng_erp"), "rev-parse", "HEAD"]
        )
        .decode("ascii")
        .strip()
    )
    require(actual == expected, "runtime_source_commit")
    for app in ("frappe", "jifeng_erp"):
        specification = importlib.util.find_spec(app)
        expected_init = target.bench / "apps" / app / app / "__init__.py"
        require(
            specification is not None
            and specification.origin is not None
            and os.path.samefile(specification.origin, expected_init),
            "actual_python_app_source",
        )
    for relative, expected_sha in NATIVE_PINS.items():
        path = target.bench / "apps/frappe" / relative
        require(
            stat.S_ISREG(path.lstat().st_mode) and sha(path) == expected_sha,
            "native_implementation_pin",
        )
    if not target.clone:
        require(
            os.readlink(target.bench / "sites/assets") == "../assets"
            and os.readlink(target.bench / "assets/jifeng_erp")
            == "../apps/jifeng_erp/jifeng_erp/public",
            "native_asset_links",
        )


def load_audit(paths, expected_sha):
    require(expected_sha == AUDIT_SHA256, "frozen_audit_pin")
    path = paths.root / "incoming-r1/tool_cost_release_audit.py"
    paths.read(path, expected_sha)
    spec = importlib.util.spec_from_file_location(
        "tool_cost_release_audit_pinned", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.OLD == OLD and module.NEW == NEW, "audit_commit_scope")
    return module


def candidate(paths, audit, reviewed):
    plan = json.loads(
        paths.read(paths.root / "native-release-plan.json", reviewed["plan_sha256"])
    )
    audit.verify_release_plan(plan)
    manifest = json.loads(
        paths.read(paths.root / "source-manifest.json", reviewed["manifest_sha256"])
    )
    source, observed = paths.root / "app", {}
    for path in (source, *source.rglob("*")):
        info = path.lstat()
        require(info.st_uid == paths.target.uid, "candidate_source_owner")
        name = path.relative_to(paths.root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            observed[name] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
        else:
            require(stat.S_ISREG(info.st_mode), "candidate_regular_source")
            observed[name] = {
                "kind": "file",
                "mode": stat.S_IMODE(info.st_mode),
                "content": path.read_bytes(),
            }
    previous = paths.read(paths.root / "previous-hooks.py")
    definition = audit.verify_source_manifest(manifest, observed, previous)
    require(
        definition["source_files"] == plan["source_files"], "plan_manifest_file_count"
    )
    return definition, manifest


def verify_cache(fr, audit, definition, manifest, paths, *, get_form_meta=None):
    for name in audit.TARGETS:
        fr.clear_cache(doctype=name)
        fr.client_cache.delete_value("doctype_form_meta::" + name)
    fr.client_cache.delete_value("app_hooks")
    css = fr.get_hooks("app_include_css", app_name="jifeng_erp")
    js = fr.get_hooks("app_include_js", app_name="jifeng_erp")
    require(
        css + js == manifest["hooks"] and len(css) == 12 and len(js) == 21,
        "actual_app_33_assets",
    )
    for section, additions in audit.NEW_HOOKS.items():
        effective = fr.get_hooks(section, app_name="jifeng_erp")
        for name, expected in additions.items():
            value = effective.get(name)
            if section == "doc_events":
                require(
                    isinstance(value, dict) and set(value) == {"before_validate"},
                    "scoped_share_hook",
                )
                value, expected = value["before_validate"], expected["before_validate"]
            require(value == expected or value == [expected], "actual_deny_hook")
    if get_form_meta is None:
        from frappe.desk.form.meta import get_meta as get_form_meta
    parent = audit.TARGETS[0]
    script_path = paths.root / (audit.SCHEMA_PATHS[parent][:-5] + ".js")
    script = paths.read(script_path).decode("utf-8")
    effective = get_form_meta(parent).get("__js") or ""
    cached = fr.client_cache.get_value("doctype_form_meta::" + parent)
    require(
        script
        and script in effective
        and cached is not None
        and cached.get("__js") == effective
        and get_form_meta(parent).get("__js") == effective,
        "parent_form_script_cached",
    )
    for name in audit.TARGETS:
        meta = get_form_meta(name)
        for declared in definition["schemas"][name]["fields"]:
            actual = meta.get_field(declared["fieldname"])
            require(
                actual is not None
                and all(
                    actual.get(flag) == declared[flag]
                    for flag in (
                        "hidden",
                        "read_only",
                        "print_hide",
                        "no_copy",
                        "unique",
                    )
                    if flag in declared
                ),
                "current_form_field_flags",
            )
    return {
        "effective_hooks": 33,
        "app_include_css": 12,
        "app_include_js": 21,
        "four_deny_hooks_current": True,
        "scoped_share_hook_current": True,
        "parent_form_script_cached": True,
        "gui_tested": False,
        "parent_effective_script_sha256": hashlib.sha256(
            effective.encode()
        ).hexdigest(),
    }


class Runner:
    def __init__(
        self, fr, audit, paths, reviewed, *, clock, backup_factory=native_backup
    ):
        self.fr, self.audit, self.paths = fr, audit, paths
        self.reviewed, self.clock, self.backup_factory = reviewed, clock, backup_factory
        self.identity = {
            "site": paths.target.site,
            "database": paths.target.database,
            "old_commit": OLD,
            "new_commit": NEW,
            **reviewed,
        }
        self._claimed_phase = None

    def claim(self, phase):
        require(
            phase in PHASES and self._claimed_phase is None, "known_unclaimed_phase"
        )
        self.paths.record(
            phase + "-started.json",
            {"phase": phase, "identity": self.identity},
            self.audit,
            control=True,
        )
        self._claimed_phase = phase

    def completed(self, phase):
        receipt = self.paths.proof(phase + "-completed.json")
        require(
            receipt.get("passed") is True
            and receipt.get("identity") == self.identity
            and receipt.get("phase") == phase,
            "successful_previous_phase_required",
        )
        return receipt

    def baseline(self, before_sha):
        completed = self.completed("before")
        require(
            before_sha and completed["proof_sha256"] == before_sha,
            "before_sha_required",
        )
        before = self.paths.proof("before.json", before_sha)
        self.audit.verify_baseline(before)
        check_backup_receipt(
            self.paths,
            self.paths.proof("backup-completed.json"),
            self.identity,
            before_sha,
        )
        return before

    def verify_delta(self, before, collection):
        intent = self.paths.proof("apply-committed.json")
        require(
            intent["identity"] == self.identity and intent["committed"] is True,
            "apply_commit_identity",
        )
        return self.audit.verify_snapshot_delta(
            before,
            collection.snapshot,
            self.definition["schemas"],
            collection.installed,
            intent["start"],
            intent["end"],
            "Administrator",
            self.definition["migration_hash"],
        )

    def run(self, phase, before_sha=None):
        require(phase in PHASES, "known_phase")
        # The marker precedes runtime checks, source parsing and connecting.
        if self._claimed_phase != phase:
            self.claim(phase)
        self._claimed_phase = None
        result = {
            "passed": False,
            "phase": phase,
            "identity": self.identity,
            "full_migrate": False,
            "automatic_retry": False,
        }
        capture = io.StringIO()
        try:
            with redirect_stdout(capture), redirect_stderr(capture):
                runtime(self.paths.target, OLD if phase == "before" else NEW)
                self.definition, self.manifest = candidate(
                    self.paths, self.audit, self.reviewed
                )
                connect(self.fr, self.paths.target)
                if phase == "before":
                    site = self.paths.target.bench / "sites" / self.paths.target.site
                    config = (site / "site_config.json").read_bytes()
                    trees = {
                        folder: file_tree(site / folder / "files")
                        for folder in ("public", "private")
                    }
                    with protected_reads(self.fr, self.audit, self.paths.target):
                        before = collect_snapshot(
                            self.fr, self.audit, self.paths.target
                        )
                        self.audit.verify_baseline(before.snapshot)
                        before_sha = self.paths.record(
                            "before.json", before.snapshot, self.audit
                        )
                        extra_sha = self.paths.record(
                            "before-extra.json", before.extra(), self.audit
                        )
                        parts = self.backup_factory(self.fr, self.paths)
                        backups = validate_backups(
                            parts, self.paths, before, config, trees
                        )
                        require(
                            collect_snapshot(
                                self.fr, self.audit, self.paths.target
                            ).snapshot
                            == before.snapshot,
                            "database_changed_inside_backup",
                        )
                    fresh = fresh_collection(self.fr, self.audit, self.paths.target)
                    require(
                        fresh.snapshot == before.snapshot
                        and fresh.extra() == before.extra(),
                        "fresh_database_changed_during_backup",
                    )
                    require(
                        (site / "site_config.json").read_bytes() == config
                        and all(
                            file_tree(site / folder / "files") == trees[folder]
                            for folder in trees
                        ),
                        "backup_sources_changed",
                    )
                    self.paths.record(
                        "backup-completed.json",
                        {
                            "identity": self.identity,
                            "before_sha256": before_sha,
                            "parts": backups,
                            "physical_objects": 932,
                            "myisam_locked_through_validation": True,
                            "before_extra_sha256": extra_sha,
                        },
                        self.audit,
                    )
                    result.update(
                        proof_sha256=before_sha, backups=backups, total_objects=932
                    )
                else:
                    before = self.baseline(before_sha)
                    if phase != "apply":
                        self.completed("apply")
                    if phase == "cache":
                        self.completed("verify")
                    current = fresh_collection(self.fr, self.audit, self.paths.target)
                    if phase == "apply":
                        require(
                            current.snapshot == before, "baseline_changed_before_apply"
                        )
                        start = self.clock()
                        self.paths.record(
                            "apply-intent.json",
                            {
                                "identity": self.identity,
                                "start": start,
                                "new_doctypes": list(self.audit.TARGETS),
                                "existing_changes": {},
                            },
                            self.audit,
                        )

                        def guard():
                            definition, _manifest = candidate(
                                self.paths, self.audit, self.reviewed
                            )
                            require(
                                definition == self.definition,
                                "candidate_changed_before_reload",
                            )

                        def attempt(name):
                            self.paths.record(
                                "reload-"
                                + name.lower().replace(" ", "-")
                                + "-attempted.json",
                                {"identity": self.identity, "doctype": name},
                                self.audit,
                            )

                        result["apply"] = apply_new_doctypes(
                            self.fr,
                            self.audit,
                            before,
                            self.definition,
                            guard=guard,
                            record_attempt=attempt,
                        )
                        self.fr.db.commit()
                        self.paths.record(
                            "apply-committed.json",
                            {
                                "identity": self.identity,
                                "committed": True,
                                "start": start,
                                "end": self.clock(),
                            },
                            self.audit,
                        )
                        current = fresh_collection(
                            self.fr, self.audit, self.paths.target
                        )
                    result["delta"] = self.verify_delta(before, current)
                    if phase == "cache":
                        result["cache"] = verify_cache(
                            self.fr,
                            self.audit,
                            self.definition,
                            self.manifest,
                            self.paths,
                        )
                        following = fresh_collection(
                            self.fr, self.audit, self.paths.target
                        )
                        require(
                            following.snapshot == current.snapshot
                            and following.extra() == current.extra(),
                            "cache_changed_database",
                        )
                    result.update(
                        total_objects=934,
                        proof_sha256=self.paths.record(
                            "after-" + phase + ".json", current.snapshot, self.audit
                        ),
                    )
                result["passed"] = True
        except Exception as error:
            result.update(
                error_type=type(error).__name__,
                reason=str(error)
                if isinstance(error, (NativeError, self.audit.AuditError))
                else "native_phase_failed",
                outcome_requires_inspection=True,
            )
        finally:
            try:
                self.fr.destroy()
            except Exception:
                result.update(
                    passed=False,
                    reason="native_cleanup_failed",
                    outcome_requires_inspection=True,
                )
        result["captured_output_sha256"] = hashlib.sha256(
            capture.getvalue().encode()
        ).hexdigest()
        self.paths.record(phase + "-result.json", result, self.audit)
        if result["passed"]:
            self.paths.record(phase + "-completed.json", result, self.audit)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES)
    for name in ("native", "audit", "plan", "manifest"):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--before-sha256")
    parser.add_argument("--control", default=str(ROOT / CONTROL_NAME))
    parser.add_argument("--private", default=str(ROOT / PRIVATE_NAME))
    args = parser.parse_args(argv)
    target = Target()
    paths = Paths(target, args.control, args.private)
    reviewed = {
        name + "_sha256": getattr(args, name + "_sha256")
        for name in ("native", "audit", "plan", "manifest")
    }
    runner = Runner(None, None, paths, reviewed, clock=None)
    runner.claim(args.phase)
    paths.read(
        paths.root / "incoming-r1/tool_cost_release_native.py", args.native_sha256
    )
    require(
        Path(__file__).resolve()
        == paths.root / "incoming-r1/tool_cost_release_native.py",
        "execute_pinned_native_file",
    )
    audit = load_audit(paths, args.audit_sha256)
    require(
        all(SHA.fullmatch(value) for value in reviewed.values()), "reviewed_input_pins"
    )
    os.chdir(target.bench / "sites")
    import frappe
    from frappe.utils import now_datetime

    runner.fr, runner.audit, runner.clock = frappe, audit, now_datetime
    result = runner.run(args.phase, args.before_sha256)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    require(result["passed"], "native_phase_failed_inspect_receipts")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
