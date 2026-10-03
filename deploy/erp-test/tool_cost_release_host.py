"""Fixed, one-use host phases for the 2ca4358 -> 5b49bac QA release.

Python 3.6, standard library only. Every command is injectable for model tests.
The caller uploads the already reviewed package using a verified direct route.
This program never downloads, builds, pulls, migrates the whole site, seeds,
restores SQL, retries a used phase or automatically rolls back a failed start.
"""

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time


ROOT = Path("/opt/jifeng-erp-test")
RELEASE = ROOT / "releases/5b49bac-tool-cost-r1"
OLD_RELEASE = ROOT / "releases/2ca4358-salary-r1"
BASE = ROOT / "releases/8858743-r2/deploy/compose.yaml"
OLD_OVERLAY = OLD_RELEASE / "compose.salary.r1.yaml"
OVERLAY = RELEASE / "compose.tool-cost.r1.yaml"
HOST_CONTROL = RELEASE / "tool-cost-host-control-r1"
CONTROL = RELEASE / "tool-cost-control-r1"
PRIVATE = RELEASE / "tool-cost-private-r1"
AUDIT_DIRECTORY = PRIVATE / "audit"
INCOMING = RELEASE / "incoming-r1"
HOST = INCOMING / "tool_cost_release_host.py"
NATIVE = INCOMING / "tool_cost_release_native.py"
AUDIT = INCOMING / "tool_cost_release_audit.py"
ARCHIVE = INCOMING / "app.tar.gz"
SITE = "jifeng-erp-test.localhost"
DATABASE = "jifeng_erp_test"
SITES = Path("/var/lib/docker/volumes/jifeng-erp-test_sites/_data")
CONFIG = SITES / SITE / "site_config.json"
BENCH = "/home/frappe/frappe-bench"
APP_TARGET = BENCH + "/apps/jifeng_erp"
CONTAINER_ROOT = "/opt/source-release"
OLD = "2ca4358398cf750da31584fc31d3afaef48b30b6"
NEW = "5b49bacde6fcc088e08b118103fee496524ec99f"
BASE_TAG = "jifeng/erp-test:8858743-test-r4"
BASE_ID = "sha256:543ecd7e7e51f5088659718cadb950f542a80bd8a17a5a8da92a3067e9372cff"
NATIVE_SHA = "5ec0a5e997a90f2d2fb52a1d9770f194ed561d7f7f13971a29c5efe9b7aeb78d"
AUDIT_SHA = "518b8cf4918e46ab5a38c80f2c5f02b73f1ec5a332a36ae58a651f169c36ead0"
MANIFEST_SHA = "dbb13ffe709338f00ca9bc42d456ce259fbb39ff6bbb2ab9d90cdad587b670e0"
PLAN_SHA = "39bd0d8019d13efdcdf1efd4e33703df40dba1a0b0746b70d6193e2bf7a3fa22"
PREVIOUS_HOOKS_SHA = "a133f27cb677ee6fdeb528de573574c9308a549f6687bd49999e510d5384949a"
ARCHIVE_SHA = "b0dcbadcc80744437a24b2192274d82a1a1225398e343b1210722d9b53ad7c2b"
ARCHIVE_BYTES = 3537261
SOURCE_FILES = 572
RUNTIME = ("backend", "worker", "scheduler", "websocket", "frontend")
DATA = ("db", "redis-cache", "redis-queue")
BASE_TOOLS = ("acme-web", "certbot", "initialize", "seed")
OLD_TOOLS = ("erp-release-before", "erp-release-candidate")
TOOLS = ("tool-cost-before-r1", "tool-cost-candidate-r1")
PHASES = (
    "prepare",
    "quiesce",
    "before",
    "apply",
    "verify",
    "cache",
    "restore",
    "start",
)
FLAGS = ("maintenance_mode", "pause_scheduler", "disable_scheduler", "mute_emails")
SHA = re.compile(r"[0-9a-f]{64}\Z")
PROTECTED = (
    ROOT / ".env",
    BASE,
    OLD_RELEASE,
    ROOT / "releases/8858743-r2/deploy/renew-certificate.sh",
    Path("/etc/systemd/system/jifeng-erp-test-cert-renew.service"),
    Path("/etc/systemd/system/jifeng-erp-test-cert-renew.timer"),
    ROOT / "acme/certificates",
    SITES / "common_site_config.json",
    SITES / "apps.txt",
)


class HostError(RuntimeError):
    """A public rule name; never native output, configuration or credentials."""


def require(condition, rule):
    if not condition:
        raise HostError(rule)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def fsync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_path(path, directory=False, owner=None, mode=None):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "absolute_scoped_path")
    for item in (path,) + tuple(path.parents):
        require(not stat.S_ISLNK(item.lstat().st_mode), "path_symlink")
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
        "regular_path_required",
    )
    require(owner is None or (info.st_uid, info.st_gid) == (owner, owner), "path_owner")
    require(mode is None or stat.S_IMODE(info.st_mode) == mode, "path_mode")
    return info


def read_regular(path, owner=None, mode=None, expected_sha=None):
    before = check_path(path, owner=owner, mode=mode)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
            == (
                before.st_dev,
                before.st_ino,
                before.st_uid,
                before.st_gid,
                before.st_mode,
            ),
            "read_path_identity_changed",
        )
        data = stream.read()
    require(expected_sha is None or digest(data) == expected_sha, "file_pin_changed")
    return data


