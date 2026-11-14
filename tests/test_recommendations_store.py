"""Search recommendation retention, account isolation and additive storage."""
import sqlite3

import pytest

from accounts import NotFound, Store
from test_accounts import store, users, document


def test_recommendations_are_private_deduplicated_and_independent_of_reading(store, users, document):
    uid, other = (user['id'] for user in users)
    store.save_settings(uid, flags={'search_history_enabled': False})
    store.add_recommendations(uid, [document['id'], document['id']], '星河' * 150)
    items = store.list_recommendations(uid)
    assert len(items) == 1
    assert items[0]['document_id'] == document['id']
    assert items[0]['source_query'] == ('星河' * 150)[:200]
    assert items[0]['title'] == 'Book one'
    assert items[0]['format'] == 'txt'
    assert items[0]['source_path'] == '/books/one.txt'
    assert items[0]['ipath'] == ''
    assert items[0]['library_key'] == 'library'
    assert store.list_recommendations(other) == []
    assert store.list_recommendations(uid, library_key='another-library') == []
    assert store.list_readings(uid)['items'] == []
    assert store.list_bookshelf(uid)['items'] == []
    with pytest.raises(NotFound):
        store.remove_recommendation(other, document['id'])
    assert Store(store.path).list_recommendations(uid) == items
    store.remove_recommendation(uid, document['id'])
    assert store.list_recommendations(uid) == []


def test_pool_rolls_to_newest_sixty_and_refreshes_existing_candidates(store, users, document, monkeypatch):
    uid = users[0]['id']
    documents = [document] + [
        store.register_document('library', f'file:///books/{index}.txt', f'/books/{index}.txt', format='txt')
        for index in range(61)
    ]
    monkeypatch.setattr('accounts.time.time', lambda: 1000000)
    store.add_recommendations(uid, [item['id'] for item in documents[:60]], 'first')
    monkeypatch.setattr('accounts.time.time', lambda: 1000001)
    store.add_recommendations(uid, [document['id']], 'refreshed')
    monkeypatch.setattr('accounts.time.time', lambda: 1000002)
    store.add_recommendations(uid, [item['id'] for item in documents[60:]], 'latest')
    items = store.list_recommendations(uid)
    assert len(items) == 60
    assert set(item['document_id'] for item in items) == {item['id'] for item in [document] + documents[3:]}
    assert {item['source_query'] for item in items[:2]} == {'latest'}
    assert items[2]['document_id'] == document['id']
    assert items[2]['source_query'] == 'refreshed'


def test_search_completion_adds_candidates_once_and_removal_survives_reload(store, users, document):
    uid = users[0]['id']
    query, snapshot = {'query': 'galaxy'}, {'stem': 0}
    event = store.begin_search(uid, query, snapshot, 'scope')
    assert store.complete_search(uid, event, query, snapshot, 1, recommendation_ids=[document['id']])
    assert store.list_recommendations(uid)[0]['source_query'] == 'galaxy'
    store.remove_recommendation(uid, document['id'])
    assert not store.complete_search(uid, event, query, snapshot, 1, recommendation_ids=[document['id']])
    assert store.list_recommendations(uid) == []


def test_stale_searches_and_paused_history_do_not_automatically_save_candidates(store, users, document):
    uid = users[0]['id']
    query = {'query': 'old search'}
    event = store.begin_search(uid, query, {}, 'scope')
    store.delete_search(uid)
    assert not store.complete_search(uid, event, query, {}, 1, recommendation_ids=[document['id']])
    event = store.begin_search(uid, query, {}, 'scope')
    store.save_settings(uid, flags={'search_history_enabled': False})
    assert not store.complete_search(uid, event, query, {}, 1, recommendation_ids=[document['id']])
    assert store.list_recommendations(uid) == []


def test_saved_recommendations_survive_cleanup_then_allow_document_collection(store, users, document):
    uid = users[0]['id']
    store.add_recommendations(uid, [document['id']], 'keep')
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE document_refs SET last_verified_at=0')
    assert store.cleanup()['document_refs'] == 0
    assert store.get_document(document['id'])['id'] == document['id']
    store.remove_recommendation(uid, document['id'])
    assert store.cleanup()['document_refs'] == 1


def test_existing_version_one_database_gains_storage_and_restore_resets_extension_state(store, users, document, tmp_path):
    uid = users[0]['id']
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP TABLE search_recommendations')
    backup = tmp_path / 'before-recommendations.sqlite3'
    store.backup(backup)
    assert store.list_recommendations(uid) == []
    store.add_recommendations(uid, [document['id']], 'new')
    assert store.get_document(document['id'])['title'] == 'Book one'
    store.restore(backup)
    assert store.list_recommendations(uid) == []
    store.add_recommendations(uid, [document['id']], 'restored')
    assert Store(store.path).list_recommendations(uid)[0]['source_query'] == 'restored'


def test_deleting_account_cascades_recommendations_without_touching_another_account(store, users, document):
    admin, member = users
    for user in users:
        store.add_recommendations(user['id'], [document['id']], user['id'])
    store.update_user(admin['id'], member['id'], 'delete')
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM search_recommendations WHERE user_id=?', (member['id'],)).fetchone()[0] == 0
    assert len(store.list_recommendations(admin['id'])) == 1
