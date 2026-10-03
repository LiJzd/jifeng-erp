"""One-use, Python 3.6 host coordinator for the reviewed ERP source release.

No downloads, image builds/pulls, full migration or automatic retry/rollback.
Prepare creates only scoped controls/tools; later phases require exact receipts.
Tests inject every command and never access a real site or Docker daemon.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path("/opt/jifeng-erp-test")
RELEASE = ROOT / "releases/4d71c35-source-r1"
OLD_RELEASE = ROOT / "releases/5aa4b3b-source-r1"
BASE = ROOT / "releases/8858743-r2/deploy/compose.yaml"
OLD_OVERLAY = OLD_RELEASE / "compose.source.yaml"
OVERLAY = RELEASE / "compose.source.r2.yaml"
CONTROL = RELEASE / "incremental-control-r2"
PRIVATE = RELEASE / "private-backup-r2"
SITE = "jifeng-erp-test.localhost"
SITES = Path("/var/lib/docker/volumes/jifeng-erp-test_sites/_data")
CONFIG = SITES / SITE / "site_config.json"
BENCH = "/home/frappe/frappe-bench"
APP_TARGET = BENCH + "/apps/jifeng_erp"
NATIVE = RELEASE / "incoming/incremental_native.py"
AUDIT = RELEASE / "incoming/incremental_audit.py"
OLD = "5aa4b3b9b252c1879c299850557750c666d8cb83"
NEW = "4d71c354d3f3f281d21d3d16b496bb6e5434e655"
BASE_TAG = "jifeng/erp-test:8858743-test-r4"
BASE_ID = "sha256:543ecd7e7e51f5088659718cadb950f542a80bd8a17a5a8da92a3067e9372cff"
RUNTIME = ("backend", "worker", "scheduler", "websocket", "frontend")
DATA = ("db", "redis-cache", "redis-queue")
PUBLIC = ("manifest.json", "source-files.json", "metadata-changes.json")
ARCHIVE_SHA = "515a82bed89fd627d59123daa932a97f26ed6795be604e60fef3eeede692a54e"
ARCHIVE_BYTES = 2821789
PHASES = ("prepare", "quiesce", "metadata", "verify", "start")
PROTECTED = (
    ROOT / ".env",
    BASE,
    OLD_OVERLAY,
    OLD_RELEASE / "manifest.json",
    OLD_RELEASE / "source-files.json",
    ROOT / "releases/8858743-r2/deploy/renew-certificate.sh",
    Path("/etc/systemd/system/jifeng-erp-test-cert-renew.service"),
    Path("/etc/systemd/system/jifeng-erp-test-cert-renew.timer"),
    SITES / "common_site_config.json",
    SITES / "apps.txt",
)
PINS = {}
HELPER_PINS = {}
PHASE_DIR = None


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "not_regular:" + path.name)
    return path.read_bytes()


def write_new(path, data, mode=0o600):
    require(
        not path.exists() and not path.is_symlink(), "one_use_path_exists:" + path.name
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)


def record(path, value):
    write_new(
        path,
        (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    )


def read_json(path):
    return json.loads(read_regular(path))


def compose(overlay):
    return [
        "docker",
        "compose",
        "--project-name",
        "jifeng-erp-test",
        "--project-directory",
        str(ROOT),
        "--env-file",
        str(ROOT / ".env"),
        "-f",
        str(BASE),
        "-f",
        str(overlay),
        "--profile",
        "tools",
    ]


def command(label, argv, payload=None, timeout=60, redact_output=False):
    record(
        PHASE_DIR / (label + "-command.json"),
        {"argv": argv, "stdin_supplied": payload is not None},
    )
    try:
        result = subprocess.run(
            argv,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        save_output(label, error.stdout or b"", error.stderr or b"", redact_output)
        raise RuntimeError("command_timeout_unknown_result:" + label)
    save_output(label, result.stdout, result.stderr, redact_output)
    require(result.returncode == 0, "command_failed_unknown_result:" + label)
    return result.stdout


def protected():
    result = {}
    for path in PROTECTED:
        read_regular(path)
        info = path.stat()
        result[str(path)] = {
            "sha256": sha(path),
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
        }
    # Observe existing daemon settings only. No invented RSS/swap threshold and
    # no service/config mutation; API deadlines are the management health gate.
    daemon_paths = [Path("/etc/docker/daemon.json")]
    daemon_paths.extend(Path("/etc/systemd/system/docker.service.d").glob("*.conf"))
    for path in daemon_paths:
        if path.exists() or path.is_symlink():
            read_regular(path)
            info = path.stat()
            result[str(path)] = {
                "sha256": sha(path),
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
    return result


def config_identity():
    read_regular(CONFIG)
    info = CONFIG.stat()
    return {
        "sha256": sha(CONFIG),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def source_mounts(rows, source, running=True):
    for name in RUNTIME:
        row = rows[name]
        mount = [item for item in row["mounts"] if item["Destination"] == APP_TARGET]
        require(
            len(mount) == 1
            and mount[0]["Type"] == "bind"
            and mount[0]["Source"] == str(source)
            and mount[0]["RW"] is False,
            "source_mount_not_exact_ro:" + name,
        )
        require(
            row["image"] == BASE_ID
            and not row["oom_killed"]
            and row["restart_count"] == 0,
            "runtime_image_restart_or_oom:" + name,
        )
        if running:
            require(row["status"] == "running", "runtime_not_running:" + name)


def data_unchanged(rows):
    prior = read_json(CONTROL / "containers-before.json")
    require(all(rows[name] == prior[name] for name in DATA), "data_container_changed")
    require(
        rows["db"]["status"] == "running" and rows["db"]["health"] == "healthy",
        "database_not_healthy",
    )


def make_overlay():
    def bind(source, target, readonly):
        return {
            "type": "bind",
            "source": str(source),
            "target": target,
            "read_only": readonly,
            "bind": {"create_host_path": False},
        }

    services = {
        name: {"volumes": [bind(RELEASE / "app", APP_TARGET, True)]} for name in RUNTIME
    }
    tool = {
        "profiles": ["tools"],
        "image": BASE_TAG,
        "pull_policy": "never",
        "restart": "no",
        "user": "1000:1000",
        "working_dir": BENCH,
        "entrypoint": ["env/bin/python"],
        "command": ["-"],
        "mem_limit": "768m",
        "memswap_limit": "1024m",
        "networks": ["erp"],
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "GIT_OPTIONAL_LOCKS": "0"},
        "volumes": [
            {
                "type": "volume",
                "source": "sites",
                "target": BENCH + "/sites",
                "read_only": False,
            },
            {
                "type": "volume",
                "source": "logs",
                "target": BENCH + "/logs",
                "read_only": False,
            },
            bind(RELEASE, "/opt/source-release", True),
            bind(PRIVATE, "/opt/source-release-backup", False),
        ],
    }
    for name, source in (
        ("erp-release-before", OLD_RELEASE / "app"),
        ("erp-release-candidate", RELEASE / "app"),
    ):
        services[name] = copy.deepcopy(tool)
        services[name]["volumes"].append(bind(source, APP_TARGET, True))
        if name == "erp-release-before":
            services[name]["volumes"].append(
                bind(
                    OLD_RELEASE / "manifest.json",
                    "/opt/previous-source-manifest.json",
                    True,
                )
            )
    write_new(
        OVERLAY, (json.dumps({"services": services}, indent=2) + "\n").encode(), 0o644
    )
    old = json.loads(
        command(
            "old-compose-config",
            compose(OLD_OVERLAY) + ["config", "--format", "json"],
            redact_output=True,
        )
    )
    new = json.loads(
        command(
            "new-compose-config",
            compose(OVERLAY) + ["config", "--format", "json"],
            redact_output=True,
        )
    )
    require(
        {key: value for key, value in old.items() if key != "services"}
        == {key: value for key, value in new.items() if key != "services"},
        "compose_top_level_changed",
    )
    require(
        set(new["services"])
        == (set(old["services"]) - {"erp-before", "erp-migrate"})
        | {"erp-release-before", "erp-release-candidate"},
        "compose_service_set_changed",
    )
    for name, source in (
        ("erp-release-before", OLD_RELEASE / "app"),
        ("erp-release-candidate", RELEASE / "app"),
    ):
        actual = new["services"][name]
        require(
            actual["image"] == BASE_TAG
            and actual["pull_policy"] == "never"
            and actual["restart"] == "no"
            and actual["user"] == "1000:1000"
            and int(actual["mem_limit"]) == 768 * 1024 * 1024
            and int(actual["memswap_limit"]) == 1024 * 1024 * 1024
            and not actual.get("ports")
            and not actual.get("depends_on")
            and not actual.get("secrets"),
            "release_tool_scope_changed:" + name,
        )
        mounts = {item["target"]: item for item in actual["volumes"]}
        require(
            len(mounts)
            == len(actual["volumes"])
            == (6 if name == "erp-release-before" else 5)
            and mounts[APP_TARGET]["source"] == str(source)
            and mounts[APP_TARGET]["read_only"]
            and mounts["/opt/source-release"]["read_only"]
            and mounts["/opt/source-release-backup"]["source"] == str(PRIVATE)
            and not mounts["/opt/source-release-backup"].get("read_only", False),
            "release_tool_mount_scope_changed",
        )
    for name in set(old["services"]) - {"erp-before", "erp-migrate"}:
        prior, actual = (
            copy.deepcopy(old["services"][name]),
            copy.deepcopy(new["services"][name]),
        )
        if name in RUNTIME:
            for value, target in (
                (prior, OLD_RELEASE / "app"),
                (actual, RELEASE / "app"),
            ):
                mount = [
                    item for item in value["volumes"] if item["target"] == APP_TARGET
                ]
                require(
                    len(mount) == 1
                    and mount[0]["source"] == str(target)
                    and mount[0]["read_only"],
                    "compose_source_mount",
                )
                value["volumes"] = [
                    item for item in value["volumes"] if item["target"] != APP_TARGET
                ]
        for value in (prior, actual):
            if "volumes" in value:
                value["volumes"].sort(key=lambda item: item["target"])
        require(prior == actual, "existing_service_config_changed:" + name)


def replace_config(data):
    metadata = read_json(CONTROL / "original-config.json")
    temporary = CONFIG.with_name("site_config.incremental-source.tmp")
    write_new(temporary, data, metadata["mode"])
    os.chown(temporary, metadata["uid"], metadata["gid"])
    os.replace(temporary, CONFIG)
    require(
        sha(CONFIG) == hashlib.sha256(data).hexdigest(), "site_config_replace_not_exact"
    )


def maintenance():
    original = read_regular(PRIVATE / "original-site-config.json")
    info = read_json(CONTROL / "original-config.json")
    require(
        hashlib.sha256(original).hexdigest() == info["sha256"],
        "original_config_backup_changed",
    )
    values = json.loads(original)
    require(values.get("db_name") == "jifeng_erp_test", "wrong_config_database")
    values.update(maintenance_mode=1, disable_scheduler=1, mute_emails=1)
    data = (json.dumps(values, indent=2) + "\n").encode()
    expected = dict(info, sha256=hashlib.sha256(data).hexdigest())
    require(config_identity() in (info, expected), "site_config_drift_do_not_overwrite")
    intent = CONTROL / "maintenance-config.json"
    if not intent.exists():
        record(intent, expected)
    else:
        require(read_json(intent) == expected, "maintenance_config_intent_changed")
    replace_config(data)


def restore_config():
    original = read_regular(PRIVATE / "original-site-config.json")
    info = read_json(CONTROL / "original-config.json")
    require(
        hashlib.sha256(original).hexdigest() == info["sha256"],
        "original_config_backup_changed",
    )
    require(
        config_identity() == read_json(CONTROL / "maintenance-config.json"),
        "config_drift_before_restore",
    )
    replace_config(original)
    actual = CONFIG.stat()
    require(
        (actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode))
        == (info["uid"], info["gid"], info["mode"]),
        "restored_config_owner_mode",
    )


def save_output(label, stdout, stderr, redacted):
    if redacted:
        record(
            PHASE_DIR / (label + "-output-hashes.json"),
            {
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stdout_bytes": len(stdout),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "stderr_bytes": len(stderr),
            },
        )
    else:
        write_new(PHASE_DIR / (label + "-stdout.log"), stdout)
        write_new(PHASE_DIR / (label + "-stderr.log"), stderr)


def manager_gate():
    value = json.loads(
        command(
            "daemon-version",
            ["docker", "version", "--format", "{{json .Server.Version}}"],
            timeout=30,
        )
    )
    require(isinstance(value, str) and bool(value), "daemon_server_version_required")
    return value


def containers(label):
    # Never request Config.Env or log resolved credentials. Projection is made
    # by Docker itself, before stdout is retained in the private control folder.
    template = '{"Id":{{json .Id}},"Image":{{json .Image}},"State":{{json .State}},"RestartCount":{{json .RestartCount}},"Mounts":{{json .Mounts}},"Service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
    output = command(
        label,
        ["docker", "inspect", "--format", template]
        + ["jifeng-erp-test-" + name + "-1" for name in RUNTIME + DATA],
        timeout=30,
    )
    result = {}
    for line in output.decode().splitlines():
        row = json.loads(line)
        name, state = row["Service"], row["State"]
        require(name in RUNTIME + DATA and name not in result, "unexpected_container")
        result[name] = {
            "id": row["Id"],
            "image": row["Image"],
            "started_at": state["StartedAt"],
            "status": state["Status"],
            "restart_count": row["RestartCount"],
            "oom_killed": state["OOMKilled"],
            "mounts": sorted(row["Mounts"], key=lambda item: item["Destination"]),
            "health": state.get("Health", {}).get("Status"),
        }
    require(set(result) == set(RUNTIME + DATA), "container_set_changed")
    return result


def candidate():
    require(
        set(PINS) == set(PUBLIC)
        and all(sha(RELEASE / name) == digest for name, digest in PINS.items()),
        "public_package_pin_changed",
    )
    require(
        {"host": sha(Path(__file__)), "native": sha(NATIVE), "audit": sha(AUDIT)}
        == HELPER_PINS,
        "helper_source_changed",
    )
    for path in (NATIVE, AUDIT):
        read_regular(path)
        require(stat.S_IMODE(path.stat().st_mode) == 0o600, "incoming_helper_mode")
    archive = RELEASE / "incoming/app.tar.gz"
    read_regular(archive)
    require(
        sha(archive) == ARCHIVE_SHA and archive.stat().st_size == ARCHIVE_BYTES,
        "incoming_archive_pin_changed",
    )
    manifest = read_json(RELEASE / "manifest.json")
    require(
        manifest["commit"] == NEW
        and manifest["previous_commit"] == OLD
        and manifest["base_image_tag"] == BASE_TAG
        and manifest["base_image_id"] == BASE_ID,
        "candidate_manifest_identity",
    )
    require(
        manifest["release_kind"] == "read_only_source_bind_mount"
        and manifest["runtime_services"] == list(RUNTIME)
        and manifest["pyproject_unchanged_from_base"] is True
        and manifest["image_build"] is False
        and manifest["full_migrate"] is False,
        "candidate_release_contract",
    )
    require(
        manifest["source_files"] == 505
        and manifest["archive_sha256"] == ARCHIVE_SHA
        and manifest["archive_bytes"] == ARCHIVE_BYTES
        and manifest["ddl_required"] is True
        and manifest["new_empty_doctypes"]
        == ["Jifeng Customer Profile", "Jifeng Order Dictionary"]
        and set(manifest["metadata_only_update"])
        == {"dictionary_type.label", "dictionary_type.options", "value.description"},
        "candidate_incremental_scope",
    )
    changes = read_json(RELEASE / "metadata-changes.json")
    require(
        set(changes) == {"doctype", "old_commit", "new_commit", "changes"}
        and changes["doctype"] == "Jifeng Customer Type"
        and changes["old_commit"] == OLD
        and changes["new_commit"] == NEW
        and set(changes["changes"]) == {"dictionary_type", "value"}
        and set(changes["changes"]["dictionary_type"]) == {"label", "options"}
        and set(changes["changes"]["value"]) == {"description"},
        "candidate_metadata_patch_scope",
    )
    for fields in changes["changes"].values():
        require(
            all(
                set(change) == {"old", "new"}
                and all(isinstance(value, str) for value in change.values())
                for change in fields.values()
            ),
            "candidate_metadata_patch_values",
        )
    records = read_json(RELEASE / "source-files.json")
    require(
        len(records) == 505 and len({item["path"] for item in records}) == 505,
        "source_manifest_count",
    )
    expected = set()
    source = RELEASE / "app"
    info = source.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        == (1000, 1000, 0o755),
        "source_root_owner_mode",
    )
    for item in records:
        require(set(item) == {"path", "bytes", "sha256"}, "source_manifest_keys")
        path = PurePosixPath(item["path"])
        require(
            path.as_posix() == item["path"]
            and path.parts[0] == "app"
            and not path.is_absolute()
            and "\\" not in item["path"]
            and not any(
                part
                in {
                    "..",
                    ".",
                    "output",
                    "sites",
                    "logs",
                    "__pycache__",
                    ".env",
                    "node_modules",
                }
                for part in path.parts
            ),
            "source_manifest_path",
        )
        target = RELEASE.joinpath(*path.parts)
        content, info = read_regular(target), target.stat()
        require(
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            == (1000, 1000, 0o644),
            "source_file_owner_mode",
        )
        require(
            len(content) == item["bytes"]
            and hashlib.sha256(content).hexdigest() == item["sha256"],
            "source_sha_changed",
        )
        expected.add(target.relative_to(source).as_posix())
    actual = set()
    for path in source.rglob("*"):
        info = path.lstat()
        require((info.st_uid, info.st_gid) == (1000, 1000), "source_owner_changed")
        if stat.S_ISREG(info.st_mode):
            actual.add(path.relative_to(source).as_posix())
        else:
            require(
                stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o755,
                "source_directory_mode_or_symlink",
            )
    require(actual == expected, "source_file_set_changed")
    hooks = manifest["verified_direct_hook_paths"]
    require(len(hooks) == 25 and len(set(hooks)) == 25, "direct_hook_count")
    for url in hooks:
        require(
            re.fullmatch(r"/assets/jifeng_erp/[^?]+\?v=[0-9a-f]{16}", url),
            "direct_hook_url",
        )
        relative, version = url[len("/assets/jifeng_erp/") :].split("?v=")
        require(
            sha(source / "jifeng_erp/public" / relative)[:16] == version,
            "direct_hook_fingerprint",
        )
    return manifest


def old_source():
    """Verify the mounted baseline code before using it for the backup."""
    manifest = read_json(OLD_RELEASE / "manifest.json")
    require(manifest["commit"] == OLD, "baseline_manifest_commit")
    records = read_json(OLD_RELEASE / "source-files.json")
    require(len(records) == manifest["source_files"], "baseline_manifest_count")
    expected = set()
    for item in records:
        relative = PurePosixPath(item["path"])
        require(
            relative.parts[0] == "app"
            and not relative.is_absolute()
            and relative.as_posix() == item["path"]
            and ".." not in relative.parts,
            "baseline_source_scope",
        )
        path = OLD_RELEASE.joinpath(*relative.parts)
        content = read_regular(path)
        require(
            len(content) == item["bytes"]
            and hashlib.sha256(content).hexdigest() == item["sha256"],
            "baseline_source_content_changed",
        )
        expected.add(path.relative_to(OLD_RELEASE / "app").as_posix())
    actual = set()
    for path in [OLD_RELEASE / "app"] + list((OLD_RELEASE / "app").rglob("*")):
        info = path.lstat()
        require((info.st_uid, info.st_gid) == (1000, 1000), "baseline_source_owner")
        if stat.S_ISREG(info.st_mode):
            require(stat.S_IMODE(info.st_mode) == 0o644, "baseline_source_mode")
            actual.add(path.relative_to(OLD_RELEASE / "app").as_posix())
        else:
            require(
                stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o755,
                "baseline_directory_or_symlink",
            )
    require(
        len(expected) == len(records) and actual == expected, "baseline_source_file_set"
    )


def success(phase_name):
    value = read_json(CONTROL / phase_name / "completed.json")
    require(
        value.get("passed") is True
        and value.get("phase") == phase_name
        and value.get("commit") == NEW,
        "prior_phase_not_passed",
    )
    return value


def private_directory(path):
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        == (1000, 1000, 0o700),
        "private_directory_identity",
    )


def incoming_permissions():
    # Native source is fed by root stdin. Only audit.py must be readable by
    # the UID1000 tool. The whole release is a read-only bind in that tool.
    incoming = RELEASE / "incoming"
    info = incoming.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
        "incoming_initial_identity",
    )
    for path in (NATIVE, AUDIT):
        read_regular(path)
        info = path.stat()
        require(
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o600),
            "incoming_initial_helper_identity",
        )
    os.chown(incoming, 1000, 1000)
    os.chown(AUDIT, 1000, 1000)
    private_directory(incoming)


def incoming_ready():
    private_directory(RELEASE / "incoming")
    for path, owner in ((NATIVE, 0), (AUDIT, 1000)):
        read_regular(path)
        info = path.stat()
        require(
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            == (owner, owner, 0o600),
            "incoming_ready_helper_identity",
        )


def proof_sha(name, digest):
    path = PRIVATE / "audit" / name
    read_regular(path)
    info = path.stat()
    require(
        (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1000, 1000, 0o600)
        and sha(path) == digest,
        "native_proof_identity_or_sha",
    )


def backup_proof(parts):
    require(
        isinstance(parts, list)
        and len(parts) == 4
        and {part["part"] for part in parts}
        == {"database", "configuration", "public-files", "private-files"},
        "backup_four_parts",
    )
    directory = PRIVATE / "native-backup"
    private_directory(directory)
    expected = set()
    for part in parts:
        require(
            set(part) == {"part", "name", "bytes", "sha256"}
            and PurePosixPath(part["name"]).name == part["name"]
            and type(part["bytes"]) is int
            and part["bytes"] > 0,
            "backup_part_scope",
        )
        path = directory / part["name"]
        read_regular(path)
        info = path.stat()
        require(
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            == (1000, 1000, 0o600)
            and info.st_size == part["bytes"]
            and sha(path) == part["sha256"],
            "backup_part_identity_or_sha",
        )
        expected.add(path)
    require(
        len(expected) == 4 and set(directory.iterdir()) == expected, "backup_file_set"
    )
    completed = read_json(PRIVATE / "audit/backup-completed.json")
    require(
        completed
        == {
            "parts": parts,
            "physical_tables": 927,
            "three_myisam_locked_through_dump": True,
        },
        "backup_locked_full_snapshot_proof",
    )
    return parts


def native(mode, old=False):
    argv = compose(OVERLAY) + [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "env/bin/python",
        "erp-release-before" if old else "erp-release-candidate",
        "-",
        mode,
        "--audit-sha",
        HELPER_PINS["audit"],
    ]
    if mode not in {"preflight", "before"}:
        baseline = success("quiesce")["before_sha256"]
        proof_sha("before.json", baseline)
        argv.extend(["--before-sha", baseline])
    result = json.loads(
        command("native-" + mode, argv, read_regular(NATIVE), timeout=600)
    )
    require(result.get("passed") is True, "native_phase_not_passed")
    if mode == "preflight":
        require(
            result
            == {
                "passed": True,
                "source_files": 505,
                "hook_count": 25,
                "backup_implementation_verified": True,
                "sql_connected": False,
            },
            "native_preflight_proof",
        )
    else:
        require(
            result.get("mode") == mode
            and result.get("protected_table_count") == 927
            and result.get("total_table_count") == (927 if mode == "before" else 929),
            "native_full_inventory_proof",
        )
        proof_sha(
            "before.json" if mode == "before" else "after-" + mode + ".json",
            result["proof_sha256"],
        )
        if mode == "before":
            backup_proof(result["backups"])
    return result


def stopped_gate(label):
    require(
        config_identity() == read_json(CONTROL / "maintenance-config.json"),
        "site_config_drift_in_maintenance",
    )
    private_directory(PRIVATE)
    private_directory(PRIVATE / "audit")
    rows = containers(label)
    require(
        all(rows[name]["status"] == "exited" for name in RUNTIME),
        "applications_not_stopped",
    )
    source_mounts(rows, OLD_RELEASE / "app", running=False)
    data_unchanged(rows)
    return rows


def phase(name):
    state = {
        "passed": False,
        "phase": name,
        "commit": NEW,
        "full_migrate": False,
        "automatic_retry": False,
        "automatic_rollback": False,
    }
    record(PHASE_DIR / "started.json", state)
    try:
        manifest = candidate()
        old_source()
        state["daemon_version"] = manager_gate()
        if name == "prepare":
            require(
                not OVERLAY.exists()
                and not PRIVATE.exists()
                and not PRIVATE.is_symlink(),
                "candidate_already_prepared",
            )
            PRIVATE.mkdir(mode=0o700)
            os.chown(PRIVATE, 1000, 1000)
            (PRIVATE / "audit").mkdir(mode=0o700)
            os.chown(PRIVATE / "audit", 1000, 1000)
            record(CONTROL / "protected-before.json", protected())
            record(CONTROL / "site-config-before.json", config_identity())
            rows = containers("containers-before")
            source_mounts(rows, OLD_RELEASE / "app")
            require(
                all(
                    rows[name]["status"] == "running"
                    and not rows[name]["oom_killed"]
                    and rows[name]["restart_count"] == 0
                    for name in DATA
                )
                and rows["db"]["health"] == "healthy",
                "data_services_unhealthy",
            )
            record(CONTROL / "containers-before.json", rows)
            actual = (
                command(
                    "old-backend-git",
                    [
                        "docker",
                        "exec",
                        "jifeng-erp-test-backend-1",
                        "git",
                        "-C",
                        APP_TARGET,
                        "rev-parse",
                        "HEAD",
                    ],
                    timeout=30,
                )
                .decode()
                .strip()
            )
            require(actual == OLD, "live_backend_not_5aa")
            incoming_permissions()
            make_overlay()
            state["preflight"] = native("preflight")
            record(CONTROL / "public-pins.json", PINS)
            record(CONTROL / "helper-pins.json", HELPER_PINS)
            record(CONTROL / "overlay-sha.json", {"sha256": sha(OVERLAY)})
            state["source_files"] = manifest["source_files"]
        else:
            success("prepare")
            incoming_ready()
            require(
                protected() == read_json(CONTROL / "protected-before.json"),
                "protected_project_file_changed",
            )
            require(
                PINS == read_json(CONTROL / "public-pins.json")
                and HELPER_PINS == read_json(CONTROL / "helper-pins.json")
                and {"sha256": sha(OVERLAY)} == read_json(CONTROL / "overlay-sha.json"),
                "prepare_pins_changed",
            )
            if name == "quiesce":
                require(
                    config_identity() == read_json(CONTROL / "site-config-before.json"),
                    "site_config_changed_since_prepare",
                )
                source_mounts(containers("before-quiesce-runtime"), OLD_RELEASE / "app")
                data_unchanged(containers("before-quiesce-data"))
                original, info = read_regular(CONFIG), CONFIG.stat()
                write_new(PRIVATE / "original-site-config.json", original)
                record(
                    CONTROL / "original-config.json",
                    {
                        "sha256": hashlib.sha256(original).hexdigest(),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                        "mode": stat.S_IMODE(info.st_mode),
                    },
                )
                maintenance()
                command(
                    "stop-five",
                    compose(OLD_OVERLAY) + ["stop", "--timeout", "40"] + list(RUNTIME),
                    timeout=180,
                )
                stopped_gate("containers-stopped")
                before = native("before", old=True)
                stopped_gate("containers-after-backup")
                record(CONTROL / "backup-inventory.json", before["backups"])
                state["before_sha256"] = before["proof_sha256"]
            elif name in {"metadata", "verify"}:
                success("quiesce" if name == "metadata" else "metadata")
                backup_proof(read_json(CONTROL / "backup-inventory.json"))
                stopped_gate(name + "-preconditions")
                state["native"] = native("apply" if name == "metadata" else "verify")
                stopped_gate(name + "-after-native")
            elif name == "start":
                verified = success("verify")
                proof_sha("after-verify.json", verified["native"]["proof_sha256"])
                backup_proof(read_json(CONTROL / "backup-inventory.json"))
                stopped_gate("start-preconditions")
                # cache verifies a NEW protected snapshot before touching cache,
                # then checks a second fresh snapshot. Do not rerun one-use verify.
                state["cache"] = native("cache")
                stopped_gate("start-after-cache")
                restore_config()
                command(
                    "start-five",
                    compose(OVERLAY)
                    + ["up", "-d", "--no-deps", "--no-build", "--pull", "never"]
                    + list(RUNTIME),
                    timeout=240,
                )
                rows = containers("containers-new-runtime")
                source_mounts(rows, RELEASE / "app")
                data_unchanged(rows)
                commit = (
                    command(
                        "new-backend-git",
                        [
                            "docker",
                            "exec",
                            "jifeng-erp-test-backend-1",
                            "git",
                            "-C",
                            APP_TARGET,
                            "rev-parse",
                            "HEAD",
                        ],
                        timeout=30,
                    )
                    .decode()
                    .strip()
                )
                require(commit == NEW, "new_backend_commit_not_exact")
                state.update(
                    runtime_cut_over=True,
                    external_http_and_gui_acceptance_required=True,
                )
        state["passed"] = True
        record(PHASE_DIR / "completed.json", state)
    except Exception as error:
        state.update(
            error_type=type(error).__name__,
            reason=str(error)
            if isinstance(error, RuntimeError)
            else "host_phase_failed",
            manual_inspection_required=True,
            automatic_resume=False,
            outcome_unknown=True,
        )
        if name == "start":
            reentry = {
                "attempted": True,
                "passed": False,
                "runtime_or_database_retried": False,
            }
            try:
                maintenance()
                reentry["passed"] = True
            except Exception as config_error:
                reentry["error_type"] = type(config_error).__name__
            record(PHASE_DIR / "maintenance-reentry.json", reentry)
        try:
            state["configuration_maintenance_confirmed"] = (
                read_json(CONFIG).get("maintenance_mode") == 1
            )
        except Exception:
            state["configuration_maintenance_confirmed"] = False
        state["failed_phase_runtime_stop_not_inferred"] = True
        record(PHASE_DIR / "failed.json", state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state["passed"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--reviewed-host-sha256", required=True)
    parser.add_argument("--reviewed-native-sha256", required=True)
    parser.add_argument("--reviewed-audit-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--source-files-sha256", required=True)
    parser.add_argument("--metadata-changes-sha256", required=True)
    args = parser.parse_args()
    global PINS, HELPER_PINS, PHASE_DIR
    HELPER_PINS = {
        "host": args.reviewed_host_sha256,
        "native": args.reviewed_native_sha256,
        "audit": args.reviewed_audit_sha256,
    }
    PINS = dict(
        zip(
            PUBLIC,
            (
                args.manifest_sha256,
                args.source_files_sha256,
                args.metadata_changes_sha256,
            ),
        )
    )
    require(
        all(
            re.fullmatch(r"[0-9a-f]{64}", value)
            for value in list(PINS.values()) + list(HELPER_PINS.values())
        ),
        "reviewed_sha256_required",
    )
    require(
        os.geteuid() == 0 and RELEASE.is_dir() and not RELEASE.is_symlink(),
        "host_root_and_release_required",
    )
    require(
        {"host": sha(Path(__file__)), "native": sha(NATIVE), "audit": sha(AUDIT)}
        == HELPER_PINS,
        "reviewed_helpers_required",
    )
    os.environ.update(GIT_OPTIONAL_LOCKS="0", PYTHONDONTWRITEBYTECODE="1")
    if args.phase == "prepare":
        require(
            not CONTROL.exists() and not CONTROL.is_symlink(), "prepare_already_started"
        )
        CONTROL.mkdir(mode=0o700)
    info = CONTROL.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
        "control_directory_identity",
    )
    PHASE_DIR = CONTROL / args.phase
    require(
        not PHASE_DIR.exists() and not PHASE_DIR.is_symlink(),
        "phase_already_started_inspect_do_not_retry",
    )
    PHASE_DIR.mkdir(mode=0o700)
    return phase(args.phase)


if __name__ == "__main__":
    sys.exit(main())
