"""Real Recoll + Bottle + SQLite integration, confined to temporary directories.

These tests require the system Recoll Python bindings and ``recollindex``. They
never connect to an existing Recoll configuration or a running web service.
"""
import importlib.util
import csv
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
import uuid

import bottle
import pytest
from webtest import TestApp

from accounts import Store


REPOSITORY = Path(__file__).resolve().parents[1]
PASSWORD = 'integration account password long enough'


@pytest.fixture(scope='module')
def recoll_index(tmp_path_factory):
    pytest.importorskip('recoll.recoll', reason='system Recoll Python bindings are required')
    if not shutil.which('recollindex'):
        pytest.skip('recollindex is required for the real-index integration tests')
    root = tmp_path_factory.mktemp('recoll-integration-index')
    books, config, index = root / 'books', root / 'conf', root / 'index'
    books.mkdir()
    config.mkdir()
    (books / 'alpha.txt').write_text('sharedgalaxy alphatoken universes\nA first chapter with enough bytes for Range.\n', encoding='utf-8')
    (books / 'beta.txt').write_text('sharedgalaxy betatoken universe\nThe second book has a different chapter.\n', encoding='utf-8')
    (books / 'attack.txt').write_text('markupattack <img src=x onerror=alert(1)> and <script>alert(1)</script>\n', encoding='utf-8')
    (books / 'README').write_text('extensionlessreadme\nThis document is plain text without a filename extension.\n', encoding='utf-8')
    (books / 'danger.html').write_text('<!doctype html><html><head><title>Sandbox book</title></head><body>sharedgalaxy gammapulse<script>fetch("/api/account")</script><form action="/logout" method="post"><button>submit</button></form></body></html>', encoding='utf-8')
    (config / 'recoll.conf').write_text(
        f'topdirs = {books}\ndbdir = {index}\nindexstemminglanguages = english\n'
        f'loglevel = 1\nlogfilename = {root / "index.log"}\n', encoding='utf-8')
    result = subprocess.run(['recollindex', '-c', str(config), '-z'], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr[-4000:] or result.stdout[-4000:]
    return SimpleNamespace(root=root, books=books, index=index)


@pytest.fixture
def site(tmp_path, monkeypatch, recoll_index):
    config = tmp_path / 'recoll-config'
    config.mkdir()
    base_config = (
        f'topdirs = {recoll_index.books}\ndbdir = {recoll_index.index}\n'
        'indexstemminglanguages = english\nwebui_perpage = 1\nwebui_stem = 0\n'
        'webui_collapsedups = 0\nwebui_logquery = 0\n')
    (config / 'recoll.conf').write_text(base_config, encoding='utf-8')
    cache = tmp_path / 'cache'
    cache.mkdir()
    monkeypatch.setenv('RECOLL_CONFDIR', str(config))
    monkeypatch.setenv('RECOLL_WEBUI_DB_PATH', str(tmp_path / 'app.sqlite3'))
    monkeypatch.setenv('RECOLL_TMPDIR', str(cache))
    monkeypatch.setenv('RECOLL_WEBUI_COOKIE_SECURE', 'auto')
    monkeypatch.delenv('RECOLL_EXTRACONFDIRS', raising=False)
    monkeypatch.delenv('RECOLL_WEBUI_TRUSTED_PROXIES', raising=False)
    store = Store()
    store.initialize()
    alice = store.create_user('alice', PASSWORD, role='admin', must_change_password=False)
    bob = store.create_user('bob', PASSWORD, must_change_password=False)
    app = bottle.Bottle()
    app.catchall = False  # Surface implementation failures with their real traceback.
    name = '_recoll_webui_integration_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, REPOSITORY / 'webui.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    template_paths = list(bottle.TEMPLATE_PATH)
    try:
        bottle.default_app.push(app)
        try:
            spec.loader.exec_module(module)
        finally:
            assert bottle.default_app.pop() is app
        result = SimpleNamespace(app=app, module=module, store=store, alice=alice, bob=bob,
                                 config=config, base_config=base_config, books=recoll_index.books,
                                 root=tmp_path)
        yield result
    finally:
        sys.modules.pop(name, None)
        bottle.TEMPLATE_PATH[:] = template_paths


def client_for(site, username='alice', prefix=''):
    client = TestApp(site.app, extra_environ={'SCRIPT_NAME': prefix} if prefix else {})
    page = client.get('/login')
    form = page.forms[0]
    form['username'], form['password'] = username, PASSWORD
    form.submit(status=302)
    return client


def identity(client):
    return client.get('/api/account').json


def post_json(client, path, data, status=200):
    return client.post_json(path, data, headers={'X-CSRF-Token': identity(client)['csrf']}, status=status)


def post_form(client, path, data=None, status=(302, 303)):
    return client.post(path, dict(data or {}, csrf=identity(client)['csrf']), status=status)


def local_url(value):
    parsed = urlsplit(value)
    return urlunsplit(('', '', parsed.path, parsed.query, ''))


def changed_query(value, **changes):
    parsed = urlsplit(value)
    query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
    query.update(changes)
    return urlunsplit(('', '', parsed.path, urlencode(query), ''))


def book_for(client, query='alphatoken'):
    return client.get('/api/reader/book', {'query': query, 'resnum': 0}).json['book']


def search_for(client, query='sharedgalaxy'):
    response = post_form(client, '/search', {'query': query, 'sort': 'filename', 'ascending': '1'}, status=303)
    return local_url(response.location)


def history_rows(site, user=None):
    return site.store.list_searches((user or site.alice)['id'])['items']


def found(response):
    return int(response.html.find(id='found').find('b').text)


def progress_body(client, book, revision=0, sequence=1, writer='reader-one', value=37.5):
    return {'handle': book['openHandle'], 'position': {'version': 1, 'format': 'txt', 'chunkIndex': 0, 'charOffset': 23},
            'revision': revision, 'writer_id': writer, 'writer_seq': sequence,
            'epoch': identity(client)['settings']['progress_epoch'],
            'content_version': book['contentVersion'], 'progress': value}


def test_search_event_completes_once_and_reads_never_add_events(site):
    client = client_for(site)
    target = search_for(client)
    event_id = parse_qs(urlsplit(target).query)['search_id'][0]
    assert history_rows(site) == []  # POST creates a pending event, not a fake success.
    assert site.store.get_search(site.alice['id'], event_id)['status'] == 'pending'
    first = client.get(target)
    assert found(first) == 3
    assert len(first.html.select('.search-result')) == 1
    assert first.html.select_one('.app-result-heading .app-folder-name').get_text() == site.books.name
    assert history_rows(site)[0]['result_count'] == 3
    completed_at = history_rows(site)[0]['completed_at']
    client.get(target)
    client.get(changed_query(target, page=2))
    client.get(changed_query(target, page=0))
    query = urlsplit(target).query
    client.get('/json?' + query + '&page=0')
    client.get('/csv?' + query + '&page=0')
    client.get('/preview/0?' + query)
    client.get('/download/0?' + query)
    client.get('/reader?' + query + '&resnum=0')
    book_for(client)
    assert len(history_rows(site)) == 1
    assert history_rows(site)[0]['completed_at'] == completed_at
    repeated = search_for(client)
    client.get(repeated)
    assert len(history_rows(site)) == 2  # A second deliberate submit is a new event.


def test_legacy_get_zero_results_execution_failure_and_disabled_history(site):
    client = client_for(site)
    client.get('/results', {'query': 'sharedgalaxy'})
    assert history_rows(site) == []
    zero = search_for(client, 'certainlymissingwordzz')
    assert found(client.get(zero)) == 0
    assert history_rows(site)[0]['result_count'] == 0
    failed = search_for(client, 'AND')
    failed_id = parse_qs(urlsplit(failed).query)['search_id'][0]
    client.get(failed, status=400)
    assert site.store.get_search(site.alice['id'], failed_id)['status'] == 'failed'
    assert len(history_rows(site)) == 1
    site.store.save_settings(site.alice['id'], flags={'search_history_enabled': False})
    target = search_for(client)
    assert 'search_id=' not in target
    client.get(target)
    with sqlite3.connect(site.store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM search_history WHERE status='pending'").fetchone()[0] == 0
    assert len(history_rows(site)) == 1


def test_search_snapshots_are_not_restored_for_another_account(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    site.store.save_settings(site.alice['id'], preferences={'stem': 1})
    target = search_for(alice, 'universe')
    site.store.save_settings(site.alice['id'], preferences={'stem': 0})
    # The initiating event keeps its captured stemming setting despite a later
    # settings change in another tab. The actual Recoll result count proves it.
    assert found(alice.get(target)) == 2
    assert history_rows(site)[0]['settings_snapshot']['stem'] == 1
    assert found(bob.get(target)) == 1
    assert history_rows(site, site.bob) == []
    assert history_rows(site)[0]['result_count'] == 2
    foreign_id = history_rows(site)[0]['id']
    own_replay = post_form(bob, '/search', {'query': 'universe', 'sort': 'filename', 'ascending': '1', 'replay_id': foreign_id}, status=303)
    assert found(bob.get(local_url(own_replay.location))) == 1
    assert history_rows(site, site.bob)[0]['settings_snapshot']['stem'] == 0


@pytest.mark.parametrize('pause_recording', [False, True])
def test_retained_history_replays_snapshot_after_other_deletion_or_recording_pause(site, pause_recording):
    client = client_for(site)
    site.store.save_settings(site.alice['id'], preferences={'stem': 1})
    original = search_for(client, 'universe')
    assert found(client.get(original)) == 2
    survivor_id = history_rows(site)[0]['id']
    disposable = search_for(client, 'alphatoken')
    client.get(disposable)
    disposable_id = history_rows(site)[0]['id']
    assert disposable_id != survivor_id
    post_form(client, '/history/search/delete', {'id': disposable_id})
    site.store.save_settings(site.alice['id'], preferences={'stem': 0},
                             flags={'search_history_enabled': False} if pause_recording else None)
    history = client.get('/history')
    replay_form = next(form for form in history.forms.values() if 'replay_id' in form.fields)
    assert replay_form['replay_id'].value == survivor_id
    replay = replay_form.submit(status=303)
    destination = local_url(replay.location)
    assert found(client.get(destination)) == 2
    assert len(history_rows(site)) == (1 if pause_recording else 2)
    if pause_recording:
        with sqlite3.connect(site.store.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM search_history WHERE status='pending'").fetchone()[0] == 0
    else:
        assert parse_qs(urlsplit(destination).query)['search_id'][0] != survivor_id
    assert site.store.get_search(site.alice['id'], survivor_id)['settings_snapshot']['stem'] == 1


def test_snapshot_keeps_old_result_navigation_and_replay_consistent_with_current_admin_policy(site):
    client = client_for(site)
    site.store.save_settings(site.alice['id'], preferences={'stem': 1})
    original = search_for(client, 'universe')
    assert found(client.get(original)) == 2
    original_id = parse_qs(urlsplit(original).query)['search_id'][0]
    site.store.save_settings(site.alice['id'], preferences={'stem': 0})
    # Restoring an old event must also govern routes resolving a result number.
    query = urlsplit(original).query
    preview = client.get('/preview/1?' + query)
    assert 'betatoken' in preview.text
    assert b'betatoken' in client.get('/download/1?' + query).body
    history = client.get('/history')
    form = next(form for form in history.forms.values() if 'replay_id' in form.fields)
    assert form['replay_id'].value == original_id
    replay = form.submit(status=303)
    replay_url = local_url(replay.location)
    replay_id = parse_qs(urlsplit(replay_url).query)['search_id'][0]
    assert replay_id != original_id
    assert site.store.get_search(site.alice['id'], replay_id)['settings_snapshot']['stem'] == 1
    assert found(client.get(replay_url)) == 2
    assert len(history_rows(site)) == 2
    # A subsequent administrator lock takes priority over every old snapshot.
    (site.config / 'recoll.conf').write_text(site.base_config + 'webui_nosettings = 1\nwebui_stem = 0\n', encoding='utf-8')
    assert found(client.get(original)) == 1
    client.get('/preview/1?' + query, status=404)
    locked_history = client.get('/history')
    locked_form = next(form for form in locked_history.forms.values() if 'replay_id' in form.fields)
    locked_replay = locked_form.submit(status=303)
    locked_url = local_url(locked_replay.location)
    assert found(client.get(locked_url)) == 1
    locked_id = parse_qs(urlsplit(locked_url).query)['search_id'][0]
    assert site.store.get_search(site.alice['id'], locked_id)['settings_snapshot']['stem'] == 0


def test_search_clear_prevents_old_result_refresh_from_recreating_history(site):
    client = client_for(site)
    target = search_for(client)
    client.get(target)
    post_form(client, '/history/search/delete')
    assert history_rows(site) == []
    assert found(client.get(target)) == 3
    assert history_rows(site) == []


@pytest.mark.parametrize('query,highlight', [('filename:attack.txt', 1), ('filename:attack.txt', 0), ('markupattack', 1), ('markupattack', 0)])
def test_plaintext_abstract_fallback_never_executes_document_markup(site, query, highlight):
    client = client_for(site)
    # Field-only searches produce no matching text abstract in real Recoll, so
    # the web interface must escape its plain-text stored-abstract fallback.
    result = client.get('/results', {'query': query, 'highlight': highlight})
    assert found(result) == 1
    snippet = result.html.select_one('.search-result-snippet')
    assert 'markupattack' in snippet.get_text()
    assert snippet.find('img') is None
    assert snippet.find('script') is None


def test_bookshelf_is_account_scoped_and_is_independent_of_history(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    book = book_for(alice)
    assert book['folder_name'] == site.books.name
    post_json(alice, '/api/bookshelf/add', {'handle': book['openHandle']})
    shelf = alice.get('/bookshelf')
    assert book['title'] in shelf.text
    assert shelf.html.select_one('.account-book-body .app-folder-name').get_text() == site.books.name
    assert book['title'] not in bob.get('/bookshelf').text
    post_json(bob, '/api/bookshelf/remove', {'document_id': book['documentId']}, status=404)
    post_json(bob, '/api/bookshelf/add', {'handle': book['openHandle']}, status=403)
    post_form(alice, '/history/reading/clear', {'include_progress': '1'})
    assert site.store.is_on_shelf(site.alice['id'], book['documentId'])
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], book['documentId']) is None
    # The shared document library does permit Bob to obtain his own handle.
    own = bob.get('/api/reader/book', {'document_id': book['documentId']}).json['book']
    post_json(bob, '/api/bookshelf/add', {'handle': own['openHandle']})
    post_form(alice, '/bookshelf/remove', {'document_id': book['documentId']})
    assert not site.store.is_on_shelf(site.alice['id'], book['documentId'])
    assert site.store.is_on_shelf(site.bob['id'], book['documentId'])


def test_book_document_id_reopens_without_old_search_or_result_position(site):
    client = client_for(site)
    first = book_for(client)
    client.get('/results', {'query': 'betatoken', 'sort': 'filename', 'ascending': '1'})
    reopened = client.get('/api/reader/book', {'document_id': first['documentId'], 'query': 'betatoken', 'resnum': 99999}).json['book']
    assert reopened['documentId'] == first['documentId']
    assert reopened['path'] == str(site.books / 'alpha.txt')
    assert b'alphatoken' in client.get(reopened['url']).body
    folder = client.get('/api/reader/folder', {'document_id': first['documentId']}).json
    same = next(item for item in folder['items'] if item['path'] == first['path'])
    assert same['documentId'] == first['documentId']
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], first['documentId']) is None


def test_extensionless_plaintext_keeps_indexed_format_when_reopened_by_document_id(site):
    client = client_for(site)
    results = client.get('/results', {'query': 'extensionlessreadme'})
    assert found(results) == 1
    assert any('/reader?' in link.get('href', '') for link in results.html.select('.search-result a'))
    original = book_for(client, 'extensionlessreadme')
    assert original['format'] == 'txt'
    assert original['path'] == str(site.books / 'README')
    reopened = client.get('/api/reader/book', {'document_id': original['documentId']}).json['book']
    assert reopened['documentId'] == original['documentId']
    assert reopened['format'] == 'txt'
    manifest = client.get(reopened['manifestUrl']).json
    assert manifest['format'] == 'txt'
    assert manifest['chunkCount'] >= 1
    chunk = client.get(manifest['contentUrl'] + '/0').json
    assert 'extensionlessreadme' in chunk['text']
    assert b'extensionlessreadme' in client.get(reopened['url']).body


def test_txt_contents_are_indexed_without_downloading_all_chunks(site):
    # The cached outline uses the same LF/UTF-16 coordinate system as DOM text.
    text = '🙂 opening\r\n第一章 山间清晨\r\nordinary text\r第二回 归途\r\nChapter 3 Evening\n'
    raw = text.encode('utf-8')
    chunks = site.module.split_txt_chunks(raw)
    toc = site.module.build_txt_outline(raw, chunks)
    assert [item['label'] for item in toc] == ['第一章 山间清晨', '第二回 归途', 'Chapter 3 Evening']
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    for item in toc:
        decoded = normalized.encode('utf-16-le')[item['textOffset'] * 2:][:item['matchLength'] * 2].decode('utf-16-le')
        assert decoded == item['label']
        assert item['index'] == 0
    client = client_for(site)
    manifest = client.get(book_for(client)['manifestUrl']).json
    assert manifest['toc'] == []  # No background whole-book request is needed.


def test_reader_bytes_range_and_html_are_sandboxed_and_bound_to_each_session(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    book = book_for(alice)
    response = alice.get(book['url'], headers={'Range': 'bytes=0-11'}, status=206)
    assert response.body == (site.books / 'alpha.txt').read_bytes()[:12]
    assert response.headers['Content-Range'].startswith('bytes 0-11/')
    assert 'no-store' in response.headers['Cache-Control']
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert "script-src 'none'" in response.headers['Content-Security-Policy']
    bob.get(book['url'], status=403)
    another_alice = client_for(site)
    another_alice.get(book['url'], status=403)  # Same user, different session.
    html = book_for(alice, 'gammapulse')
    raw = alice.get(html['url'])
    assert '<script>' in raw.text  # CSP is applied even when original HTML is returned.
    policy = raw.headers['Content-Security-Policy']
    assert 'sandbox allow-same-origin' in policy and "form-action 'none'" in policy
    assert 'allow-scripts' not in policy
    preview = alice.get('/preview/0', {'query': 'gammapulse'})
    assert "script-src 'none'" in preview.headers['Content-Security-Policy']
    download = alice.get('/download/0', {'query': 'gammapulse'})
    assert download.headers['Content-Disposition'].startswith('attachment;')
    token = alice.cookies['recoll_session']
    post_form(alice, '/logout', status=200)
    alice.get(book['url'], status=401)
    assert site.store.get_session(token) is None
    login = client_for(site)
    login.get(book['url'], status=403)
    login.get('/staticdoc/' + str(site.store.path).lstrip('/'), status=404)


def test_only_explicit_open_creates_reading_then_cas_and_clear_prevent_late_writes(site):
    client = client_for(site)
    book = book_for(client)
    manifest = client.get(book['manifestUrl']).json
    client.get(manifest['contentUrl'] + '/0')
    assert site.store.list_readings(site.alice['id'])['items'] == []
    path = '/api/reader/progress/' + book['documentId']
    assert client.get(path).json['position'] is None
    initial = post_json(client, '/api/reader/open', {'handle': book['openHandle']}).json
    assert initial['reading_recorded'] and initial['progress_enabled']
    assert len(site.store.list_readings(site.alice['id'])['items']) == 1
    first_body = progress_body(client, book)
    first = post_json(client, path, first_body).json
    assert first['revision'] == 1
    assert first['progress'] == 37.5
    assert post_json(client, path, first_body).json['revision'] == 1  # Identical retry.
    stale = progress_body(client, book, writer='other-device', value=70)
    conflict = post_json(client, path, stale, status=409).json
    assert conflict['current']['revision'] == 1
    assert client.get(path).json['progress'] == 37.5
    post_json(client, '/api/bookshelf/add', {'handle': book['openHandle']})
    assert '已读 38%' in client.get('/bookshelf').text
    assert '已读 38%' in client.get('/history?tab=reading').text
    assert '3750%' not in client.get('/bookshelf').text
    post_form(client, '/history/reading/clear', {'include_progress': '1'})
    assert client.get(path).json['position'] is None
    post_json(client, '/api/reader/open', {'handle': book['openHandle']}, status=409)
    post_json(client, path, first_body, status=409)
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], book['documentId']) is None
    fresh = client.get('/api/reader/book', {'document_id': book['documentId']}).json['book']
    post_json(client, '/api/reader/open', {'handle': fresh['openHandle']})
    assert len(site.store.list_readings(site.alice['id'])['items']) == 1


