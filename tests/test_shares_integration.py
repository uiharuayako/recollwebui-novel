"""Public share capabilities through the real Recoll, Bottle and SQLite stack."""
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit, urlunsplit
import zipfile

import pytest
from webtest import TestApp

import accounts
from test_webui_integration import (
    book_for,
    client_for,
    post_form,
    post_json,
    recoll_index,
    site,
)


def anonymous(site, prefix=''):
    return TestApp(site.app, extra_environ={'SCRIPT_NAME': prefix} if prefix else {})


def share_for(client, document_id, **values):
    return post_json(client, '/api/shares', dict(document_id=document_id, **values), status=201).json['share']


def request_target(url, prefix=''):
    parsed = urlsplit(url)
    path = parsed.path
    if prefix:
        assert path.startswith(prefix + '/')
        path = path[len(prefix):]
    return urlunsplit(('', '', path, parsed.query, ''))


def reader_config(response):
    match = re.search(r'window\.RECOLL_READER\s*=\s*(\{.*?\});', response.text, re.S)
    assert match, 'The public page must bootstrap the existing reader'
    return json.loads(match.group(1))


@pytest.fixture
def shared_folder(site):
    client = client_for(site)
    indexed = book_for(client)
    library_key = site.store.get_document(indexed['documentId'])['library_key']
    root = Path(tempfile.mkdtemp(prefix='公开书单-', dir=site.books))
    first, second = root / '01-first.txt', root / '第二卷' / '02-second.txt'
    second.parent.mkdir()
    first.write_text('First shared chapter.\n', encoding='utf-8')
    second.write_text('Second shared chapter, from a nested directory.\n', encoding='utf-8')
    ref = site.store.register_document(library_key, first.as_uri(), str(first),
                                       title='Shared chapter', format='txt', content_version='fixture')
    try:
        yield root, first, second, ref['id']
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_single_book_share_opens_without_login_and_serves_txt_and_range(site, prefix):
    owner = client_for(site, prefix=prefix)
    book = book_for(owner)
    before = time.time()
    share = share_for(owner, book['documentId'])
    assert before + 7 * accounts.DAY <= share['expires_at'] <= time.time() + 7 * accounts.DAY
    assert share['url'] == prefix + '/s/' + share['token']

    guest = anonymous(site, prefix)
    page = guest.get(request_target(share['url'], prefix))
    config = reader_config(page)
    assert str(site.books) not in page.text
    assert config['account'] is None
    assert config['shared'] is True
    assert config['mode'] == 'book'
    assert config['apiBase'] == share['url'] + '/api'
    assert page.headers['Cache-Control'] == 'no-store'
    assert page.headers['Referrer-Policy'] == 'no-referrer'
    assert not guest.cookies

    data = guest.get(request_target(config['apiBase'] + '/book', prefix)).json
    shared = data['book']
    assert str(site.books) not in json.dumps(data)
    assert shared['documentId'] == book['documentId']
    assert shared['folder_name'] == site.books.name
    assert not shared.get('openHandle')
    assert 'onShelf' not in shared or shared['onShelf'] is False
    resource = request_target(shared['url'], prefix)
    assert guest.get(resource).body == (site.books / 'alpha.txt').read_bytes()
    guest.head(resource)
    assert guest.get(resource, headers={'Range': 'bytes=0-11'}, status=206).body == b'sharedgalaxy'
    manifest = guest.get(request_target(shared['manifestUrl'], prefix)).json
    assert str(site.books) not in json.dumps(manifest)
    chunk = guest.get(request_target(manifest['contentUrl'] + '/0', prefix)).json
    assert 'alphatoken' in chunk['text']
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], book['documentId']) is None
    assert site.store.list_bookshelf(site.alice['id'])['items'] == []
    assert site.store.list_searches(site.alice['id'])['items'] == []


def test_single_book_capability_cannot_be_expanded_to_other_books_or_folder(site):
    owner = client_for(site)
    first, other = book_for(owner), book_for(owner, 'betatoken')
    share = share_for(owner, first['documentId'])
    guest = anonymous(site)
    base = share['url'] + '/api'
    selected = guest.get(base + '/book', {'document_id': other['documentId'], 'mode': 'folder',
                                          'query': 'betatoken'}).json['book']
    assert selected['documentId'] == first['documentId']
    guest.get(base + '/folder', status=404)
    forged = '/api/reader/file/share.' + share['token'] + '.' + other['documentId']
    guest.get(forged, status=404)
    # A share never authenticates unrelated application or mutation routes.
    guest.get('/api/account', status=401)
    guest.get('/api/reader/book', {'share': share['token'], 'document_id': other['documentId']}, status=401)
    guest.get('/results', {'share': share['token']}, status=302)
    guest.post(base + '/book', status=405)
    guest.post_json('/api/reader/open', {'handle': share['token']}, status=401)
    guest.post_json('/api/shares', {'document_id': first['documentId']}, status=401)


