"""Exercise the real WSGI boundary and SQLite ownership checks without Recoll."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import bottle
import pytest
from webtest import TestApp

import account_web
from accounts import Store


PASSWORD = 'account test password long enough'
NEW_PASSWORD = 'replacement password long enough'


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / 'app.sqlite3')
    result.initialize()
    return result


def make_app(store, prefix='', callbacks=None):
    app = bottle.Bottle()
    app.get('/protected')(lambda: 'private search result')
    app.get('/api/protected')(lambda: {'private': True})
    app.post('/api/protected')(lambda: {'private': True})
    app.get('/static/test.css')(lambda: 'body{}')
    app.get('/osd.xml')(lambda: '<OpenSearchDescription/>')
    app.get('/api/sign')(lambda: {'token': account_web.sign_payload({'document_id': 'document'}, 'reader')})
    app.post('/api/read-token')(lambda: {'payload': account_web.read_payload(bottle.request.json['token'], 'reader')})
    account_web.install(app, store, callbacks=callbacks)
    return TestApp(app, extra_environ={'SCRIPT_NAME': prefix} if prefix else {})


@pytest.fixture
def users(store):
    return (store.create_user('admin', PASSWORD, role='admin', must_change_password=False),
            store.create_user('member', PASSWORD, must_change_password=False))


def login(client, username='member', password=PASSWORD, target='/protected'):
    page = client.get('/login', params={'next': target})
    form = page.forms[0]
    form['username'], form['password'] = username, password
    return form.submit()


def csrf(client):
    return client.get('/api/account').json['csrf']


def test_setup_never_opens_anonymous_routes(tmp_path):
    client = make_app(Store(tmp_path / 'missing.sqlite3'))
    assert client.get('/login', status=503).content_type == 'text/html'
    assert client.get('/api/reader/file/token', status=503).json['error'] == 'setup_required'
    client.get('/static/test.css', status=200)
    client.get('/osd.xml', status=200)
    assert not (tmp_path / 'missing.sqlite3').exists()


def test_all_private_legacy_and_new_routes_are_protected(store):
    client = make_app(store)
    for path in ('/protected', '/reader', '/history', '/bookshelf', '/admin/users'):
        result = client.get(path, status=302)
        assert urlsplit(result.location).path == '/login'
        assert result.headers['Cache-Control'] == 'no-store'
    for path in ('/api/reader/book/0', '/preview/0', '/download/0', '/staticdoc/a', '/json', '/csv', '/api/account'):
        result = client.get(path, status=401)
        assert result.content_type == 'application/json'
        assert result.json['error'] == 'authentication_required'
        assert 'private search result' not in result.text


def test_login_requires_signed_double_submit_and_establishes_distinct_session(store, users):
    client = make_app(store)
    page = client.get('/login')
    token = page.forms[0]['csrf'].value
    client.post('/login', {'username': 'member', 'password': PASSWORD, 'csrf': token + 'x'}, status=403)
    client.post('/login', {'username': 'member', 'password': PASSWORD}, status=403)
    # A forged form token with a matching forged cookie still lacks a valid signature.
    client.set_cookie(account_web.LOGIN_CSRF_COOKIE, 'forged')
    client.post('/login', {'username': 'member', 'password': PASSWORD, 'csrf': 'forged'}, status=403)
    client.reset()
    login(client)
    before = client.cookies[account_web.SESSION_COOKIE]
    assert client.get('/protected').text == 'private search result'
    # A fresh browser login gets an independent unpredictable session token.
    client.reset()
    login(client)
    assert client.cookies[account_web.SESSION_COOKIE] != before
    client.post('/logout', {'csrf': csrf(client)})
    assert store.get_session(client.cookies.get(account_web.SESSION_COOKIE)) is None


def test_csrf_required_for_form_and_json_write(store, users):
    client = make_app(store)
    login(client)
    client.post('/api/protected', status=403)
    client.post_json('/api/account/preferences', {'font_size': 24}, status=403)
    token = csrf(client)
    client.post_json('/api/account/preferences', {'font_size': 24}, headers={'X-CSRF-Token': token})
    assert store.get_settings(users[1]['id'])['preferences']['font_size'] == 24
    layout = {'reader_theme': 'dark', 'reader_font_family': 'serif', 'reader_line_height': 195,
              'reader_paragraph_spacing': 20, 'reader_content_width': 840,
              'reader_text_align': 'justify', 'reader_indent': 1, 'reader_preserve_publisher': 0}
    response = client.post_json('/api/account/preferences', {'preferences': layout}, headers={'X-CSRF-Token': token})
    assert all(response.json['settings']['preferences'][key] == value for key, value in layout.items())
    assert not any(key in store.get_settings(users[0]['id'])['preferences'] for key in layout)
    client.post_json('/api/account/preferences', {'preferences': {'reader_theme': 'url(evil)'}},
                     headers={'X-CSRF-Token': token}, status=400)
    client.post_json('/api/account/preferences', {'user_id': users[0]['id'], 'font_size': 30},
                     headers={'X-CSRF-Token': token}, status=400)
    assert 'font_size' not in store.get_settings(users[0]['id'])['preferences']


@pytest.mark.parametrize('target', ['https://example.com', '//example.com', '//[', '/\\example.com', '/app/../../other', '/app/%2e%2e/other', '/app/%0aother', '/application/protected'])
def test_malicious_next_cannot_leave_wsgi_mount(store, users, target):
    client = make_app(store, '/app')
    response = login(client, target=target)
    assert urlsplit(response.location).path == '/app/'
    assert 'Path=/app/' in response.headers['Set-Cookie']


def test_wsgi_mount_is_used_for_redirects_forms_and_context(store, users):
    client = make_app(store, '/app')
    response = client.get('/protected?query=book', status=302)
    parsed = urlsplit(response.location)
    assert parsed.path == '/app/login'
    assert parse_qs(parsed.query)['next'] == ['/app/protected?query=book']
    response = login(client, target='/app/protected?query=book')
    assert urlsplit(response.location).path == '/app/protected'
    context = client.get('/api/account').json
    assert context['baseURL'] == '/app'
    assert context['session']['id'] == context['sessionId']
    assert 'action="/app/account/preferences"' in client.get('/account').text


def test_cookie_security_does_not_trust_unconfigured_forwarded_scheme(store, users, monkeypatch):
    monkeypatch.delenv('RECOLL_WEBUI_TRUSTED_PROXIES', raising=False)
    client = make_app(store)
    response = client.get('/login', headers={'X-Forwarded-Proto': 'https'})
    assert 'Secure' not in response.headers['Set-Cookie']
    response = client.get('/login', extra_environ={'wsgi.url_scheme': 'https'})
    cookie = response.headers['Set-Cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie
    monkeypatch.setenv('RECOLL_WEBUI_TRUSTED_PROXIES', '127.0.0.1/32')
    trusted = make_app(store)
    response = trusted.get('/login', headers={'X-Forwarded-Proto': 'https'}, extra_environ={'REMOTE_ADDR': '127.0.0.1'})
    assert 'Secure' in response.headers['Set-Cookie']


def test_initial_password_gate_and_password_change_revoke_all_sessions(store):
    store.create_user('firstlogin', PASSWORD)
    client = make_app(store)
    assert urlsplit(login(client, username='firstlogin').location).path == '/account/password'
    token = client.cookies[account_web.SESSION_COOKIE]
    client.get('/protected', status=302)
    assert client.get('/api/protected', status=403).json['error'] == 'password_change_required'
    page = client.get('/account/password')
    form = page.forms[1] if len(page.forms) > 1 else page.forms[0]
    form['old_password'], form['password'], form['password_confirm'] = PASSWORD, NEW_PASSWORD, NEW_PASSWORD
    form.submit(status=200)
    assert store.get_session(token) is None
    login(client, username='firstlogin', password=NEW_PASSWORD)
    client.get('/protected', status=200)


def test_member_cannot_manage_users_and_last_admin_is_protected(store, users):
    client = make_app(store)
    login(client)
    client.get('/admin/users', status=403)
    client.post('/admin/users', {'csrf': csrf(client), 'username': 'illegal', 'password': PASSWORD}, status=403)
    assert len(store.list_users()) == 2
    admin = make_app(store)
    login(admin, 'admin')
    admin.post('/admin/users/' + users[0]['id'] + '/update', {'csrf': csrf(admin), 'action': 'delete'}, status=409)
    admin.post('/admin/users', {'csrf': csrf(admin), 'username': 'created', 'password': PASSWORD})
    assert next(item for item in store.list_users() if item['username'] == 'created')['role'] == 'member'


def test_history_and_session_ids_cannot_cross_accounts(store, users):
    admin, member = users
    event = store.begin_search(admin['id'], {'query': 'private admin query'}, {}, 'v1')
    store.complete_search(admin['id'], event, {'query': 'private admin query'}, {}, 1)
    _, other_session = store.authenticate('admin', PASSWORD)
    client = make_app(store)
    login(client)
    assert 'private admin query' not in client.get('/history').text
    token = csrf(client)
    client.post('/history/search/delete', {'csrf': token, 'id': event, 'user_id': admin['id']}, status=404)
    client.post('/account/sessions/revoke', {'csrf': token, 'session_id': other_session['id']}, status=404)
    assert len(store.list_searches(admin['id'])['items']) == 1
    assert any(item['id'] == other_session['id'] for item in store.list_sessions(admin['id']))


def test_tokens_are_bound_to_user_session_and_purpose(store, users):
    first, second = make_app(store), make_app(store)
    login(first)
    signed = first.get('/api/sign').json['token']
    assert first.post_json('/api/read-token', {'token': signed}, headers={'X-CSRF-Token': csrf(first)}).json['payload'] == {'document_id': 'document'}
    login(second)
    assert second.post_json('/api/read-token', {'token': signed}, headers={'X-CSRF-Token': csrf(second)}).json['payload'] is None
    first.post('/logout', {'csrf': csrf(first)})
    first.post_json('/api/read-token', {'token': signed}, status=401)


def test_context_and_history_escape_untrusted_display_and_query(store, users):
    admin = users[0]
    store.update_user(admin['id'], admin['id'], 'update', display_name='</script><script>alert(1)</script>')
    query = {'query': '<img src=x onerror=alert(1)>'}
    event = store.begin_search(admin['id'], query, {}, 'v1')
    store.complete_search(admin['id'], event, query, {}, 1)
    client = make_app(store)
    login(client, 'admin')
    page = client.get('/history')
    assert '<img src=x onerror=alert(1)>' not in page.text
    context_text = re.search(r'<script id="recoll-account-data" type="application/json">(.*?)</script>', page.text).group(1)
    assert '</script>' not in context_text
    assert json.loads(context_text)['user']['display_name'] == '</script><script>alert(1)</script>'


def test_bookshelf_is_private_and_removal_keeps_reading_progress(store, users):
    document = store.register_document('library', 'file:///books/book.txt', '/books/book.txt', title='Private shelf title', format='txt')
    store.add_to_shelf(users[0]['id'], document['id'])
    client = make_app(store)
    login(client)
    assert 'Private shelf title' not in client.get('/bookshelf').text
    client.post('/bookshelf/remove', {'csrf': csrf(client), 'document_id': document['id']}, status=404)
    assert store.is_on_shelf(users[0]['id'], document['id'])


def test_import_callback_receives_only_authenticated_csrf_protected_request(store, users):
    received = []
    client = make_app(store, callbacks={'legacy_import': lambda data: received.append((account_web.current_user()['id'], data)) or {'imported': []}})
    client.post_json('/api/account/import', {'positions': []}, status=401)
    login(client)
    client.post_json('/api/account/import', {'positions': []}, status=403)
    body = {'positions': [], 'progress_epoch': 1, 'overwrite': False}
    client.post_json('/api/account/import', body, headers={'X-CSRF-Token': csrf(client)})
    assert received == [(users[1]['id'], body)]