def test_progress_enabled_with_recent_reading_disabled_and_folder_resume_use_document_identity(site):
    client = client_for(site)
    site.store.save_settings(site.alice['id'], flags={'reading_history_enabled': False})
    book = book_for(client)
    folder = client.get('/api/reader/folder', {'document_id': book['documentId']}).json
    selected = next(item for item in folder['items'] if item['path'].endswith('beta.txt'))
    post_json(client, '/api/reader/open', {'handle': selected['openHandle']})
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], selected['documentId']) is not None
    resumed = client.get('/api/reader/folder', {'document_id': book['documentId']}).json
    assert resumed['items'][resumed['currentIndex']]['documentId'] == selected['documentId']
    bob = client_for(site, 'bob')
    other = bob.get('/api/reader/folder', {'document_id': book['documentId']}).json
    assert other['items'][other['currentIndex']]['documentId'] == book['documentId']


def test_progress_is_private_even_when_a_document_id_is_shared(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    first = book_for(alice)
    path = '/api/reader/progress/' + first['documentId']
    post_json(alice, '/api/reader/open', {'handle': first['openHandle']})
    post_json(alice, path, progress_body(alice, first, value=42.5))
    assert bob.get(path, {'user_id': site.alice['id']}).json['position'] is None
    post_json(bob, '/api/reader/open', {'handle': first['openHandle'], 'user_id': site.alice['id']}, status=403)
    post_json(bob, path, dict(progress_body(alice, first), user_id=site.alice['id']), status=403)
    own = bob.get('/api/reader/book', {'document_id': first['documentId']}).json['book']
    post_json(bob, '/api/reader/open', {'handle': own['openHandle']})
    body = dict(progress_body(bob, own, value=12), user_id=site.alice['id'])
    post_json(bob, path, body)
    assert alice.get(path).json['progress'] == 42.5
    assert bob.get(path).json['progress'] == 12
    assert '4250%' not in alice.get('/history?tab=reading').text


def test_reading_history_remains_independent_when_progress_is_disabled(site):
    client = client_for(site)
    site.store.save_settings(site.alice['id'], flags={'progress_sync_enabled': False})
    book = book_for(client)
    result = post_json(client, '/api/reader/open', {'handle': book['openHandle']}).json
    assert result['reading_recorded'] is True
    assert result['progress_enabled'] is False
    assert len(site.store.list_readings(site.alice['id'])['items']) == 1
    assert site.store.get_progress(site.alice['id'], book['documentId']) is None
    post_json(client, '/api/reader/progress/' + book['documentId'], progress_body(client, book), status=409)


def test_changed_or_missing_source_is_reported_without_opening_another_result(site):
    client = client_for(site)
    book = book_for(client)
    source = Path(book['path'])
    original = source.read_bytes()
    progress_path = '/api/reader/progress/' + book['documentId']
    post_json(client, '/api/reader/open', {'handle': book['openHandle']})
    post_json(client, progress_path, progress_body(client, book))
    try:
        source.write_bytes(original + b'An additional chapter changes the content version.\n')
        changed = client.get(progress_path).json
        assert changed['contentChanged'] is True
        assert changed['position']['charOffset'] == 23
        post_json(client, progress_path, progress_body(client, book, revision=1, sequence=2), status=409)
        source.unlink()
        # A stale result number must not be used as a fallback for a missing ID.
        client.get('/api/reader/book', {'document_id': book['documentId'], 'query': 'betatoken', 'resnum': 0}, status=404)
        client.get(book['url'], status=403)
    finally:
        source.write_bytes(original)


def test_legacy_import_returns_successful_paths_and_rejects_outside_sources(site):
    client = client_for(site)
    outside = site.root / 'private.txt'
    outside.write_text('This path is outside the shared library.', encoding='utf-8')
    payload = {'positions': [{'path': str(site.books / 'alpha.txt'), 'position': {'version': 1, 'format': 'txt', 'chunkIndex': 0, 'charOffset': 14}},
                             {'path': str(outside), 'position': {'version': 1, 'format': 'txt'}}],
               'preferences': {'font_size': 24}, 'progress_epoch': identity(client)['settings']['progress_epoch'], 'overwrite': False}
    result = post_json(client, '/api/account/import', payload).json
    assert result['imported'] == [str(site.books / 'alpha.txt')]
    assert result['skipped'] == [str(outside)]
    assert result['preferences_imported'] is True
    book = book_for(client)
    progress = client.get('/api/reader/progress/' + book['documentId']).json
    assert progress['position']['charOffset'] == 14 and progress['origin'] == 'import'
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert identity(client)['settings']['preferences']['font_size'] == 24
    payload['positions'][0]['position']['charOffset'] = 99
    # Explicit import does not overwrite existing server progress by default.
    repeated = post_json(client, '/api/account/import', payload).json
    assert str(site.books / 'alpha.txt') in repeated['skipped']
    assert client.get('/api/reader/progress/' + book['documentId']).json['position']['charOffset'] == 14
    post_json(client, '/api/account/import', {'preferences': 'bad'}, status=400)
    post_json(client, '/api/account/import', {'overwrite': 'false'}, status=400)
    post_form(client, '/history/reading/clear', {'include_progress': '1'})
    post_json(client, '/api/account/import', payload, status=409)


def test_admin_settings_constraints_and_device_mappings_are_enforced_in_actual_requests(site):
    client = client_for(site)
    (site.config / 'recoll.conf').write_text(site.base_config + 'webui_nojsoncsv = 1\nwebui_maxperpage = 1\nwebui_nosettings = 1\n', encoding='utf-8')
    site.store.save_settings(site.alice['id'], preferences={'perpage': 0})
    result = client.get('/results', {'query': 'sharedgalaxy'})
    assert len(result.html.select('.search-result')) == 1
    client.get('/json', {'query': 'sharedgalaxy'}, status=403)
    client.get('/csv', {'query': 'sharedgalaxy'}, status=403)
    client.get('/set', {'perpage': 0}, status=405)
    post_form(client, '/set', {'perpage': 0}, status=403)
    # Personal history controls and account security remain available.
    post_form(client, '/account/preferences', {'font_size': '22'}, status=302)
    assert identity(client)['settings']['preferences']['font_size'] == 22


def test_default_result_limit_is_500_and_users_can_raise_or_disable_it(site):
    client = client_for(site)
    settings = client.get('/settings')
    form = next(form for form in settings.forms.values() if form.action.endswith('/set'))
    assert form['maxresults'].value == '500'
    assert identity(client)['settings']['preferences'] == {}
    for limit in (1000, 0):
        post_form(client, '/set', {'maxresults': limit, 'perpage': 500})
        assert identity(client)['settings']['preferences']['maxresults'] == limit
        settings = client.get('/settings')
        form = next(form for form in settings.forms.values() if form.action.endswith('/set'))
        assert form['maxresults'].value == str(limit)
        result = client.get(search_for(client))
        assert found(result) == 3
        assert len(result.html.select('.search-result')) == 3


def test_result_limit_bounds_rendering_and_pagination(site):
    client = client_for(site)
    post_form(client, '/set', {'maxresults': 2, 'perpage': 500})
    target = search_for(client)
    result = client.get(target)
    assert found(result) == 2
    assert len(result.html.select('.search-result')) == 2
    assert len(client.get(changed_query(target, page=0)).html.select('.search-result')) == 2
    post_form(client, '/set', {'perpage': 1})
    target = search_for(client)
    assert len(client.get(target).html.select('.search-result')) == 1
    assert len(client.get(changed_query(target, page=2)).html.select('.search-result')) == 1
    assert len(client.get(changed_query(target, page=3)).html.select('.search-result')) == 0


def test_complete_settings_form_saves_types_and_device_mapping_without_cross_account_leaks(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    page = alice.get('/settings')
    form = next(form for form in page.forms.values() if form.action.endswith('/set'))
    expected = {'perpage': 2, 'stem': 1, 'collapsedups': 1, 'context': 45,
                'maxchars': 750, 'maxresults': 99, 'dirdepth': 0}
    for key, value in expected.items():
        form[key] = str(value)
    form['timefmt'], form['title_link'], form['csvfields'] = '%Y-%m-%d', 'preview', 'filename title url'
    mount_name = 'mount_' + str(site.books)
    form[mount_name] = 'https://files.example.com/library'
    form.submit(status=303)
    saved = identity(alice)['settings']['preferences']
    for key, value in expected.items():
        assert saved[key] == value and isinstance(saved[key], int)
    assert saved['timefmt'] == '%Y-%m-%d'
    assert saved['csvfields'] == 'filename title url'
    own = alice.get('/settings')
    own_form = next(form for form in own.forms.values() if form.action.endswith('/set'))
    assert own_form['perpage'].value == '2'
    assert own_form[mount_name].value == 'https://files.example.com/library'
    assert len(alice.get('/results', {'query': 'sharedgalaxy'}).html.select('.search-result')) == 2
    # Copying device cookies cannot make Bob load Alice's settings or mappings.
    for key, value in alice.cookies.items():
        if key.startswith('mount_'):
            bob.set_cookie(key, value)
    other = bob.get('/settings')
    other_form = next(form for form in other.forms.values() if form.action.endswith('/set'))
    assert other_form['perpage'].value == '1'
    assert other_form[mount_name].value != 'https://files.example.com/library'
    assert identity(bob)['settings']['preferences'] == {}
    assert len(bob.get('/results', {'query': 'sharedgalaxy'}).html.select('.search-result')) == 1
    before = dict(identity(alice)['settings']['preferences'])
    post_form(alice, '/set', {'perpage': 3, mount_name: 'javascript:alert(1)'}, status=400)
    assert identity(alice)['settings']['preferences'] == before


def test_wsgi_mount_prefix_applies_to_forms_reader_resources_and_opensearch(site):
    client = client_for(site, prefix='/library')
    home = client.get('/')
    assert 'action="/library/search"' in home.text
    assert 'href="/library/bookshelf"' in home.text
    assert identity(client)['baseURL'] == '/library'
    target = search_for(client)
    assert urlsplit(target).path == '/library/results'
    # WebTest receives PATH_INFO; an actual WSGI host strips SCRIPT_NAME.
    response = client.get(target[len('/library'):])
    assert found(response) == 3
    book = book_for(client)
    assert book['url'].startswith('/library/api/reader/file/')
    assert book['manifestUrl'].startswith('/library/api/reader/txt/')
    assert client.get(book['url'][len('/library'):]).status_int == 200
    reader = client.get('/reader', {'document_id': book['documentId']})
    assert '"apiBase": "/library/api/reader"' in reader.text
    osd = client.get('/osd.xml')
    assert '/library/results?' in osd.text


def test_reader_default_title_action_has_a_stable_document_link(site):
    client = client_for(site)
    site.store.save_settings(site.alice['id'], preferences={'title_link': 'reader'})
    result = client.get('/results', {'query': 'alphatoken'})
    title = result.html.select_one('.search-result-title a')
    assert title is not None and title.text.strip()
    destination = urlsplit(title['href'])
    assert destination.path == '/reader'
    expected = book_for(client)['documentId']
    assert parse_qs(destination.query)['document_id'] == [expected]
    settings = client.get('/settings')
    form = next(form for form in settings.forms.values() if form.action.endswith('/set'))
    assert form['title_link'].value == 'reader'


@pytest.mark.parametrize('prefix', ['', '/library'])
@pytest.mark.parametrize('entry', ['title', 'book', 'folder'])
def test_result_reader_entries_return_to_the_original_search_page(site, prefix, entry):
    client = client_for(site, prefix=prefix)
    site.store.save_settings(site.alice['id'], preferences={'title_link': 'reader'})
    expression = 'sharedgalaxy OR 银河阅读'
    submitted = post_form(client, '/search', {
        'query': expression, 'sort': 'filename', 'ascending': '1',
        'before': '2100-01-01', 'after': '2000-01-01', 'dir': 'books',
        'highlight': '0', 'snippets': '0',
    }, status=303)
    target = changed_query(local_url(submitted.location), page=2)
    original_query = parse_qs(urlsplit(target).query)
    assert original_query['search_id'] and original_query['query'] == [expression]
    results = client.get(target[len(prefix):] if prefix else target)
    assert found(results) == 3
    assert len(results.html.select('.search-result')) == 1
    if entry == 'title':
        link = results.html.select_one('.search-result-title a')
    else:
        label = '单独阅读此文件' if entry == 'book' else '阅读当前文件夹'
        link = next(link for link in results.html.select('.search-result-links a')
                    if link.text.strip() == label)
    destination = local_url(link['href'])
    assert urlsplit(destination).path == prefix + '/reader'
    parameters = parse_qs(urlsplit(destination).query)
    assert parameters['document_id']
    assert parameters['return_to'] == [target]
    if entry != 'title':
        assert parameters['mode'] == [entry]
    reader = client.get(destination[len(prefix):] if prefix else destination)
    back = reader.html.select_one('.reader-toolbar a[aria-label="返回搜索结果"]')
    assert back['href'] == target
    returned = client.get(target[len(prefix):] if prefix else target)
    assert found(returned) == 3
    assert returned.html.select_one('input[name=query]')['value'] == expression
    assert len(history_rows(site)) == 1  # A return visit must not add a search event.


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_reader_search_hits_are_separate_from_continue_reading(site, prefix):
    client = client_for(site, prefix=prefix)
    results = client.get('/results', params={'query': 'sharedgalaxy'})
    links = results.html.select('.search-result-links a')
    hit = next(link for link in links if link.text.strip() == '查看命中')
    resume = next(link for link in links if link.text.strip() == '单独阅读此文件')
    hit_query = parse_qs(urlsplit(hit['href']).query)
    assert hit_query['reader_search'] == ['sharedgalaxy']
    assert 'reader_search' not in parse_qs(urlsplit(resume['href']).query)
    destination = local_url(hit['href'])
    reader = client.get(destination[len(prefix):] if prefix else destination)
    script = next(script.text for script in reader.html.select('script') if 'window.RECOLL_READER =' in script.text)
    config = json.loads(script.split('=', 1)[1].strip().rstrip(';'))
    assert config['initialSearch']['query'] == 'sharedgalaxy'
    assert config['initialSearch']['terms'] == ['sharedgalaxy']
    assert 'reader_search' not in config['resultsQueryString']


def test_reader_hit_terms_prefer_actual_highlights_and_skip_metadata_and_negation(site):
    terms = site.module.reader_match_terms
    assert terms('<span class="search-result-highlight">universes</span>', 'universe') == ['universes']
    assert terms('<span class="search-result-highlight">银河 &amp; 星</span>', '银河') == ['银河 & 星']
    assert terms('', 'title:"Book Name" AND needle NOT excluded -minus') == ['needle']
    assert terms('', 'filename:manual*') == []


def test_reader_hit_terms_roundtrip_unicode_and_do_not_pollute_return_query(site):
    client = client_for(site)
    response = client.get('/reader', params=[('reader_search', '银河'), ('reader_term', '银河'), ('reader_term', '宇宙'), ('reader_page', '12')])
    script = next(script.text for script in response.html.select('script') if 'window.RECOLL_READER =' in script.text)
    config = json.loads(script.split('=', 1)[1].strip().rstrip(';'))
    assert config['initialSearch'] == {'query': '银河', 'terms': ['银河', '宇宙'], 'pageNumber': 12}
    assert config['resultsQueryString'] == ''


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_reader_legacy_search_link_retains_search_and_removes_reader_parameters(site, prefix):
    client = client_for(site, prefix=prefix)
    query = {
        'query': 'sharedgalaxy OR 银河阅读', 'sort': 'filename', 'ascending': '1',
        'page': '2', 'before': '2100-01-01', 'after': '2000-01-01',
        'dir': 'books', 'highlight': '0', 'snippets': '0',
    }
    reader = client.get('/reader', dict(query, mode='folder', resnum='1',
                                        document_id=book_for(client)['documentId']))
    back = reader.html.select_one('.reader-toolbar a[aria-label="返回搜索结果"]')
    destination = urlsplit(back['href'])
    assert destination.path == prefix + '/results'
    assert parse_qs(destination.query) == {key: [value] for key, value in query.items()}
    returned = client.get(local_url(back['href'])[len(prefix):] if prefix else local_url(back['href']))
    assert found(returned) == 3
    assert len(returned.html.select('.search-result')) == 1


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_reader_rejects_external_or_mount_escaping_return_destinations(site, prefix):
    client = client_for(site, prefix=prefix)
    unsafe_targets = [
        'https://evil.example/results?query=private', '//evil.example/results',
        '/%2Fevil.example/results', '/\\evil.example/results',
    ]
    if prefix:
        unsafe_targets += ['/results?query=private', '/library/../outside']
    for target in unsafe_targets:
        reader = client.get('/reader', {'query': 'alphatoken', 'resnum': '0',
                                        'mode': 'book', 'return_to': target})
        back = reader.html.select_one('.reader-toolbar a[aria-label="返回搜索结果"]')
        destination = urlsplit(back['href'])
        assert not destination.scheme and not destination.netloc
        assert destination.path in (prefix + '/', prefix + '/results')
        assert 'return_to' not in parse_qs(destination.query)


def test_page_zero_cannot_bypass_administrator_page_limit_but_exports_stay_complete(site):
    client = client_for(site)
    (site.config / 'recoll.conf').write_text(site.base_config + 'webui_maxperpage = 1\n', encoding='utf-8')
    site.store.save_settings(site.alice['id'], preferences={'perpage': 0})
    result = client.get('/results', {'query': 'sharedgalaxy', 'page': 0})
    assert found(result) == 3
    assert len(result.html.select('.search-result')) == 1
    exported = client.get('/json', {'query': 'sharedgalaxy', 'page': 0}).json
    assert len(exported['results']) == 3
    csv_result = client.get('/csv', {'query': 'sharedgalaxy'})
    assert len(list(csv.reader(io.StringIO(csv_result.text)))) == 4  # Header plus all documents.


def test_utf8_search_round_trips_through_post_redirect_history_and_replay(site):
    client = client_for(site)
    expression = 'extensionlessreadme OR 银河阅读'
    target = search_for(client, expression)
    assert parse_qs(urlsplit(target).query)['query'] == [expression]
    result = client.get(target)
    assert found(result) >= 1
    assert result.html.select_one('input[name=query]')['value'] == expression
    assert history_rows(site)[0]['query']['query'] == expression
    history = client.get('/history')
    replay = next(form for form in history.forms.values() if form.action.endswith('/search'))
    repeated = local_url(replay.submit(status=303).location)
    assert parse_qs(urlsplit(repeated).query)['query'] == [expression]
    assert found(client.get(repeated)) >= 1
    assert len(history_rows(site)) == 2
    client.get('/results?query=%FF', status=400)


def test_utf8_settings_and_account_display_names_are_preserved(site):
    client = client_for(site)
    post_form(client, '/set', {'timefmt': '日期 %Y-%m-%d',
                             'mount_' + str(site.books): 'https://files.example.com/文档'})
    assert identity(client)['settings']['preferences']['timefmt'] == '日期 %Y-%m-%d'
    settings = client.get('/settings')
    form = next(form for form in settings.forms.values() if form.action.endswith('/set'))
    assert form['timefmt'].value == '日期 %Y-%m-%d'
    assert form['mount_' + str(site.books)].value == 'https://files.example.com/文档'
    post_form(client, '/admin/users', {'username': 'reader-cn', 'display_name': '中文读者',
                                             'password': '中文密码足够长足够长用于集成验证', 'role': 'member'}, status=302)
    created = next(user for user in site.store.list_users() if user['username'] == 'reader-cn')
    assert created['display_name'] == '中文读者'
