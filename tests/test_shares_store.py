"""Durable public capabilities remain separate from account sessions."""
import concurrent.futures
import sqlite3

import pytest

from accounts import DAY, NotFound, Store, ValidationError


PASSWORD = "share test password longer than fifteen"


@pytest.fixture
def share_site(tmp_path):
    store = Store(tmp_path / "accounts.sqlite3")
    store.initialize()
    admin = store.create_user("admin", PASSWORD, role="admin", must_change_password=False)
    member = store.create_user("member", PASSWORD, must_change_password=False)
    document = store.register_document("library", "file:///books/one.txt", "/books/one.txt", title="Book one", format="txt")
    return store, admin, member, document


def test_book_and_folder_links_are_unique_persistent_and_default_to_seven_days(share_site):
    store, admin, _, document = share_site
    folder = store.register_document("library", "file:///books", "/books", title="Books", format="folder")
    book_share = store.create_share(admin["id"], document["id"])
    folder_share = store.create_share(admin["id"], folder["id"], 30)
    another = store.create_share(admin["id"], document["id"])
    assert len({book_share["token"], folder_share["token"], another["token"]}) == 3
    assert len(book_share["token"]) == 64
    assert book_share["expires_at"] - book_share["created_at"] == 7 * DAY
    assert folder_share["expires_at"] - folder_share["created_at"] == 30 * DAY
    restored_store = Store(store.path)
    assert restored_store.get_public_share(book_share["token"])["title"] == "Book one"
    assert restored_store.get_public_share(folder_share["token"])["format"] == "folder"
    assert not restored_store.get_public_share(book_share["token"])["expired"]


def test_management_is_owned_and_paginated_even_when_sharing_the_same_document(share_site):
    store, admin, member, document = share_site
    shares = [store.create_share(admin["id"], document["id"]) for _ in range(3)]
    own = store.create_share(member["id"], document["id"])
    first = store.list_shares(admin["id"], limit=2)
    second = store.list_shares(admin["id"], limit=2, cursor=first["next_cursor"])
    assert {row["id"] for row in first["items"] + second["items"]} == {row["id"] for row in shares}
    assert second["next_cursor"] is None
    assert store.list_shares(member["id"])["items"] == [own]
    for method in (store.update_share, store.delete_share):
        with pytest.raises(NotFound):
            method(member["id"], shares[0]["id"])
    assert store.get_public_share(shares[0]["token"])


def test_expiration_boundary_is_immediate_and_expired_links_can_be_renewed(share_site, monkeypatch):
    store, admin, _, document = share_site
    monkeypatch.setattr("accounts.time.time", lambda: 1000000)
    share = store.create_share(admin["id"], document["id"], 1)
    monkeypatch.setattr("accounts.time.time", lambda: share["expires_at"])
    assert store.get_public_share(share["token"]) is None
    assert store.list_shares(admin["id"])["items"][0]["expired"]
    renewed = store.update_share(admin["id"], share["id"], 14)
    assert renewed["token"] == share["token"]
    assert renewed["expires_at"] == share["expires_at"] + 14 * DAY
    assert renewed["created_at"] == share["created_at"]
    assert store.get_public_share(share["token"])


def test_delete_is_permanent_and_independent_of_other_links(share_site):
    store, admin, _, document = share_site
    share = store.create_share(admin["id"], document["id"])
    other = store.create_share(admin["id"], document["id"])
    assert store.delete_share(admin["id"], share["id"])["deleted"]
    assert store.get_public_share(share["token"]) is None
    assert Store(store.path).get_public_share(share["token"]) is None
    assert store.get_public_share(other["token"])
    with pytest.raises(NotFound):
        store.update_share(admin["id"], share["id"])


@pytest.mark.parametrize("invalid", [0, -1, 3651, "7", 7.0, True, None])
def test_expiration_input_is_strict_and_invalid_updates_do_not_change_the_link(share_site, invalid):
    store, admin, _, document = share_site
    share = store.create_share(admin["id"], document["id"])
    with pytest.raises(ValidationError):
        store.create_share(admin["id"], document["id"], invalid)
    with pytest.raises(ValidationError):
        store.update_share(admin["id"], share["id"], invalid)
    assert store.get_public_share(share["token"])["expires_at"] == share["expires_at"]


def test_disabled_or_deleted_owner_cannot_publish_or_manage_links(share_site):
    store, admin, member, document = share_site
    share = store.create_share(member["id"], document["id"])
    store.update_user(admin["id"], member["id"], "disable")
    assert store.get_public_share(share["token"]) is None
    for method, arguments in ((store.create_share, (document["id"],)), (store.list_shares, ()),
                              (store.update_share, (share["id"],)), (store.delete_share, (share["id"],))):
        with pytest.raises(NotFound):
            method(member["id"], *arguments)
    store.update_user(admin["id"], member["id"], "enable")
    assert store.get_public_share(share["token"])
    store.update_user(admin["id"], member["id"], "delete")
    assert store.get_public_share(share["token"]) is None
    with sqlite3.connect(store.path) as connection:
        assert not connection.execute("SELECT 1 FROM share_links WHERE id=?", (share["id"],)).fetchone()


