"""Failure cleanup matters: never leave the three nontransactional tables locked."""

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def load_native():
    path = Path(__file__).resolve().parents[1] / "deploy/erp-test/incremental_native.py"
    spec = importlib.util.spec_from_file_location("incremental_native_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_loader_uses_only_new_incoming_namespace_and_sha(tmp_path, monkeypatch):
    native = load_native()
    monkeypatch.setattr(native, "ROOT", tmp_path)
    current = tmp_path / "incoming-r2"
    previous = tmp_path / "incoming"
    current.mkdir()
    previous.mkdir()
    source = b"NAMESPACE = 'fresh-r2'\n"
    (current / "incremental_audit.py").write_bytes(source)
    stale_source = b"NAMESPACE = 'used-r1'\n"
    (previous / "incremental_audit.py").write_bytes(stale_source)
    module = native.load_audit(hashlib.sha256(source).hexdigest())
    assert module.NAMESPACE == "fresh-r2"
    with pytest.raises(RuntimeError, match="audit_source_pin"):
        native.load_audit(hashlib.sha256(stale_source).hexdigest())


class Cursor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, sql):
        self.owner.commands.append(sql)
        if sql == "UNLOCK TABLES" and self.owner.unlock_error:
            raise RuntimeError("unlock failed")

    def fetchone(self):
        self.owner.identity_reads += 1
        return (
            11 + int(self.owner.identity_changed and self.owner.identity_reads > 1),
        )

    def close(self):
        self.owner.cursor_closed = True


class Connection:
    def __init__(
        self, *, unlock_error=False, rollback_error=False, identity_changed=False
    ):
        self.commands = []
        self.unlock_error = unlock_error
        self.rollback_error = rollback_error
        self.identity_changed = identity_changed
        self.identity_reads = 0
        self.rollbacks = 0
        self.cursor_closed = False
        self.closed = False

    def select_db(self, name):
        self.database = name

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error and self.rollbacks > 1:
            raise RuntimeError("rollback failed")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("failure", [None, "body", "rollback", "unlock", "identity"])
def test_read_locks_always_released_or_connection_closed(failure):
    native = load_native()
    main = Connection(rollback_error=failure == "rollback")
    locker = Connection(
        unlock_error=failure == "unlock", identity_changed=failure == "identity"
    )
    native.audit = SimpleNamespace(
        MYISAM={"tabData Import Log", "tabError Log", "__global_search"}
    )
    native.frappe = SimpleNamespace(
        db=SimpleNamespace(_conn=main, get_connection=lambda: locker)
    )
    expectation = pytest.raises(RuntimeError) if failure else nullcontext()
    with expectation:
        with native.protected_reads():
            if failure == "body":
                raise RuntimeError("collector failed")
    assert locker.database == "jifeng_erp_test"
    assert (
        "LOCK TABLES `__global_search` READ, `tabData Import Log` READ, `tabError Log` READ"
        in locker.commands
    )
    assert "UNLOCK TABLES" in locker.commands
    assert locker.cursor_closed and locker.closed
    assert main.rollbacks == 2
    assert "SET TRANSACTION READ ONLY" in main.commands


def test_single_connection_cannot_claim_separate_myisam_lock_proof():
    native = load_native()
    main = Connection()
    native.audit = SimpleNamespace(MYISAM=set())
    native.frappe = SimpleNamespace(
        db=SimpleNamespace(_conn=main, get_connection=lambda: main)
    )
    with pytest.raises(RuntimeError, match="independent_lock_connection"):
        with native.protected_reads():
            pytest.fail("A shared connection must never enter the protected body")
    assert main.closed


@pytest.mark.parametrize("change", ["old_batch", "doctype", "patch", "extra", None])
def test_metadata_change_scope_is_empty_and_specific_to_this_batch(change):
    native = load_native()
    value = {
        "old_commit": native.OLD,
        "new_commit": native.NEW,
        "doctype": None,
        "changes": {},
    }
    previous = deepcopy(value)
    if change == "old_batch":
        value["new_commit"] = native.OLD
    elif change == "doctype":
        value["doctype"] = "Jifeng Customer Type"
    elif change == "patch":
        value["changes"]["value"] = {"description": {"old": "x", "new": "y"}}
    elif change == "extra":
        value["target"] = "Operation"
    if change:
        with pytest.raises(RuntimeError, match="zero_existing_metadata_changes"):
            native.verify_changes(value)
    else:
        native.verify_changes(value)
        assert value == previous


def test_only_two_new_doctypes_can_reload_and_source_is_rechecked(monkeypatch):
    native = load_native()
    native.audit = SimpleNamespace(
        TARGETS=("Jifeng Plating Color", "Jifeng Order Operation"), PATCHES={}
    )
    native.release_plan = {"frozen": True}
    native.plan_sha256 = "a" * 64
    observed = []
    monkeypatch.setattr(
        native, "load_release_plan", lambda pin: dict(native.release_plan)
    )
    monkeypatch.setattr(native, "candidate", lambda: observed.append("verified_source"))
    native.frappe = SimpleNamespace(
        reload_doc=lambda *args, **kwargs: observed.append((args, kwargs))
    )
    native.apply_new_doctypes()
    assert observed == [
        "verified_source",
        (("jifeng_erp", "doctype", "jifeng_plating_color"), {"force": True}),
        "verified_source",
        (("jifeng_erp", "doctype", "jifeng_order_operation"), {"force": True}),
    ]
    native.audit.TARGETS = ("Operation", "Jifeng Order Operation")
    with pytest.raises(RuntimeError, match="fixed_new_doctype_scope"):
        native.apply_new_doctypes()
    assert len(observed) == 4