def sha(path):
    info = check_path(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    result = hashlib.sha256()
    with os.fdopen(fd, "rb") as stream:
        actual = os.fstat(stream.fileno())
        require(
            (actual.st_dev, actual.st_ino, actual.st_mode)
            == (info.st_dev, info.st_ino, info.st_mode),
            "hash_path_identity_changed",
        )
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_new(path, data, mode=0o600, owner=0):
    path = Path(path)
    check_path(path.parent, directory=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        os.fchown(stream.fileno(), owner, owner)
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def record(path, value, owner=0):
    write_new(path, canonical(value), owner=owner)


def read_json(path, owner=0, expected_sha=None):
    return json.loads(
        read_regular(path, owner=owner, mode=0o600, expected_sha=expected_sha).decode(
            "utf-8"
        )
    )


def mkdir_new(path, owner=0):
    check_path(path.parent, directory=True)
    path.mkdir(mode=0o700)
    os.chown(str(path), owner, owner)
    os.chmod(str(path), 0o700)
    fsync_directory(path.parent)


def metadata(path):
    info = check_path(path)
    return {
        "sha256": sha(path),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def protected():
    """Keep existing salary receipts/backups and certificate symlink identities."""
    roots = list(PROTECTED)
    optional = Path("/etc/docker/daemon.json")
    if optional.exists() or optional.is_symlink():
        roots.append(optional)
    daemon = Path("/etc/systemd/system/docker.service.d")
    if daemon.exists() or daemon.is_symlink():
        check_path(daemon, directory=True)
        roots.extend(sorted(daemon.glob("*.conf")))
    result = {}
    for root in roots:
        # Certificate children may be legitimate links. No directory link is followed.
        check_path(root, directory=root.is_dir())
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            item = {
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": stat.S_IMODE(info.st_mode),
            }
            if stat.S_ISDIR(info.st_mode):
                item["kind"] = "directory"
                pending.extend(sorted(path.iterdir()))
            elif stat.S_ISREG(info.st_mode):
                item.update(kind="file", bytes=info.st_size, sha256=sha(path))
            else:
                require(stat.S_ISLNK(info.st_mode), "protected_special_path")
                item.update(kind="symlink", target=os.readlink(str(path)))
            result[str(path)] = item
    return result


def compose(new=False):
    argv = [
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
        str(OLD_OVERLAY),
    ]
    if new:
        argv.extend(["-f", str(OVERLAY)])
    return argv + ["--profile", "tools"]


def bind(source, target, readonly):
    return {
        "type": "bind",
        "source": str(source),
        "target": target,
        "read_only": readonly,
        "bind": {"create_host_path": False},
    }


def overlay_document():
    services = {
        name: {"volumes": [bind(RELEASE / "app", APP_TARGET, True)]} for name in RUNTIME
    }
    for name, source in zip(TOOLS, (OLD_RELEASE / "app", RELEASE / "app")):
        services[name] = {
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
                {"type": "volume", "source": "sites", "target": BENCH + "/sites"},
                {"type": "volume", "source": "logs", "target": BENCH + "/logs"},
                bind(RELEASE, CONTAINER_ROOT, True),
                bind(CONTROL, CONTAINER_ROOT + "/tool-cost-control-r1", False),
                bind(PRIVATE, CONTAINER_ROOT + "/tool-cost-private-r1", False),
                bind(source, APP_TARGET, True),
            ],
        }
    return {"services": services}


def mount_map(service):
    volumes = service.get("volumes", [])
    require(
        isinstance(volumes, list) and all(isinstance(item, dict) for item in volumes),
        "compose_mount_list",
    )
    result = {item["target"]: item for item in volumes}
    require(len(result) == len(volumes), "duplicate_compose_mount")
    return result


def validate_compose(old, new):
    require(
        {key: value for key, value in old.items() if key != "services"}
        == {key: value for key, value in new.items() if key != "services"},
        "compose_top_level_changed",
    )
    expected = set(RUNTIME + DATA + BASE_TOOLS + OLD_TOOLS)
    require(
        set(old["services"]) == expected
        and set(new["services"]) == expected | set(TOOLS),
        "compose_service_set",
    )
    for name in sorted(expected):
        prior, current = (
            copy.deepcopy(old["services"][name]),
            copy.deepcopy(new["services"][name]),
        )
        if name in RUNTIME:
            for service, source in (
                (prior, OLD_RELEASE / "app"),
                (current, RELEASE / "app"),
            ):
                mounts = mount_map(service)
                mount = mounts.get(APP_TARGET, {})
                require(
                    mount.get("type") == "bind"
                    and mount.get("source") == str(source)
                    and mount.get("read_only") is True,
                    "compose_runtime_source_mount",
                )
                require(
                    service.get("image") == BASE_TAG
                    and service.get("pull_policy") == "never",
                    "compose_runtime_image",
                )
                service["volumes"] = [
                    item for item in service["volumes"] if item["target"] != APP_TARGET
                ]
        for service in (prior, current):
            if "volumes" in service:
                service["volumes"].sort(key=lambda item: item["target"])
        require(prior == current, "existing_service_configuration_changed")
    template = overlay_document()["services"]
    for name in TOOLS:
        service, wanted = new["services"][name], template[name]
        require(set(service) <= set(wanted) | {"name"}, "tool_unexpected_configuration")
        for key in (
            "image",
            "pull_policy",
            "restart",
            "user",
            "working_dir",
            "entrypoint",
            "command",
            "environment",
            "profiles",
        ):
            require(service.get(key) == wanted[key], "tool_fixed_configuration")
        require(
            int(service["mem_limit"]) == 768 * 1024 * 1024
            and int(service["memswap_limit"]) == 1024 * 1024 * 1024,
            "tool_memory_scope",
        )
        networks = service["networks"]
        require(set(networks) == {"erp"}, "tool_network_scope")
        actual_mounts, wanted_mounts = mount_map(service), mount_map(wanted)
        require(set(actual_mounts) == set(wanted_mounts), "tool_mount_set")
        for target, expected_mount in wanted_mounts.items():
            actual = actual_mounts[target]
            require(
                actual.get("type") == expected_mount["type"]
                and actual.get("source") == expected_mount["source"]
                and actual.get("read_only", False)
                == expected_mount.get("read_only", False),
                "tool_mount_identity",
            )
            if actual["type"] == "bind":
                require(
                    actual.get("bind", {}).get("create_host_path") is False,
                    "tool_bind_auto_creation",
                )


def source_mounts(rows, source, status):
    for name in RUNTIME:
        row = rows[name]
        found = [item for item in row["mounts"] if item["Destination"] == APP_TARGET]
        require(
            len(found) == 1
            and found[0]["Type"] == "bind"
            and found[0]["Source"] == str(source)
            and found[0]["RW"] is False,
            "runtime_exact_readonly_source",
        )
        require(
            row["image"] == BASE_ID
            and (status is None or row["status"] == status)
            and row["oom_killed"] is False
            and row["restart_count"] == 0,
            "runtime_state_image_restart_oom",
        )


def data_gate(rows, before=None):
    for name in DATA:
        row = rows[name]
        require(
            row["status"] == "running"
            and row["oom_killed"] is False
            and row["restart_count"] == 0,
            "data_service_health",
        )
    require(rows["db"]["health"] == "healthy", "database_health")
    require(
        before is None or all(rows[name] == before[name] for name in DATA),
        "data_container_identity_changed",
    )


def source_inventory(root):
    observed = {}
    for path in (root,) + tuple(root.rglob("*")):
        info = check_path(path, directory=path.is_dir(), owner=1000)
        name = path.relative_to(RELEASE).as_posix()
        if stat.S_ISDIR(info.st_mode):
            observed[name] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
        else:
            observed[name] = {
                "kind": "file",
                "mode": stat.S_IMODE(info.st_mode),
                "content": read_regular(path, owner=1000),
            }
    return observed


def verify_candidate_inventory(manifest, observed):
    """Byte integrity of the frozen manifest; semantic audit runs in native Python."""
    require(
        set(manifest) == {"commit", "files", "hooks"} and manifest["commit"] == NEW,
        "candidate_manifest_scope",
    )
    records = manifest["files"]
    require(
        isinstance(records, list) and len(records) == SOURCE_FILES,
        "candidate_source_file_count",
    )
    expected, directories = {}, {"app"}
    for item in records:
        require(
            isinstance(item, dict) and set(item) == {"path", "bytes", "sha256", "mode"},
            "candidate_file_record",
        )
        name = item["path"]
        require(isinstance(name, str), "candidate_file_path")
        path = PurePosixPath(name)
        require(
            path.parts
            and path.parts[0] == "app"
            and not path.is_absolute()
            and path.as_posix() == name
            and "\\" not in name
            and not set(path.parts)
            & {"..", ".env", "output", "sites", "logs", "node_modules", "__pycache__"},
            "candidate_file_path",
        )
        require(
            name not in expected
            and type(item["bytes"]) is int
            and item["bytes"] >= 0
            and item["mode"] == 0o644
            and type(item["mode"]) is int
            and isinstance(item["sha256"], str)
            and SHA.fullmatch(item["sha256"]),
            "candidate_file_identity",
        )
        expected[name] = item
        directories.update(
            parent.as_posix() for parent in path.parents if parent.as_posix() != "."
        )
    directories.update(set(observed) & {"app/.git/objects/info", "app/.git/refs/heads"})
    require(
        set(observed) == set(expected) | directories, "candidate_exact_source_inventory"
    )
    for name, value in observed.items():
        if name in directories:
            require(
                value == {"kind": "directory", "mode": 0o755},
                "candidate_directory_mode",
            )
        else:
            require(
                set(value) == {"kind", "mode", "content"}
                and value["kind"] == "file"
                and value["mode"] == 0o644
                and isinstance(value["content"], bytes)
                and len(value["content"]) == expected[name]["bytes"]
                and digest(value["content"]) == expected[name]["sha256"],
                "candidate_file_bytes",
            )
    hooks = manifest["hooks"]
    require(
        isinstance(hooks, list) and len(hooks) == len(set(hooks)) == 33,
        "candidate_33_hook_urls",
    )
    extensions = []
    for url in hooks:
        require(
            isinstance(url, str)
            and re.fullmatch(r"/assets/jifeng_erp/[^?]+\?v=[0-9a-f]{16}", url),
            "candidate_hook_url",
        )
        name, version = url[len("/assets/jifeng_erp/") :].split("?v=")
        path = "app/jifeng_erp/public/" + name
        require(
            path in expected and expected[path]["sha256"][:16] == version,
            "candidate_hook_fingerprint",
        )
        extensions.append(PurePosixPath(name).suffix)
    require(
        extensions.count(".css") == 12 and extensions.count(".js") == 21,
        "candidate_12_css_21_js",
    )
    return {"source_files": len(expected), "hook_count": 33}


def candidate(host_sha):
    check_path(RELEASE, directory=True, owner=1000, mode=0o755)
    check_path(INCOMING, directory=True, owner=1000, mode=0o755)
    require(isinstance(host_sha, str) and SHA.fullmatch(host_sha), "reviewed_host_pin")
    for path, pin in (
        (HOST, host_sha),
        (NATIVE, NATIVE_SHA),
        (AUDIT, AUDIT_SHA),
        (RELEASE / "source-manifest.json", MANIFEST_SHA),
        (RELEASE / "native-release-plan.json", PLAN_SHA),
        (RELEASE / "previous-hooks.py", PREVIOUS_HOOKS_SHA),
        (ARCHIVE, ARCHIVE_SHA),
    ):
        read_regular(path, owner=1000, mode=0o644, expected_sha=pin)
    require(ARCHIVE.stat().st_size == ARCHIVE_BYTES, "archive_size")
    manifest = json.loads(read_regular(RELEASE / "source-manifest.json"))
    plan = json.loads(read_regular(RELEASE / "native-release-plan.json"))
    require(
        set(plan) == {"old_commit", "new_commit", "source_files", "new_table_layout"}
        and plan["old_commit"] == OLD
        and plan["new_commit"] == NEW
        and plan["source_files"] == SOURCE_FILES
        and set(plan["new_table_layout"])
        == {"tabJifeng Tool Cost Allocation", "tabJifeng Tool Cost Allocation Item"},
        "candidate_plan_scope",
    )
    definition = verify_candidate_inventory(manifest, source_inventory(RELEASE / "app"))
    require(
        definition["source_files"] == plan["source_files"] == SOURCE_FILES,
        "candidate_source_file_count",
    )
    return definition


def old_source():
    manifest = json.loads(read_regular(OLD_RELEASE / "manifest.json"))
    records = json.loads(read_regular(OLD_RELEASE / "source-files.json"))
    require(
        manifest["commit"] == OLD and manifest["source_files"] == len(records),
        "old_manifest_identity",
    )
    root, expected = OLD_RELEASE / "app", set()
    for item in records:
        relative = PurePosixPath(item["path"])
        require(
            relative.parts
            and relative.parts[0] == "app"
            and relative.as_posix() == item["path"]
            and not relative.is_absolute()
            and ".." not in relative.parts
            and "\\" not in item["path"],
            "old_source_path_scope",
        )
        path = OLD_RELEASE.joinpath(*relative.parts)
        content = read_regular(path, owner=1000, mode=0o644)
        require(
            len(content) == item["bytes"] and digest(content) == item["sha256"],
            "old_source_content",
        )
        expected.add(path.relative_to(root).as_posix())
    actual = set()
    for path in (root,) + tuple(root.rglob("*")):
        info = check_path(
            path,
            directory=path.is_dir(),
            owner=1000,
            mode=0o755 if path.is_dir() else 0o644,
        )
        if stat.S_ISREG(info.st_mode):
            actual.add(path.relative_to(root).as_posix())
    require(
        len(expected) == len(records) and actual == expected, "old_source_inventory"
    )
    require(
        sha(root / "jifeng_erp/hooks.py") == PREVIOUS_HOOKS_SHA,
        "old_hook_exact_git_bytes",
    )


def native_pins():
    tree = ast.parse(read_regular(NATIVE, expected_sha=NATIVE_SHA))
    found = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "NATIVE_PINS"
            for target in node.targets
        )
    ]
    require(len(found) == 1, "native_implementation_pin_declaration")
    pins = ast.literal_eval(found[0])
    require(
        isinstance(pins, dict)
        and len(pins) == 8
        and all(SHA.fullmatch(value) for value in pins.values()),
        "eight_native_pins",
    )
    return pins


