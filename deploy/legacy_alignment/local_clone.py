"""One-use local test clone, pinned app source, and private restore provenance.

No download, original database write, old service restart, or automatic retry.
Run as the WSL root user to use the existing MariaDB unix-socket authority.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile

import pymysql


BASE = Path("/home/jifengtest/jifeng-dev/bench")
ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
SOURCE = Path("/home/jifengtest/jifeng-dev/report-runtime-qa-20261004-r1/bench/sites/jifeng-report-runtime-qa-20261004-r1.localhost")
SITE = "jifeng-legacy-alignment-20261015-r1.localhost"
DATABASE = "jf_legacy_align_20261015_r1"
SOCKET = "/run/mysqld/mysqld.sock"


def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(repository, commit):
    assert os.geteuid() == 0
    assert len(commit) == 40 and all(char in "0123456789abcdef" for char in commit)
    git = ["git", "-c", "safe.directory=" + str(repository), "-C", str(repository)]
    assert subprocess.check_output([*git, "rev-parse", commit]).decode().strip() == commit
    assert (ROOT / "report-source-probe.private.json").is_file()
    assert not (ROOT / "clone.intent.private.json").exists(), "Inspect any previous intent; never replay this clone"
    assert not (ROOT / "bench").exists()
    configuration = json.loads((SOURCE / "site_config.json").read_text())
    assert Path(configuration["db_socket"]).resolve() == Path(SOCKET) and configuration["db_name"] != DATABASE
    connection = pymysql.connect(user="root", unix_socket=SOCKET, autocommit=True,
                                 cursorclass=pymysql.cursors.DictCursor, read_timeout=60)
    ROOT.chmod(0o700)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (DATABASE,))
            assert not cursor.fetchone(), "The new test database must not already exist"
            cursor.execute("SELECT User FROM mysql.user WHERE User=%s", (DATABASE,))
            assert not cursor.fetchone(), "The new test database user must not already exist"
            for catalog in ("ROUTINES", "TRIGGERS", "EVENTS"):
                column = {"ROUTINES": "ROUTINE_SCHEMA", "TRIGGERS": "TRIGGER_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[catalog]
                cursor.execute("SELECT COUNT(*) AS total FROM information_schema." + catalog + " WHERE " + column + "=%s", (configuration["db_name"],))
                assert cursor.fetchone()["total"] == 0, "No executable SQL objects may be copied"

            intent = {"phase": "clone", "source_config_sha256": digest(SOURCE / "site_config.json"),
                      "source_database_sha256": hashlib.sha256(configuration["db_name"].encode()).hexdigest(),
                      "target_database": DATABASE, "target_site": SITE, "commit": commit,
                      "source_probe_sha256": digest(ROOT / "report-source-probe.private.json"),
                      "socket_only": True, "old_services_untouched": True}
            write_new(ROOT / "clone.intent.private.json", intent)
            backup = ROOT / "source.sql.gz"
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream, gzip.GzipFile(fileobj=stream, mode="wb") as compressed:
                process = subprocess.Popen(["mariadb-dump", "--socket=" + SOCKET, "--user=root",
                    "--single-transaction", "--skip-lock-tables", "--hex-blob", "--default-character-set=utf8mb4",
                    configuration["db_name"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                shutil.copyfileobj(process.stdout, compressed)
                error = process.stderr.read()
                assert process.wait(timeout=60) == 0, "The local backup failed (no credential-bearing stderr printed)"
                assert not error, "Inspect local dump diagnostics privately"
            assert backup.stat().st_size > 1000

            cursor.execute("CREATE DATABASE `" + DATABASE + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            password = secrets.token_hex(32)
            cursor.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s", (DATABASE, password))
            cursor.execute("GRANT ALL PRIVILEGES ON `" + DATABASE + "`.* TO %s@'localhost'", (DATABASE,))
            with gzip.open(backup, "rb") as stream:
                process = subprocess.Popen(["mariadb", "--socket=" + SOCKET, "--user=root", DATABASE],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                shutil.copyfileobj(stream, process.stdin)
                process.stdin.close()
                error = process.stderr.read()
                assert process.wait(timeout=120) == 0, "The restore failed; inspect the existing new database, do not repeat"
                assert not error, "Inspect restore diagnostics privately"
            cursor.execute("USE `" + DATABASE + "`")
            cursor.execute("DELETE FROM `tabSessions`")
            cursor.execute("SELECT COUNT(*) AS total FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
            assert cursor.fetchone()["total"] == json.loads((ROOT / "report-source-probe.private.json").read_text())["size"]["tables"]

        bench, app = ROOT / "bench", ROOT / "app"
        bench.mkdir(mode=0o700)
        app.mkdir(mode=0o755)
        archive = ROOT / "source.tar"
        subprocess.run([*git, "archive", "--format=tar", "--output=" + str(archive), commit], check=True)
        archive.chmod(0o600)
        with tarfile.open(archive) as package:
            package.extractall(app, filter="data")
        apps, sites = bench / "apps", bench / "sites"
        apps.mkdir()
        sites.mkdir(mode=0o700)
        (bench / "env").symlink_to(BASE / "env", target_is_directory=True)
        for name in ("frappe", "erpnext", "hrms"):
            (apps / name).symlink_to(BASE / "apps" / name, target_is_directory=True)
        (apps / "jifeng_erp").symlink_to(app, target_is_directory=True)
        shutil.copyfile(BASE / "sites/apps.txt", sites / "apps.txt")
        assets = sites / "assets"
        assets.mkdir()
        for original in (BASE / "sites/assets").iterdir():
            if original.name != "jifeng_erp":
                (assets / original.name).symlink_to(original, target_is_directory=original.is_dir())
        (assets / "jifeng_erp").symlink_to(app / "jifeng_erp/public", target_is_directory=True)
        site = sites / SITE
        site.mkdir(mode=0o700)
        for name in ("public", "private"):
            shutil.copytree(SOURCE / name, site / name, symlinks=True)
        for name in ("logs", "locks"):
            (site / name).mkdir(mode=0o700)
        subprocess.run(["redis-server", "--port", "0", "--unixsocket", str(ROOT / "redis.sock"),
            "--unixsocketperm", "700", "--daemonize", "yes", "--pidfile", str(ROOT / "redis.pid"),
            "--logfile", str(ROOT / "redis.log"), "--dir", str(ROOT), "--save", "", "--appendonly", "no"], check=True)
        configuration.update(db_name=DATABASE, db_user=DATABASE, db_password=password, developer_mode=0,
            disable_scheduler=1, pause_scheduler=1, mute_emails=1, disable_global_search=1,
            redis_cache="unix://" + str(ROOT / "redis.sock") + "?db=0",
            redis_queue="unix://" + str(ROOT / "redis.sock") + "?db=1",
            redis_socketio="unix://" + str(ROOT / "redis.sock") + "?db=1", host_name="http://127.0.0.1:18868",
            maintenance_mode=0)
        write_new(site / "site_config.json", configuration)
        write_new(sites / "common_site_config.json", {"serve_default_site": True, "developer_mode": 0,
            "redis_cache": configuration["redis_cache"], "redis_queue": configuration["redis_queue"]})
        (sites / "currentsite.txt").write_text(SITE + "\n")
        done = {**intent, "backup_bytes": backup.stat().st_size, "backup_sha256": digest(backup),
                "archive_sha256": digest(archive), "clone_sessions_cleared": True,
                "source_config_unchanged": digest(SOURCE / "site_config.json") == intent["source_config_sha256"],
                "bench": str(bench), "candidate_app": str(app), "private": True}
        assert done["source_config_unchanged"]
        write_new(ROOT / "clone.done.private.json", done)
        print(json.dumps({key: done[key] for key in ("target_site", "commit", "backup_bytes", "socket_only", "source_config_unchanged")}))
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("commit")
    args = parser.parse_args()
    run(args.repository, args.commit)
