"""Failure cleanup matters: never leave the three nontransactional tables locked."""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_native():
    path = Path(__file__).resolve().parents[1] / "deploy/erp-test/incremental_native.py"
    spec = importlib.util.spec_from_file_location("incremental_native_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