def probe_script(commit, estimate=False):
    """Container-only read checks; no mutation or credential output."""
    header = "EXPECTED = " + repr(commit) + "\nPINS = " + repr(native_pins()) + "\n"
    body = """import hashlib, importlib.util, json, os, pathlib, subprocess, sys
bench = pathlib.Path("/home/frappe/frappe-bench")
assert os.geteuid() == 1000
assert subprocess.check_output(["git", "-C", str(bench / "apps/jifeng_erp"), "rev-parse", "HEAD"]).decode().strip() == EXPECTED
for app in ("frappe", "jifeng_erp"):
    spec = importlib.util.find_spec(app)
    assert spec and spec.origin and os.path.samefile(spec.origin, bench / "apps" / app / app / "__init__.py")
for relative, pin in PINS.items():
    path = bench / "apps/frappe" / relative
    assert path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == pin
assert os.readlink(bench / "sites/assets") == "../assets"
assert os.readlink(bench / "assets/jifeng_erp") == "../apps/jifeng_erp/jifeng_erp/public"
config = json.loads((bench / "sites/jifeng-erp-test.localhost/site_config.json").read_text())
assert config["db_name"] == "jifeng_erp_test"
result = {"passed": True, "commit": EXPECTED, "native_pins": PINS, "uid": 1000, "asset_links_current": True}
"""
    if estimate:
        body += """os.chdir(bench / "sites")
import frappe
try:
    frappe.init(site="jifeng-erp-test.localhost", sites_path=str(bench / "sites"))
    frappe.connect()
    assert frappe.conf.db_name == "jifeng_erp_test"
    rows = frappe.db.sql("SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0), COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
    result.update(database_bytes=int(rows[0][0]), physical_objects=int(rows[0][1]))
finally:
    frappe.destroy()
"""
    else:
        body += """import urllib.request
request = urllib.request.Request("http://127.0.0.1:8000/api/method/frappe.ping", headers={"Host": "jifeng-erp-test.localhost"})
with urllib.request.urlopen(request, timeout=20) as response:
    assert response.status == 200 and json.loads(response.read())["message"] == "pong"
result["backend_ping"] = True
"""
    return (header + body + "print(json.dumps(result, sort_keys=True))\n").encode(
        "utf-8"
    )


