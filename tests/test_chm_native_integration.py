"""Native CHM resource capabilities remain isolated from application sessions."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import time
from urllib.parse import urlsplit
import zipfile

import pytest
from webtest import TestApp

from test_webui_integration import recoll_index, site, client_for, identity


PREFIX = '/library'


@pytest.fixture
def native_chm(site):
    if not shutil.which('7z'):
        pytest.skip('7z is required for archive extraction')
    source = site.books / 'native-sandbox.chm'
    entries = {
        'Index.htm': b'<html><body><iframe src="Cover.htm"></iframe></body></html>',
        'Cover.htm': ('<html><head><meta charset="gb2312"><script src="List.js"></script></head>'
                      '<body><a href="Chapter.htm">开始阅读</a><img src="/image.png"></body></html>').encode('gbk'),
        'Chapter.htm': ('<html><head><meta charset="gb2312"></head><body>'
                        '<script src="txt/1.txt"></script></body></html>').encode('gbk'),
        'Early.htm': (b'<!doctype html><body onload="window.originalEventRan=true">'
                      b'<iframe src="Cover.htm"></iframe><script>window.originalScriptRan=true</script></body>'),
        'Comment.htm': b'<!--> <script>window.originalScriptRan=true</script> -->',
        'Doctype.htm': b'<!doctype html broken "<x> <script>window.originalScriptRan=true</script>">',
        'List.js': 'var chapterTitle="第一章";'.encode('gbk'),
        'txt/1.txt': 'document.write("<h1>第一章</h1><p>正文内容。</p>");'.encode('gbk'),
        'txt/utf8.txt': 'document.write("<h1>另一章</h1>");'.encode('utf-8'),
        'image.png': b'fixture-image-bytes',
    }
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    config = {'dirs': {str(site.books): str(site.config)}, 'confdir': str(site.config)}
    document = site.module.appdata.register_path(str(source), title='测试电子书', format='chm', config=config)
    client = client_for(site, prefix=PREFIX)
    book = client.get('/api/reader/book', {'document_id': document['id']}).json['book']
    manifest = client.get(book['manifestUrl'].removeprefix(PREFIX)).json
    ticket = manifest['renderBaseUrl'].split('/chm-render/', 1)[1].split('/', 1)[0]
    anonymous = TestApp(site.app, extra_environ={'SCRIPT_NAME': PREFIX})
    cache = Path(site.module.get_chm_cache_paths(str(source))['files_root'])
    return SimpleNamespace(client=client, anonymous=anonymous, source=source, entries=entries,
                           document=document, book=book, manifest=manifest, ticket=ticket, cache=cache)


def local_url(url):
    return urlsplit(url).path.removeprefix(PREFIX)


def content_url(book, path):
    return local_url(book.manifest['renderBaseUrl'] + path)


def altered_ticket(site, ticket, **changes):
    data = json.loads(base64.urlsafe_b64decode(ticket.split('.')[0] + '==='))
    data.update(changes)
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':'), sort_keys=True).encode()).rstrip(b'=')
    signature = base64.urlsafe_b64encode(hmac.new(site.store.secret(), body, hashlib.sha256).digest()).rstrip(b'=')
    return (body + b'.' + signature).decode()


def test_native_responses_preserve_archive_and_inert_reader_routes(site, native_chm):
    book = native_chm
    before = hashlib.sha256(book.source.read_bytes()).hexdigest()
    path = content_url(book, 'Cover.htm')
    response = book.anonymous.get(path, headers={'Sec-Fetch-Dest': 'iframe'})
    assert response.charset == 'gbk'
    text = response.body.decode('gbk')
    assert '开始阅读' in text
    assert text.index('chm-bridge.js') < text.index('List.js')
    assert 'data-chm-channel="' + book.manifest['renderChannel'] + '"' in text
    assert 'data-chm-root="http://localhost' + book.manifest['renderBaseUrl'] + '"' in text
    assert 'src="' + book.manifest['renderBaseUrl'] + 'image.png"' in text
    policy = response.headers['Content-Security-Policy']
    assert 'sandbox allow-scripts;' in policy
    assert 'allow-same-origin' not in policy
    assert "connect-src 'none'" in policy
    assert "object-src 'none'" in policy
    assert "form-action 'none'" in policy
    assert 'frame-src http://localhost' + book.manifest['renderBaseUrl'] + ';' in policy
    assert "script-src 'unsafe-inline' http://localhost" + book.manifest['renderBaseUrl'] in policy
    assert 'http://localhost/library/static/reader/chm-bridge.js;' in policy
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Cache-Control'] == 'no-store'
    assert not response.headers.get('Set-Cookie')
    direct = book.anonymous.get(path, headers={'Sec-Fetch-Dest': 'document'})
    assert "script-src 'none'" in direct.headers['Content-Security-Policy']
    assert "frame-src 'none'" in direct.headers['Content-Security-Policy']
    assert 'allow-scripts' not in direct.headers['Content-Security-Policy']
    assert b'chm-bridge.js' not in direct.body
    lan = book.anonymous.get(content_url(book, 'Early.htm'))
    assert 'sandbox allow-scripts;' in lan.headers['Content-Security-Policy']
    assert lan.body.startswith(b'<!doctype html>')
    assert lan.text.index('if(window===window.top)') < lan.text.index('chm-bridge.js') < lan.text.index('onload=')
    assert 'script-src \'none\'' in lan.text
    for name in ('Comment.htm', 'Doctype.htm'):
        malformed = book.anonymous.get(content_url(book, name))
        assert malformed.text.index('if(window===window.top)') < malformed.text.index('window.originalScriptRan')
    assert book.anonymous.head(path).body == b''
    old = book.client.get(local_url(book.manifest['contentBaseUrl'] + 'Cover.htm'))
    assert "script-src 'none'" in old.headers['Content-Security-Policy']
    assert "frame-src 'none'" in old.headers['Content-Security-Policy']
    assert b'chm-bridge.js' not in old.body
    assert hashlib.sha256(book.source.read_bytes()).hexdigest() == before
    assert {str(path.relative_to(book.cache)): path.read_bytes() for path in book.cache.rglob('*') if path.is_file()} == book.entries


def test_script_backed_txt_resources_keep_encoding_and_nosniff(native_chm):
    book = native_chm
    for name, charset, expected in [('List.js', 'gb18030', '第一章'),
                                     ('txt/1.txt', 'gb18030', '正文内容'),
                                     ('txt/utf8.txt', 'utf-8', '另一章')]:
        response = book.anonymous.get(content_url(book, name), headers={'Sec-Fetch-Dest': 'script'})
        assert response.content_type == 'application/javascript'
        assert response.charset == charset
        assert expected in response.text
        assert response.body == book.entries[name]
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert book.anonymous.get(content_url(book, 'txt/1.txt')).content_type == 'application/javascript'
    assert book.anonymous.get(content_url(book, 'txt/1.txt'), headers={'Sec-Fetch-Dest': 'document'}).content_type == 'text/plain'
    assert book.client.get(local_url(book.manifest['contentBaseUrl'] + 'txt/1.txt')).content_type == 'text/plain'


def test_resource_ticket_cannot_authenticate_other_routes_or_methods(site, native_chm):
    book = native_chm
    book.anonymous.get(local_url(book.manifest['startUrl']), status=401)
    book.anonymous.get(local_url(book.book['manifestUrl']), status=401)
    book.anonymous.get('/api/account', {'ticket': book.ticket}, status=401)
    book.anonymous.get('/api/reader/book', {'ticket': book.ticket, 'document_id': book.document['id']}, status=401)
    book.anonymous.get('/api/reader/chm-render/' + book.ticket + '/manifest', status=401)
    book.anonymous.post(content_url(book, 'Cover.htm'), status=401)
    book.anonymous.get(content_url(book, 'Cover.htm'), headers={'Cookie': 'recoll_session=invalid'}, status=401)
    other = client_for(site, username='bob', prefix=PREFIX)
    other.get(content_url(book, 'Cover.htm'), status=403)
    other_session = client_for(site, prefix=PREFIX)
    other_session.get(content_url(book, 'Cover.htm'), status=403)
    reader_token = book.book['manifestUrl'].split('/chm/', 1)[1].split('/', 1)[0]
    book.anonymous.get(content_url(book, 'Cover.htm').replace(book.ticket, reader_token), status=401)
    book.client.get('/api/reader/chm/' + book.ticket + '/manifest', status=403)


@pytest.mark.parametrize('change', ['signature', 'expired', 'purpose', 'user', 'channel'])
def test_invalid_capabilities_are_rejected(site, native_chm, change):
    book = native_chm
    if change == 'signature':
        ticket = book.ticket[:-1] + ('a' if book.ticket[-1] != 'a' else 'b')
    else:
        changes = {'expired': {'exp': time.time() - 1}, 'purpose': {'purpose': 'reader'},
                   'user': {'user_id': site.bob['id']}, 'channel': {'payload': {'document_id': book.document['id']}}}
        ticket = altered_ticket(site, book.ticket, **changes[change])
    book.anonymous.get(content_url(book, 'Cover.htm').replace(book.ticket, ticket), status=401)


@pytest.mark.parametrize('invalidate', ['revoked_at', 'idle_expires_at', 'absolute_expires_at', 'auth_version', 'status', 'deleted'])
def test_resource_tickets_stop_when_the_session_is_no_longer_active(site, native_chm, invalidate):
    book = native_chm
    session_id = identity(book.client)['sessionId']
    with sqlite3.connect(site.store.path) as connection:
        if invalidate in ('revoked_at', 'idle_expires_at', 'absolute_expires_at'):
            connection.execute('UPDATE sessions SET ' + invalidate + '=? WHERE id=?', (time.time() - 1, session_id))
        elif invalidate == 'auth_version':
            connection.execute('UPDATE users SET auth_version=auth_version+1 WHERE id=?', (site.alice['id'],))
        elif invalidate == 'status':
            connection.execute("UPDATE users SET status='disabled' WHERE id=?", (site.alice['id'],))
        else:
            connection.execute('DELETE FROM sessions WHERE id=?', (session_id,))
    book.anonymous.get(content_url(book, 'Cover.htm'), status=401)


def test_resource_reads_do_not_refresh_sessions_and_confine_paths(site, native_chm):
    book = native_chm
    session_id = identity(book.client)['sessionId']
    with sqlite3.connect(site.store.path) as connection:
        connection.execute('UPDATE sessions SET last_seen_at=? WHERE id=?', (time.time() - 600, session_id))
        before = connection.execute('SELECT last_seen_at,idle_expires_at FROM sessions WHERE id=?', (session_id,)).fetchone()
    book.anonymous.get(content_url(book, 'Cover.htm'))
    with sqlite3.connect(site.store.path) as connection:
        assert connection.execute('SELECT last_seen_at,idle_expires_at FROM sessions WHERE id=?', (session_id,)).fetchone() == before
    outside = site.root / 'outside.txt'
    outside.write_text('outside-archive fixture', encoding='utf-8')
    (book.cache / 'escape.txt').symlink_to(outside)
    book.anonymous.get(content_url(book, 'escape.txt'), status=404)
    book.anonymous.get(content_url(book, '../outside.txt'), status=404)
    book.anonymous.get(content_url(book, '%2e%2e%2foutside.txt'), status=404)