def test_folder_share_includes_descendants_but_excludes_symlink_escape(site, shared_folder):
    root, first, second, first_id = shared_folder
    owner = client_for(site)
    outsider = book_for(owner, 'betatoken')
    # The outside file is in the same allowed library. Folder containment must
    # therefore be checked independently of the normal library boundary.
    (root / 'escape.txt').symlink_to(site.books / 'beta.txt')
    folder = owner.get('/api/reader/folder', {'document_id': first_id}).json
    folder_id = folder['shelf']['documentId']
    share = share_for(owner, folder_id)
    guest = anonymous(site)
    assert reader_config(guest.get(share['url']))['mode'] == 'folder'
    shared = guest.get(share['url'] + '/api/folder').json
    assert str(site.books) not in json.dumps(shared)
    assert len(shared['items']) == 2
    assert {item['name'] for item in shared['items']} == {first.name, second.name}
    assert {item['name']: item['folder_name'] for item in shared['items']} == {
        first.name: first.parent.name, second.name: second.parent.name}
    for item in shared['items']:
        response = guest.get(item['url'])
        assert b'shared chapter' in response.body
        assert not item.get('openHandle')
    guest.get('/api/reader/file/share.' + share['token'] + '.' + outsider['documentId'], status=404)
    # Replacing a previously listed file with a symlink must invalidate its
    # already-issued resource URL as well as excluding it from future listings.
    listed = next(item for item in shared['items'] if item['name'] == first.name)
    first.unlink()
    first.symlink_to(site.books / 'beta.txt')
    guest.get(listed['url'], status=404)
    assert site.store.list_readings(site.alice['id'])['items'] == []


