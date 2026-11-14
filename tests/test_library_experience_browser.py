"""Account-backed bookshelf controls work at a WSGI mount and on touch screens."""
from pathlib import Path
import os
import shutil
from urllib.parse import urlsplit

import pytest

from test_account_web import make_app, login, PASSWORD
from accounts import Store

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/library'
FOLDER_NAMES = ('设计 与阅读', '这是一个用于验证窄屏截断显示的很长很长的文件夹名称', '文学 & <资料>')


@pytest.fixture
def shelf_client(tmp_path):
    store = Store(tmp_path / 'library.sqlite3')
    store.initialize()
    user = store.create_user('member', PASSWORD, must_change_password=False)
    for index, (title, format_) in enumerate([
        ('The Design of Everyday Things', 'epub'),
        ('阅读的艺术', 'pdf'),
        ('<script>alert("book")</script>', 'txt'),
    ]):
        path = f'/{FOLDER_NAMES[index]}/book{index}'
        document = store.register_document('fixture', 'file://' + path, path, title=title, format=format_)
        store.add_to_shelf(user['id'], document['id'])
        if index == 0:
            settings = store.get_settings(user['id'])
            opened = store.open_reading(user['id'], document['id'], settings['reading_epoch'], settings['progress_epoch'])
            store.save_progress(user['id'], document['id'], {'chapterDocIndex': 1}, opened['progress']['revision'],
                                'fixture-writer', 1, settings['progress_epoch'], progress=42)
    client = make_app(store, PREFIX)
    login(client)
    return client


def route_client(page, client):
    def handler(route):
        parsed = urlsplit(route.request.url)
        path = parsed.path.removeprefix(PREFIX)
        if path.startswith('/static/'):
            source = ROOT / path.lstrip('/')
            if source.is_file():
                mime = 'text/css' if source.suffix == '.css' else 'text/javascript' if source.suffix == '.js' else 'application/octet-stream'
                return route.fulfill(content_type=mime, body=source.read_bytes())
            return route.fulfill(status=404, body='')
        result = client.get(path + ('?' + parsed.query if parsed.query else ''), expect_errors=True)
        route.fulfill(status=result.status_int, content_type=result.content_type, body=result.body)
    page.route('**/*', handler)


@pytest.mark.parametrize('mobile', [False, True])
def test_bookshelf_filter_view_progress_and_keyboard(shelf_client, mobile):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900},
                                      is_mobile=mobile, has_touch=mobile)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        route_client(page, shelf_client)
        page.goto('http://recoll.test' + PREFIX + '/bookshelf')
        page.locator('.library-toolbar').wait_for(state='visible')
        assert page.locator('[data-book-title]:visible').count() == 3
        assert set(page.locator('.app-folder-name').all_text_contents()) == set(FOLDER_NAMES)
        assert page.locator('.app-folder-name').filter(has_text=FOLDER_NAMES[1]).get_attribute('title') == FOLDER_NAMES[1]
        assert page.locator('.app-folder-name *').count() == 0
        metadata_layout = '''nodes => nodes.map(node => {
            const format = node.querySelector('.app-format-tag').getBoundingClientRect();
            const folder = node.querySelector('.app-folder-name').getBoundingClientRect();
            const title = node.querySelector('h2').getBoundingClientRect();
            return folder.width > 0 && folder.bottom <= title.top
                && Math.abs(format.top + format.height / 2 - folder.top - folder.height / 2) < 1;
        })'''
        assert all(page.locator('.account-book-body').evaluate_all(metadata_layout))
        assert page.locator('progress').get_attribute('value') == '42.0'
        assert page.locator('progress').get_attribute('aria-label') == '已读 42%'
        assert page.get_by_text('<script>alert("book")</script>', exact=True).count() == 1
        assert page.locator('form[action="/library/bookshelf/remove"]').count() == 3
        assert page.locator('form[action="/library/bookshelf/remove"] input[name=csrf]').first.get_attribute('value')
        assert page.locator('.account-bookshelf a').first.get_attribute('href').startswith('/library/reader?document_id=')

        page.get_by_role('button', name='列表视图', exact=True).click()
        assert page.locator('.account-bookshelf').get_attribute('data-view') == 'list'
        assert all(page.locator('.account-book-body').evaluate_all(metadata_layout))
        assert page.get_by_role('button', name='列表视图', exact=True).get_attribute('aria-pressed') == 'true'
        page.reload()
        page.locator('.library-toolbar').wait_for(state='visible')
        assert page.locator('.account-bookshelf').get_attribute('data-view') == 'list'
        page.get_by_label('筛选本页').fill('DESIGN')
        assert page.locator('[data-book-title]:visible').count() == 1
        assert page.locator('#library-count').text_content() == '本页匹配 1 / 3 本'
        page.get_by_label('筛选本页').fill('不在本页的标题')
        assert page.locator('[data-book-title]:visible').count() == 0
        assert page.locator('#library-no-results').is_visible()
        page.get_by_role('button', name='清除筛选', exact=True).click()
        assert page.locator('[data-book-title]:visible').count() == 3
        assert page.get_by_label('筛选本页').evaluate('node => node === document.activeElement')
        page.get_by_role('button', name='网格视图', exact=True).focus()
        page.keyboard.press('Enter')
        assert page.locator('.account-bookshelf').get_attribute('data-view') == 'grid'
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        if mobile:
            for selector in ('.library-view button', '.account-bookshelf .account-button', '.account-bookshelf button'):
                assert all(box['height'] >= 44 for box in page.locator(selector).evaluate_all('nodes => nodes.map(n => ({height: n.getBoundingClientRect().height}))'))
        assert not errors, errors
        context.close()
        browser.close()


def test_bookshelf_remains_usable_without_javascript(shelf_client):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(java_script_enabled=False)
        route_client(page, shelf_client)
        page.goto('http://recoll.test' + PREFIX + '/bookshelf')
        assert page.locator('[data-book-title]:visible').count() == 3
        assert not page.locator('.library-toolbar').is_visible()
        assert not page.locator('#library-no-results').is_visible()
        assert page.get_by_role('link', name='继续阅读').count() == 3
        browser.close()


def test_navigation_fits_long_account_names_at_intermediate_widths(shelf_client):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page()
        route_client(page, shelf_client)
        page.goto('http://recoll.test' + PREFIX + '/bookshelf')
        page.locator('.app-user-name').evaluate("node => node.textContent = '我的读书账号名称很长'")
        for width in (601, 640, 700, 768, 1024):
            page.set_viewport_size({'width': width, 'height': 900})
            for selector in ('.app-brand', '.account-nav', '.app-user-menu'):
                box = page.locator(selector).bounding_box()
                assert box['x'] >= 0 and box['x'] + box['width'] <= width, (width, selector, box)
        for width in (1280, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            page.screenshot(path=str(ROOT / f'.tmp/ux-previews/library-{width}.png'), full_page=True)
        browser.close()
