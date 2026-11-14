"""Registration policy, private persistence and concurrent account creation."""
import concurrent.futures
import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

import accounts
from accounts import (AuthenticationError, ConflictError, RegistrationClosed,
                      RegistrationError, RegistrationThrottled, SetupRequired,
                      Store, ValidationError)

PASSWORD = "registration test password longer than fifteen"
CODE = "fixture activation code"


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "accounts.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def admin(store):
    return store.create_user("Admin", PASSWORD, role="admin", must_change_password=False)


def enable(store, admin, required=True, code=CODE):
    return store.configure_registration(admin["id"], True, required, code)


def test_missing_registration_configuration_is_closed_without_creating_files(store):
    expected = {"enabled": False, "activation_required": True, "activation_code_set": False}
    assert store.registration_settings() == expected
    assert not os.path.exists(store.registration_path)
    assert not os.path.exists(store.path + ".registration.lock")
    with pytest.raises(RegistrationClosed):
        store.register_user("reader", PASSWORD, CODE)
    assert store.list_users() == []


def test_registration_creates_normal_member_with_private_settings(store, admin):
    assert enable(store, admin) == {
        "enabled": True, "activation_required": True, "activation_code_set": True}
    user = store.register_user("Reader", PASSWORD, CODE, address="192.0.2.1")
    assert user["username"] == "reader"
    assert user["role"] == "member"
    assert user["status"] == "active"
    assert user["must_change_password"] is False
    assert "password_hash" not in user
    assert store.get_settings(user["id"])["preferences"] == {}
    assert store.authenticate("READER", PASSWORD)[1]["user"]["id"] == user["id"]
    store.save_settings(user["id"], preferences={"reader_theme": "dark"})
    assert store.get_settings(admin["id"])["preferences"] == {}


def test_invalid_codes_and_closed_registration_never_create_accounts(store, admin):
    enable(store, admin)
    for index, code in enumerate(("", "incorrect", CODE.upper())):
        with pytest.raises(RegistrationError, match="Activation code is invalid"):
            store.register_user("reader" + str(index), PASSWORD, code, address=str(index))
    store.configure_registration(admin["id"], False, True)
    with pytest.raises(RegistrationClosed):
        store.register_user("reader", PASSWORD, CODE, address="other")
    assert len(store.list_users()) == 1


def test_optional_activation_retains_code_for_later_reenable(store, admin):
    enable(store, admin)
    store.configure_registration(admin["id"], True, False)
    assert store.register_user("reader", PASSWORD)["role"] == "member"
    store.configure_registration(admin["id"], True, True)
    with pytest.raises(RegistrationError):
        store.register_user("nocode", PASSWORD, address="no-code")
    assert store.register_user("withcode", PASSWORD, CODE, address="with-code")


def test_new_install_may_enable_registration_without_activation_code(store, admin):
    with pytest.raises(ValidationError, match="activation code"):
        store.configure_registration(admin["id"], True, True)
    assert not os.path.exists(store.registration_path)
    assert enable(store, admin, required=False, code="")["activation_code_set"] is False
    assert store.register_user("reader", PASSWORD)["role"] == "member"


def test_rotation_restart_and_sidecar_permissions(store, admin):
    enable(store, admin)
    replacement = "replacement fixture code"
    store.configure_registration(admin["id"], True, True, replacement)
    restarted = Store(store.path)
    assert restarted.registration_settings() == {
        "enabled": True, "activation_required": True, "activation_code_set": True}
    with pytest.raises(RegistrationError):
        restarted.register_user("oldcode", PASSWORD, CODE, address="old")
    assert restarted.register_user("newcode", PASSWORD, replacement, address="new")
    with open(store.registration_path, encoding="utf-8") as stream:
        raw = stream.read()
    assert CODE not in raw and replacement not in raw
    encoded = json.loads(raw)["activation_code_hash"]
    assert encoded.startswith("$argon2id$")
    assert accounts.PASSWORDS.verify(encoded, replacement)
    assert stat.S_IMODE(os.stat(store.registration_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path + ".registration.lock").st_mode) == 0o600
    assert not any(entry.name.endswith(".tmp") for entry in os.scandir(os.path.dirname(store.path)))


def test_only_active_admin_can_change_registration(store, admin):
    member = store.create_user("Member", PASSWORD)
    disabled = store.create_user("Disabled", PASSWORD, role="admin")
    store.update_user(admin["id"], disabled["id"], "disable")
    for actor in (None, "nonexistent", member["id"], disabled["id"]):
        with pytest.raises(AuthenticationError):
            store.configure_registration(actor, True, True, CODE)
    assert not os.path.exists(store.registration_path)


