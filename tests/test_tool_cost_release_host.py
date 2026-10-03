"""Host release failure gates with injected commands and an in-memory filesystem.

No Docker daemon, database, browser or network is contacted. Linux-only tests
also exercise real exclusive writes, symlink rejection and permission checks.
"""

import ast
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tool_cost_host_test", ROOT / "deploy/erp-test/tool_cost_release_host.py"
)
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
DIGEST = "a" * 64
NATIVE_BYTES = (ROOT / "deploy/erp-test/tool_cost_release_native.py").read_bytes()


def container_fixture(source=None, status="running"):
    source = source or H.OLD_RELEASE / "app"
    return {
        name: {
            "id": name + "-id",
            "image": H.BASE_ID,
            "started_at": "fixed-start",
            "status": status if name in H.RUNTIME else "running",
            "oom_killed": False,
            "restart_count": 0,
            "health": "healthy" if name == "db" else None,
            "mounts": [
                {
                    "Type": "bind",
                    "Source": str(source),
                    "Destination": H.APP_TARGET,
                    "RW": False,
                }
            ]
            if name in H.RUNTIME
            else [
                {
                    "Type": "volume",
                    "Name": name + "-volume",
                    "Source": "/owned/" + name,
                    "Destination": "/data",
                    "RW": True,
                }
            ],
        }
        for name in H.RUNTIME + H.DATA
    }


def compose_fixture():
    old = {
        "name": "jifeng-erp-test",
        "volumes": {"sites": {}},
        "networks": {"erp": {}},
        "services": {
            name: {
                "command": [name],
                "image": H.BASE_TAG,
                "pull_policy": "never",
                "volumes": [],
            }
            for name in H.RUNTIME + H.DATA + H.BASE_TOOLS + H.OLD_TOOLS
        },
    }
    for name in H.RUNTIME:
        old["services"][name]["volumes"] = [
            H.bind(H.OLD_RELEASE / "app", H.APP_TARGET, True),
            {"type": "volume", "source": "sites", "target": H.BENCH + "/sites"},
        ]
    new = copy.deepcopy(old)
    for name in H.RUNTIME:
        new["services"][name]["volumes"][0] = H.bind(
            H.RELEASE / "app", H.APP_TARGET, True
        )
    for name in H.TOOLS:
        service = copy.deepcopy(H.overlay_document()["services"][name])
        service["mem_limit"] = 768 * 1024 * 1024
        service["memswap_limit"] = 1024 * 1024 * 1024
        service["networks"] = {"erp": None}
        new["services"][name] = service
    return old, new


def inventory_fixture():
    records, observed, hooks = [], {"app": {"kind": "directory", "mode": 0o755}}, []
    for index in range(H.SOURCE_FILES):
        if index < 33:
            extension = "css" if index < 12 else "js"
            name = (
                "app/jifeng_erp/public/"
                + extension
                + "/asset"
                + str(index)
                + "."
                + extension
            )
        else:
            name = "app/file" + str(index) + ".py"
        content = ("file " + str(index)).encode()
        records.append(
            {
                "path": name,
                "mode": 0o644,
                "bytes": len(content),
                "sha256": H.digest(content),
            }
        )
        observed[name] = {"kind": "file", "mode": 0o644, "content": content}
        for parent in H.PurePosixPath(name).parents:
            if parent.as_posix() != ".":
                observed[parent.as_posix()] = {"kind": "directory", "mode": 0o755}
        if index < 33:
            hooks.append(
                "/assets/jifeng_erp/"
                + name[len("app/jifeng_erp/public/") :]
                + "?v="
                + H.digest(content)[:16]
            )
    return {"commit": H.NEW, "files": records, "hooks": hooks}, observed


