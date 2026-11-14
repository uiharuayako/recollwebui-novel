"""Behavioral tests for account isolation, concurrency and recovery; no Recoll needed."""
import concurrent.futures
import os
import sqlite3
import stat
import subprocess
import sys
import time

import pytest

from accounts import (AuthenticationError, ConflictError, NotFound, SetupRequired,
                      Store, ValidationError, DAY, folder_identity)

PASSWORD = "test password longer than fifteen"
NEW_PASSWORD = "new password longer than fifteen"


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "accounts.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def users(store):
    return (store.create_user("Admin", PASSWORD, role="admin", must_change_password=False),
            store.create_user("Member", PASSWORD, must_change_password=False))


@pytest.fixture
def document(store):
    return store.register_document("library", "file:///books/one.txt", "/books/one.txt", title="Book one", format="txt", content_version="v1")


def opened(store, user_id, document_id):
    settings = store.get_settings(user_id)
    return store.open_reading(user_id, document_id, settings["reading_epoch"], settings["progress_epoch"])


def test_explicit_initialization_and_persistent_secret(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = Store(path)
    assert not store.initialized()
    with pytest.raises(SetupRequired):
        store.list_users()
    assert not path.exists()
    info = store.initialize()
    assert info["schema_version"] == 1
    assert store.initialized()
    key = store.secret()
    assert len(key) == 64
    assert Store(path).secret() == key
    assert store.initialize()["schema_version"] == 1
    assert store.secret() == key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.secret_path).st_mode) == 0o600


def test_reader_preferences_are_validated_and_isolated(store, users):
    admin, member = users
    preferences = {"reader_theme": "dark", "reader_font_family": "sans", "reader_line_height": 190,
                   "reader_paragraph_spacing": 18, "reader_content_width": 840,
                   "reader_text_align": "justify", "reader_indent": 1, "reader_preserve_publisher": 0}
    assert store.save_settings(admin["id"], preferences=preferences)["preferences"] == preferences
    assert store.get_settings(member["id"])["preferences"] == {}
    for invalid in ({"reader_theme": "url(evil)"}, {"reader_content_width": 10000},
                    {"reader_line_height": 1.8}, {"reader_indent": True}, {"reader_font_family": "custom css"}):
        with pytest.raises(ValidationError):
            store.save_settings(admin["id"], preferences=invalid)


def test_usernames_passwords_and_role_validation(store):
    admin = store.create_user("Alice", PASSWORD, role="admin")
    assert admin["username"] == "alice"
    assert "password_hash" not in admin
    with pytest.raises(ConflictError):
        store.create_user("ALICE", PASSWORD)
    for name in ("a", "中文用户", "a b", "../username"):
        with pytest.raises(ValidationError):
            store.create_user(name, PASSWORD)
    with pytest.raises(ValidationError):
        store.create_user("newuser", "short")
    with pytest.raises(ValidationError):
        store.create_user("newuser", PASSWORD, role="owner")


def test_session_token_is_hashed_and_revocation_is_immediate(store, users):
    admin, member = users
    token, session = store.authenticate("ADMIN", PASSWORD, device_label="Device A")
    assert session["user"]["id"] == admin["id"]
    assert Store(store.path).get_session(token)["id"] == session["id"]
    with sqlite3.connect(store.path) as connection:
        stored = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert token != stored and token not in stored
    with pytest.raises(NotFound):
        store.revoke_session(member["id"], session["id"])
    assert store.get_session(token)
    store.revoke_session(admin["id"], session["id"])
    assert Store(store.path).get_session(token) is None


def test_disabled_role_and_password_changes_revoke_sessions(store, users):
    admin, member = users
    token, _ = store.authenticate("member", PASSWORD)
    store.update_user(admin["id"], member["id"], "disable")
    assert store.get_session(token) is None
    with pytest.raises(AuthenticationError):
        store.authenticate("member", PASSWORD)
    store.update_user(admin["id"], member["id"], "enable")
    token, _ = store.authenticate("member", PASSWORD)
    store.update_user(admin["id"], member["id"], "role", role="admin")
    assert store.get_session(token) is None
    token, _ = store.authenticate("member", PASSWORD)
    store.change_password(member["id"], PASSWORD, NEW_PASSWORD)
    assert store.get_session(token) is None
    new_token, session = store.authenticate("member", NEW_PASSWORD)
    assert not session["user"]["must_change_password"]
    store.update_user(admin["id"], member["id"], "reset-password", password=PASSWORD)
    assert store.get_session(new_token) is None
    assert store.authenticate("member", PASSWORD)[1]["user"]["must_change_password"]