def test_share_management_is_owner_scoped_and_requires_csrf(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    book = book_for(alice)
    share = share_for(alice, book['documentId'], expires_days=2)
    assert {row['id'] for row in alice.get('/api/shares').json['items']} == {share['id']}
    assert bob.get('/api/shares').json['items'] == []
    assert share['token'] in alice.get('/shares').text
    assert share['token'] not in bob.get('/shares').text
    for action in ('update', 'delete'):
        post_json(bob, f"/api/shares/{share['id']}/{action}", {'expires_days': 3}, status=404)
        post_form(bob, f"/shares/{share['id']}/{action}", {'expires_days': '3'}, status=404)
        alice.post_json(f"/api/shares/{share['id']}/{action}", {'expires_days': 3}, status=403)
        alice.post(f"/shares/{share['id']}/{action}", {'expires_days': '3'}, status=403)
    alice.post_json('/api/shares', {'document_id': book['documentId']}, status=403)
    updated = post_json(alice, f"/api/shares/{share['id']}/update", {'expires_days': 30}).json['share']
    assert updated['token'] == share['token']
    assert updated['expires_at'] > share['expires_at'] + 27 * accounts.DAY
    guest = anonymous(site)
    item = guest.get(share['url'] + '/api/book').json['book']
    manifest = guest.get(item['manifestUrl']).json
    post_json(alice, f"/api/shares/{share['id']}/delete", {})
    assert alice.get('/api/shares').json['items'] == []
    guest.get(share['url'], status=404)
    guest.get(item['url'], status=404)
    guest.get(item['manifestUrl'], status=404)
    guest.get(manifest['contentUrl'] + '/0', status=404)


def test_expiration_is_checked_for_every_resource_and_links_can_be_renewed(site, monkeypatch):
    owner = client_for(site)
    share = share_for(owner, book_for(owner)['documentId'], expires_days=1)
    guest = anonymous(site)
    book = guest.get(share['url'] + '/api/book').json['book']
    manifest = guest.get(book['manifestUrl']).json
    with monkeypatch.context() as patch:
        patch.setattr(accounts, 'time', SimpleNamespace(time=lambda: share['expires_at'] + 1))
        for url in (share['url'], share['url'] + '/api/book', book['url'],
                    book['manifestUrl'], manifest['contentUrl'] + '/0'):
            guest.get(url, status=404)
    post_json(owner, f"/api/shares/{share['id']}/update", {'expires_days': 14})
    guest.get(share['url'])
    guest.get(book['url'])


def test_disabling_owner_immediately_invalidates_public_link_and_resources(site):
    owner = client_for(site, 'bob')
    share = share_for(owner, book_for(owner)['documentId'])
    guest = anonymous(site)
    book = guest.get(share['url'] + '/api/book').json['book']
    site.store.update_user(site.alice['id'], site.bob['id'], 'disable')
    guest.get(share['url'], status=404)
    guest.get(book['url'], status=404)


@pytest.mark.parametrize('expires_days', [0, -1, 3651, 'abc', 1.5, True, None])
def test_invalid_expiration_rejected_without_creating_links(site, expires_days):
    client = client_for(site)
    post_json(client, '/api/shares', {'document_id': book_for(client)['documentId'],
                                     'expires_days': expires_days}, status=400)
    assert site.store.list_shares(site.alice['id'])['items'] == []


def test_missing_and_unavailable_documents_cannot_be_shared(site):
    client = client_for(site)
    post_json(client, '/api/shares', {'document_id': 'missing'}, status=404)
    book = book_for(client)
    other_root = site.root / 'different-library'
    other_root.mkdir()
    (site.config / 'recoll.conf').write_text(
        site.base_config.replace(f'topdirs = {site.books}', f'topdirs = {other_root}'), encoding='utf-8')
    post_json(client, '/api/shares', {'document_id': book['documentId']}, status=404)
    assert site.store.list_shares(site.alice['id'])['items'] == []


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_html_forms_create_manage_and_cancel_folder_share(site, shared_folder, prefix):
    root, _, _, first_id = shared_folder
    owner = client_for(site, prefix=prefix)
    page = owner.get('/shares/new', {'document_id': first_id, 'mode': 'folder'})
    assert page.html.select_one('.share-target .app-folder-name').get_text() == root.parent.name
    form = next(form for form in page.forms.values() if 'document_id' in form.fields)
    assert form['expires_days'].value == '7'
    folder_id = form['document_id'].value
    assert site.store.get_document(folder_id)['format'] == 'folder'
    assert site.store.get_document(folder_id)['source_path'] == str(root)
    response = form.submit(status=303)
    redirect = urlsplit(response.location)
    assert redirect.path == prefix + '/shares'
    created_id = parse_qs(redirect.query)['created'][0]
    share = next(item for item in site.store.list_shares(site.alice['id'])['items'] if item['id'] == created_id)
    page = owner.get(request_target(response.location, prefix))
    assert share['token'] in page.text
    assert page.html.select_one('.share-card .app-folder-name').get_text() == root.parent.name
    assert 'source_path' not in share
    post_form(owner, f"/shares/{share['id']}/update", {'expires_days': '30'}, status=303)
    post_form(owner, f"/shares/{share['id']}/delete", status=303)
    anonymous(site, prefix).get('/s/' + share['token'], status=404)


def test_public_access_does_not_depend_on_or_expose_existing_login(site):
    owner, visitor = client_for(site), client_for(site, 'bob')
    share = share_for(owner, book_for(owner)['documentId'])
    # Even an authenticated guest receives a read-only share reader without an
    # account progress handle or authenticated account bootstrap.
    page = visitor.get(share['url'])
    assert reader_config(page)['account'] is None
    shared = visitor.get(share['url'] + '/api/book').json['book']
    assert not shared.get('openHandle')
    visitor.get(shared['url'])
    assert site.store.list_readings(site.bob['id'])['items'] == []
    assert site.store.list_readings(site.alice['id'])['items'] == []


def test_epub_share_serves_manifest_and_sandboxed_members_until_revoked(site, shared_folder):
    root, _, _, first_id = shared_folder
    source = root / 'Shared.epub'
    chapter = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Shared EPUB chapter</h1><script>alert(1)</script></body></html>'
    package = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Shared EPUB</dc:title></metadata>'
               '<manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
               '<spine><itemref idref="c1"/></spine></package>')
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('OEBPS/book.opf', package)
        archive.writestr('OEBPS/chapter.xhtml', chapter)
        archive.writestr('OEBPS/image.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>')
    library = site.store.get_document(first_id)['library_key']
    document = site.store.register_document(library, source.as_uri(), str(source),
                                             title='Shared EPUB', format='epub')
    owner = client_for(site)
    share = share_for(owner, document['id'])
    guest = anonymous(site)
    book = guest.get(share['url'] + '/api/book').json['book']
    manifest = guest.get(book['manifestUrl']).json
    assert manifest['title'] == 'Shared EPUB'
    assert len(manifest['chapters']) == 1
    response = guest.get(manifest['startUrl'])
    assert response.text == chapter
    policy = response.headers['Content-Security-Policy']
    assert "script-src 'none'" in policy and "form-action 'none'" in policy
    assert 'sandbox' in policy
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    image_url = manifest['contentBaseUrl'] + 'OEBPS/image.svg'
    assert guest.get(image_url).content_type == 'image/svg+xml'
    guest.get(manifest['contentBaseUrl'] + '../outside.txt', status=404)
    post_json(owner, f"/api/shares/{share['id']}/delete", {})
    for url in (book['url'], book['manifestUrl'], manifest['startUrl'], image_url):
        guest.get(url, status=404)