class Model:
    """Fake every host command; retain actual phase/receipt/config validation."""

    def __init__(self):
        self.files, self.directories, self.commands = {}, {}, []
        self.rows = container_fixture()
        self.native_called, self.native_applied = [], False
        self.protected_value = {"existing-salary-backup": {"sha256": DIGEST}}
        self.fail_label, self.tamper_data, self.backend_ping = None, False, True
        self.image_id, self.extra_writer, self.foreign_uid = H.BASE_ID, None, False
        self.now, self.readiness_results = 0, [True]
        self.coordinator = H.Coordinator(
            DIGEST,
            self.runner,
            lambda: self.now,
            lambda seconds: setattr(self, "now", self.now + seconds),
        )
        self.add_dir(H.RELEASE, 1000, 0o755)
        self.add_dir(H.INCOMING, 1000, 0o755)
        self.add_dir(H.SITES / H.SITE, 1000, 0o755)
        self.add_file(
            H.CONFIG,
            b'{"db_name":"jifeng_erp_test","mute_emails":0,"custom": "keep exact bytes"}\n',
            1000,
            0o640,
        )
        self.add_file(H.NATIVE, NATIVE_BYTES, 1000, 0o644)

    def add_dir(self, path, uid=0, mode=0o700):
        self.directories[path] = {"uid": uid, "gid": uid, "mode": mode}

    def add_file(self, path, data, uid=0, mode=0o600):
        self.files[path] = {"data": data, "uid": uid, "gid": uid, "mode": mode}

    def check(self, path, directory=False, owner=None, mode=None):
        entries = self.directories if directory else self.files
        if path not in entries:
            raise FileNotFoundError("private missing path")
        item = entries[path]
        H.require(
            owner is None or (item["uid"], item["gid"]) == (owner, owner), "path_owner"
        )
        H.require(mode is None or item["mode"] == mode, "path_mode")
        return SimpleNamespace(
            st_uid=item["uid"],
            st_gid=item["gid"],
            st_mode=(stat.S_IFDIR if directory else stat.S_IFREG) | item["mode"],
            st_size=len(item.get("data", b"")),
        )

    def read(self, path, owner=None, mode=None, expected_sha=None):
        self.check(path, owner=owner, mode=mode)
        data = self.files[path]["data"]
        H.require(
            expected_sha is None or H.digest(data) == expected_sha, "file_pin_changed"
        )
        return data

    def write(self, path, data, mode=0o600, owner=0):
        if path in self.files or path in self.directories:
            raise FileExistsError("one-use path")
        self.add_file(path, data, owner, mode)

    def record(self, path, value, owner=0):
        self.write(path, H.canonical(value), owner=owner)

    def read_json(self, path, owner=0, expected_sha=None):
        return json.loads(
            self.read(path, owner=owner, mode=0o600, expected_sha=expected_sha)
        )

    def mkdir(self, path, owner=0):
        if path in self.files or path in self.directories:
            raise FileExistsError("one-use directory")
        self.add_dir(path, owner)

    def metadata(self, path):
        item = self.files[path]
        return {
            "uid": item["uid"],
            "gid": item["gid"],
            "mode": item["mode"],
            "sha256": H.digest(item["data"]),
        }

    def replace(self, source, target):
        self.files[Path(target)] = self.files.pop(Path(source))

    def chown(self, path, uid, gid):
        item = self.files[Path(path)]
        item.update(uid=uid, gid=gid)

    def iterdir(self, path):
        return iter(
            item
            for item in set(self.files) | set(self.directories)
            if item.parent == path
        )

    def snapshot_native(self, phase):
        snapshot = b'{"protected": "model full snapshot"}\n'
        proof = "before.json" if phase == "before" else "after-" + phase + ".json"
        self.add_file(H.AUDIT_DIRECTORY / proof, snapshot, 1000)
        value = {
            "passed": True,
            "phase": phase,
            "identity": self.coordinator.native_identity,
            "proof_sha256": H.digest(snapshot),
            "total_objects": 932 if phase == "before" else 934,
            "full_migrate": False,
            "automatic_retry": False,
        }
        if phase == "before":
            extra = b'{"all_six_sequences": true}\n'
            self.add_file(H.AUDIT_DIRECTORY / "before-extra.json", extra, 1000)
            directory = H.PRIVATE / "native-backup"
            self.add_dir(directory, 1000)
            parts = []
            for part in ("database", "configuration", "public-files", "private-files"):
                data = (
                    self.read(H.CONFIG)
                    if part == "configuration"
                    else (part + " contents").encode()
                )
                self.add_file(directory / part, data, 1000)
                parts.append(
                    {
                        "part": part,
                        "name": part,
                        "bytes": len(data),
                        "sha256": H.digest(data),
                    }
                )
            value["backups"] = parts
            backup = {
                "identity": self.coordinator.native_identity,
                "before_sha256": H.digest(snapshot),
                "parts": parts,
                "physical_objects": 932,
                "myisam_locked_through_validation": True,
                "before_extra_sha256": H.digest(extra),
            }
            self.add_file(
                H.AUDIT_DIRECTORY / "backup-completed.json", H.canonical(backup), 1000
            )
        else:
            value["delta"] = {
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
            }
        if phase == "apply":
            self.native_applied = True
            value["apply"] = {
                "attempted": [
                    "Jifeng Tool Cost Allocation Item",
                    "Jifeng Tool Cost Allocation",
                ],
                "existing_doctypes_reloaded": [],
            }
        if phase == "cache":
            value["cache"] = {
                "effective_hooks": 33,
                "app_include_css": 12,
                "app_include_js": 21,
                "four_deny_hooks_current": True,
                "scoped_share_hook_current": True,
                "parent_form_script_cached": True,
                "gui_tested": False,
                "parent_effective_script_sha256": DIGEST,
            }
        for suffix in ("result", "completed"):
            self.add_file(
                H.AUDIT_DIRECTORY / (phase + "-" + suffix + ".json"),
                H.canonical(value),
                1000,
            )
        return value

    def runner(self, argv, payload, timeout):
        label = self.coordinator.phase_directory.name
        started = H.read_json(self.coordinator.phase_directory / "started.json")
        assert started["phase"] == label
        self.commands.append((argv, payload, timeout))
        if self.fail_label and self.fail_label(argv, payload):
            raise subprocess.TimeoutExpired(
                argv, timeout, output=b"private partial output"
            )
        if argv[1:3] == ["image", "inspect"]:
            return 0, json.dumps(self.image_id).encode(), b""
        if argv[1] == "ps":
            names = [
                name for name, row in self.rows.items() if row["status"] == "running"
            ]
            if self.extra_writer:
                names.append(self.extra_writer)
            return 0, "\n".join(names).encode(), b""
        if argv[1] == "inspect":
            rows = []
            for name, item in self.rows.items():
                rows.append(
                    {
                        "Id": item["id"],
                        "Image": item["image"],
                        "Service": name,
                        "RestartCount": item["restart_count"],
                        "Mounts": item["mounts"],
                        "State": {
                            "StartedAt": item["started_at"],
                            "Status": item["status"],
                            "OOMKilled": item["oom_killed"],
                            "Health": {"Status": item["health"]},
                        },
                    }
                )
            return 0, "\n".join(json.dumps(row) for row in rows).encode(), b""
        if argv[1] == "exec":
            if b"ready = False" in payload:
                ready = self.readiness_results[0]
                if len(self.readiness_results) > 1:
                    self.readiness_results.pop(0)
                return 0, H.canonical({"backend_ping": ready}), b""
            estimate = b"database_bytes" in payload
            value = {
                "passed": True,
                "commit": H.OLD if estimate else H.NEW,
                "native_pins": H.native_pins(),
                "uid": 0 if self.foreign_uid else 1000,
                "asset_links_current": True,
            }
            if estimate:
                value.update(database_bytes=1000, physical_objects=932)
            else:
                value["backend_ping"] = self.backend_ping
            return 0, H.canonical(value), b""
        if (
            argv[:2] == ["docker", "run"]
            and "--entrypoint" in argv
            and argv[argv.index("--entrypoint") + 1] == "git"
        ):
            if "rev-parse" in argv:
                return (
                    0,
                    (
                        (
                            H.NEW
                            if argv[argv.index("--mount") + 1]
                            == "type=bind,source="
                            + str(H.RELEASE / "app")
                            + ",target=/source,readonly"
                            else H.OLD
                        )
                        + "\n"
                    ).encode(),
                    b"",
                )
            return 0, b"", b""
        if "config" in argv:
            old, new = compose_fixture()
            return 0, H.canonical(new if str(H.OVERLAY) in argv else old), b""
        if "stop" in argv:
            for name in H.RUNTIME:
                self.rows[name]["status"] = "exited"
            return 0, b"", b""
        if "up" in argv:
            for name in H.RUNTIME:
                self.rows[name]["status"] = "running"
                self.rows[name]["mounts"][0]["Source"] = str(H.RELEASE / "app")
            if self.tamper_data:
                self.rows["db"]["id"] = "unexpected-recreated-db"
            return 0, b"", b""
        if "run" in argv:
            index = argv.index(
                H.CONTAINER_ROOT + "/incoming-r1/tool_cost_release_native.py"
            )
            phase = argv[index + 1]
            self.native_called.append(phase)
            assert phase not in self.native_called[:-1]
            return 0, H.canonical(self.snapshot_native(phase)), b""
        raise AssertionError("unexpected fake command")

    def patch(self, stack):
        for name, function in (
            ("read_regular", self.read),
            ("check_path", self.check),
            ("write_new", self.write),
            ("record", self.record),
            ("read_json", self.read_json),
            ("mkdir_new", self.mkdir),
            ("metadata", self.metadata),
            ("sha", lambda path: H.digest(self.read(path))),
            ("protected", lambda: copy.deepcopy(self.protected_value)),
            ("candidate", lambda _pin: {}),
            ("old_source", lambda: None),
            ("fsync_directory", lambda _path: None),
        ):
            stack.enter_context(patch.object(H, name, function))
        stack.enter_context(
            patch.object(H.Path, "iterdir", lambda path: self.iterdir(path))
        )
        stack.enter_context(patch.object(H.os, "replace", self.replace))
        stack.enter_context(patch.object(H.os, "chown", self.chown, create=True))
        stack.enter_context(
            patch.object(self.coordinator, "disk_gate", return_value={"passed": True})
        )

    def run_through(self, phase):
        results = []
        for name in H.PHASES[: H.PHASES.index(phase) + 1]:
            result = self.coordinator.run(name)
            results.append(result)
            if not result["passed"]:
                break
        return results