def test_last_administrator_protection_and_unauthorized_actor(store, users):
    admin, member = users
    for action, values in (("disable", {}), ("delete", {}), ("role", {"role": "member"})):
        with pytest.raises(ConflictError):
            store.update_user(admin["id"], admin["id"], action, **values)
    with pytest.raises(AuthenticationError):
        store.update_user(member["id"], admin["id"], "reset-password", password=PASSWORD)
    assert store.list_users()[0]["status"] == "active"


def test_last_admin_protection_across_processes(store, users):
    admin, member = users
    store.update_user(admin["id"], member["id"], "role", role="admin")
    program = "from accounts import Store,ConflictError; import sys; s=Store(sys.argv[1]);\ntry: s.update_user(None,sys.argv[2],'disable'); print('disabled')\nexcept ConflictError: print('protected')"
    processes = [subprocess.Popen([sys.executable, "-c", program, store.path, user["id"]], stdout=subprocess.PIPE, text=True) for user in (admin, member)]
    outcomes = [process.communicate(timeout=15)[0].strip() for process in processes]
    assert sorted(outcomes) == ["disabled", "protected"]
    assert sum(user["role"] == "admin" and user["status"] == "active" for user in store.list_users()) == 1


def test_shared_throttle_survives_store_instances(store, users):
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            Store(store.path).authenticate("admin", "wrong password", address="127.0.0.5")
    with pytest.raises(AuthenticationError):
        Store(store.path).authenticate("admin", PASSWORD, address="127.0.0.5")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM login_throttle").fetchone()[0] == 2
        connection.execute("UPDATE login_throttle SET expires_at=0")
    assert store.authenticate("admin", PASSWORD)[1]["user"]["username"] == "admin"


def test_session_expiry_is_enforced_before_cleanup(store, users):
    token, session = store.authenticate("admin", PASSWORD)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE sessions SET idle_expires_at=0 WHERE id=?", (session["id"],))
    assert store.get_session(token) is None
    assert store.list_sessions(users[0]["id"]) == []


def test_settings_are_isolated_whitelisted_and_increment_epochs(store, users):
    admin, member = users
    initial = store.get_settings(admin["id"])
    saved = store.save_settings(admin["id"], {"perpage": 80, "font_size": 24}, {"reading_history_enabled": False})
    assert saved["preferences"] == {"perpage": 80, "font_size": 24}
    assert saved["reading_epoch"] == initial["reading_epoch"] + 1
    assert saved["progress_epoch"] == initial["progress_epoch"]
    assert store.get_settings(member["id"])["preferences"] == {}
    for values in ({"RECOLL_CONFDIR": "/tmp"}, {"perpage": "20"}, {"font_size": 9}, {"title_link": []}):
        with pytest.raises(ValidationError):
            store.save_settings(admin["id"], values)
    with pytest.raises(ValidationError):
        store.save_settings(admin["id"], flags={"progress_sync_enabled": "false"})


def test_search_event_completion_is_idempotent_and_user_scoped(store, users):
    admin, member = users
    query, snapshot = {"query": 'Author:"Mixed Case"'}, {"stem": 1}
    identifier = store.begin_search(admin["id"], query, snapshot, "rules1")
    assert not store.complete_search(member["id"], identifier, query, snapshot, 3)
    with pytest.raises(NotFound):
        store.get_search(member["id"], identifier)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(lambda _: Store(store.path).complete_search(admin["id"], identifier, query, snapshot, 0), range(4)))
    assert outcomes.count(True) == 1
    events = store.list_searches(admin["id"])["items"]
    assert len(events) == 1 and events[0]["result_count"] == 0
    assert events[0]["query"] == query
    assert store.list_searches(member["id"])["items"] == []
    with pytest.raises(NotFound):
        store.delete_search(member["id"], identifier)


