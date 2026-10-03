import ast
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "salary_host_test", ROOT / "deploy/erp-test/salary_release_host.py"
)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)
DIGEST = "a" * 64
PACKAGE = {"source_files": 553, "archive_sha256": DIGEST, "archive_bytes": 3362045}


def container_fixture():
    return {
        name: {
            "id": name + "-id",
            "image": host.BASE_ID,
            "started_at": "fixed-start",
            "status": "running",
            "restart_count": 0,
            "oom_killed": False,
            "health": "healthy" if name == "db" else None,
            "mounts": [
                {
                    "Destination": host.APP_TARGET,
                    "Type": "bind",
                    "Source": str(host.OLD_RELEASE / "app"),
                    "RW": False,
                }
            ],
        }
        for name in host.RUNTIME + host.DATA
    }


class TestSalaryHostFailureGates(unittest.TestCase):
    def setUp(self):
        self.package_patch = patch.object(host, "PACKAGE_PINS", deepcopy(PACKAGE))
        self.public_patch = patch.object(
            host, "PINS", {name: DIGEST for name in host.PUBLIC}
        )
        self.package_patch.start()
        self.public_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.addCleanup(self.public_patch.stop)

    def test_python36_syntax_and_exact_phase_cli(self):
        ast.parse(
            (ROOT / "deploy/erp-test/salary_release_host.py").read_bytes(),
            feature_version=(3, 6),
        )
        self.assertEqual(
            host.PHASES, ("prepare", "quiesce", "metadata", "verify", "start")
        )

    def test_container_projection_and_restart_identity(self):
        rows = []
        for name, row in container_fixture().items():
            rows.append(
                {
                    "Id": row["id"],
                    "Image": row["image"],
                    "Service": name,
                    "RestartCount": 0,
                    "Mounts": row["mounts"],
                    "State": {
                        "StartedAt": row["started_at"],
                        "Status": row["status"],
                        "OOMKilled": False,
                        "Health": {"Status": row["health"]},
                    },
                }
            )
        stdout = "\n".join((json.dumps(row) for row in rows)).encode()
        with patch.object(host, "command", return_value=stdout) as command:
            result = host.containers("probe")
        self.assertEqual(result, container_fixture())
        argv = command.call_args.args[1]
        self.assertIn("--format", argv)
        self.assertNotIn(".Config.Env", argv[3])
        self.assertIn(".RestartCount", argv[3])
        self.assertEqual(command.call_args.kwargs["timeout"], 30)

    def test_duplicate_container_fails_closed(self):
        row = {
            "Id": "x",
            "Image": "x",
            "Service": "db",
            "RestartCount": 0,
            "Mounts": [],
            "State": {"StartedAt": "x", "Status": "running", "OOMKilled": False},
        }
        with patch.object(
            host,
            "command",
            return_value=(json.dumps(row) + "\n" + json.dumps(row)).encode(),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_container"):
                host.containers("probe")

    def test_data_identity_includes_id_start_restart_image_mounts(self):
        baseline = container_fixture()
        for key, value in (
            ("id", "changed"),
            ("started_at", "changed"),
            ("restart_count", 1),
            ("image", "changed"),
            ("mounts", []),
        ):
            with self.subTest(key=key):
                rows = deepcopy(baseline)
                rows["db"][key] = value
                with patch.object(host, "read_json", return_value=baseline):
                    with self.assertRaisesRegex(RuntimeError, "data_container_changed"):
                        host.data_unchanged(rows)

    def test_source_mount_requires_exact_readonly_and_image(self):
        baseline = container_fixture()
        host.source_mounts(baseline, host.OLD_RELEASE / "app")
        for key, value in (
            ("RW", True),
            ("Source", str(host.RELEASE / "app")),
            ("Type", "volume"),
        ):
            with self.subTest(key=key):
                rows = deepcopy(baseline)
                rows["backend"]["mounts"][0][key] = value
                with self.assertRaisesRegex(RuntimeError, "source_mount_not_exact_ro"):
                    host.source_mounts(rows, host.OLD_RELEASE / "app")

    def test_sensitive_compose_output_is_only_hashed(self):
        with (
            patch.object(host, "record") as record,
            patch.object(host, "write_new") as write,
        ):
            with patch.object(host, "PHASE_DIR", Path("mock-phase")):
                host.save_output(
                    "compose", b"PRIVATE-CREDENTIAL", b"PRIVATE-STDERR", True
                )
        write.assert_not_called()
        value = record.call_args.args[1]
        self.assertEqual(
            value["stdout_sha256"], hashlib.sha256(b"PRIVATE-CREDENTIAL").hexdigest()
        )
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_timeout_retains_unknown_receipt_no_retry(self):
        error = subprocess.TimeoutExpired(
            ["docker"], 30, output=b"partial", stderr=b"error"
        )
        with (
            patch.object(host, "PHASE_DIR", Path("mock-phase")),
            patch.object(host, "record") as record,
            patch.object(host, "save_output") as save,
            patch.object(host.subprocess, "run", side_effect=error) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "command_timeout_unknown_result"):
                host.command("single", ["docker"], timeout=30)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            record.call_args.args[1], {"argv": ["docker"], "stdin_supplied": False}
        )
        save.assert_called_once_with("single", b"partial", b"error", False)

    def common_phase_mocks(self, stack):
        records = []
        values = {"native": {"proof_sha256": DIGEST}, "before_sha256": DIGEST}
        for name, value in (
            ("candidate", {"source_files": PACKAGE["source_files"]}),
            ("old_source", None),
            ("manager_gate", "26.1.3"),
            ("success", values),
            ("protected", {}),
            ("read_json", {}),
            ("sha", DIGEST),
            ("backup_proof", []),
            ("proof_sha", None),
            ("stopped_gate", None),
            ("incoming_ready", None),
            ("restore_config", None),
            ("maintenance", None),
            ("source_mounts", None),
            ("data_unchanged", None),
            ("containers", container_fixture()),
        ):
            stack.enter_context(patch.object(host, name, return_value=value))
        stack.enter_context(patch.object(host, "HELPER_PINS", {}))
        stack.enter_context(patch.object(host, "PHASE_DIR", Path("mock-phase")))
        stack.enter_context(
            patch.object(
                host,
                "record",
                side_effect=lambda path, value: records.append(
                    (str(path), deepcopy(value))
                ),
            )
        )

        def receipt(path):
            return {
                "overlay-sha.json": {"sha256": DIGEST},
                "public-pins.json": host.PINS,
                "package-pins.json": host.PACKAGE_PINS,
            }.get(path.name, {})

        host.read_json.side_effect = receipt
        return records

    def test_every_phase_checks_candidate_before_action(self):
        for phase_name in host.PHASES:
            with self.subTest(phase=phase_name), ExitStack() as stack:
                records = self.common_phase_mocks(stack)
                host.candidate.side_effect = RuntimeError("candidate_changed")
                native = stack.enter_context(patch.object(host, "native"))
                stack.enter_context(redirect_stdout(io.StringIO()))
                self.assertEqual(host.phase(phase_name), 1)
                native.assert_not_called()
                host.manager_gate.assert_not_called()
                self.assertTrue(
                    any((name.endswith("failed.json") for name, value in records))
                )

    def test_stopped_gate_failure_blocks_apply(self):
        with ExitStack() as stack:
            records = self.common_phase_mocks(stack)
            host.stopped_gate.side_effect = RuntimeError("applications_not_stopped")
            native = stack.enter_context(patch.object(host, "native"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(host.phase("metadata"), 1)
            native.assert_not_called()
            failure = next(
                (value for name, value in records if name.endswith("failed.json"))
            )
            self.assertFalse(failure["automatic_retry"])
            self.assertFalse(failure["automatic_rollback"])

    def test_changed_package_after_prepare_blocks_native_and_config_mutation(self):
        with ExitStack() as stack:
            self.common_phase_mocks(stack)
            prepared = dict(PACKAGE, archive_bytes=PACKAGE["archive_bytes"] + 1)
            original_reader = host.read_json.side_effect
            host.read_json.side_effect = lambda path: (
                prepared if path.name == "package-pins.json" else original_reader(path)
            )
            native = stack.enter_context(patch.object(host, "native"))
            command = stack.enter_context(patch.object(host, "command"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(host.phase("quiesce"), 1)
            native.assert_not_called()
            command.assert_not_called()
            host.maintenance.assert_not_called()

    def test_changed_baseline_blocks_backup_and_release_mutations(self):
        with ExitStack() as stack:
            self.common_phase_mocks(stack)
            host.old_source.side_effect = RuntimeError(
                "baseline_source_content_changed"
            )
            native = stack.enter_context(patch.object(host, "native"))
            command = stack.enter_context(patch.object(host, "command"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(host.phase("quiesce"), 1)
            native.assert_not_called()
            command.assert_not_called()
            host.maintenance.assert_not_called()

    def test_start_requires_cache_fresh_proof_then_never_build_or_pull(self):
        with ExitStack() as stack:
            self.common_phase_mocks(stack)
            native = stack.enter_context(
                patch.object(host, "native", return_value={"passed": True})
            )
            command = stack.enter_context(
                patch.object(
                    host, "command", side_effect=[b"started", host.NEW.encode()]
                )
            )
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(host.phase("start"), 0)
            native.assert_called_once_with("cache")
            argv = command.call_args_list[0].args[1]
            self.assertIn("--no-build", argv)
            self.assertIn("--no-deps", argv)
            self.assertEqual(argv[argv.index("--pull") + 1], "never")
            self.assertEqual(argv[-5:], list(host.RUNTIME))
            host.restore_config.assert_called_once()

    def test_unknown_start_result_only_reenters_config_no_repeat_up(self):
        with ExitStack() as stack:
            records = self.common_phase_mocks(stack)
            native = stack.enter_context(
                patch.object(host, "native", return_value={"passed": True})
            )
            command = stack.enter_context(
                patch.object(
                    host,
                    "command",
                    side_effect=RuntimeError(
                        "command_timeout_unknown_result:start-five"
                    ),
                )
            )
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(host.phase("start"), 1)
            self.assertEqual(command.call_count, 1)
            native.assert_called_once_with("cache")
            host.maintenance.assert_called_once()
            failure = next(
                (value for name, value in records if name.endswith("failed.json"))
            )
            self.assertTrue(failure["outcome_unknown"])
            self.assertTrue(failure["failed_phase_runtime_stop_not_inferred"])