class HostModelTests(unittest.TestCase):
    def test_all_eight_phases_command_scope_and_exact_original_bytes(self):
        model = Model()
        original = model.files[H.CONFIG].copy()
        data = copy.deepcopy({name: model.rows[name] for name in H.DATA})
        with ExitStack() as stack:
            model.patch(stack)
            results = model.run_through("start")
        self.assertEqual([row["phase"] for row in results], list(H.PHASES))
        self.assertTrue(all(row["passed"] for row in results), results)
        self.assertEqual(model.files[H.CONFIG], original)
        self.assertEqual({name: model.rows[name] for name in H.DATA}, data)
        self.assertEqual(model.native_called, ["before", "apply", "verify", "cache"])
        native = [
            args
            for args, _payload, _timeout in model.commands
            if H.CONTAINER_ROOT + "/incoming-r1/tool_cost_release_native.py" in args
        ]
        self.assertEqual(len(native), 4)
        self.assertIn(H.TOOLS[0], native[0])
        self.assertTrue(all(H.TOOLS[1] in args for args in native[1:]))
        self.assertTrue(
            all("--no-deps" in args and "--pull" in args for args in native)
        )
        self.assertTrue(all("--before-sha256" in args for args in native[1:]))
        self.assertTrue(all("--before-sha256" not in args for args in native[:1]))
        stop = [args for args, _, _ in model.commands if "stop" in args]
        start = [args for args, _, _ in model.commands if "up" in args]
        self.assertEqual(len(stop), 1)
        self.assertEqual(len(start), 1)
        self.assertEqual(stop[0][-5:], list(H.RUNTIME))
        self.assertEqual(start[0][-5:], list(H.RUNTIME))
        self.assertTrue(
            all(
                flag in start[0]
                for flag in ("--no-deps", "--no-build", "--pull", "never")
            )
        )
        self.assertFalse(results[-1]["gui_tested"])
        self.assertFalse(results[-1]["all_modules_formally_accepted"])

    def test_missing_predecessor_claims_once_without_commands(self):
        model = Model()
        model.add_dir(H.HOST_CONTROL)
        with ExitStack() as stack:
            model.patch(stack)
            result = model.coordinator.run("apply")
            self.assertFalse(result["passed"])
            self.assertIn(H.HOST_CONTROL / "apply/started.json", model.files)
            with self.assertRaises(FileExistsError):
                model.coordinator.run("apply")
        self.assertEqual(model.commands, [])

    def test_changed_caller_pin_invalidates_entire_phase_identity(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            self.assertTrue(model.coordinator.run("prepare")["passed"])
            changed = H.Coordinator("b" * 64, model.runner)
            result = changed.run("quiesce")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "previous_host_phase_required")

    def test_receipt_chain_tampering_blocks_next_phase(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            self.assertTrue(
                all(value["passed"] for value in model.run_through("quiesce"))
            )
            path = H.HOST_CONTROL / "prepare/completed.json"
            value = model.read_json(path)
            value["disk"] = {"changed": True}
            model.files[path]["data"] = H.canonical(value)
            result = model.coordinator.run("before")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "host_phase_chain_changed")
        self.assertEqual(model.native_called, [])

    def test_unknown_stop_is_not_replayed_and_maintenance_is_preserved(self):
        model = Model()
        model.fail_label = lambda argv, _payload: "stop" in argv
        with ExitStack() as stack:
            model.patch(stack)
            results = model.run_through("quiesce")
            self.assertFalse(results[-1]["passed"])
            self.assertEqual(results[-1]["reason"], "command_timeout_unknown_result")
            values = json.loads(model.read(H.CONFIG))
            self.assertTrue(all(values[flag] == 1 for flag in H.FLAGS))
            with self.assertRaises(FileExistsError):
                model.coordinator.run("quiesce")
        self.assertEqual(sum("stop" in argv for argv, _, _ in model.commands), 1)
        self.assertEqual(model.native_called, [])

    def test_unknown_native_apply_is_not_replayed_or_started(self):
        model = Model()
        model.fail_label = lambda argv, _payload: "run" in argv and "apply" in argv
        with ExitStack() as stack:
            model.patch(stack)
            results = model.run_through("apply")
            self.assertFalse(results[-1]["passed"])
            with self.assertRaises(FileExistsError):
                model.coordinator.run("apply")
        self.assertEqual(model.native_called, ["before"])
        self.assertEqual(
            sum("run" in argv and "apply" in argv for argv, _, _ in model.commands), 1
        )
        self.assertFalse(any("up" in argv for argv, _, _ in model.commands))

    def test_start_health_failure_leaves_runtime_and_config_without_rollback(self):
        model = Model()
        original = model.files[H.CONFIG].copy()
        model.backend_ping = False
        with ExitStack() as stack:
            model.patch(stack)
            results = model.run_through("start")
            self.assertFalse(results[-1]["passed"])
            self.assertEqual(results[-1]["reason"], "independent_backend_http_health")
            with self.assertRaises(FileExistsError):
                model.coordinator.run("start")
        self.assertEqual(model.files[H.CONFIG], original)
        self.assertTrue(
            all(model.rows[name]["status"] == "running" for name in H.RUNTIME)
        )
        self.assertEqual(sum("up" in argv for argv, _, _ in model.commands), 1)
        self.assertEqual(sum("stop" in argv for argv, _, _ in model.commands), 1)
        self.assertFalse(results[-1]["automatic_rollback"])

    def test_data_recreation_after_start_fails_independent_identity_gate(self):
        model = Model()
        model.tamper_data = True
        with ExitStack() as stack:
            model.patch(stack)
            result = model.run_through("start")[-1]
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "data_container_identity_changed")

    def test_start_readiness_samples_until_ready_without_replaying_mutations(self):
        model = Model()
        model.readiness_results = [False, False, True]
        with ExitStack() as stack:
            model.patch(stack)
            result = model.run_through("start")[-1]
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["readiness"]["attempts"], 3)
        self.assertEqual(result["readiness"]["elapsed_seconds"], 2)
        self.assertEqual(sum("up" in argv for argv, _, _ in model.commands), 1)
        self.assertEqual(sum("stop" in argv for argv, _, _ in model.commands), 1)
        self.assertEqual(model.native_called, ["before", "apply", "verify", "cache"])

    def test_start_readiness_has_sixty_second_bound_without_rollback(self):
        model = Model()
        model.readiness_results = [False]
        with ExitStack() as stack:
            model.patch(stack)
            result = model.run_through("start")[-1]
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "readiness_timeout_unknown_result")
        self.assertEqual(model.now, 60)
        self.assertEqual(sum("up" in argv for argv, _, _ in model.commands), 1)
        self.assertTrue(
            all(
                timeout <= 3
                for argv, payload, timeout in model.commands
                if payload is not None and b"ready = False" in payload
            )
        )

    def test_readonly_probe_timeout_may_wait_but_never_replays_start(self):
        model = Model()
        calls = []

        def first_probe_timeout(_argv, payload):
            if payload is None or b"ready = False" not in payload:
                return False
            calls.append(True)
            return len(calls) == 1

        model.fail_label = first_probe_timeout
        with ExitStack() as stack:
            model.patch(stack)
            result = model.run_through("start")[-1]
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["readiness"]["attempts"], 2)
        self.assertEqual(sum("up" in argv for argv, _, _ in model.commands), 1)

    def test_changed_protected_salary_backup_prevents_quiesce(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            self.assertTrue(model.coordinator.run("prepare")["passed"])
            model.protected_value["existing-salary-backup"]["sha256"] = "b" * 64
            result = model.coordinator.run("quiesce")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "protected_files_changed")
        self.assertFalse(any("stop" in argv for argv, _, _ in model.commands))

    def test_original_config_drift_is_never_overwritten(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            self.assertTrue(model.coordinator.run("prepare")["passed"])
            changed = b'{"private": "foreign change"}'
            model.files[H.CONFIG]["data"] = changed
            result = model.coordinator.run("quiesce")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "site_config_changed_after_prepare")
        self.assertEqual(model.files[H.CONFIG]["data"], changed)

    def test_maintenance_config_drift_blocks_native(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            self.assertTrue(all(row["passed"] for row in model.run_through("quiesce")))
            model.files[H.CONFIG]["data"] += b" "
            result = model.coordinator.run("before")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "maintenance_config_drift")
        self.assertEqual(model.native_called, [])

    def test_additional_running_writer_prevents_prepare(self):
        model = Model()
        model.extra_writer = "seed"
        with ExitStack() as stack:
            model.patch(stack)
            result = model.coordinator.run("prepare")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "unexpected_running_project_writer")

    def test_tag_pointing_at_other_image_prevents_prepare(self):
        model = Model()
        model.image_id = "sha256:" + "b" * 64
        with ExitStack() as stack:
            model.patch(stack)
            result = model.coordinator.run("prepare")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "fixed_image_tag_id")

    def test_actual_runtime_uid_must_match_before_stopping(self):
        model = Model()
        model.foreign_uid = True
        with ExitStack() as stack:
            model.patch(stack)
            result = model.coordinator.run("prepare")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "independent_runtime_pin_gate")
        self.assertFalse(any("stop" in argv for argv, _, _ in model.commands))

    def test_native_completed_tampering_prevents_apply(self):
        for field, value in (
            ("passed", False),
            ("total_objects", 931),
            ("identity", {"commit": H.NEW}),
        ):
            with self.subTest(field=field):
                model = Model()
                with ExitStack() as stack:
                    model.patch(stack)
                    self.assertTrue(
                        all(row["passed"] for row in model.run_through("before"))
                    )
                    path = H.AUDIT_DIRECTORY / "before-completed.json"
                    receipt = model.read_json(path, owner=1000)
                    receipt[field] = value
                    model.files[path]["data"] = H.canonical(receipt)
                    result = model.coordinator.run("apply")
                self.assertFalse(result["passed"])
                self.assertEqual(result["reason"], "native_completed_identity")
                self.assertEqual(model.native_called, ["before"])

    def test_backup_changed_bytes_extra_part_or_unknown_result_blocks_apply(self):
        for mutation in (
            "bytes",
            "extra",
            "locked",
            "before_sha",
            "native_result",
            "forged_parts",
        ):
            with self.subTest(mutation=mutation):
                model = Model()
                with ExitStack() as stack:
                    model.patch(stack)
                    self.assertTrue(
                        all(row["passed"] for row in model.run_through("before"))
                    )
                    directory = H.PRIVATE / "native-backup"
                    path = H.AUDIT_DIRECTORY / "backup-completed.json"
                    receipt = model.read_json(path, owner=1000)
                    if mutation == "bytes":
                        model.files[directory / "database"]["data"] += b"changed"
                    elif mutation == "extra":
                        model.add_file(directory / "foreign-part", b"foreign", 1000)
                    elif mutation == "locked":
                        receipt["myisam_locked_through_validation"] = False
                        model.files[path]["data"] = H.canonical(receipt)
                    elif mutation == "before_sha":
                        receipt["before_sha256"] = "b" * 64
                        model.files[path]["data"] = H.canonical(receipt)
                    elif mutation == "forged_parts":
                        item = receipt["parts"][0]
                        data = b"forged backup"
                        item.update(bytes=len(data), sha256=H.digest(data))
                        model.files[directory / item["name"]]["data"] = data
                        model.files[path]["data"] = H.canonical(receipt)
                    else:
                        model.files[H.AUDIT_DIRECTORY / "before-result.json"][
                            "data"
                        ] = b"{}\n"
                    result = model.coordinator.run("apply")
                self.assertFalse(result["passed"])
                self.assertEqual(model.native_called, ["before"])