def test_search_failure_empty_compatibility_and_deletion_generation(store, users):
    uid = users[0]["id"]
    assert store.begin_search(uid, "  ", {}, "v1") is None
    assert store.begin_search(uid, {"query": "", "dir": "/books"}, {}, "v1")
    failed = store.begin_search(uid, {"query": "broken"}, {}, "v1")
    store.fail_search(uid, failed)
    assert not store.complete_search(uid, failed, {"query": "broken"}, {}, 10)
    pending = store.begin_search(uid, {"query": "later"}, {}, "v1")
    store.delete_search(uid)
    assert not store.complete_search(uid, pending, {"query": "later"}, {}, 10)
    assert store.list_searches(uid)["items"] == []
    store.save_settings(uid, flags={"search_history_enabled": False})
    assert store.begin_search(uid, "secret query", {}, "v1") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM search_history").fetchone()[0] == 0


def test_document_identity_is_independent_of_title_and_query_url(store, document):
    other = store.register_document("library", "file:///books/one.txt#search=query", "/books/../books/one.txt", title="Updated title", format="txt", content_version="v2")
    assert document["id"] == other["id"]
    nested = store.register_document("library", "file:///books/one.txt", "/books/one.txt", ipath="child", format="txt")
    assert nested["id"] != document["id"]
    library = store.register_document("second", "file:///books/one.txt", "/books/one.txt", format="txt")
    assert library["id"] != document["id"]


def test_reading_and_progress_flags_are_independent(store, users, document):
    uid = users[0]["id"]
    settings = store.save_settings(uid, flags={"reading_history_enabled": False})
    result = store.open_reading(uid, document["id"], settings["reading_epoch"], settings["progress_epoch"], folder_key="folder1")
    assert result["progress"]["revision"] == 0
    assert not result["reading_recorded"]
    assert store.list_readings(uid)["items"] == []
    assert store.get_folder_resume(uid, "folder1")["last_document_id"] == document["id"]
    store.save_settings(uid, flags={"progress_sync_enabled": False, "reading_history_enabled": True})
    result = opened(store, uid, document["id"])
    assert result["reading_recorded"] and not result["progress_enabled"]
    assert len(store.list_readings(uid)["items"]) == 1


def test_progress_revision_order_retry_and_account_isolation(store, users, document):
    uid, other = users[0]["id"], users[1]["id"]
    initial = opened(store, uid, document["id"])
    epoch = initial["settings"]["progress_epoch"]
    position = {"chunkIndex": 2, "chunkProgressRatio": .25}
    first = store.save_progress(uid, document["id"], position, 0, "tab-a", 1, epoch, "v1", 25)
    assert first["revision"] == 1
    assert store.save_progress(uid, document["id"], position, 0, "tab-a", 1, epoch, "v1", 25)["revision"] == 1
    with pytest.raises(ConflictError) as conflict:
        store.save_progress(uid, document["id"], {"chunkIndex": 0}, 0, "tab-b", 1, epoch, "v1")
    assert conflict.value.current["position"] == position
    with pytest.raises(ConflictError):
        store.save_progress(uid, document["id"], {"chunkIndex": 3}, 1, "tab-a", 1, epoch, "v1")
    assert store.get_progress(other, document["id"]) is None
    with pytest.raises(ConflictError):
        store.save_progress(other, document["id"], position, 1, "tab-a", 2, epoch, "v1")
    second = store.save_progress(uid, document["id"], {"chunkIndex": 0}, 1, "tab-a", 2, epoch, "v1", 0)
    assert second["progress"] == 0  # Reading backwards is valid.


