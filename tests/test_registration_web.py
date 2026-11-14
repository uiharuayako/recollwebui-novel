"""Registration through the real WSGI boundary, with disposable test codes."""
from urllib.parse import parse_qs, urlsplit

import pytest

import account_web
from test_account_web import PASSWORD, csrf, login, make_app, store, users


ACTIVATION_CODE = 'fixture-code'


@pytest.fixture
def registration(store, users):
    store.configure_registration(users[0]['id'], enabled=True,
                                 activation_required=True, activation_code=ACTIVATION_CODE)
    return store


def registration_form(client, target='/protected'):
    page = client.get('/register', params={'next': target})
    return next(form for form in page.forms.values() if 'username' in form.fields)


def register(client, username='new-reader', password=PASSWORD, code=ACTIVATION_CODE,
             target='/protected', status=302):
    form = registration_form(client, target)
    values = {'csrf': form['csrf'].value, 'next': form['next'].value,
              'username': username, 'password': password}
    if code is not None:
        values['activation_code'] = code
    return client.post('/register', values, status=status)


def save_registration(client, *, enabled=True, activation_required=True, code=''):
    values = {'csrf': csrf(client), 'activation_code': code}
    if enabled:
        values['enabled'] = '1'
    if activation_required:
        values['activation_required'] = '1'
    result = client.post('/admin/registration', values, status=302)
    assert urlsplit(result.location).path == '/admin/users'
    assert parse_qs(urlsplit(result.location).query) == {'registration_saved': ['1']}
    return result


def test_registration_creates_active_member_and_requires_explicit_login(registration):
    client = make_app(registration)
    form = registration_form(client)
    assert set(form.fields) >= {'csrf', 'next', 'username', 'password', 'activation_code'}
    assert 'email' not in form.fields and 'password_confirm' not in form.fields
    form['username'], form['password'], form['activation_code'] = 'New-Reader', PASSWORD, ACTIVATION_CODE
    response = form.submit(status=302)
    parsed = urlsplit(response.location)
    assert parsed.path == '/login'
    assert parse_qs(parsed.query) == {'registered': ['1'], 'next': ['/protected']}
    assert PASSWORD not in response.location and ACTIVATION_CODE not in response.location
    assert account_web.SESSION_COOKIE not in client.cookies
    member = next(user for user in registration.list_users() if user['username'] == 'new-reader')
    assert member['role'] == 'member' and member['status'] == 'active'
    assert member['must_change_password'] is False
    page = response.follow()
    assert PASSWORD not in page.text and ACTIVATION_CODE not in page.text
    assert urlsplit(login(client, 'NEW-READER').location).path == '/protected'
    client.get('/protected', status=200)


def test_closed_registration_hides_link_and_rejects_direct_requests(store, users):
    client = make_app(store)
    assert not any(urlsplit(link.get('href', '')).path == '/register'
                   for link in client.get('/login').html.find_all('a'))
    client.get('/register', status=403)
    # Obtain a valid public CSRF token while open, then close before submission.
    store.configure_registration(users[0]['id'], enabled=True,
                                 activation_required=True, activation_code=ACTIVATION_CODE)
    assert any(urlsplit(link.get('href', '')).path == '/register'
               for link in client.get('/login').html.find_all('a'))
    form = registration_form(client)
    store.configure_registration(users[0]['id'], enabled=False, activation_required=True)
    client.post('/register', {'csrf': form['csrf'].value, 'username': 'closed-reader',
                             'password': PASSWORD, 'activation_code': ACTIVATION_CODE}, status=403)
    assert len(store.list_users()) == 2


@pytest.mark.parametrize('username,password', [
    ('bad name', PASSWORD), ('ab', PASSWORD), ('valid-reader', 'too short'),
    ('valid-reader', 'x' * 129),
])
def test_registration_enforces_username_and_password_rules(registration, username, password):
    response = register(make_app(registration), username=username, password=password, status=400)
    assert password not in response.text
    assert ACTIVATION_CODE not in response.text
    assert len(registration.list_users()) == 2


def test_registration_rejects_case_insensitive_username_conflicts(registration):
    response = register(make_app(registration), username='MEMBER', status=409)
    assert PASSWORD not in response.text and ACTIVATION_CODE not in response.text
    assert len(registration.list_users()) == 2


def test_registration_errors_do_not_disclose_submitted_secrets(registration):
    client = make_app(registration)
    wrong_code = 'private-wrong-test-code'
    response = register(client, code=wrong_code, status=400)
    assert PASSWORD not in response.text and wrong_code not in response.text
    assert 'Location' not in response.headers
    form = registration_form(client)
    response = client.post('/register', {'csrf': form['csrf'].value, 'username': 'new-reader',
                                       'password': PASSWORD, 'activation_code': wrong_code},
                           headers={'Accept': 'application/json'}, status=400)
    assert PASSWORD not in response.text and wrong_code not in response.text
    assert len(registration.list_users()) == 2


