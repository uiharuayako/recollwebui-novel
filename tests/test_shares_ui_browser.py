"""Sharing links stay usable on mobile and public readers remain account-free."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import shutil
import time

import bottle
import pytest

sync_api = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/library'


@pytest.fixture
def browser():
    chromium = (os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
                or shutil.which('chromium-browser') or shutil.which('google-chrome'))
    if not chromium:
        pytest.skip('Chromium is required for sharing browser checks')
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium,
                                              args=['--no-sandbox'])
        yield browser
        browser.close()


def render(name, **values):
    return bottle.template(name, template_lookup=[str(ROOT / 'views')],
                           app_url=lambda path='': PREFIX + '/' + path.lstrip('/'),
                           account_user=lambda: None, account_csrf=lambda: 'csrf',
                           account_context=lambda: '{}', **values)


def static_response(route, path):
    source = ROOT / path.lstrip('/')
    if not source.is_file():
        return route.fulfill(status=404, body='')
    mime = ('text/javascript' if source.suffix in ('.js', '.mjs') else
            'text/css' if source.suffix == '.css' else 'application/octet-stream')
    route.fulfill(content_type=mime, body=source.read_bytes())


@pytest.mark.parametrize('width', [320, 1280])
def test_share_copy_fallback_and_expiry_form_are_usable(browser, width):
    item = {'id': 'share-one', 'token': 'opaque-token', 'document_id': 'book-one',
            'title': '<script>unsafe</script>', 'format': 'folder',
            'created_at': time.time(), 'expires_at': time.time() + 7 * 86400}
    html = render('account_shares', shares={'items': [item]}, created_id=item['id'], next_url='')
    page = browser.new_page(viewport={'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', { value: undefined });")

    def handler(route):
        path = urlsplit(route.request.url).path.removeprefix(PREFIX)
        if path.startswith('/static/'):
            return static_response(route, path)
        route.fulfill(content_type='text/html', body=html)

    page.route('**/*', handler)
    page.goto('http://recoll.test' + PREFIX + '/shares')
    sync_api.expect(page.locator('.share-card h2')).to_have_text(item['title'])
    sync_api.expect(page.locator('[data-share-url]')).to_have_value(
        'http://recoll.test/library/s/opaque-token')
    page.get_by_role('button', name='复制链接').click()
    sync_api.expect(page.locator('[data-share-copy-status]')).to_contain_text('已选中链接')
    assert page.locator('[data-share-url]').evaluate('el => el.selectionEnd - el.selectionStart') > 0
    page.get_by_text('修改有效期', exact=True).click()
    expiry = page.locator('input[name=expires_days]')
    sync_api.expect(expiry).to_be_visible()
    sync_api.expect(expiry).to_have_value('7')
    assert page.locator('form').filter(has=expiry).get_attribute('action') == PREFIX + '/shares/share-one/update'
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    page.close()


@pytest.mark.parametrize('mode', ['book', 'folder'])
def test_public_reader_has_no_account_controls_or_requests(browser, mode):
    config = {'mode': mode, 'apiBase': PREFIX + '/s/opaque-token/api', 'shared': True, 'account': None}
    html = render('reader', mode=mode, query_string='', results_url=PREFIX + '/s/opaque-token',
                  results_query_string='', reader_config_json=json.dumps(config), shared=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    item = {'documentId': 'book-one', 'contentVersion': 'v1', 'format': 'html',
            'name': 'Shared chapter', 'url': PREFIX + '/s/opaque-token/content/book-one'}

    def handler(route):
        path = urlsplit(route.request.url).path.removeprefix(PREFIX)
        calls.append((route.request.method, path))
        if path.startswith('/static/'):
            return static_response(route, path)
        if path == '/s/opaque-token/api/book':
            return route.fulfill(content_type='application/json', body=json.dumps({'book': item}))
        if path == '/s/opaque-token/api/folder':
            return route.fulfill(content_type='application/json', body=json.dumps({'items': [item], 'currentIndex': 0}))
        if path == '/s/opaque-token/content/book-one':
            return route.fulfill(content_type='text/html', body='<h1>Shared chapter</h1><p>Public content.</p>')
        route.fulfill(content_type='text/html', body=html)

    page.route('**/*', handler)
    page.goto('http://recoll.test' + PREFIX + '/s/opaque-token')
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Shared chapter').wait_for()
    sync_api.expect(page.locator('.reader-share-button')).to_have_count(0)
    sync_api.expect(page.locator('.reader-shelf-button')).to_have_count(0)
    sync_api.expect(page.get_by_label('分享内容', exact=True)).to_be_visible()
    assert not any(method == 'POST' or path.startswith('/api/') for method, path in calls), calls
    page.evaluate("window.dispatchEvent(new CustomEvent('recoll-account-change', { detail: { type: 'logout' } }))")
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Shared chapter').wait_for()
    assert not errors, errors
    page.close()


@pytest.mark.parametrize('mode', ['book', 'folder'])
@pytest.mark.parametrize('width', [320, 1280])
def test_reader_share_target_follows_the_current_scope(browser, mode, width):
    from test_reader_bookshelf_browser import open_reader

    page, _, _, errors = open_reader(browser, mode)
    page.set_viewport_size({'width': width, 'height': 900})
    share = page.locator('.reader-share-button')
    document_id = 'folder-id' if mode == 'folder' else 'file-1'
    sync_api.expect(share).to_have_attribute('href', PREFIX + '/shares/new?document_id=' + document_id)
    sync_api.expect(share).to_be_visible()
    if width == 320:
        assert share.bounding_box()['width'] == 44
        assert share.bounding_box()['x'] + share.bounding_box()['width'] <= width
        assert page.locator('#reader-toolbar').evaluate('el => el.scrollWidth <= el.clientWidth')
    if mode == 'folder':
        page.locator('#reader-tab-books').click()
        page.locator('#reader-next-book').click()
        page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 2').wait_for()
        sync_api.expect(share).to_have_attribute('href', PREFIX + '/shares/new?document_id=folder-id')
    assert not errors, errors
    page.close()