def test_delete_or_disable_does_not_allow_late_progress_or_open_revival(store, users, document):
    uid = users[0]["id"]
    result = opened(store, uid, document["id"])
    settings = result["settings"]
    store.delete_reading(uid, document["id"], clear_progress=True)
    assert store.get_progress(uid, document["id"]) is None
    with pytest.raises(ConflictError):
        store.save_progress(uid, document["id"], {"chunkIndex": 2}, 0, "tab", 1, settings["progress_epoch"])
    result = store.open_reading(uid, document["id"], settings["reading_epoch"], settings["progress_epoch"])
    assert not result["reading_recorded"] and not result["progress_enabled"]
    assert store.list_readings(uid)["items"] == []
    assert store.get_progress(uid, document["id"]) is None
    fresh = opened(store, uid, document["id"])
    store.save_settings(uid, flags={"progress_sync_enabled": False})
    store.save_settings(uid, flags={"progress_sync_enabled": True})
    with pytest.raises(ConflictError):
        store.save_progress(uid, document["id"], {"chunkIndex": 2}, 0, "tab", 1, fresh["settings"]["progress_epoch"])


def test_import_has_no_fabricated_reading_time_and_preserves_existing(store, users, document):
    uid = users[0]["id"]
    epoch = store.get_settings(uid)["progress_epoch"]
    progress = store.import_progress(uid, document["id"], {"chunkIndex": 3}, epoch)
    assert progress["origin"] == "import" and progress["imported_at"]
    assert store.list_readings(uid)["items"] == []
    with pytest.raises(ConflictError):
        store.import_progress(uid, document["id"], {"chunkIndex": 0}, epoch)
    assert store.get_progress(uid, document["id"])["position"]["chunkIndex"] == 3


def test_shelf_does_not_create_reading_and_protects_saved_progress(store, users, document):
    uid, other = users[0]["id"], users[1]["id"]
    store.add_to_shelf(uid, document["id"])
    store.add_to_shelf(uid, document["id"])
    assert len(store.list_bookshelf(uid)["items"]) == 1
    assert store.list_bookshelf(other)["items"] == []
    assert store.get_progress(uid, document["id"]) is None
    assert store.list_readings(uid)["items"] == []
    result = opened(store, uid, document["id"])
    store.save_progress(uid, document["id"], {"chunkIndex": 4}, 0, "tab", 1, result["settings"]["progress_epoch"])
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE reading_progress SET updated_at=0")
        connection.execute("UPDATE document_refs SET last_verified_at=0")
    store.cleanup()
    assert store.get_progress(uid, document["id"])["position"]["chunkIndex"] == 4
    assert store.list_bookshelf(uid)["items"][0]["position"]["chunkIndex"] == 4
    store.clear_progress(uid, document["id"])
    assert store.is_on_shelf(uid, document["id"])
    assert store.get_progress(uid, document["id"]) is None
    store.remove_from_shelf(uid, document["id"])
    assert not store.is_on_shelf(uid, document["id"])


def test_folder_and_book_shelves_are_independent_and_resume_is_private(store, users, document):
    uid, other = users[0]["id"], users[1]["id"]
    folder = store.register_document("library", "file:///books", "/books", title="My books", format="folder")
    folder_key = folder_identity("library", "/books")
    assert folder_key == folder_identity("library", "/books/./")
    store.add_to_shelf(uid, folder["id"])
    store.add_to_shelf(uid, folder["id"])
    store.add_to_shelf(uid, document["id"])
    assert len(store.list_bookshelf(uid)["items"]) == 2
    assert store.list_bookshelf(other)["items"] == []
    assert store.list_readings(uid)["items"] == []
    assert store.get_progress(uid, folder["id"]) is None
    assert store.get_folder_resume(uid, folder_key) is None

    settings = store.get_settings(uid)
    store.open_reading(uid, document["id"], settings["reading_epoch"], settings["progress_epoch"], folder_key)
    store.save_progress(uid, document["id"], {"chunkIndex": 2}, 0, "tab", 1,
                        settings["progress_epoch"], progress=35)
    item = next(item for item in store.list_bookshelf(uid)["items"] if item["format"] == "folder")
    assert item["document_id"] == folder["id"]
    assert item["title"] == "My books"
    assert item["resume_document_id"] == document["id"]
    assert item["resume_title"] == "Book one"
    assert item["progress"] == 35 and item["position"] == {"chunkIndex": 2}
    store.add_to_shelf(other, folder["id"])
    other_item = store.list_bookshelf(other)["items"][0]
    assert other_item["resume_title"] is None and other_item["progress"] is None
    with pytest.raises(NotFound):
        store.remove_from_shelf(other, document["id"])

    store.remove_from_shelf(uid, document["id"])
    assert store.is_on_shelf(uid, folder["id"])
    assert store.get_progress(uid, document["id"])["position"] == {"chunkIndex": 2}
    store.clear_progress(uid)
    assert store.get_folder_resume(uid, folder_key) is None
    assert store.get_progress(uid, document["id"]) is None
    assert store.is_on_shelf(uid, folder["id"])
    assert store.list_bookshelf(uid)["items"][0]["resume_title"] is None
    store.remove_from_shelf(uid, folder["id"])
    assert store.is_on_shelf(other, folder["id"])