def test_html_share_keeps_scripts_and_forms_inert(site):
    owner = client_for(site)
    share = share_for(owner, book_for(owner, 'gammapulse')['documentId'])
    guest = anonymous(site)
    book = guest.get(share['url'] + '/api/book').json['book']
    response = guest.get(book['url'])
    assert '<script>' in response.text
    assert '<form' in response.text
    policy = response.headers['Content-Security-Policy']
    assert 'sandbox allow-same-origin' in policy
    assert "script-src 'none'" in policy
    assert "form-action 'none'" in policy
    assert "connect-src 'none'" in policy
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert not response.headers.get('Set-Cookie')


def test_chm_share_render_capabilities_preserve_sandbox_and_revoke_immediately(site, shared_folder, monkeypatch):
    root, _, _, first_id = shared_folder
    source = root / 'Shared.chm'
    source.write_bytes(b'fixture CHM source; extraction is injected')
    extracted = site.root / 'extracted-chm'
    extracted.mkdir()
    (extracted / 'Index.htm').write_text('<html><body><script src="/Chapter.js"></script></body></html>', encoding='utf-8')
    script = 'document.write("<h1>Shared CHM chapter</h1>");'
    (extracted / 'Chapter.js').write_text(script, encoding='utf-8')
    outside = site.root / 'outside-archive.txt'
    outside.write_text('private outside archive', encoding='utf-8')
    (extracted / 'escape.txt').symlink_to(outside)
    manifest_fixture = {'title': 'Shared CHM', 'start_path': 'Index.htm',
                        'toc': [{'label': 'Start', 'href': 'Index.htm', 'depth': 0}]}
    monkeypatch.setattr(site.module, 'get_reader_chm_content_root',
                        lambda path: (str(extracted), manifest_fixture))
    library = site.store.get_document(first_id)['library_key']
    document = site.store.register_document(library, source.as_uri(), str(source),
                                             title='Shared CHM', format='chm')
    owner = client_for(site)
    share = share_for(owner, document['id'])
    guest = anonymous(site)
    book = guest.get(share['url'] + '/api/book').json['book']
    manifest = guest.get(book['manifestUrl']).json
    rendered = guest.get(manifest['renderStartUrl'], headers={'Sec-Fetch-Dest': 'iframe'})
    assert b'chm-bridge.js' in rendered.body
    assert manifest['renderChannel'] in rendered.text
    assert manifest['renderBaseUrl'] + 'Chapter.js' in rendered.text
    policy = rendered.headers['Content-Security-Policy']
    assert 'sandbox allow-scripts;' in policy and 'allow-same-origin' not in policy
    assert "connect-src 'none'" in policy and "form-action 'none'" in policy
    assert rendered.headers['Referrer-Policy'] == 'no-referrer'
    assert not rendered.headers.get('Set-Cookie')
    script_url = manifest['renderBaseUrl'] + 'Chapter.js'
    script_response = guest.get(script_url, headers={'Sec-Fetch-Dest': 'script'})
    assert script_response.content_type == 'application/javascript'
    assert script_response.text == script
    direct = guest.get(manifest['renderStartUrl'], headers={'Sec-Fetch-Dest': 'document'})
    assert "script-src 'none'" in direct.headers['Content-Security-Policy']
    assert b'chm-bridge.js' not in direct.body
    inert = guest.get(manifest['startUrl'])
    assert "script-src 'none'" in inert.headers['Content-Security-Policy']
    guest.get(manifest['renderBaseUrl'] + 'escape.txt', status=404)
    guest.get(manifest['renderBaseUrl'] + '../outside-archive.txt', status=404)
    guest.post(manifest['renderStartUrl'], status=405)
    guest.get('/api/account', {'ticket': manifest['renderBaseUrl']}, status=401)
    post_json(owner, f"/api/shares/{share['id']}/delete", {})
    for url in (book['manifestUrl'], manifest['startUrl'], manifest['renderStartUrl'], script_url):
        guest.get(url, status=404)


@pytest.mark.parametrize('document_id', [None, 7, [], {}])
def test_nonstring_document_ids_are_bad_requests(site, document_id):
    owner = client_for(site)
    post_json(owner, '/api/shares', {'document_id': document_id}, status=400)
    assert site.store.list_shares(site.alice['id'])['items'] == []


@pytest.mark.parametrize('token', ['missing', 'a' * 63, 'a' * 64, 'a' * 65, 'a' * 63 + '.'])
def test_malformed_and_unknown_share_tokens_are_404(site, token):
    guest = anonymous(site)
    guest.get('/s/' + token, status=404)
    guest.get('/s/' + token + '/api/book', status=404)
    guest.get('/api/reader/file/share.' + token + '.' + 'a' * 32, status=404)