class HostScopeTests(unittest.TestCase):
    def test_all_git_commands_use_existing_fixed_image_offline_and_exact_readonly_source(
        self,
    ):
        coordinator = H.Coordinator(DIGEST)
        for root, commit in (
            (H.OLD_RELEASE / "app", H.OLD),
            (H.RELEASE / "app", H.NEW),
        ):
            with (
                self.subTest(root=root),
                patch.object(
                    coordinator,
                    "command",
                    side_effect=[(commit + "\n").encode(), b"", b"", b""],
                ) as command,
            ):
                coordinator.git_source("git-probe", root, commit)
                self.assertEqual(command.call_count, 4)
                expected = [
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
                    H.BASE_TAG,
                    "-C",
                    "/source",
                ]
                for call in command.call_args_list:
                    argv = call.args[1]
                    self.assertEqual(argv[: len(expected)], expected)
                    self.assertEqual(argv.count("--mount"), 1)
                    self.assertEqual(argv.count(H.BASE_TAG), 1)
                    self.assertNotIn("--global", argv)
                    self.assertNotIn("safe.directory", " ".join(argv))
                    self.assertNotIn("*", " ".join(argv))
                self.assertEqual(
                    [call.args[1][len(expected) :] for call in command.call_args_list],
                    [
                        ["rev-parse", "HEAD"],
                        ["diff", "--exit-code", "HEAD", "--"],
                        ["ls-files", "--others", "--exclude-standard"],
                        ["fsck", "--full", "--strict"],
                    ],
                )

    def test_git_source_rejects_unreviewed_paths_or_mismatched_commit_before_commands(
        self,
    ):
        coordinator = H.Coordinator(DIGEST)
        for root, commit in (
            (Path("/foreign/app"), H.NEW),
            (H.RELEASE, H.NEW),
            (H.OLD_RELEASE / "app", H.NEW),
            (H.RELEASE / "app", H.OLD),
        ):
            with (
                self.subTest(root=root, commit=commit),
                patch.object(coordinator, "command") as command,
            ):
                with self.assertRaisesRegex(H.HostError, "fixed_git_source_scope"):
                    coordinator.git_source("git-probe", root, commit)
                command.assert_not_called()

    def test_full_inventory_assets_hashes_modes_and_unlisted_files(self):
        manifest, observed = inventory_fixture()
        self.assertEqual(
            H.verify_candidate_inventory(manifest, observed),
            {"source_files": 572, "hook_count": 33},
        )
        for mutation in (
            "extra",
            "bytes",
            "mode",
            "directory",
            "hook",
            "duplicate",
            "path",
        ):
            with self.subTest(mutation=mutation):
                changed_manifest, changed_observed = (
                    copy.deepcopy(manifest),
                    copy.deepcopy(observed),
                )
                name = changed_manifest["files"][0]["path"]
                if mutation == "extra":
                    changed_observed["app/private-password.txt"] = {
                        "kind": "file",
                        "mode": 0o644,
                        "content": b"private",
                    }
                elif mutation == "bytes":
                    changed_observed[name]["content"] = b"tampered"
                elif mutation == "mode":
                    changed_observed[name]["mode"] = 0o777
                elif mutation == "directory":
                    changed_observed["app"]["mode"] = 0o777
                elif mutation == "hook":
                    changed_manifest["hooks"][0] = (
                        changed_manifest["hooks"][0][:-16] + "b" * 16
                    )
                elif mutation == "duplicate":
                    changed_manifest["files"][-1] = copy.deepcopy(
                        changed_manifest["files"][0]
                    )
                else:
                    changed_manifest["files"][0]["path"] = "app/../foreign"
                with self.assertRaises(H.HostError):
                    H.verify_candidate_inventory(changed_manifest, changed_observed)

    def test_python36_syntax_and_cli_has_only_fixed_phase_and_reviewed_host_pin(self):
        source = (ROOT / "deploy/erp-test/tool_cost_release_host.py").read_bytes()
        tree = ast.parse(source, feature_version=(3, 6))
        flags = [
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ]
        self.assertEqual(flags, ["phase", "--host-sha256"])
        self.assertEqual(
            H.PHASES,
            (
                "prepare",
                "quiesce",
                "before",
                "apply",
                "verify",
                "cache",
                "restore",
                "start",
            ),
        )

    def test_reviewed_host_sha_cannot_be_missing_or_invalid(self):
        for value in (None, "", "g" * 64, "a" * 63, 12):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(H.HostError, "reviewed_host_pin"),
            ):
                H.Coordinator(value)

    def test_real_frozen_native_pin_is_literal_eight_runtime_files(self):
        with patch.object(H, "read_regular", return_value=NATIVE_BYTES):
            pins = H.native_pins()
        self.assertEqual(len(pins), 8)
        self.assertEqual(hashlib.sha256(NATIVE_BYTES).hexdigest(), H.NATIVE_SHA)
        for pin in pins.values():
            self.assertRegex(pin, r"^[0-9a-f]{64}$")

    def test_compose_preserves_existing_services_and_new_scope(self):
        old, new = compose_fixture()
        H.validate_compose(old, new)
        for mutation in (
            "db",
            "certificate",
            "source",
            "rw",
            "tool_privileged",
            "tool_env",
            "tool_private_ro",
            "tool_auto_create",
            "tool_extra_mount",
            "network",
            "top",
        ):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(new)
                if mutation == "db":
                    changed["services"]["db"]["command"] = ["changed"]
                elif mutation == "certificate":
                    changed["services"]["frontend"]["volumes"].append(
                        {"type": "bind", "source": "/foreign", "target": "/cert"}
                    )
                elif mutation in {"source", "rw"}:
                    mount = changed["services"]["backend"]["volumes"][0]
                    mount["source" if mutation == "source" else "read_only"] = (
                        "/foreign" if mutation == "source" else False
                    )
                elif mutation == "tool_privileged":
                    changed["services"][H.TOOLS[0]]["privileged"] = True
                elif mutation == "tool_env":
                    changed["services"][H.TOOLS[0]]["environment"]["FOREIGN"] = (
                        "private"
                    )
                elif mutation == "tool_private_ro":
                    changed["services"][H.TOOLS[0]]["volumes"][4]["read_only"] = True
                elif mutation == "tool_auto_create":
                    changed["services"][H.TOOLS[0]]["volumes"][2]["bind"][
                        "create_host_path"
                    ] = True
                elif mutation == "tool_extra_mount":
                    changed["services"][H.TOOLS[0]]["volumes"].append(
                        H.bind("/foreign", "/foreign", False)
                    )
                elif mutation == "network":
                    changed["services"][H.TOOLS[0]]["networks"]["foreign"] = None
                else:
                    changed["volumes"]["foreign"] = {}
                with self.assertRaises(H.HostError):
                    H.validate_compose(old, changed)

    def test_tools_have_exact_readonly_inputs_and_only_named_rw_binds(self):
        for name, tool in H.overlay_document()["services"].items():
            if name in H.RUNTIME:
                continue
            mounts = H.mount_map(tool)
            self.assertTrue(mounts[H.CONTAINER_ROOT]["read_only"])
            self.assertTrue(mounts[H.APP_TARGET]["read_only"])
            rw = {
                target
                for target, mount in mounts.items()
                if mount["type"] == "bind" and not mount["read_only"]
            }
            self.assertEqual(
                rw,
                {
                    H.CONTAINER_ROOT + "/tool-cost-control-r1",
                    H.CONTAINER_ROOT + "/tool-cost-private-r1",
                },
            )
            self.assertEqual(tool["user"], "1000:1000")
            self.assertEqual(tool["pull_policy"], "never")

    def test_runtime_mount_and_restart_oom_gates(self):
        baseline = container_fixture()
        H.source_mounts(baseline, H.OLD_RELEASE / "app", "running")
        for field, value in (
            ("image", "foreign"),
            ("status", "exited"),
            ("oom_killed", True),
            ("restart_count", 1),
        ):
            with self.subTest(field=field):
                rows = copy.deepcopy(baseline)
                rows["backend"][field] = value
                with self.assertRaisesRegex(
                    H.HostError, "runtime_state_image_restart_oom"
                ):
                    H.source_mounts(rows, H.OLD_RELEASE / "app", "running")
        for field, value in (("Source", "/foreign"), ("RW", True), ("Type", "volume")):
            rows = copy.deepcopy(baseline)
            rows["backend"]["mounts"][0][field] = value
            with self.assertRaisesRegex(H.HostError, "runtime_exact_readonly_source"):
                H.source_mounts(rows, H.OLD_RELEASE / "app", "running")

    def test_data_identity_checks_all_columns_including_mounts(self):
        baseline = container_fixture()
        for field, value in (
            ("id", "foreign"),
            ("started_at", "new start"),
            ("image", "foreign"),
            ("mounts", []),
            ("health", "unhealthy"),
            ("restart_count", 1),
            ("oom_killed", True),
        ):
            with self.subTest(field=field):
                rows = copy.deepcopy(baseline)
                rows["db"][field] = value
                with self.assertRaises(H.HostError):
                    H.data_gate(rows, baseline)

    def test_secret_command_output_is_only_retained_as_hashes(self):
        receipts = []
        coordinator = H.Coordinator(
            DIGEST, lambda _a, _p, _t: (0, b"PRIVATE-CREDENTIAL", b"PRIVATE-STDERR")
        )
        coordinator.phase_directory = Path("model-phase")
        with patch.object(
            H, "record", side_effect=lambda path, value: receipts.append((path, value))
        ):
            self.assertEqual(
                coordinator.command("probe", ["read-only"]), b"PRIVATE-CREDENTIAL"
            )
        self.assertNotIn("PRIVATE", json.dumps([value for _path, value in receipts]))
        self.assertEqual(
            receipts[-1][1]["stdout_sha256"], H.digest(b"PRIVATE-CREDENTIAL")
        )

    def test_subprocess_binary_python36_argument_and_no_shell(self):
        process = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with patch.object(H.subprocess, "run", return_value=process) as run:
            self.assertEqual(
                H.subprocess_runner(["readonly"], b"data", 30), (0, b"", b"")
            )
        self.assertIs(run.call_args.kwargs["universal_newlines"], False)
        self.assertNotIn("text", run.call_args.kwargs)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_disk_gate_rejects_insufficient_actual_free_space(self):
        coordinator = H.Coordinator(DIGEST)
        with (
            patch.object(H, "check_path"),
            patch.object(H.Path, "rglob", return_value=[]),
            patch.object(H.shutil, "disk_usage", return_value=SimpleNamespace(free=1)),
        ):
            with self.assertRaisesRegex(
                H.HostError, "insufficient_release_backup_disk"
            ):
                coordinator.disk_gate(1000)

    def test_generic_failure_does_not_echo_private_exception(self):
        model = Model()
        with ExitStack() as stack:
            model.patch(stack)
            stack.enter_context(
                patch.object(
                    H, "candidate", side_effect=ValueError("PRIVATE-CREDENTIAL")
                )
            )
            result = model.coordinator.run("prepare")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "host_phase_failed")
        self.assertNotIn("PRIVATE", json.dumps(result))