def test_invalid_inputs_and_normalized_duplicates(store, admin):
    enable(store, admin)
    for index, (username, password) in enumerate((("a", PASSWORD), ("中文用户", PASSWORD), ("valid", "short"))):
        with pytest.raises(ValidationError):
            store.register_user(username, password, CODE, address=str(index))
    first = store.register_user("Alice", PASSWORD, CODE, address="first")
    with pytest.raises(ConflictError):
        store.register_user("ALICE", PASSWORD, CODE, address="second")
    assert store.get_settings(first["id"])["preferences"] == {}
    for enabled, required, code in ((1, True, CODE), (True, "false", CODE), (True, True, None), (True, True, "x" * 129)):
        with pytest.raises(ValidationError):
            store.configure_registration(admin["id"], enabled, required, code)


def test_registration_has_no_role_argument(store, admin):
    enable(store, admin)
    with pytest.raises(TypeError):
        store.register_user("injected", PASSWORD, CODE, role="admin")
    assert len(store.list_users()) == 1


def test_concurrent_processes_create_one_normalized_account(store, admin):
    enable(store, admin)
    program = """from accounts import Store, ConflictError
import sys
try:
    user = Store(sys.argv[1]).register_user(sys.argv[2], sys.argv[3], sys.argv[4], address=sys.argv[2])
    print(user['role'])
except ConflictError:
    print('duplicate')
"""
    processes = [subprocess.Popen([sys.executable, "-c", program, store.path, name, PASSWORD, CODE],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for name in ("Reader", "READER", "reader", "rEADER")]
    outcomes = []
    for process in processes:
        output, errors = process.communicate(timeout=20)
        assert process.returncode == 0, errors
        outcomes.append(output.strip())
    assert sorted(outcomes) == ["duplicate", "duplicate", "duplicate", "member"]
    assert len(store.list_users()) == 2


@pytest.mark.parametrize("successful", [False, True])
def test_registration_throttle_counts_failures_and_successes_across_instances(store, admin, monkeypatch, successful):
    enable(store, admin)
    now = [2000000000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: now[0])
    for index in range(5):
        if successful:
            Store(store.path).register_user("reader" + str(index), PASSWORD, CODE, address="192.0.2.9")
        else:
            with pytest.raises(RegistrationError):
                Store(store.path).register_user("reader" + str(index), PASSWORD, "wrong", address="192.0.2.9")
    with pytest.raises(RegistrationThrottled):
        Store(store.path).register_user("sixth", PASSWORD, CODE, address="192.0.2.9")
    # Registration never consumes the login budget for the same address.
    assert store.authenticate("admin", PASSWORD, address="192.0.2.9")[1]["user"]["id"] == admin["id"]
    assert store.register_user("anotheraddress", PASSWORD, CODE, address="192.0.2.10")
    now[0] += 901
    assert Store(store.path).register_user("afterexpiry", PASSWORD, CODE, address="192.0.2.9")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT failure_count FROM login_throttle WHERE bucket_key LIKE 'register:%' AND failure_count>1").fetchone() is None


def test_concurrent_registration_attempts_share_address_budget(store, admin, monkeypatch):
    enable(store, admin, required=False)
    monkeypatch.setattr(accounts.time, "time", lambda: 2000000000.0)

    def attempt(index):
        try:
            Store(store.path).register_user("reader" + str(index), PASSWORD, address="192.0.2.5")
            return "created"
        except RegistrationThrottled:
            return "throttled"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert sorted(results) == ["created"] * 5 + ["throttled"] * 3


@pytest.mark.parametrize("change", ["close", "rotate", "require"])
def test_configuration_is_rechecked_after_password_hashing(store, admin, monkeypatch, change):
    enable(store, admin, required=change != "require")
    original = accounts.PASSWORDS

    class RacingHasher:
        def hash(self, value):
            encoded = original.hash(value)
            if value == PASSWORD:
                if change == "close":
                    store.configure_registration(admin["id"], False, True)
                elif change == "rotate":
                    store.configure_registration(admin["id"], True, True, "rotated fixture code")
                else:
                    store.configure_registration(admin["id"], True, True)
            return encoded

        def verify(self, encoded, value):
            return original.verify(encoded, value)

    monkeypatch.setattr(accounts, "PASSWORDS", RacingHasher())
    with pytest.raises(RegistrationClosed if change == "close" else RegistrationError):
        store.register_user("reader", PASSWORD, "" if change == "require" else CODE)
    assert len(store.list_users()) == 1


@pytest.mark.parametrize("raw", ["{", '{"enabled":true}', '{"enabled":1,"activation_required":true,"activation_code_hash":""}', b"\xff"])
def test_corrupt_registration_configuration_fails_closed(store, raw):
    with open(store.registration_path, "wb") as stream:
        stream.write(raw if isinstance(raw, bytes) else raw.encode())
    with pytest.raises(SetupRequired):
        store.registration_settings()
    with pytest.raises(SetupRequired):
        store.register_user("reader", PASSWORD, CODE)
    assert store.list_users() == []