def test_registration_requires_signed_csrf_separate_from_login(registration):
    client = make_app(registration)
    token = registration_form(client)['csrf'].value
    values = {'username': 'new-reader', 'password': PASSWORD, 'activation_code': ACTIVATION_CODE}
    client.post('/register', values, status=403)
    client.post('/register', dict(values, csrf=token + 'x'), status=403)
    login_token = client.get('/login').forms[0]['csrf'].value
    client.set_cookie(account_web.REGISTER_CSRF_COOKIE, login_token)
    client.post('/register', dict(values, csrf=login_token), status=403)
    client.set_cookie(account_web.LOGIN_CSRF_COOKIE, token)
    client.post('/login', {'csrf': token, 'username': 'member', 'password': PASSWORD}, status=403)
    client.set_cookie(account_web.REGISTER_CSRF_COOKIE, 'forged')
    client.post('/register', dict(values, csrf='forged'), status=403)
    assert len(registration.list_users()) == 2


def test_admin_registration_controls_require_admin_and_csrf(registration, users):
    anonymous = make_app(registration)
    anonymous.post('/admin/registration', {'enabled': '1'}, status=401)
    member = make_app(registration)
    login(member)
    member.post('/admin/registration', {'csrf': csrf(member), 'enabled': '1'}, status=403)
    admin = make_app(registration)
    login(admin, 'admin')
    admin.post('/admin/registration', {'enabled': '1'}, status=403)
    page = admin.get('/admin/users')
    form = next(form for form in page.forms.values()
                if urlsplit(form.action).path == '/admin/registration')
    assert {'csrf', 'enabled', 'activation_required', 'activation_code'} <= set(form.fields)
    assert form['activation_code'].value == ''
    assert ACTIVATION_CODE not in page.text
    assert ACTIVATION_CODE not in admin.get('/api/account').text
    settings = registration.registration_settings()
    assert settings['enabled'] and settings['activation_required'] and settings['activation_code_set']
    assert ACTIVATION_CODE not in repr(settings)


def test_admin_can_rotate_code_and_blank_input_preserves_it(registration):
    admin = make_app(registration)
    login(admin, 'admin')
    replacement = 'replacement-fixture-code'
    save_registration(admin, code=replacement)
    client = make_app(registration)
    register(client, username='old-code-reader', status=400)
    save_registration(admin, code='')
    register(client, username='new-code-reader', code=replacement)
    assert replacement not in admin.get('/admin/users').text
    assert replacement not in admin.get('/api/account').text


def test_admin_can_disable_code_requirement_and_close_registration(registration):
    admin = make_app(registration)
    login(admin, 'admin')
    save_registration(admin, activation_required=False)
    settings = registration.registration_settings()
    assert settings['enabled'] and not settings['activation_required']
    assert settings['activation_code_set']
    client = make_app(registration)
    register(client, username='without-code-reader', code=None)
    pending = registration_form(client)
    save_registration(admin, enabled=False, activation_required=False)
    client.post('/register', {'csrf': pending['csrf'].value, 'username': 'late-reader',
                             'password': PASSWORD}, status=403)
    client.get('/register', status=403)
    assert not any(user['username'] == 'late-reader' for user in registration.list_users())
    save_registration(admin, activation_required=True)
    register(client, username='restored-code-reader')


def test_registration_rate_limit_is_shared_by_clients_and_does_not_block_login(registration):
    client = make_app(registration)
    for attempt in range(12):
        form = registration_form(client)
        response = client.post('/register', {'csrf': form['csrf'].value,
                                             'username': 'limited-reader-' + str(attempt),
                                             'password': PASSWORD, 'activation_code': 'incorrect-fixture-code'},
                               status=(400, 429))
        if response.status_int == 429:
            break
    else:
        pytest.fail('Repeated registration failures never reached the rate limit')
    other_browser = make_app(registration)
    register(other_browser, username='other-browser-reader', status=429)
    assert len(registration.list_users()) == 2
    assert urlsplit(login(other_browser, 'admin').location).path == '/protected'


def test_logged_in_registration_preserves_current_account(registration, users):
    client = make_app(registration)
    form = registration_form(client)
    login(client)
    session = client.cookies[account_web.SESSION_COOKIE]
    response = client.get('/register', params={'next': '/protected'}, status=302)
    assert urlsplit(response.location).path == '/protected'
    response = client.post('/register', {'csrf': form['csrf'].value, 'next': '//example.com',
                                        'username': 'unexpected-reader', 'password': PASSWORD,
                                        'activation_code': ACTIVATION_CODE}, status=302)
    assert urlsplit(response.location).path == '/'
    assert client.cookies[account_web.SESSION_COOKIE] == session
    assert client.get('/api/account').json['user']['id'] == users[1]['id']
    assert len(registration.list_users()) == 2


@pytest.mark.parametrize('target,expected', [
    ('/app/protected?query=book', '/app/protected?query=book'),
    ('https://example.com', '/app/'), ('//example.com', '/app/'),
    ('/app/%2e%2e/other', '/app/'), ('/application/protected', '/app/'),
])
def test_registration_respects_wsgi_mount_and_sanitizes_next(registration, target, expected):
    client = make_app(registration, '/app')
    page = client.get('/register', params={'next': target})
    assert 'Path=/app/' in page.headers['Set-Cookie']
    form = next(form for form in page.forms.values() if 'username' in form.fields)
    assert urlsplit(form.action).path == '/app/register'
    form['username'], form['password'], form['activation_code'] = 'mounted-reader', PASSWORD, ACTIVATION_CODE
    response = form.submit(status=302)
    parsed = urlsplit(response.location)
    assert parsed.path == '/app/login'
    assert parse_qs(parsed.query) == {'registered': ['1'], 'next': [expected]}