def subprocess_runner(argv, payload, timeout):
    process = subprocess.run(
        argv,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        universal_newlines=False,
    )
    return process.returncode, process.stdout, process.stderr


class Coordinator:
    def __init__(self, host_sha, runner=None, clock=None, sleeper=None):
        require(
            isinstance(host_sha, str) and SHA.fullmatch(host_sha), "reviewed_host_pin"
        )
        self.runner = runner or subprocess_runner
        self.clock, self.sleeper = clock or time.monotonic, sleeper or time.sleep
        self.host_sha, self.phase_directory = host_sha, None
        self.identity = {
            "site": SITE,
            "database": DATABASE,
            "release": str(RELEASE),
            "old_commit": OLD,
            "new_commit": NEW,
            "image_tag": BASE_TAG,
            "image_id": BASE_ID,
            "host_sha256": host_sha,
            "native_sha256": NATIVE_SHA,
            "audit_sha256": AUDIT_SHA,
            "plan_sha256": PLAN_SHA,
            "manifest_sha256": MANIFEST_SHA,
            "previous_hooks_sha256": PREVIOUS_HOOKS_SHA,
            "archive_sha256": ARCHIVE_SHA,
            "archive_bytes": ARCHIVE_BYTES,
            "source_files": SOURCE_FILES,
        }
        self.native_identity = {
            key: self.identity[key]
            for key in (
                "site",
                "database",
                "old_commit",
                "new_commit",
                "native_sha256",
                "audit_sha256",
                "plan_sha256",
                "manifest_sha256",
            )
        }

    def command(self, label, argv, payload=None, timeout=60, allow_failure=False):
        require(re.fullmatch(r"[a-z][a-z0-9-]*", label), "command_label")
        record(
            self.phase_directory / (label + "-started.json"),
            {
                "argv": argv,
                "stdin_supplied": payload is not None,
                "stdin_sha256": digest(payload) if payload is not None else None,
            },
        )
        try:
            code, stdout, stderr = self.runner(argv, payload, timeout)
        except subprocess.TimeoutExpired as error:
            self.output_receipt(label, error.stdout or b"", error.stderr or b"", None)
            if allow_failure:
                return None, b""
            raise HostError("command_timeout_unknown_result")
        require(
            isinstance(stdout, bytes) and isinstance(stderr, bytes),
            "command_binary_output",
        )
        self.output_receipt(label, stdout, stderr, code)
        require(code == 0 or allow_failure, "command_failed_unknown_result")
        if allow_failure:
            return code, stdout
        return stdout

    def output_receipt(self, label, stdout, stderr, code):
        record(
            self.phase_directory / (label + "-result.json"),
            {
                "exit_code": code,
                "stdout_sha256": digest(stdout),
                "stdout_bytes": len(stdout),
                "stderr_sha256": digest(stderr),
                "stderr_bytes": len(stderr),
            },
        )

    def completed(self, phase):
        path = HOST_CONTROL / phase / "completed.json"
        value = read_json(path)
        require(
            value.get("passed") is True
            and value.get("phase") == phase
            and value.get("identity") == self.identity,
            "previous_host_phase_required",
        )
        index = PHASES.index(phase)
        if index:
            _prior, prior_sha = self.completed(PHASES[index - 1])
            require(
                value.get("previous_sha256") == prior_sha, "host_phase_chain_changed"
            )
        return value, sha(path)

    def containers(self, label, timeout=30, sample=False):
        template = (
            '{"Id":{{json .Id}},"Image":{{json .Image}},'
            '"State":{{json .State}},"RestartCount":{{json .RestartCount}},'
            '"Mounts":{{json .Mounts}},"Service":'
            '{{json (index .Config.Labels "com.docker.compose.service")}}}'
        )
        output = self.command(
            label,
            ["docker", "inspect", "--format", template]
            + ["jifeng-erp-test-" + name + "-1" for name in RUNTIME + DATA],
            timeout=timeout,
            allow_failure=sample,
        )
        if sample:
            code, output = output
            if code != 0:
                return None
        result = {}
        for line in output.decode("utf-8").splitlines():
            row = json.loads(line)
            name, state = row["Service"], row["State"]
            require(
                name in RUNTIME + DATA and name not in result,
                "container_service_identity",
            )
            result[name] = {
                "id": row["Id"],
                "image": row["Image"],
                "started_at": state["StartedAt"],
                "status": state["Status"],
                "oom_killed": state["OOMKilled"],
                "restart_count": row["RestartCount"],
                "health": state.get("Health", {}).get("Status"),
                "mounts": row["Mounts"],
            }
        require(set(result) == set(RUNTIME + DATA), "eight_runtime_containers")
        return result

    def running_project_gate(self, label, expected):
        output = self.command(
            label,
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=jifeng-erp-test",
                "--filter",
                "status=running",
                "--format",
                '{{.Label "com.docker.compose.service"}}',
            ],
            timeout=30,
        )
        names = output.decode("utf-8").splitlines()
        require(
            len(names) == len(expected) and set(names) == set(expected),
            "unexpected_running_project_writer",
        )

    def image_gate(self, label):
        actual = json.loads(
            self.command(
                label,
                ["docker", "image", "inspect", "--format", "{{json .Id}}", BASE_TAG],
                timeout=30,
            )
        )
        require(actual == BASE_ID, "fixed_image_tag_id")

    def runtime_probe(self, label, commit, estimate=False):
        result = json.loads(
            self.command(
                label,
                [
                    "docker",
                    "exec",
                    "-i",
                    "--user",
                    "1000:1000",
                    "--workdir",
                    BENCH,
                    "jifeng-erp-test-backend-1",
                    "env/bin/python",
                    "-",
                ],
                probe_script(commit, estimate),
                timeout=60,
            )
        )
        require(
            result.get("passed") is True
            and result.get("commit") == commit
            and result.get("native_pins") == native_pins()
            and result.get("uid") == 1000
            and result.get("asset_links_current") is True,
            "independent_runtime_pin_gate",
        )
        if estimate:
            require(
                result.get("physical_objects") == 932
                and type(result.get("database_bytes")) is int
                and result["database_bytes"] >= 0,
                "backup_database_estimate",
            )
        else:
            require(
                result.get("backend_ping") is True, "independent_backend_http_health"
            )
        return result

    def readiness(self):
        started, attempts = self.clock(), 0
        deadline = started + 60
        payload = b"""import json, urllib.request
ready = False
try:
    request = urllib.request.Request("http://127.0.0.1:8000/api/method/frappe.ping", headers={"Host": "jifeng-erp-test.localhost"})
    with urllib.request.urlopen(request, timeout=2) as response:
        ready = response.status == 200 and json.loads(response.read()).get("message") == "pong"
except Exception:
    pass
print(json.dumps({"backend_ping": ready}))
"""
        while self.clock() < deadline:
            attempts += 1
            prefix = "readiness-" + str(attempts)
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            rows = self.containers(
                prefix + "-services", timeout=min(3, remaining), sample=True
            )
            if rows is not None:
                source_mounts(rows, RELEASE / "app", None)
                data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
            if rows is not None and all(
                rows[name]["status"] == "running" for name in RUNTIME
            ):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                code, output = self.command(
                    prefix + "-ping",
                    [
                        "docker",
                        "exec",
                        "-i",
                        "--user",
                        "1000:1000",
                        "jifeng-erp-test-backend-1",
                        "env/bin/python",
                        "-",
                    ],
                    payload,
                    timeout=min(3, remaining),
                    allow_failure=True,
                )
                try:
                    ready = code == 0 and json.loads(output).get("backend_ping") is True
                except (ValueError, AttributeError):
                    ready = False
                if ready and self.clock() <= deadline:
                    return {
                        "passed": True,
                        "attempts": attempts,
                        "elapsed_seconds": self.clock() - started,
                        "only_readonly_samples": True,
                        "maximum_seconds": 60,
                    }
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleeper(min(1, remaining))
        raise HostError("readiness_timeout_unknown_result")

    def git_source(self, label, root, commit):
        allowed = {OLD_RELEASE / "app": OLD, RELEASE / "app": NEW}
        require(root in allowed and allowed[root] == commit, "fixed_git_source_scope")
        prefix = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--env",
            "GIT_OPTIONAL_LOCKS=0",
            "--mount",
            "type=bind,source=" + str(root) + ",target=/source,readonly",
            "--entrypoint",
            "git",
            BASE_TAG,
            "-C",
            "/source",
        ]
        require(
            self.command(
                label + "-head",
                prefix + ["rev-parse", "HEAD"],
                timeout=30,
            )
            .decode("ascii")
            .strip()
            == commit,
            "git_exact_commit",
        )
        for suffix, argv in (
            ("tracked", ["diff", "--exit-code", "HEAD", "--"]),
            ("untracked", ["ls-files", "--others", "--exclude-standard"]),
            ("objects", ["fsck", "--full", "--strict"]),
        ):
            output = self.command(label + "-" + suffix, prefix + argv, timeout=60)
            if suffix in {"tracked", "untracked"}:
                require(not output.strip(), "git_source_pollution")

    def disk_gate(self, database_bytes):
        file_bytes = 0
        for folder in ("public", "private"):
            root = SITES / SITE / folder / "files"
            check_path(root, directory=True)
            for path in root.rglob("*"):
                info = check_path(path, directory=path.is_dir())
                if stat.S_ISREG(info.st_mode):
                    file_bytes += info.st_size
        # Room for SQL expansion, both file tar streams and fixed working overhead.
        required = (
            4 * database_bytes + 2 * file_bytes + ARCHIVE_BYTES + 256 * 1024 * 1024
        )
        free = shutil.disk_usage(str(RELEASE)).free
        require(free >= required, "insufficient_release_backup_disk")
        return {
            "database_bytes": database_bytes,
            "site_file_bytes": file_bytes,
            "required_bytes": required,
            "free_bytes": free,
        }

    def common_gate(self):
        candidate(self.host_sha)
        old_source()
        require(
            protected() == read_json(HOST_CONTROL / "protected-before.json"),
            "protected_files_changed",
        )
        expected = read_json(HOST_CONTROL / "overlay.json")
        require(metadata(OVERLAY) == expected, "overlay_identity_changed")
        for path in (CONTROL, PRIVATE, AUDIT_DIRECTORY):
            check_path(path, directory=True, owner=1000, mode=0o700)

    def stopped_gate(self, label):
        require(
            metadata(CONFIG) == read_json(HOST_CONTROL / "maintenance-config.json"),
            "maintenance_config_drift",
        )
        values = json.loads(read_regular(CONFIG))
        require(
            values.get("db_name") == DATABASE
            and all(values.get(flag) == 1 for flag in FLAGS),
            "four_maintenance_flags",
        )
        rows = self.containers(label)
        source_mounts(rows, OLD_RELEASE / "app", "exited")
        data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
        self.running_project_gate(label + "-writers", DATA)
        return rows

    def native_completed(self, phase):
        path = AUDIT_DIRECTORY / (phase + "-completed.json")
        value = read_json(path, owner=1000)
        require(
            value.get("passed") is True
            and value.get("phase") == phase
            and value.get("identity") == self.native_identity
            and value.get("total_objects") == (932 if phase == "before" else 934)
            and SHA.fullmatch(value.get("proof_sha256", "")),
            "native_completed_identity",
        )
        proof = "before.json" if phase == "before" else "after-" + phase + ".json"
        read_regular(
            AUDIT_DIRECTORY / proof,
            owner=1000,
            mode=0o600,
            expected_sha=value["proof_sha256"],
        )
        require(
            read_json(AUDIT_DIRECTORY / (phase + "-result.json"), owner=1000) == value,
            "native_result_completed_differ",
        )
        if phase != "before":
            delta = value.get("delta", {})
            require(
                delta
                == {
                    "passed": True,
                    "protected_objects": 932,
                    "total_objects": 934,
                    "new_empty_doctypes": [
                        "Jifeng Tool Cost Allocation",
                        "Jifeng Tool Cost Allocation Item",
                    ],
                    "existing_fields_changed": 0,
                    "old_permissions_unchanged": True,
                    "sequences_unchanged": True,
                },
                "native_complete_delta_proof",
            )
        if phase == "apply":
            require(
                value.get("apply")
                == {
                    "attempted": [
                        "Jifeng Tool Cost Allocation Item",
                        "Jifeng Tool Cost Allocation",
                    ],
                    "existing_doctypes_reloaded": [],
                },
                "native_child_parent_only",
            )
        if phase == "cache":
            cache = value.get("cache", {})
            require(
                cache.get("effective_hooks") == 33
                and cache.get("app_include_css") == 12
                and cache.get("app_include_js") == 21
                and all(
                    cache.get(key) is True
                    for key in (
                        "four_deny_hooks_current",
                        "scoped_share_hook_current",
                        "parent_form_script_cached",
                    )
                )
                and cache.get("gui_tested") is False
                and SHA.fullmatch(cache.get("parent_effective_script_sha256", "")),
                "native_cache_full_proof",
            )
        return value, sha(path)

    def backup_gate(self):
        before, _receipt_sha = self.native_completed("before")
        backup = read_json(AUDIT_DIRECTORY / "backup-completed.json", owner=1000)
        require(
            set(backup)
            == {
                "identity",
                "before_sha256",
                "parts",
                "physical_objects",
                "myisam_locked_through_validation",
                "before_extra_sha256",
            }
            and backup["identity"] == self.native_identity
            and backup["before_sha256"] == before["proof_sha256"]
            and backup["physical_objects"] == 932
            and backup["myisam_locked_through_validation"] is True,
            "full_locked_backup_receipt",
        )
        parts = backup["parts"]
        require(parts == before.get("backups"), "backup_before_parts_binding")
        require(
            isinstance(parts, list)
            and len(parts) == 4
            and {part["part"] for part in parts}
            == {"database", "configuration", "public-files", "private-files"},
            "four_backup_parts",
        )
        directory = PRIVATE / "native-backup"
        check_path(directory, directory=True, owner=1000, mode=0o700)
        expected = set()
        for part in parts:
            name = part.get("name")
            require(
                set(part) == {"part", "name", "bytes", "sha256"}
                and isinstance(name, str)
                and name not in {"", ".", ".."}
                and PurePosixPath(name).name == name
                and "/" not in name
                and "\\" not in name
                and type(part["bytes"]) is int
                and part["bytes"] > 0
                and isinstance(part["sha256"], str)
                and SHA.fullmatch(part["sha256"]),
                "backup_part_scope",
            )
            path = directory / name
            require(path not in expected, "backup_duplicate_part_path")
            expected.add(path)
            info = check_path(path, owner=1000, mode=0o600)
            require(
                info.st_size == part["bytes"] and sha(path) == part["sha256"],
                "backup_part_size_or_sha",
            )
            if part["part"] == "configuration":
                content = read_regular(path, owner=1000, mode=0o600)
                require(
                    digest(content)
                    == read_json(HOST_CONTROL / "maintenance-config.json")["sha256"],
                    "backup_maintenance_config_bytes",
                )
        require(set(directory.iterdir()) == expected, "backup_exact_inventory")
        read_regular(
            AUDIT_DIRECTORY / "before-extra.json",
            owner=1000,
            mode=0o600,
            expected_sha=backup["before_extra_sha256"],
        )
        return backup

    def previous_native_gate(self, phase):
        native_order = ("before", "apply", "verify", "cache")
        for prior in native_order[: native_order.index(phase)]:
            native_receipt, native_sha = self.native_completed(prior)
            host_receipt, _host_sha = self.completed(prior)
            require(
                host_receipt.get("native_receipt_sha256") == native_sha
                and host_receipt.get("native_proof_sha256")
                == native_receipt["proof_sha256"],
                "native_host_receipt_binding",
            )
        if phase != "before":
            self.backup_gate()

    def native(self, phase):
        self.previous_native_gate(phase)
        self.image_gate("native-image")
        argv = compose(True) + [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--pull",
            "never",
            TOOLS[0] if phase == "before" else TOOLS[1],
            CONTAINER_ROOT + "/incoming-r1/tool_cost_release_native.py",
            phase,
        ]
        for name, pin in (
            ("native", NATIVE_SHA),
            ("audit", AUDIT_SHA),
            ("plan", PLAN_SHA),
            ("manifest", MANIFEST_SHA),
        ):
            argv.extend(["--" + name + "-sha256", pin])
        if phase != "before":
            argv.extend(
                ["--before-sha256", self.native_completed("before")[0]["proof_sha256"]]
            )
        result = json.loads(self.command("native-" + phase, argv, timeout=1200))
        completed, receipt_sha = self.native_completed(phase)
        require(result == completed, "native_stdout_receipt_differ")
        if phase == "before":
            self.backup_gate()
        return {
            "native_receipt_sha256": receipt_sha,
            "native_proof_sha256": completed["proof_sha256"],
        }

    def replace_config(self, data, expected):
        require(metadata(CONFIG) == expected, "configuration_changed_do_not_overwrite")
        info = read_json(HOST_CONTROL / "original-config.json")
        temporary = CONFIG.with_name("site_config.5b49bac-tool-cost-r1.tmp")
        write_new(temporary, data, mode=info["mode"], owner=info["uid"])
        if info["gid"] != info["uid"]:
            os.chown(str(temporary), info["uid"], info["gid"])
        require(metadata(CONFIG) == expected, "configuration_changed_before_replace")
        os.replace(str(temporary), str(CONFIG))
        fsync_directory(CONFIG.parent)
        require(
            metadata(CONFIG) == dict(info, sha256=digest(data)),
            "configuration_exact_replace",
        )

    def prepare(self):
        candidate(self.host_sha)
        old_source()
        rows = self.containers("prepare-containers")
        source_mounts(rows, OLD_RELEASE / "app", "running")
        data_gate(rows)
        self.running_project_gate("prepare-writers", RUNTIME + DATA)
        self.image_gate("prepare-image")
        self.git_source("candidate-git", RELEASE / "app", NEW)
        self.git_source("old-git", OLD_RELEASE / "app", OLD)
        estimate = self.runtime_probe("prepare-runtime", OLD, estimate=True)
        disk = self.disk_gate(estimate["database_bytes"])
        record(HOST_CONTROL / "protected-before.json", protected())
        record(HOST_CONTROL / "containers-before.json", rows)
        record(HOST_CONTROL / "site-config-before.json", metadata(CONFIG))
        for path in (CONTROL, PRIVATE, AUDIT_DIRECTORY):
            mkdir_new(path, owner=1000)
        write_new(OVERLAY, canonical(overlay_document()), mode=0o644)
        old = json.loads(
            self.command("old-compose", compose() + ["config", "--format", "json"])
        )
        new = json.loads(
            self.command("new-compose", compose(True) + ["config", "--format", "json"])
        )
        validate_compose(old, new)
        record(HOST_CONTROL / "overlay.json", metadata(OVERLAY))
        self.common_gate()
        return {"disk": disk, "gui_tested": False}

    def quiesce(self):
        require(
            metadata(CONFIG) == read_json(HOST_CONTROL / "site-config-before.json"),
            "site_config_changed_after_prepare",
        )
        rows = self.containers("quiesce-before")
        source_mounts(rows, OLD_RELEASE / "app", "running")
        data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
        self.running_project_gate("quiesce-writers", RUNTIME + DATA)
        self.image_gate("quiesce-image")
        original = read_regular(CONFIG)
        values = json.loads(original)
        require(values.get("db_name") == DATABASE, "site_config_database")
        info = metadata(CONFIG)
        record(HOST_CONTROL / "original-config.json", info)
        write_new(HOST_CONTROL / "original-site-config.private.json", original)
        values.update({flag: 1 for flag in FLAGS})
        maintenance = canonical(values)
        record(
            HOST_CONTROL / "maintenance-config.json",
            dict(info, sha256=digest(maintenance)),
        )
        self.replace_config(maintenance, info)
        self.command(
            "stop-five",
            compose() + ["stop", "--timeout", "40"] + list(RUNTIME),
            timeout=180,
        )
        self.stopped_gate("quiesce-after")
        return {"applications_stopped": list(RUNTIME), "four_maintenance_flags": True}

    def restore(self):
        self.previous_native_gate("cache")
        value, native_sha = self.native_completed("cache")
        host, _sha = self.completed("cache")
        require(
            host.get("native_receipt_sha256") == native_sha
            and host.get("native_proof_sha256") == value["proof_sha256"],
            "cache_host_receipt_binding",
        )
        self.backup_gate()
        self.stopped_gate("restore-before")
        info = read_json(HOST_CONTROL / "original-config.json")
        original = read_regular(
            HOST_CONTROL / "original-site-config.private.json",
            owner=0,
            mode=0o600,
            expected_sha=info["sha256"],
        )
        self.replace_config(
            original, read_json(HOST_CONTROL / "maintenance-config.json")
        )
        require(metadata(CONFIG) == info, "restored_site_config_identity")
        rows = self.containers("restore-after")
        source_mounts(rows, OLD_RELEASE / "app", "exited")
        data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
        return {
            "exact_original_config_restored": True,
            "applications_still_stopped": True,
        }

    def start(self):
        info = read_json(HOST_CONTROL / "original-config.json")
        require(metadata(CONFIG) == info, "original_config_required_before_start")
        self.previous_native_gate("cache")
        value, native_sha = self.native_completed("cache")
        prior, _sha = self.completed("cache")
        require(
            prior.get("native_receipt_sha256") == native_sha
            and prior.get("native_proof_sha256") == value["proof_sha256"],
            "start_cache_host_binding",
        )
        self.backup_gate()
        rows = self.containers("start-before")
        source_mounts(rows, OLD_RELEASE / "app", "exited")
        data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
        self.running_project_gate("start-before-writers", DATA)
        self.image_gate("start-image")
        self.command(
            "start-five",
            compose(True)
            + ["up", "-d", "--no-deps", "--no-build", "--pull", "never"]
            + list(RUNTIME),
            timeout=240,
        )
        readiness = self.readiness()
        rows = self.containers("start-after")
        source_mounts(rows, RELEASE / "app", "running")
        data_gate(rows, read_json(HOST_CONTROL / "containers-before.json"))
        self.running_project_gate("start-after-writers", RUNTIME + DATA)
        self.runtime_probe("start-independent-runtime", NEW)
        self.common_gate()
        require(metadata(CONFIG) == info, "site_config_changed_during_start")
        return {
            "runtime_cut_over": True,
            "eight_services_running": True,
            "gui_tested": False,
            "readiness": readiness,
            "external_http_and_gui_acceptance_required": True,
            "all_modules_formally_accepted": False,
        }

    def run(self, phase):
        require(phase in PHASES, "known_phase")
        check_path(RELEASE, directory=True, owner=1000, mode=0o755)
        if phase == "prepare":
            mkdir_new(HOST_CONTROL)
        check_path(HOST_CONTROL, directory=True, owner=0, mode=0o700)
        self.phase_directory = HOST_CONTROL / phase
        mkdir_new(self.phase_directory)
        state = {
            "passed": False,
            "phase": phase,
            "identity": self.identity,
            "full_migrate": False,
            "automatic_retry": False,
            "automatic_rollback": False,
        }
        # Exclusive phase marker precedes all command execution and domain mutation.
        record(self.phase_directory / "started.json", state)
        try:
            index = PHASES.index(phase)
            if index:
                _prior, prior_sha = self.completed(PHASES[index - 1])
                state["previous_sha256"] = prior_sha
                self.common_gate()
            if phase in {"before", "apply", "verify", "cache"}:
                self.stopped_gate(phase + "-before")
                state.update(self.native(phase))
                self.stopped_gate(phase + "-after")
                self.common_gate()
            else:
                state.update(getattr(self, phase)())
            if phase != "prepare":
                self.common_gate()
            state["passed"] = True
            record(self.phase_directory / "completed.json", state)
        except Exception as error:
            state.update(
                passed=False,
                error_type=type(error).__name__,
                reason=str(error)
                if isinstance(error, HostError)
                else "host_phase_failed",
                outcome_requires_inspection=True,
                automatic_resume=False,
                runtime_or_database_retried=False,
            )
            record(self.phase_directory / "failed.json", state)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--host-sha256", required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0, "host_root_required")
    require(Path(__file__).absolute() == HOST, "execute_fixed_host_file")
    read_regular(HOST, owner=1000, mode=0o644, expected_sha=args.host_sha256)
    os.environ.update(GIT_OPTIONAL_LOCKS="0", PYTHONDONTWRITEBYTECODE="1")
    result = Coordinator(args.host_sha256).run(args.phase)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(error).__name__,
                    "reason": str(error)
                    if isinstance(error, HostError)
                    else "host_entry_failed",
                    "outcome_requires_inspection": True,
                }
            ),
            flush=True,
        )
        sys.exit(1)