@pytest.mark.parametrize(
    "change", ["previous_count", "wrong_source", "wrong_mapping", None]
)
def test_package_count_and_native_order_script_have_one_frozen_source(change):
    native = load_native()
    native.audit = SimpleNamespace(
        DOCTYPE_JS={"Sales Order": "public/js/jifeng_sales_order.js"}
    )
    native.release_plan = {"source_files": 532}
    definition = {
        "source_files": 532,
        "doctype_js": {"Sales Order": {"sha256": "a" * 64}},
    }
    manifest = {
        "commit": native.NEW,
        "source_files": 532,
        "verified_doctype_js": {
            "Sales Order": {
                "path": "public/js/jifeng_sales_order.js",
                "sha256": "a" * 64,
            }
        },
    }
    files = [None] * 532
    if change == "previous_count":
        manifest["source_files"] = 505
    elif change == "wrong_source":
        definition["doctype_js"]["Sales Order"]["sha256"] = "b" * 64
    elif change == "wrong_mapping":
        manifest["verified_doctype_js"]["Sales Order"]["path"] = "public/js/old.js"
    if change:
        with pytest.raises(RuntimeError):
            native.verify_candidate_metadata(manifest, files, definition)
    else:
        native.verify_candidate_metadata(manifest, files, definition)


@pytest.mark.parametrize(
    "change", ["hook_order", "old_form", "cached_form", "duplicate", None]
)
def test_effective_hooks_and_actual_formmeta_must_share_current_source(
    tmp_path, monkeypatch, change
):
    native = load_native()
    native.ROOT = tmp_path
    native.audit = SimpleNamespace(
        TARGETS=("Jifeng Plating Color", "Jifeng Order Operation"),
        CSS_COUNT=10,
        JS_COUNT=18,
        DOCTYPE_JS={"Sales Order": "public/js/jifeng_sales_order.js"},
    )
    relative = "app/jifeng_erp/public/js/jifeng_sales_order.js"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    script = "window.JifengOrderCustomerPicker = class JifengOrderCustomerPicker {};"
    source.write_bytes(script.encode())
    definition = {
        "doctype_js": {
            "Sales Order": {
                "path": relative,
                "sha256": hashlib.sha256(script.encode()).hexdigest(),
            }
        }
    }
    css = ["/assets/jifeng_erp/css/" + str(index) + ".css?v=x" for index in range(10)]
    js = ["/assets/jifeng_erp/js/" + str(index) + ".js?v=x" for index in range(18)]
    (tmp_path / "manifest.json").write_text(
        json.dumps({"verified_direct_hook_paths": css + js})
    )
    if change == "hook_order":
        js[0], js[1] = js[1], js[0]
    meta_js = "old" if change == "old_form" else script
    if change == "duplicate":
        meta_js += script
    cache_js = "old cached" if change == "cached_form" else meta_js
    invalidated, cleared = [], []
    native.frappe = SimpleNamespace(
        clear_cache=lambda **kwargs: cleared.append(kwargs),
        client_cache=SimpleNamespace(
            delete_value=invalidated.append, get_value=lambda key: {"__js": cache_js}
        ),
        get_hooks=lambda name, *args: {
            "app_include_css": css,
            "app_include_js": js,
            "doctype_js": {"Sales Order": ["public/js/jifeng_sales_order.js"]},
        }[name],
    )
    monkeypatch.setitem(
        sys.modules,
        "frappe.desk.form.meta",
        SimpleNamespace(get_meta=lambda name: {"__js": meta_js}),
    )
    if change:
        with pytest.raises(RuntimeError):
            native.verify_cached_assets(definition)
    else:
        result = native.verify_cached_assets(definition)
        assert result["effective_hooks"] == 28
        assert result["source_current"] and result["cached_current"]
        assert result["gui_tested"] is False
    assert cleared == [
        {"doctype": "Jifeng Plating Color"},
        {"doctype": "Jifeng Order Operation"},
    ]
    assert invalidated == ["app_hooks", "doctype_form_meta::Sales Order"]


def test_previous_batch_apply_receipt_cannot_validate_current_delta():
    native = load_native()
    native.plan_sha256 = "a" * 64
    native.audit = SimpleNamespace(
        verify_snapshot_delta=lambda *args: pytest.fail("old receipt reached verifier")
    )
    with pytest.raises(RuntimeError, match="apply_receipt_release_identity"):
        native.verify_delta(
            {}, {}, {}, {}, {"commit": native.OLD, "plan_sha256": "b" * 64}
        )