@pytest.mark.parametrize("token", [None, "", [], "a" * 64, "a" * 65, "' OR 1=1 --", "/" * 64])
def test_unknown_and_malformed_public_tokens_return_no_share(share_site, token):
    store, _, _, _ = share_site
    assert store.get_public_share(token) is None


def test_old_schema_is_extended_atomically_across_workers_without_changing_existing_data(share_site):
    store, admin, _, document = share_site
    secret = store.secret()
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE share_links")
    def create(_):
        worker = Store(store.path)
        assert worker.initialized()
        return worker.create_share(admin["id"], document["id"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        shares = list(pool.map(create, range(4)))
    assert len({share["id"] for share in shares}) == 4
    assert store.get_document(document["id"]) == document
    assert store.secret() == secret
    assert len(store.list_users()) == 2
    assert len(store.list_shares(admin["id"])["items"]) == 4
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_cleanup_preserves_share_targets_including_expired_links_that_can_be_renewed(share_site):
    store, admin, _, document = share_site
    share = store.create_share(admin["id"], document["id"])
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE document_refs SET last_verified_at=0")
        connection.execute("UPDATE share_links SET expires_at=0")
    assert store.cleanup()["document_refs"] == 0
    assert store.get_document(document["id"])
    store.update_share(admin["id"], share["id"])
    assert store.get_public_share(share["token"])
    store.delete_share(admin["id"], share["id"])
    assert store.cleanup()["document_refs"] == 1


@pytest.mark.parametrize('expired', [False, True])
def test_cleanup_preserves_folder_share_chapter_ids_until_cancellation(share_site, tmp_path, expired):
    store, admin, _, book = share_site
    root = tmp_path / 'books' / 'shared'
    first, nested = root / 'first.txt', root / 'volume' / 'next.txt'
    nested.parent.mkdir(parents=True)
    first.write_text('First public chapter.', encoding='utf-8')
    nested.write_text('Second public chapter.', encoding='utf-8')
    sibling = root.parent / 'shared-old' / 'private.txt'
    sibling.parent.mkdir()
    sibling.write_text('Outside the shared directory.', encoding='utf-8')
    folder = store.register_document('library', root.as_uri(), str(root), format='folder')
    chapters = [store.register_document('library', path.as_uri(), str(path), format='txt')
                for path in (first, nested)]
    outside = store.register_document('library', sibling.as_uri(), str(sibling), format='txt')
    other_library = store.register_document('different-library', first.as_uri(), str(first), format='txt')
    folder_share = store.create_share(admin['id'], folder['id'])
    book_share = store.create_share(admin['id'], book['id'])
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE document_refs SET last_verified_at=0')
        if expired:
            connection.execute('UPDATE share_links SET expires_at=0')

    assert store.cleanup()['document_refs'] == 2
    for document in [folder, book, *chapters]:
        assert store.get_document(document['id'])['id'] == document['id']
    for document in (outside, other_library):
        with pytest.raises(NotFound):
            store.get_document(document['id'])
    if expired:
        store.update_share(admin['id'], folder_share['id'])
    assert store.get_public_share(folder_share['token'])

    store.delete_share(admin['id'], folder_share['id'])
    assert store.cleanup()['document_refs'] == 3
    for document in [folder, *chapters]:
        with pytest.raises(NotFound):
            store.get_document(document['id'])
    assert store.get_document(book['id'])
    store.delete_share(admin['id'], book_share['id'])
    assert store.cleanup()['document_refs'] == 1


def test_backup_restore_preserves_links_and_accepts_older_backups(share_site, tmp_path):
    store, admin, _, document = share_site
    share = store.create_share(admin["id"], document["id"])
    backup = store.backup(tmp_path / "backup.sqlite3")
    store.delete_share(admin["id"], share["id"])
    store.restore(backup)
    assert store.get_public_share(share["token"])
    with sqlite3.connect(backup) as connection:
        connection.execute("DROP TABLE share_links")
    store.restore(backup)
    assert store.list_shares(admin["id"])["items"] == []
    assert store.create_share(admin["id"], document["id"])


def test_missing_or_unsupported_document_cannot_be_shared(share_site):
    store, admin, _, _ = share_site
    with pytest.raises(NotFound):
        store.create_share(admin["id"], "missing")
    unknown = store.register_document("library", "file:///books/data.exe", "/books/data.exe", format="exe")
    with pytest.raises(ValidationError):
        store.create_share(admin["id"], unknown["id"])