@pytest.mark.parametrize("maintenance", ["cleanup", "cap"])
def test_folder_shelf_protects_only_its_account_library_and_descendants(store, users, document, maintenance):
    uid, other = users[0]["id"], users[1]["id"]
    folder = store.register_document("library", "file:///books", "/books", title="My books", format="folder")
    nested = store.register_document("library", "file:///books/part/two.txt", "/books/part/two.txt", title="Chapter two", format="txt")
    sibling = store.register_document("library", "file:///books-other/one.txt", "/books-other/one.txt", format="txt")
    foreign = store.register_document("other-library", "file:///books/one.txt", "/books/one.txt", format="txt")
    folder_key = folder_identity("library", "/books")
    store.add_to_shelf(uid, folder["id"])
    for user_id in (uid, other):
        settings = store.get_settings(user_id)
        for ref, key in ((document, folder_key), (nested, folder_key),
                         (sibling, folder_identity("library", "/books-other")),
                         (foreign, folder_identity("other-library", "/books"))):
            store.open_reading(user_id, ref["id"], settings["reading_epoch"], settings["progress_epoch"], key)
            store.save_progress(user_id, ref["id"], {"chunkIndex": 3}, 0, "tab", 1,
                                settings["progress_epoch"], progress=42)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE reading_progress SET updated_at=0")
        connection.execute("UPDATE folder_resume SET updated_at=0")
        connection.execute("UPDATE reading_history SET last_opened_at=0")
        connection.execute("UPDATE document_refs SET last_verified_at=0")

    assert store.get_folder_resume(uid, folder_key)["last_document_id"] == nested["id"]
    assert store.get_folder_resume(other, folder_key) is None
    assert store.get_progress(uid, document["id"])["position"] == {"chunkIndex": 3}
    assert store.get_progress(uid, nested["id"])["position"] == {"chunkIndex": 3}
    assert store.get_progress(uid, sibling["id"]) is None
    assert store.get_progress(uid, foreign["id"]) is None
    assert store.get_progress(other, document["id"]) is None
    if maintenance == "cleanup":
        store.cleanup()
    else:
        with store._connection(write=True) as connection:
            for user_id in (uid, other):
                store._cap(connection, "reading_progress", user_id, "updated_at", 0)
                store._cap(connection, "folder_resume", user_id, "updated_at", 0)
    with sqlite3.connect(store.path) as connection:
        assert set(connection.execute("SELECT user_id,document_id FROM reading_progress")) == {
            (uid, document["id"]), (uid, nested["id"])}
        assert connection.execute("SELECT user_id,folder_key FROM folder_resume").fetchall() == [(uid, folder_key)]
    item = store.list_bookshelf(uid)["items"][0]
    assert item["resume_title"] == "Chapter two"
    assert item["progress"] == 42 and item["position"] == {"chunkIndex": 3}
    assert opened(store, uid, document["id"])["progress"]["position"] == {"chunkIndex": 3}


def test_expired_reads_and_progress_are_filtered_without_cleanup(store, users, document):
    uid = users[0]["id"]
    opened(store, uid, document["id"])
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE reading_history SET last_opened_at=0")
        connection.execute("UPDATE reading_progress SET updated_at=0")
    assert store.list_readings(uid)["items"] == []
    assert store.get_progress(uid, document["id"]) is None
    opened(store, uid, document["id"])
    assert store.get_progress(uid, document["id"])["revision"] == 0


