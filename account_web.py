"""Bottle account pages and the common authentication boundary.

The database is injected, so importing this module never initializes a database.
"""
import base64
import functools
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sqlite3
import time
from urllib.parse import unquote, urlencode, urlsplit

import bottle


SESSION_COOKIE = 'recoll_session'
LOGIN_CSRF_COOKIE = 'recoll_login_csrf'
FLAGS = ('search_history_enabled', 'reading_history_enabled', 'progress_sync_enabled')


def current_user():
    return bottle.request.environ.get('recoll.user')


def current_session():
    return bottle.request.environ.get('recoll.session')


def current_settings():
    return bottle.request.environ.get('recoll.settings', {})


def csrf_token():
    return bottle.request.environ.get('recoll.csrf', '')


def app_url(path=''):
    """An application-local URL, including a WSGI SCRIPT_NAME mount."""
    prefix = bottle.request.environ.get('SCRIPT_NAME', '').rstrip('/')
    return prefix + '/' + path.lstrip('/')


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _secret():
    return bottle.request.environ['recoll.store'].secret()


def _encode(payload):
    body = _b64(json.dumps(payload, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    return body + '.' + _b64(hmac.new(_secret(), body.encode('ascii'), hashlib.sha256).digest())


def _decode(token):
    if not isinstance(token, str) or len(token) > 16384:
        return None
    try:
        body, signature = token.split('.')
        expected = _b64(hmac.new(_secret(), body.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if not isinstance(data, dict) or not isinstance(data.get('exp'), (int, float)) or data['exp'] <= time.time():
            return None
        return data
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def sign_payload(payload, purpose, ttl=43200):
    user, session = current_user(), current_session()
    if not user or not session:
        raise ValueError('A logged-in session is required')
    return _encode({'payload': payload, 'purpose': purpose, 'user_id': user['id'],
                    'session_id': session['id'], 'exp': int(time.time()) + int(ttl)})


signed_payload = sign_payload


def read_payload(token, purpose):
    data = _decode(token)
    user, session = current_user(), current_session()
    if not data or not user or not session:
        return None
    if data.get('purpose') != purpose or data.get('user_id') != user['id'] or data.get('session_id') != session['id']:
        return None
    return data.get('payload')


def account_context():
    """JSON safe for a non-executable application/json script element."""
    user, session = current_user(), current_session()
    data = {'user': user, 'sessionId': session.get('id') if session else None,
            'session': {'id': session['id']} if session else None,
            'settings': current_settings(), 'csrf': csrf_token(), 'baseURL': app_url().rstrip('/')}
    return json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


account_bootstrap_json = account_context


def render(template, **kwargs):
    return bottle.template(template, **kwargs)


def safe_next(value):
    fallback = app_url()
    if not isinstance(value, str) or not value.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return fallback
    try:
        parsed = urlsplit(value)
    except ValueError:
        return fallback
    path = unquote(unquote(parsed.path))
    if (parsed.scheme or parsed.netloc or path.startswith('//') or '\\' in value or '\\' in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        return fallback
    prefix = bottle.request.environ.get('SCRIPT_NAME', '').rstrip('/')
    if prefix and path != prefix and not path.startswith(prefix + '/'):
        return fallback
    # Normalize path traversal before deciding whether it remains inside the mount.
    segments = []
    for segment in path.split('/'):
        if segment == '..':
            if segments:
                segments.pop()
        elif segment not in ('', '.'):
            segments.append(segment)
    normalized = '/' + '/'.join(segments)
    if prefix and normalized != prefix and not normalized.startswith(prefix + '/'):
        return fallback
    return value


def _api_request():
    path = bottle.request.path
    return (path.startswith(('/api/', '/preview/', '/download/', '/staticdoc/'))
            or path in ('/json', '/csv') or 'application/json' in bottle.request.headers.get('Accept', ''))


def _fail(status, code, message):
    if _api_request():
        raise bottle.HTTPResponse(json.dumps({'error': code, 'message': message}), status=status,
                                  headers={'Content-Type': 'application/json; charset=utf-8',
                                           'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    raise bottle.HTTPResponse(render('account_error', status=status, message=message), status=status,
                              headers={'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'})


def _proxy_networks():
    values = os.environ.get('RECOLL_WEBUI_TRUSTED_PROXIES', '').replace(',', ' ').split()
    return [ipaddress.ip_network(value, strict=False) for value in values]


def _trusted(address, networks):
    try:
        return any(ipaddress.ip_address(address) in network for network in networks)
    except ValueError:
        return False


def _request_address(networks):
    address = bottle.request.environ.get('REMOTE_ADDR', '')
    if _trusted(address, networks):
        chain = bottle.request.headers.get('X-Forwarded-For', '').split(',')
        for candidate in reversed(chain):
            candidate = candidate.strip()
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            address = candidate
            if not _trusted(address, networks):
                break
    return address


def _cookie_options(networks):
    secure_mode = os.environ.get('RECOLL_WEBUI_COOKIE_SECURE', 'auto').lower()
    scheme = bottle.request.environ.get('wsgi.url_scheme', 'http')
    if _trusted(bottle.request.environ.get('REMOTE_ADDR', ''), networks):
        forwarded = bottle.request.headers.get('X-Forwarded-Proto', '').split(',')[-1].strip().lower()
        if forwarded in ('http', 'https'):
            scheme = forwarded
    secure = scheme == 'https' if secure_mode == 'auto' else secure_mode in ('1', 'true', 'yes', 'on')
    return {'path': app_url(), 'secure': secure, 'httponly': True, 'samesite': 'lax'}


def _body():
    if bottle.request.content_type.split(';')[0].lower() == 'application/json':
        data = bottle.request.json
        if not isinstance(data, dict):
            _fail(400, 'invalid_request', '请求内容必须是 JSON 对象。')
        return data
    return bottle.request.forms


def install(app, store, callbacks=None):
    """Install protected routes. Existing and future routes are protected too."""
    callbacks = callbacks or {}
    networks = _proxy_networks()
    for name, helper in {'account_user': current_user, 'account_csrf': csrf_token,
                         'account_settings': current_settings, 'app_url': app_url,
                         'account_context': account_context, 'account_bootstrap_json': account_context}.items():
        bottle.BaseTemplate.defaults[name] = helper

    def guarded(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except bottle.HTTPResponse:
                raise
            except sqlite3.Error:
                _fail(503, 'storage_unavailable', '账号存储暂时不可用，请稍后重试。')
            except Exception as error:
                # Store's public exceptions are deliberately mapped to generic
                # authentication errors, but preserve actionable validation text.
                name = type(error).__name__
                if name == 'AuthenticationError':
                    _fail(400, 'authentication_failed', '密码错误或操作无法完成。')
                if name == 'ValidationError':
                    _fail(400, 'invalid_request', str(error))
                if name == 'ConflictError':
                    _fail(409, 'conflict', str(error))
                if name == 'NotFound':
                    _fail(404, 'not_found', '记录不存在。')
                if name == 'SetupRequired':
                    _fail(503, 'setup_required', '服务尚未完成账号初始化，请联系管理员。')
                raise
        return wrapped

    @app.hook('before_request')
    def authenticate_request():
        env = bottle.request.environ
        env.update({'recoll.store': store, 'recoll.user': None, 'recoll.session': None,
                    'recoll.settings': {}, 'recoll.csrf': ''})
        path = bottle.request.path
        public = path.startswith('/static/') or path == '/osd.xml'
        if public:
            return
        try:
            if not store.initialized():
                _fail(503, 'setup_required', '服务尚未完成账号初始化，请联系管理员。')
            token = bottle.request.get_cookie(SESSION_COOKIE)
            session = store.get_session(token) if token else None
            if session:
                env['recoll.user'], env['recoll.session'] = session['user'], session
                env['recoll.settings'] = session.get('settings', {})
                env['recoll.csrf'] = _b64(hmac.new(store.secret(), ('csrf:' + str(session['id'])).encode(), hashlib.sha256).digest())
        except bottle.HTTPResponse:
            raise
        except Exception:
            _fail(503, 'storage_unavailable', '账号存储暂时不可用，请稍后重试。')
        if not session and path != '/login':
            if _api_request() or bottle.request.method not in ('GET', 'HEAD'):
                _fail(401, 'authentication_required', '登录已过期，请重新登录。')
            target = app_url(path)
            if bottle.request.query_string:
                target += '?' + bottle.request.query_string
            bottle.redirect(app_url('/login') + '?' + urlencode({'next': target}))
        if session and session['user'].get('must_change_password') and path not in ('/account/password', '/logout', '/api/account'):
            if _api_request() or bottle.request.method not in ('GET', 'HEAD'):
                _fail(403, 'password_change_required', '请先更换初始密码。')
            bottle.redirect(app_url('/account/password'))
        if bottle.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = bottle.request.headers.get('X-CSRF-Token') or bottle.request.forms.get('csrf', '')
            if path == '/login':
                cookie = bottle.request.get_cookie(LOGIN_CSRF_COOKIE, '')
                data = _decode(cookie)
                valid = data and data.get('purpose') == 'login-csrf' and cookie and hmac.compare_digest(str(cookie), str(supplied))
            else:
                valid = csrf_token() and hmac.compare_digest(str(csrf_token()), str(supplied))
            if not valid:
                _fail(403, 'csrf_failed', '页面验证已失效，请刷新后重试。')

    @app.hook('after_request')
    def private_response():
        bottle.response.set_header('X-Content-Type-Options', 'nosniff')
        if not bottle.request.path.startswith('/static/'):
            bottle.response.set_header('Cache-Control', 'no-store')
            bottle.response.set_header('Pragma', 'no-cache')
            bottle.response.set_header('Referrer-Policy', 'same-origin')
            bottle.response.set_header('Vary', 'Cookie')

    def login_page(error='', next_path=None):
        token = _encode({'purpose': 'login-csrf', 'nonce': secrets.token_urlsafe(24), 'exp': int(time.time()) + 1800})
        bottle.response.set_cookie(LOGIN_CSRF_COOKIE, token, max_age=1800, **_cookie_options(networks))
        return render('account_login', error=error, login_csrf=token,
                      next_path=safe_next(next_path if next_path is not None else bottle.request.query.get('next', '')))

    @app.get('/login')
    @guarded
    def login():
        if current_user():
            bottle.redirect(safe_next(bottle.request.query.get('next', '')))
        return login_page()

    @app.post('/login')
    @guarded
    def login_submit():
        target = safe_next(bottle.request.forms.get('next', ''))
        try:
            token, session = store.authenticate(bottle.request.forms.getunicode('username', ''),
                                               bottle.request.forms.getunicode('password', ''),
                                               address=_request_address(networks),
                                               device_label=bottle.request.headers.get('User-Agent', '')[:200],
                                               remember=bottle.request.forms.get('remember') == '1')
        except Exception as error:
            if type(error).__name__ != 'AuthenticationError':
                raise
            bottle.response.status = 401
            return login_page('用户名或密码错误，或尝试过于频繁，请稍后重试。', target)
        old_session = current_session()
        if old_session:
            store.revoke_session(old_session['user_id'], old_session['id'])
        options = _cookie_options(networks)
        if bottle.request.forms.get('remember') == '1':
            options['max_age'] = 30 * 86400
        bottle.response.set_cookie(SESSION_COOKIE, token, **options)
        bottle.response.delete_cookie(LOGIN_CSRF_COOKIE, **_cookie_options(networks))
        bottle.redirect(app_url('/account/password') if session['user'].get('must_change_password') else target)

    @app.post('/logout')
    @guarded
    def logout():
        store.revoke_session(current_user()['id'], current_session()['id'])
        bottle.response.delete_cookie(SESSION_COOKIE, **_cookie_options(networks))
        return render('account_signed_out', reason='你已退出登录。', action='logout')

    @app.get('/account')
    @guarded
    def account():
        return render('account_profile', user=current_user(), settings=current_settings(),
                      sessions=store.list_sessions(current_user()['id']), session=current_session(),
                      saved=bottle.request.query.get('saved') == '1')

    @app.get('/account/password')
    def password():
        return render('account_password', forced=bool(current_user().get('must_change_password')))

    @app.post('/account/password')
    @guarded
    def password_submit():
        values = bottle.request.forms
        if values.getunicode('password') != values.getunicode('password_confirm'):
            _fail(400, 'password_mismatch', '两次输入的新密码不一致。')
        store.change_password(current_user()['id'], values.getunicode('old_password', ''), values.getunicode('password', ''))
        bottle.response.delete_cookie(SESSION_COOKIE, **_cookie_options(networks))
        return render('account_signed_out', reason='密码已更改，请使用新密码重新登录。', action='logout')

    @app.post('/account/sessions/revoke')
    @guarded
    def revoke():
        session_id = bottle.request.forms.get('session_id') or None
        store.revoke_session(current_user()['id'], session_id)
        if session_id is None or session_id == str(current_session()['id']):
            bottle.response.delete_cookie(SESSION_COOKIE, **_cookie_options(networks))
            return render('account_signed_out', reason='登录会话已撤销，请重新登录。', action='logout')
        bottle.redirect(app_url('/account'))

    @app.post('/account/preferences')
    @guarded
    def preferences():
        flags = {key: bottle.request.forms.get(key) == '1' for key in FLAGS}
        try:
            values = {'font_size': int(bottle.request.forms.get('font_size', '20'))}
        except (ValueError, TypeError):
            _fail(400, 'invalid_request', '阅读字号必须是整数。')
        store.save_settings(current_user()['id'], preferences=values, flags=flags)
        bottle.redirect(app_url('/account?saved=1'))

    @app.get('/api/account')
    def account_api():
        return json.loads(account_context())

    @app.post('/api/account/preferences')
    @guarded
    def preferences_api():
        data = _body()
        values = data.get('preferences', data)
        reader_preferences = {'font_size', 'reader_theme', 'reader_font_family', 'reader_line_height',
                              'reader_paragraph_spacing', 'reader_content_width', 'reader_text_align',
                              'reader_indent', 'reader_preserve_publisher'}
        if not isinstance(values, dict) or any(key not in reader_preferences for key in values):
            _fail(400, 'invalid_request', '此接口仅支持阅读设置。')
        result = store.save_settings(current_user()['id'], preferences=values)
        return {'settings': result}

    @app.post('/api/account/import')
    @guarded
    def import_api():
        callback = callbacks.get('legacy_import')
        if not callback:
            _fail(404, 'not_found', '此服务尚未启用旧数据导入。')
        return callback(_body())

    def admin_user():
        if current_user().get('role') != 'admin':
            _fail(403, 'forbidden', '需要管理员权限。')
        return current_user()['id']

    @app.get('/admin/users')
    @guarded
    def admin():
        admin_user()
        return render('account_admin', users=store.list_users(), saved=bottle.request.query.get('saved') == '1')

    @app.post('/admin/users')
    @guarded
    def create():
        admin_user()
        values = bottle.request.forms
        store.create_user(values.getunicode('username', ''), values.getunicode('password', ''),
                          display_name=values.getunicode('display_name', ''), role=values.get('role', 'member'),
                          must_change_password=True)
        bottle.redirect(app_url('/admin/users?saved=1'))

    @app.post('/admin/users/<target_id>/update')
    @guarded
    def update(target_id):
        actor = admin_user()
        values = bottle.request.forms
        action = values.get('action', '')
        if action not in ('enable', 'disable', 'delete', 'role', 'reset-password'):
            _fail(400, 'invalid_request', '未知的账号操作。')
        store.update_user(actor, target_id, action, role=values.get('role', 'member'),
                          password=values.getunicode('password', ''))
        bottle.redirect(app_url('/admin/users?saved=1'))

    @app.get('/history')
    @guarded
    def history():
        uid = current_user()['id']
        kind = 'reading' if bottle.request.query.get('tab') == 'reading' else 'search'
        cursor = bottle.request.query.get('cursor') or None
        records = store.list_readings(uid, cursor=cursor) if kind == 'reading' else store.list_searches(uid, cursor=cursor)
        return render('account_history', kind=kind, records=records,
                      next_url=app_url('/history?') + urlencode({'tab': kind, 'cursor': records['next_cursor']}) if records.get('next_cursor') else '')

    @app.post('/history/search/delete')
    @guarded
    def delete_search():
        store.delete_search(current_user()['id'], bottle.request.forms.get('id') or None)
        bottle.redirect(app_url('/history?changed=search'))

    @app.post('/history/reading/delete')
    @guarded
    def delete_reading():
        doc_id = bottle.request.forms.get('document_id')
        if not doc_id:
            _fail(400, 'invalid_request', '缺少文档标识。')
        store.delete_reading(current_user()['id'], doc_id, clear_progress=False)
        bottle.redirect(app_url('/history?tab=reading&changed=reading'))

    @app.post('/history/progress/delete')
    @guarded
    def delete_progress():
        doc_id = bottle.request.forms.get('document_id')
        if not doc_id:
            _fail(400, 'invalid_request', '缺少文档标识。')
        store.clear_progress(current_user()['id'], doc_id)
        bottle.redirect(app_url('/history?tab=reading&changed=progress'))

    @app.post('/history/reading/clear')
    @guarded
    def clear_reading():
        clear_all = bottle.request.forms.get('include_progress') == '1'
        store.delete_reading(current_user()['id'], None, clear_progress=clear_all)
        bottle.redirect(app_url('/history?tab=reading&changed=') + ('reading-all' if clear_all else 'reading'))

    @app.get('/bookshelf')
    @guarded
    def bookshelf():
        records = store.list_bookshelf(current_user()['id'], cursor=bottle.request.query.get('cursor') or None)
        return render('account_bookshelf', records=records,
                      next_url=app_url('/bookshelf?') + urlencode({'cursor': records['next_cursor']}) if records.get('next_cursor') else '')

    @app.post('/bookshelf/remove')
    @guarded
    def remove_from_shelf():
        doc_id = bottle.request.forms.get('document_id')
        if not doc_id:
            _fail(400, 'invalid_request', '缺少文档标识。')
        store.remove_from_shelf(current_user()['id'], doc_id)
        bottle.redirect(app_url('/bookshelf?changed=bookshelf'))

    return {'store': store, 'callbacks': callbacks}
