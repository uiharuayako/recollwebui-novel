"""Bookshelf scope follows reader mode, including switches during a save."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import shutil

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'views/reader.tpl').read_text()
TEMPLATE = TEMPLATE[TEMPLATE.index('<div id="reader-app"'):TEMPLATE.index('<script>')]
PREFIX = '/library'


@pytest.fixture
def browser():
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    with playwright_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        yield instance
        instance.close()


def open_reader(browser, mode, on_shelf=False, hold_add=False, shelf_errors=None):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    requests, pending, errors = [], [], []
    settings = {'progress_epoch': 3, 'reading_epoch': 4, 'progress_sync_enabled': True, 'preferences': {}}
    progress = {'position': None, 'revision': 0, 'content_version': 'v1'}
    items = [
        {'documentId': f'file-{index}', 'contentVersion': 'v1', 'openHandle': f'file-handle-{index}',
         'format': 'html', 'path': f'/fixture/{index}.html', 'name': f'Chapter {index}',
         'folder_name': f'小说 <精选> & 完结 {index}',
         'url': f'{PREFIX}/content/{index}', 'onShelf': on_shelf if mode == 'book' else not on_shelf}
        for index in (1, 2)
    ]

    def handler(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix(PREFIX)

        def send(value, mime='application/json', status=200):
            route.fulfill(status=status, content_type=mime,
                          body=value if isinstance(value, (str, bytes)) else json.dumps(value))

        if request.method == 'POST':
            if path.startswith('/api/bookshelf/'):
                requests.append((urlsplit(request.url).path, request.post_data_json))
                error_status = (shelf_errors or {}).get(path.rsplit('/', 1)[-1])
                if error_status:
                    return send({'error': 'Shelf update failed'}, status=error_status)
                if hold_add and path.endswith('/add'):
                    pending.append(route)
                    return
                return send({'onShelf': path.endswith('/add')})
            if path.endswith('/open'):
                return send({'progress': progress, 'settings': settings})
            if '/progress/' in path:
                progress.update(position=request.post_data_json['position'], revision=progress['revision'] + 1)
                return send(progress)
            return send({'settings': settings})
        if path.startswith('/static/'):
            source = ROOT / path.lstrip('/')
            if source.is_file():
                return send(source.read_bytes(), 'text/javascript' if source.suffix in ('.js', '.mjs') else 'text/css')
            return send({}, status=404)
        if path == '/reader':
            config = {'mode': mode, 'apiBase': PREFIX + '/api/reader',
                      'account': {'userId': 'a', 'sessionId': 's', 'csrf': 'c', 'baseUrl': PREFIX, 'settings': settings}}
            return send('<!doctype html><html><head><meta charset="utf-8">'
                        f'<link rel="stylesheet" href="{PREFIX}/static/style.css">'
                        f'<link rel="stylesheet" href="{PREFIX}/static/reader/reader-ui.css">'
                        '</head><body>' + TEMPLATE + '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                        f'<script type="module" src="{PREFIX}/static/reader/app.js"></script></body></html>', 'text/html')
        if path == '/api/reader/book':
            return send({'book': items[0]})
        if path == '/api/reader/folder':
            return send({'items': items, 'currentIndex': 0, 'folderKey': 'folder-key',
                         'shelf': {'documentId': 'folder-id', 'openHandle': 'folder-handle',
                                   'title': 'Fixture folder', 'format': 'folder', 'onShelf': on_shelf}})
        if path.startswith('/api/reader/progress/'):
            return send({**progress, 'settings': settings})
        if path == '/api/account':
            return send({'user': {'id': 'a'}, 'sessionId': 's', 'settings': settings})
        if path.startswith('/content/'):
            chapter = path.rsplit('/', 1)[-1]
            return send(f'<!doctype html><html><body><h1>Chapter {chapter}</h1>'
                        + '<p>A paragraph in the selected file.</p>' * 20 + '</body></html>', 'text/html')
        return send({}, status=404)

    page.route('**/*', handler)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(f'http://recoll.test{PREFIX}/reader?mode={mode}')
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 1').wait_for()
    playwright_api.expect(page.locator('.reader-shelf-button')).to_be_enabled()
    return page, requests, pending, errors


@pytest.mark.parametrize('width', [1280, 390, 320])
def test_document_folder_is_above_title_and_beside_format(browser, width):
    page, _, _, errors = open_reader(browser, 'folder')
    page.set_viewport_size({'width': width, 'height': 900})
    if width < 700:
        page.locator('#reader-close-sidebar').click()
    meta = page.locator('#reader-meta')
    playwright_api.expect(meta.locator('.reader-folder-name')).to_have_text('小说 <精选> & 完结 1')
    assert meta.bounding_box()['y'] < page.locator('#reader-title').bounding_box()['y']
    assert meta.locator('.reader-format-tag').bounding_box()['y'] == pytest.approx(
        meta.locator('.reader-folder-name').bounding_box()['y'], abs=3)
    assert meta.locator('.reader-folder-name').bounding_box()['width'] >= 60
    page.locator('#reader-progress-next-book').click()
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 2').wait_for()
    playwright_api.expect(meta.locator('.reader-folder-name')).to_have_text('小说 <精选> & 完结 2')
    if width < 700:
        page.locator('#reader-toggle-toc').click()
    page.locator('#reader-tab-books').click()
    active_item = page.locator('#reader-booklist .is-active')
    playwright_api.expect(active_item.locator('.reader-folder-name')).to_have_text('小说 <精选> & 完结 2')
    assert active_item.locator('.reader-format-tag').bounding_box()['y'] == pytest.approx(
        active_item.locator('.reader-folder-name').bounding_box()['y'], abs=3)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    page.close()


@pytest.mark.parametrize('mode', ['book', 'folder'])
@pytest.mark.parametrize('on_shelf', [False, True])
def test_bookshelf_target_follows_reader_mode(browser, mode, on_shelf):
    page, requests, _, errors = open_reader(browser, mode, on_shelf)
    button = page.locator('.reader-shelf-button')
    add_label, remove_label = ('加入文件夹', '移出文件夹') if mode == 'folder' else ('加入书架', '移出书架')
    document_id = 'folder-id' if mode == 'folder' else 'file-1'
    handle = 'folder-handle' if mode == 'folder' else 'file-handle-1'
    playwright_api.expect(button).to_have_text(remove_label if on_shelf else add_label)
    if mode == 'folder':
        page.locator('#reader-tab-books').click()
        page.locator('#reader-next-book').click()
        page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 2').wait_for()
        playwright_api.expect(button).to_have_text(remove_label if on_shelf else add_label)

    for saved in (on_shelf, not on_shelf):
        button.click()
        playwright_api.expect(button).to_be_enabled()
        playwright_api.expect(button).to_have_text(add_label if saved else remove_label)
        assert requests[-1] == (PREFIX + '/api/bookshelf/' + ('remove' if saved else 'add'),
                                {'document_id': document_id} if saved else {'handle': handle})
    assert len(requests) == 2
    assert not errors, errors
    page.close()


def test_already_removed_folder_can_be_added_again(browser):
    page, requests, _, errors = open_reader(browser, 'folder', on_shelf=True, shelf_errors={'remove': 404})
    button = page.locator('.reader-shelf-button')
    button.click()
    playwright_api.expect(button).to_be_enabled()
    playwright_api.expect(button).to_have_text('加入文件夹')
    page.get_by_text('文件夹已移出书架', exact=True).wait_for()
    button.click()
    playwright_api.expect(button).to_have_text('移出文件夹')
    assert requests == [(PREFIX + '/api/bookshelf/remove', {'document_id': 'folder-id'}),
                        (PREFIX + '/api/bookshelf/add', {'handle': 'folder-handle'})]
    assert not errors, errors
    page.close()


@pytest.mark.parametrize(('on_shelf', 'shelf_errors'), [(False, {'add': 404}), (True, {'remove': 500})])
def test_failed_shelf_update_preserves_existing_state(browser, on_shelf, shelf_errors):
    page, requests, _, errors = open_reader(browser, 'folder', on_shelf=on_shelf, shelf_errors=shelf_errors)
    button = page.locator('.reader-shelf-button')
    button.click()
    playwright_api.expect(button).to_be_enabled()
    playwright_api.expect(button).to_have_text('移出文件夹' if on_shelf else '加入文件夹')
    page.get_by_text('书架未更新：Shelf update failed', exact=True).wait_for()
    assert len(requests) == 1
    assert not errors, errors
    page.close()


def test_folder_save_stays_pending_across_file_switch(browser):
    page, requests, pending, errors = open_reader(browser, 'folder', hold_add=True)
    button = page.locator('.reader-shelf-button')
    button.click()
    playwright_api.expect(button).to_be_disabled()
    page.locator('#reader-tab-books').click()
    page.locator('#reader-next-book').click()
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 2').wait_for()
    playwright_api.expect(button).to_be_disabled()
    # Even a programmatic click cannot issue another save while the same folder is pending.
    button.evaluate('node => node.click()')
    assert requests == [(PREFIX + '/api/bookshelf/add', {'handle': 'folder-handle'})]
    assert len(pending) == 1
    pending.pop().fulfill(content_type='application/json', body='{"onShelf":true}')
    playwright_api.expect(button).to_be_enabled()
    playwright_api.expect(button).to_have_text('移出文件夹')
    page.locator('#reader-prev-book').click()
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 1').wait_for()
    playwright_api.expect(button).to_have_text('移出文件夹')
    button.click()
    playwright_api.expect(button).to_have_text('加入文件夹')
    assert requests[-1] == (PREFIX + '/api/bookshelf/remove', {'document_id': 'folder-id'})
    assert len(requests) == 2
    assert not errors, errors
    page.close()


def test_progress_arrows_switch_adjacent_books_and_stop_at_list_boundaries(browser):
    page, _, _, errors = open_reader(browser, 'folder')
    previous_book = page.locator('#reader-progress-prev-book')
    next_book = page.locator('#reader-progress-next-book')
    playwright_api.expect(previous_book).to_be_disabled()
    playwright_api.expect(next_book).to_be_enabled()
    next_book.click()
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 2').wait_for()
    playwright_api.expect(next_book).to_be_disabled()
    playwright_api.expect(previous_book).to_be_enabled()
    previous_book.click()
    page.frame_locator('#page-area iframe').get_by_role('heading', name='Chapter 1').wait_for()
    playwright_api.expect(previous_book).to_be_disabled()
    playwright_api.expect(next_book).to_be_enabled()
    assert not errors, errors
    page.close()
