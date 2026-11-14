"""Use real Recoll, account sessions and mounted WSGI forms to check the app UI."""
from pathlib import Path
import os
import shutil
from urllib.parse import urlsplit

import pytest

from test_webui_integration import (
    recoll_index, site, client_for, book_for, post_json, progress_body,
    post_form, local_url,
)

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/library'


def route_site(page, client):
    def handler(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path.removeprefix(PREFIX)
        if path.startswith('/static/'):
            source = ROOT / path.lstrip('/')
            if not source.is_file():
                return route.fulfill(status=404, body='')
            mime = 'text/css' if source.suffix == '.css' else 'text/javascript' if source.suffix in ('.js', '.mjs') else 'application/octet-stream'
            return route.fulfill(content_type=mime, body=source.read_bytes())
        target = path + ('?' + parsed.query if parsed.query else '')
        if request.method == 'POST':
            headers = {'Content-Type': request.headers.get('content-type', '')}
            if 'x-csrf-token' in request.headers:
                headers['X-CSRF-Token'] = request.headers['x-csrf-token']
            response = client.post(target, params=request.post_data or '', headers=headers, expect_errors=True)
        else:
            response = client.get(target, expect_errors=True)
        headers = {'Content-Type': response.content_type + '; charset=utf-8'}
        if response.location:
            headers['Location'] = response.location
        route.fulfill(status=response.status_int, headers=headers, body=response.body)
    page.route('**/*', handler)


@pytest.mark.parametrize('width', [1280, 390, 320])
def test_search_reading_entry_and_responsive_chrome(site, width):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    client = client_for(site, prefix=PREFIX)
    book = book_for(client)
    post_json(client, '/api/reader/open', {'handle': book['openHandle']})
    post_json(client, '/api/reader/progress/' + book['documentId'], progress_body(client, book))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': width, 'height': 900}, is_mobile=width < 700, has_touch=width < 700)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        route_site(page, client)
        page.goto('http://recoll.test' + PREFIX + '/')
        assert page.locator('.account-nav [aria-current=page]').text_content() == '发现文档'
        assert page.locator('.app-reading-progress progress').get_attribute('value') == '37.5'
        assert page.locator('.app-continue-card').get_attribute('href').startswith(PREFIX + '/reader?document_id=')
        page.locator('#main-content').focus()
        page.keyboard.press('/')
        assert page.locator('#library-query').evaluate('node => node === document.activeElement')
        page.locator('#library-query').fill('sharedgalaxy')
        page.keyboard.press('/')
        assert page.locator('#library-query').input_value() == 'sharedgalaxy/'
        page.locator('#library-query').fill('sharedgalaxy')
        page.locator('.app-search-filters > summary').click()
        page.locator('#search-before').click()
        calendar = page.locator('.date_selector:visible')
        calendar.wait_for(state='visible')
        page.wait_for_timeout(200)
        box = calendar.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width
        assert box['y'] >= 0 and box['y'] < 900
        calendar.locator('td.selectable_day').first.click()
        assert page.locator('#search-before').input_value()
        page.locator('#search-before').fill('')
        page.keyboard.press('Tab')
        page.locator('#search-sort').select_option('filename')
        page.locator('#search-order').select_option('1')
        page.get_by_role('button', name='搜索文库').click()
        page.wait_for_url('**/results?**')
        assert page.locator('#found b').text_content() == '3'
        assert page.locator('#search-sort').input_value() == 'filename'
        assert page.locator('#search-order').input_value() == '1'
        assert page.locator('.search-result-title').count() == 1
        assert '/library/results?' in page.locator('.pages a').first.get_attribute('href')
        assert page.locator('.search-result-links > a').first.is_visible()
        page.locator('.shelf-toggle').click()
        page.wait_for_function("document.querySelector('.shelf-toggle').dataset.onShelf === '1'")
        assert page.locator('.app-shelf-status').text_content() == '已加入我的书架。'
        page.locator('.app-result-more > summary').click()
        assert page.get_by_role('link', name='下载文档', exact=True).is_visible()
        page.keyboard.press('Escape')
        assert not page.locator('.app-result-more').evaluate('node => node.open')
        assert page.locator('.app-result-more > summary').evaluate('node => node === document.activeElement')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        screenshots = ROOT / '.tmp/ux-previews'
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / f'results-{width}.png'), full_page=True)
        page.goto('http://recoll.test' + PREFIX + '/')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(screenshots / f'home-{width}.png'), full_page=True)
        page.goto('http://recoll.test' + PREFIX + '/settings')
        assert page.get_by_role('heading', name='搜索设置').is_visible()
        assert page.locator('form[action="/library/set"] input[name="csrf"]').get_attribute('value')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize('width', [1280, 390])
def test_history_replays_date_filters_without_creating_calendars(site, width):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    client = client_for(site, prefix=PREFIX)
    dates = {'after': '2000-01-01', 'before': '2100-01-01'}
    response = post_form(client, '/search', {'query': 'sharedgalaxy', **dates}, status=303)
    client.get(local_url(response.location).removeprefix(PREFIX))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': width, 'height': 900}, is_mobile=width < 700, has_touch=width < 700)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        route_site(page, client)
        page.goto('http://recoll.test' + PREFIX + '/history')
        replay = page.locator('form').filter(has=page.locator('input[name="replay_id"]'))
        assert replay.count() == 1
        for name, value in dates.items():
            assert replay.locator(f'input[type="hidden"][name="{name}"]').input_value() == value
        assert page.locator('.date_selector, .jdpicker_w').count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        replay.get_by_role('button', name='重新搜索').click()
        page.wait_for_url('**/results?**')
        assert page.locator('#found b').text_content() == '3'
        for name, value in dates.items():
            assert page.locator(f'#search-{name}').input_value() == value
        assert not errors, errors
        browser.close()


def test_search_empty_state_and_no_script_forms(site):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    client = client_for(site, prefix=PREFIX)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(java_script_enabled=False, viewport={'width': 390, 'height': 844})
        route_site(page, client)
        page.goto('http://recoll.test' + PREFIX + '/')
        page.get_by_label('搜索文档全文').fill('missingdocumentzzzz')
        page.get_by_role('button', name='搜索文库').click()
        page.wait_for_url('**/results?**')
        assert page.locator('#found b').text_content() == '0'
        assert page.get_by_role('heading', name='还没有找到匹配的文档').is_visible()
        page.get_by_text('筛选与排序', exact=False).first.click()
        assert page.get_by_label('所在文件夹').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()