@unittest.skipUnless(
    os.name == "posix", "Production filesystem permissions require Linux"
)
class HostFilesystemTests(unittest.TestCase):
    def test_exclusive_write_permission_identity_and_symlink_rejection(self):
        uid = os.geteuid()
        if uid != os.getegid():
            self.skipTest("owner uid and gid must match this fixture")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "receipt.json"
            H.write_new(file, b"original", owner=uid)
            self.assertEqual(H.read_regular(file, owner=uid, mode=0o600), b"original")
            with self.assertRaises(FileExistsError):
                H.write_new(file, b"replayed", owner=uid)
            self.assertEqual(file.read_bytes(), b"original")
            link = root / "link.json"
            link.symlink_to(file)
            with self.assertRaisesRegex(H.HostError, "path_symlink"):
                H.read_regular(link)
            directory_link = root / "directory-link"
            directory_link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(H.HostError, "path_symlink"):
                H.read_regular(directory_link / file.name)

    def test_actual_owner_mode_and_sha_are_required(self):
        uid = os.geteuid()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input"
            path.write_bytes(b"bytes")
            path.chmod(0o644)
            with self.assertRaisesRegex(H.HostError, "path_owner"):
                H.read_regular(path, owner=uid + 1)
            with self.assertRaisesRegex(H.HostError, "path_mode"):
                H.read_regular(path, mode=0o600)
            with self.assertRaisesRegex(H.HostError, "file_pin_changed"):
                H.read_regular(path, expected_sha=DIGEST)
            self.assertEqual(H.sha(path), hashlib.sha256(b"bytes").hexdigest())


if __name__ == "__main__":
    unittest.main()
