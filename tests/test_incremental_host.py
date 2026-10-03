"""Host phase simulations. Docker, Frappe, root paths and subprocesses are mocked."""

import ast
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "incremental_host", ROOT / "deploy/erp-test/incremental_host.py"
)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)
DIGEST = "a" * 64
PACKAGE = {"source_files": 532, "archive_sha256": DIGEST, "archive_bytes": 3099538}


def release_fixture():
    manifest = {
        "commit": host.NEW,
        "previous_commit": host.OLD,
        "base_image_tag": host.BASE_TAG,
        "base_image_id": host.BASE_ID,
        "release_kind": "read_only_source_bind_mount",
        "runtime_services": list(host.RUNTIME),
        "pyproject_unchanged_from_base": True,
        "image_build": False,
        "full_migrate": False,
        "ddl_required": True,
        "new_empty_doctypes": list(host.NEW_DOCTYPES),
        "metadata_only_update": [],
        "verified_doctype_js": deepcopy(host.DOCTYPE_JS),
        **PACKAGE,
    }
    changes = {
        "old_commit": host.OLD,
        "new_commit": host.NEW,
        "doctype": None,
        "changes": {},
    }
    plan = {
        "old_commit": host.OLD,
        "new_commit": host.NEW,
        "source_files": PACKAGE["source_files"],
        "new_table_layout": {
            "tab" + name: {"schema_sha256": DIGEST, "indexes_sha256": DIGEST}
            for name in host.NEW_DOCTYPES
        },
    }
    return manifest, changes, plan


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