def test_position_validation_and_changed_content(store, users, document):
    uid = users[0]["id"]
    initial = opened(store, uid, document["id"])
    epoch = initial["settings"]["progress_epoch"]
    for position in ({"source_path": "/etc/passwd"}, {"chunkIndex": float("nan")}, {"engine": "x" * 20000}, []):
        with pytest.raises(ValidationError):
            store.save_progress(uid, document["id"], position, 0, "tab", 1, epoch)
    store.register_document("library", "file:///books/one.txt", "/books/one.txt", format="txt", content_version="v2")
    with pytest.raises(ConflictError):
        store.save_progress(uid, document["id"], {"chunkIndex": 1}, 0, "tab", 1, epoch, content_version="v1")


def test_pagination_is_stable_and_bounded(store, users):
    uid = users[0]["id"]
    for i in range(5):
        query = {"query": str(i)}
        identifier = store.begin_search(uid, query, {}, "v1")
        assert store.complete_search(uid, identifier, query, {}, i)
    first = store.list_searches(uid, limit=2)
    second = store.list_searches(uid, limit=2, cursor=first["next_cursor"])
    third = store.list_searches(uid, limit=2, cursor=second["next_cursor"])
    assert len({item["id"] for page in (first, second, third) for item in page["items"]}) == 5
    assert third["next_cursor"] is None
    for cursor in ("invalid", "e30", "WzAsbnVsbF0"):
        with pytest.raises(ValidationError):
            store.list_searches(uid, cursor=cursor)
    with pytest.raises(ValidationError):
        store.list_searches(uid, limit=101)


def test_online_backup_restore_invalidates_sessions_and_generations(store, users, document, tmp_path):
    uid = users[0]["id"]
    token, _ = store.authenticate("admin", PASSWORD)
    opened(store, uid, document["id"])
    old_settings = store.get_settings(uid)
    pending = store.begin_search(uid, {"query": "unfinished"}, {}, "v1")
    backup = tmp_path / "backup.sqlite3"
    key = store.secret()
    store.backup(backup)
    with pytest.raises(ValidationError):
        store.backup(backup)
    store.clear_progress(uid)
    assert store.restore(backup)["sessions_revoked"]
    assert store.get_session(token) is None
    assert store.secret() == key
    assert store.get_progress(uid, document["id"]) is not None
    assert store.get_settings(uid)["progress_epoch"] > old_settings["progress_epoch"]
    assert not store.complete_search(uid, pending, {"query": "unfinished"}, {}, 2)
    assert store.authenticate("admin", PASSWORD)[1]["user"]["id"] == uid


def test_failed_restore_leaves_database_available(store, users, tmp_path):
    bad = tmp_path / "corrupt.sqlite3"
    bad.write_bytes(b"not a database")
    with pytest.raises(ValidationError):
        store.restore(bad)
    assert len(store.list_users()) == 2


def test_delete_account_cascades_personal_data_but_preserves_library(store, users, document):
    uid = users[1]["id"]
    store.authenticate("member", PASSWORD)
    opened(store, uid, document["id"])
    store.add_to_shelf(uid, document["id"])
    store.begin_search(uid, {"query": "hello"}, {}, "v1")
    store.update_user(users[0]["id"], uid, "delete")
    with sqlite3.connect(store.path) as connection:
        for table in ("sessions", "user_settings", "search_history", "reading_history", "reading_progress", "folder_resume", "bookshelf"):
            assert connection.execute("SELECT COUNT(*) FROM " + table + " WHERE user_id=?", (uid,)).fetchone()[0] == 0
    assert store.get_document(document["id"])["id"] == document["id"]


def test_cli_init_and_restore_require_explicit_management(tmp_path):
    path = tmp_path / "cli.sqlite3"
    process = subprocess.run([sys.executable, "manage.py", "--db", str(path), "init", "--username", "first", "--password-stdin"], input=PASSWORD + "\n", text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    assert Store(path).authenticate("first", PASSWORD)[1]["user"]["role"] == "admin"
    assert PASSWORD not in process.stdout
    second = subprocess.run([sys.executable, "manage.py", "--db", str(path), "init"], input="", text=True, capture_output=True)
    assert second.returncode == 0  # Idempotent; no password prompt when an admin exists.
