"""Anonymous sharing works through the real WSGI app, CSP, and Chromium."""
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
import threading
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest

from test_webui_integration import book_for, client_for, post_json, recoll_index, site


playwright_api = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope='module')
def browser():
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    with playwright_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        yield instance
        instance.close()


@pytest.fixture
def live_site(site):
    server = make_server('127.0.0.1', 0, site.app, server_class=ThreadedServer, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield site, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def account_record_counts(store):
    with sqlite3.connect(store.path) as connection:
        return {table: connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ('reading_history', 'reading_progress', 'folder_resume', 'bookshelf')}


@pytest.mark.parametrize(('mode', 'mobile'), [('book', False), ('book', True), ('folder', False)])
def test_guest_reads_real_share_and_switches_folder_books_without_account_requests(live_site, browser, mode, mobile):
    site, origin = live_site
    owner = client_for(site)
    book = book_for(owner)
    folder_root = None
    if mode == 'folder':
        folder_root = Path(tempfile.mkdtemp(prefix='share-live-', dir=site.books))
        first, second = folder_root / '01-first.txt', folder_root / '02-second.txt'
        first.write_text('First public live chapter.\nRead this book without signing in.\n', encoding='utf-8')
        second.write_text('Second public live chapter.\nThe next book is also public.\n', encoding='utf-8')
        document = site.store.register_document(
            site.store.get_document(book['documentId'])['library_key'], first.as_uri(), str(first),
            title='First live book', format='txt')
        folder = owner.get('/api/reader/folder', {'document_id': document['id']}).json
        document_id = folder['shelf']['documentId']
        first_text = 'First public live chapter.'
    else:
        document_id = book['documentId']
        first_text = 'alphatoken'
    share = post_json(owner, '/api/shares', {'document_id': document_id}, status=201).json['share']
    before = account_record_counts(site.store)
    context = browser.new_context(
        viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900},
        is_mobile=mobile, has_touch=mobile)
    page = context.new_page()
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    context.on('request', lambda request: requests.append((request.method, urlsplit(request.url).path)))
    try:
        response = page.goto(origin + share['url'])
        assert response.status == 200
        assert "default-src 'self'" in response.headers['content-security-policy']
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert response.headers['cache-control'] == 'no-store'
        content = page.frame_locator('#page-area iframe').locator('body')
        playwright_api.expect(content).to_contain_text(first_text, timeout=20000)
        assert page.evaluate('window.RECOLL_READER.account') is None
        assert page.evaluate('window.RECOLL_READER.shared') is True
        assert page.locator('.reader-shelf-button, .reader-share-button').count() == 0
        if mode == 'folder':
            page.locator('#reader-tab-books').click()
            playwright_api.expect(page.locator('#reader-booklist button')).to_have_count(2)
            page.locator('#reader-next-book').click()
            playwright_api.expect(content).to_contain_text('Second public live chapter.', timeout=20000)
            playwright_api.expect(page.locator('#reader-prev-book')).to_be_enabled()
            page.locator('#reader-prev-book').click()
            playwright_api.expect(content).to_contain_text(first_text, timeout=20000)
            (ROOT / '.tmp').mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT / '.tmp/share-public-reader.png'), full_page=True)
        page.close()
        assert not context.cookies()
        assert all(method in ('GET', 'HEAD') for method, _ in requests), requests
        assert any(path == share['url'] + '/api/' + mode for _, path in requests)
        assert any(path.startswith('/api/reader/txt/share.') and path.endswith('/chunk/0') for _, path in requests)
        assert not any(path == '/api/account' or path.startswith(('/api/reader/progress/', '/api/bookshelf/'))
                       or path == '/api/reader/open' for _, path in requests), requests
        assert account_record_counts(site.store) == before
        assert not errors, errors
    finally:
        context.close()
        if folder_root:
            shutil.rmtree(folder_root, ignore_errors=True)