class HostTests(unittest.TestCase):
    def setUp(self):
        self.package_patch = patch.object(host, "PACKAGE_PINS", deepcopy(PACKAGE))
        self.public_patch = patch.object(
            host, "PINS", {name: DIGEST for name in host.PUBLIC}
        )
        self.package_patch.start()
        self.public_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.addCleanup(self.public_patch.stop)

    def test_release_scope_accepts_reviewed_package_without_widening_targets(self):
        host.release_scope(*release_fixture())
        self.assertEqual(host.OLD, "4d71c354d3f3f281d21d3d16b496bb6e5434e655")
        self.assertEqual(host.NEW, "18e5835cfda4171ec691d5e701c8f42018b5448c")
        self.assertEqual(host.RELEASE.name, "18e5835-source-r1")
        self.assertEqual(host.OLD_OVERLAY.name, "compose.source.r2.yaml")
        self.assertEqual(host.OVERLAY.name, "newcompose.source.r2.yaml")
        self.assertEqual(host.CONTROL.name, "incremental-control-r2")
        self.assertEqual(host.PRIVATE.name, "private-backup-r2")
        self.assertEqual(host.NATIVE.parent.name, "incoming-r2")
        self.assertEqual(host.AUDIT.parent.name, "incoming-r2")

    def test_release_scope_rejects_existing_metadata_and_any_other_ddl(self):
        for target, key, value in (
            (0, "commit", "b" * 40),
            (0, "source_files", 505),
            (0, "full_migrate", True),
            (0, "image_build", True),
            (0, "new_empty_doctypes", ["Jifeng Customer Profile"]),
            (0, "metadata_only_update", ["customer.label"]),
            (1, "doctype", "Customer"),
            (1, "changes", {"customer": {"read_only": {"old": 1, "new": 0}}}),
            (2, "source_files", 533),
            (2, "new_commit", "b" * 40),
            (2, "path", "/opt/another-release"),
            (2, "new_table_layout", {"tabCustomer": {}}),
        ):
            with self.subTest(target=target, key=key):
                values = release_fixture()
                values[target][key] = value
                with self.assertRaises(RuntimeError):
                    host.release_scope(*values)

    def test_layout_pins_and_package_measurements_are_required(self):
        for value in (None, {}, {"schema_sha256": DIGEST, "indexes_sha256": "bad"}):
            with self.subTest(layout=value):
                values = release_fixture()
                values[2]["new_table_layout"]["tabJifeng Order Operation"] = value
                with self.assertRaisesRegex(RuntimeError, "native_layout_pins"):
                    host.release_scope(*values)
        for key, value in (
            ("source_files", True),
            ("source_files", 505),
            ("archive_bytes", 0),
            ("archive_sha256", "not-a-hash"),
        ):
            with self.subTest(key=key, value=value):
                pins = dict(PACKAGE, **{key: value})
                with patch.object(host, "PACKAGE_PINS", pins):
                    with self.assertRaisesRegex(RuntimeError, "package_pins_required"):
                        host.package_pins_valid()

    def test_prior_receipt_requires_same_package_and_plan(self):
        receipt = {
            "passed": True,
            "phase": "prepare",
            "commit": host.NEW,
            "package_pins": deepcopy(PACKAGE),
            "plan_sha256": DIGEST,
        }
        with patch.object(host, "read_json", return_value=receipt):
            self.assertEqual(host.success("prepare"), receipt)
            for key, value in (
                ("package_pins", dict(PACKAGE, archive_bytes=1)),
                ("plan_sha256", "b" * 64),
            ):
                changed = dict(receipt, **{key: value})
                with patch.object(host, "read_json", return_value=changed):
                    with self.assertRaisesRegex(RuntimeError, "prior_phase_not_passed"):
                        host.success("prepare")

    def test_overlay_preserves_four_base_tools_and_all_eight_runtime_services(self):
        real_compose = host.compose
        for changed in (
            None,
            "backend",
            "db",
            *host.BASE_TOOLS,
            "unknown-service",
            "default-profiles",
        ):
            with self.subTest(changed=changed):
                prior = {
                    "name": "jifeng-erp-test",
                    "services": {
                        name: {
                            "image": host.BASE_TAG,
                            "restart": "unless-stopped",
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(host.OLD_RELEASE / "app"),
                                    "target": host.APP_TARGET,
                                    "read_only": True,
                                }
                            ]
                            if name in host.RUNTIME
                            else [],
                        }
                        for name in host.RUNTIME + host.DATA + host.BASE_TOOLS
                    },
                }
                for name in host.TOOLS:
                    prior["services"][name] = {"old_backup": "old-private-only"}
                if changed == "unknown-service":
                    prior["services"][changed] = {"image": host.BASE_TAG}
                overlay = {}

                def written(path, content, mode):
                    self.assertEqual(path, host.OVERLAY)
                    self.assertEqual(mode, 0o644)
                    overlay.update(json.loads(content))

                def resolved(label, argv, **kwargs):
                    self.assertTrue(kwargs["redact_output"])
                    if "--profile" not in argv:
                        defaults = deepcopy(prior)
                        defaults["services"] = {
                            name: prior["services"][name]
                            for name in host.RUNTIME + host.DATA
                        }
                        return json.dumps(defaults).encode()
                    self.assertEqual(argv.count("--profile"), 1)
                    self.assertEqual(argv[argv.index("--profile") + 1], "tools")
                    if label == "old-compose-config":
                        return json.dumps(prior).encode()
                    current = deepcopy(prior)
                    for name, settings in overlay["services"].items():
                        if name in host.RUNTIME:
                            current["services"][name]["volumes"] = settings["volumes"]
                        else:
                            settings = deepcopy(settings)
                            settings["mem_limit"] = 768 * 1024 * 1024
                            settings["memswap_limit"] = 1024 * 1024 * 1024
                            current["services"][name] = settings
                    if changed in host.RUNTIME + host.DATA + host.BASE_TOOLS:
                        current["services"][changed]["restart"] = "always"
                    return json.dumps(current).encode()

                with (
                    patch.object(host, "write_new", side_effect=written),
                    patch.object(host, "command", side_effect=resolved),
                    patch.object(
                        host,
                        "compose",
                        side_effect=lambda path: (
                            real_compose(path)[:-2]
                            if changed == "default-profiles"
                            else real_compose(path)
                        ),
                    ),
                ):
                    if changed:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "existing_service_config_changed|compose_service_set_changed",
                        ):
                            host.make_overlay()
                    else:
                        host.make_overlay()
                tool = overlay["services"]["erp-release-before"]
                mounts = {item["target"]: item for item in tool["volumes"]}
                self.assertEqual(
                    mounts[host.APP_TARGET]["source"], str(host.OLD_RELEASE / "app")
                )
                self.assertEqual(
                    mounts["/opt/source-release-backup"]["source"], str(host.PRIVATE)
                )
                self.assertFalse(mounts["/opt/source-release-backup"]["read_only"])
                self.assertEqual(
                    set(overlay["services"]), set(host.RUNTIME + host.TOOLS)
                )

    def test_incoming_permissions_preserve_existing_plan_and_only_chown_new_helpers(
        self,
    ):
        release, incoming, native, audit_path, plan = (
            MagicMock() for unused in range(5)
        )
        release.__truediv__.side_effect = lambda name: (
            incoming if name == "incoming-r2" else plan
        )
        incoming.lstat.return_value = SimpleNamespace(
            st_mode=0o40700, st_uid=0, st_gid=0
        )
        for path in (native, audit_path):
            path.stat.return_value = SimpleNamespace(
                st_mode=0o100600, st_uid=0, st_gid=0
            )
        plan.stat.return_value = SimpleNamespace(
            st_mode=0o100644, st_uid=1000, st_gid=1000
        )
        with (
            patch.object(host, "RELEASE", release),
            patch.object(host, "NATIVE", native),
            patch.object(host, "AUDIT", audit_path),
            patch.object(host, "read_regular", return_value=b"reviewed"),
            patch.object(host.os, "chown", create=True) as chown,
            patch.object(host, "private_directory"),
        ):
            host.incoming_permissions()
        self.assertEqual(
            [call.args for call in chown.call_args_list],
            [(incoming, 1000, 1000), (audit_path, 1000, 1000)],
        )

    def test_initial_plan_owner_mode_rejected_before_any_chown(self):
        for owner, mode in ((0, 0o100644), (1000, 0o100600), (1000, 0o100666)):
            with self.subTest(owner=owner, mode=mode):
                release, incoming, native, audit_path, plan = (
                    MagicMock() for unused in range(5)
                )
                release.__truediv__.side_effect = lambda name: (
                    incoming if name == "incoming-r2" else plan
                )
                incoming.lstat.return_value = SimpleNamespace(
                    st_mode=0o40700, st_uid=0, st_gid=0
                )
                for path in (native, audit_path):
                    path.stat.return_value = SimpleNamespace(
                        st_mode=0o100600, st_uid=0, st_gid=0
                    )
                plan.stat.return_value = SimpleNamespace(
                    st_mode=mode, st_uid=owner, st_gid=owner
                )
                with (
                    patch.object(host, "RELEASE", release),
                    patch.object(host, "NATIVE", native),
                    patch.object(host, "AUDIT", audit_path),
                    patch.object(host, "read_regular", return_value=b"reviewed"),
                    patch.object(host.os, "chown", create=True) as chown,
                    patch.object(host, "private_directory"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "initial_plan_identity"):
                        host.incoming_permissions()
                chown.assert_not_called()

    def test_ready_requires_exact_plan_owner_mode(self):
        release, native, audit_path, plan = (MagicMock() for unused in range(4))
        release.__truediv__.return_value = plan
        native.stat.return_value = SimpleNamespace(st_mode=0o100600, st_uid=0, st_gid=0)
        audit_path.stat.return_value = SimpleNamespace(
            st_mode=0o100600, st_uid=1000, st_gid=1000
        )
        with (
            patch.object(host, "RELEASE", release),
            patch.object(host, "NATIVE", native),
            patch.object(host, "AUDIT", audit_path),
            patch.object(host, "read_regular", return_value=b"reviewed"),
            patch.object(host, "private_directory"),
        ):
            plan.stat.return_value = SimpleNamespace(
                st_mode=0o100644, st_uid=1000, st_gid=1000
            )
            host.incoming_ready()
            for owner, mode in ((0, 0o100644), (1000, 0o100600)):
                with self.subTest(owner=owner, mode=mode):
                    plan.stat.return_value = SimpleNamespace(
                        st_mode=mode, st_uid=owner, st_gid=owner
                    )
                    with self.assertRaisesRegex(RuntimeError, "ready_plan_identity"):
                        host.incoming_ready()

    def test_python36_syntax_and_exact_phase_cli(self):
        ast.parse(
            (ROOT / "deploy/erp-test/incremental_host.py").read_bytes(),
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
        stdout = "\n".join(json.dumps(row) for row in rows).encode()
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

    def test_native_before_includes_backup_and_correct_old_source(self):
        result = {
            "passed": True,
            "mode": "before",
            "protected_table_count": 929,
            "total_table_count": 929,
            "proof_sha256": DIGEST,
            "plan_sha256": DIGEST,
            "backups": ["four-reviewed-parts"],
        }
        with (
            patch.object(host, "HELPER_PINS", {"audit": DIGEST}),
            patch.object(
                host, "command", return_value=json.dumps(result).encode()
            ) as command,
            patch.object(host, "read_regular", return_value=b"reviewed-native"),
            patch.object(host, "proof_sha") as proof,
            patch.object(host, "backup_proof") as backups,
        ):
            self.assertEqual(host.native("before", old=True), result)
        argv = command.call_args.args[1]
        self.assertIn("erp-release-before", argv)
        self.assertIn("--audit-sha", argv)
        self.assertEqual(argv[argv.index("--plan-sha256") + 1], DIGEST)
        self.assertNotIn("--before-sha", argv)
        self.assertNotIn("bench", argv)
        proof.assert_called_once_with("before.json", DIGEST)
        backups.assert_called_once_with(result["backups"])

    def test_native_apply_requires_sha_pinned_baseline_and_931(self):
        result = {
            "passed": True,
            "mode": "apply",
            "protected_table_count": 929,
            "total_table_count": 931,
            "proof_sha256": DIGEST,
            "plan_sha256": DIGEST,
        }
        with (
            patch.object(host, "HELPER_PINS", {"audit": DIGEST}),
            patch.object(host, "success", return_value={"before_sha256": DIGEST}),
            patch.object(
                host, "command", return_value=json.dumps(result).encode()
            ) as command,
            patch.object(host, "read_regular", return_value=b"reviewed-native"),
            patch.object(host, "proof_sha") as proof,
        ):
            host.native("apply")
            self.assertIn("--before-sha", command.call_args.args[1])
            self.assertEqual(proof.call_args_list[0].args, ("before.json", DIGEST))
            result["protected_table_count"] = 919
            command.return_value = json.dumps(result).encode()
            with self.assertRaisesRegex(RuntimeError, "native_full_inventory_proof"):
                host.native("apply")

    def test_preflight_has_no_sql(self):
        result = {
            "passed": True,
            "source_files": PACKAGE["source_files"],
            "hook_count": 28,
            "doctype_js": {
                "Sales Order": {
                    "path": "app/jifeng_erp/public/js/jifeng_sales_order.js",
                    "bytes": len(b"native"),
                    "sha256": host.DOCTYPE_JS["Sales Order"]["sha256"],
                }
            },
            "plan_sha256": DIGEST,
            "backup_implementation_verified": True,
            "sql_connected": False,
        }
        with (
            patch.object(host, "HELPER_PINS", {"audit": DIGEST}),
            patch.object(host, "command", return_value=json.dumps(result).encode()),
            patch.object(host, "read_regular", return_value=b"native"),
            patch.object(host, "proof_sha") as proof,
        ):
            host.native("preflight")
        proof.assert_not_called()

    def test_cache_requires_current_sales_order_form_without_gui_claim(self):
        result = {
            "passed": True,
            "mode": "cache",
            "protected_table_count": 929,
            "total_table_count": 931,
            "proof_sha256": DIGEST,
            "plan_sha256": DIGEST,
            "cache": {
                "effective_hooks": 28,
                "app_include_css": 10,
                "app_include_js": 18,
                "order_js_sha256": host.DOCTYPE_JS["Sales Order"]["sha256"],
                "form_meta_js_sha256": DIGEST,
                "source_current": True,
                "cached_current": True,
                "gui_tested": False,
            },
        }
        with (
            patch.object(host, "HELPER_PINS", {"audit": DIGEST}),
            patch.object(host, "success", return_value={"before_sha256": DIGEST}),
            patch.object(host, "command") as command,
            patch.object(host, "read_regular", return_value=b"reviewed-native"),
            patch.object(host, "proof_sha"),
        ):
            command.return_value = json.dumps(result).encode()
            self.assertEqual(host.native("cache"), result)
            for key, value in (
                ("effective_hooks", 25),
                ("order_js_sha256", "b" * 64),
                ("form_meta_js_sha256", ""),
                ("cached_current", False),
                ("gui_tested", True),
            ):
                with self.subTest(key=key):
                    changed = deepcopy(result)
                    changed["cache"][key] = value
                    command.return_value = json.dumps(changed).encode()
                    with self.assertRaisesRegex(RuntimeError, "native_cache_form_meta"):
                        host.native("cache")

    def test_native_wrong_plan_receipt_fails_before_accepting_backup(self):
        result = {
            "passed": True,
            "mode": "before",
            "protected_table_count": 929,
            "total_table_count": 929,
            "proof_sha256": DIGEST,
            "plan_sha256": "b" * 64,
            "backups": [],
        }
        with (
            patch.object(host, "HELPER_PINS", {"audit": DIGEST}),
            patch.object(host, "command", return_value=json.dumps(result).encode()),
            patch.object(host, "read_regular", return_value=b"reviewed-native"),
            patch.object(host, "proof_sha") as proof,
            patch.object(host, "backup_proof") as backups,
        ):
            with self.assertRaisesRegex(RuntimeError, "native_full_inventory_proof"):
                host.native("before", old=True)
        proof.assert_not_called()
        backups.assert_not_called()

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
                    any(name.endswith("failed.json") for name, value in records)
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
                value for name, value in records if name.endswith("failed.json")
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
                value for name, value in records if name.endswith("failed.json")
            )
            self.assertTrue(failure["outcome_unknown"])
            self.assertTrue(failure["failed_phase_runtime_stop_not_inferred"])

    def test_same_phase_cannot_restart(self):
        flags = [
            "--reviewed-host-sha256",
            "--reviewed-native-sha256",
            "--reviewed-audit-sha256",
            "--manifest-sha256",
            "--source-files-sha256",
            "--metadata-changes-sha256",
            "--plan-sha256",
            "--archive-sha256",
        ]
        argv = ["host", "prepare"]
        for flag in flags:
            argv.extend([flag, DIGEST])
        argv.extend(["--archive-bytes", str(PACKAGE["archive_bytes"])])
        argv.extend(["--source-files", str(PACKAGE["source_files"])])
        control, release = MagicMock(), MagicMock()
        control.exists.return_value = True
        release.is_dir.return_value, release.is_symlink.return_value = True, False
        with (
            patch.object(sys, "argv", argv),
            patch.object(host.os, "geteuid", return_value=0, create=True),
            patch.object(host, "CONTROL", control),
            patch.object(host, "RELEASE", release),
            patch.object(host, "sha", return_value=DIGEST),
            patch.object(host, "phase") as phase,
        ):
            with self.assertRaisesRegex(RuntimeError, "prepare_already_started"):
                host.main()
        phase.assert_not_called()


if __name__ == "__main__":
    unittest.main()
