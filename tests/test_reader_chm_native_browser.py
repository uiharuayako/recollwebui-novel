"""Original script-driven CHM pages through a real, authenticated HTTP server.

An opaque sandbox drops SameSite cookies on nested document/script requests.
Using a real server here catches that browser behavior as well as CSP and MIME
failures that a Playwright route mocked with an authenticated client would hide.
"""
import json
import os
from pathlib import Path
import shutil
from socketserver import ThreadingMixIn
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
import zipfile

import pytest

from test_webui_integration import PASSWORD, recoll_index, site


sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright


ISOLATION_PROBE = r"""
window.__nativeIsolation = {};
try { top.document.body; window.__nativeIsolation.parentBlocked = false; }
catch (error) { window.__nativeIsolation.parentBlocked = error.name === 'SecurityError'; }
try {
  Object.getOwnPropertyDescriptor(Document.prototype, 'cookie').get.call(document);
  window.__nativeIsolation.cookieBlocked = false;
}
catch (error) { window.__nativeIsolation.cookieBlocked = error.name === 'SecurityError'; }
fetch('/api/account').then(() => { window.__nativeIsolation.accountBlocked = false; },
                          () => { window.__nativeIsolation.accountBlocked = true; });
const forbiddenImage = new Image();
forbiddenImage.src = 'https://chm-external.invalid/blocked.png';
"""


@pytest.fixture
def native_chm(site):
    if not shutil.which('7z'):
        pytest.skip('7z is required for the native CHM browser check')
    source = site.books / 'native-browser.chm'
    header = '<!doctype html><html><head><meta charset="gb2312"></head><body>'
    loader = r"""
function loadurl(url, number) {
  localStorage.setItem('skytxt', number);
  document.location = url;
}
"""
    cover = header + '<h1>原版电子书目录</h1><script src="List1.js"></script><script>' + loader + r"""
for (let index = 0; index < pages.length; index++) {
  document.write('<p><a id="chapter-' + index + '" href="javascript:loadurl(\'Chapter1.htm\',' + index + ')">' + pages[index][1] + '</a></p>');
}
""" + ISOLATION_PROBE + '</script></body></html>'
    chapter = header + '<script src="List1.js"></script><script>' + loader + r"""
window.__legacyChapterNumber = Number(localStorage.getItem('skytxt') || 0);
document.write('<scr' + 'ipt src="txt/' + pages[window.__legacyChapterNumber][0] + '.txt"></scr' + 'ipt>');
""" + ISOLATION_PROBE + '</script></body></html>'
    entries = {
        'Index.htm': header + '<iframe src="Cover1.htm" style="width:100%;height:100vh;border:0"></iframe></body></html>',
        'Cover1.htm': cover,
        'Chapter1.htm': chapter,
        'List1.js': "var pages=new Array();\npages[0]=['101','第一章 开始'];\npages[1]=['102','第二章 归来'];\n",
    }
    for number, title in [(101, '第一章 开始'), (102, '第二章 归来')]:
        paragraphs = ''.join(f'<p>原版章节 {number} 段落 {index:03d} 的阅读内容，用于检验真实滚动和恢复。</p>'
                             for index in range(160))
        entries[f'txt/{number}.txt'] = (
            'document.write(' + json.dumps('<h1>' + title + '</h1>', ensure_ascii=False) + ');\n'
            'document.write(' + json.dumps(paragraphs, ensure_ascii=False) + ');\n')
    # 7z detects the small ZIP container; only the extracted CHM layout matters.
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content.encode('gbk'))
    config = {'dirs': {str(site.books): str(site.config)}, 'confdir': str(site.config)}
    document = site.module.appdata.register_path(str(source), title='原版电子书', format='chm', config=config)
    return SimpleNamespace(source=source, document=document, originals={
        name: content.encode('gbk') for name, content in entries.items()})


@pytest.fixture
def live_site(site):
    requests = []

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    def application(environ, start_response):
        # Record presence only; credentials must never appear in test output.
        requests.append({'path': environ.get('PATH_INFO', ''),
                         'method': environ.get('REQUEST_METHOD'),
                         'cookie': bool(environ.get('HTTP_COOKIE')),
                         'destination': environ.get('HTTP_SEC_FETCH_DEST', '')})
        return site.app(environ, start_response)

    server = make_server('127.0.0.1', 0, application, server_class=ThreadedServer, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _frame_with_heading(page, title, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in page.frames:
            try:
                if frame.get_by_role('heading', name=title, exact=True).count():
                    return frame
            except Exception:
                # A legacy navigation may replace a frame during this poll.
                pass
        page.wait_for_timeout(50)
    pytest.fail(f'Original CHM heading did not render: {title}; frame entries: '
                + repr([urlsplit(frame.url).path.rsplit('/content/', 1)[-1] for frame in page.frames]))


def _wait_for_scroll(page, frame, minimum=500, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if frame.evaluate('() => window.scrollY') > minimum:
            return
        page.wait_for_timeout(50)
    pytest.fail('Original CHM scrolling was not restored or controlled')


@pytest.mark.parametrize('mobile', [False, True])
@pytest.mark.parametrize('remote_http', [False, True], ids=['localhost', 'remote-http'])
def test_native_chm_original_scripts_nested_frames_and_progress(site, native_chm, live_site, mobile, remote_http):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Chromium is required for native CHM browser checks')
    errors, external_requests = [], []
    base_url = live_site.url.replace('127.0.0.1', 'chm-native.test') if remote_http else live_site.url
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=[
            '--no-sandbox', '--no-proxy-server', '--host-resolver-rules=MAP chm-native.test 127.0.0.1'])
        context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1200, 'height': 900},
                                      is_mobile=mobile, has_touch=mobile)

        def block_external(route):
            external_requests.append(route.request.url)
            route.abort()

        context.route('https://chm-external.invalid/**', block_external)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        page.goto(base_url + '/login')
        assert page.evaluate('window.isSecureContext') is not remote_http
        page.locator('input[name=username]').fill('alice')
        page.locator('input[name=password]').fill(PASSWORD)
        with page.expect_navigation():
            page.locator('button[type=submit]').click()
        # Use the browser's hostname resolver and real cookie handling for API reads.
        book = page.evaluate("""async id => (await (await fetch('/api/reader/book?document_id=' +
            encodeURIComponent(id))).json()).book""", native_chm.document['id'])
        with page.expect_response(lambda response: urlsplit(response.url).path.endswith('/manifest')
                                  and '/chm/' in urlsplit(response.url).path) as manifest_response:
            page.goto(base_url + '/reader?document_id=' + book['documentId'])
        manifest = manifest_response.value.json()
        assert manifest['renderStartUrl'] and manifest['renderBaseUrl'] and manifest['renderChannel']

        cover = _frame_with_heading(page, '原版电子书目录')
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
        page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
        assert page.locator('.reader-error').count() == 0
        assert cover.locator('#chapter-1').inner_text() == '第二章 归来'
        assert page.locator('#reader-chm-iframe').get_attribute('sandbox') == 'allow-scripts'
        cover.locator('#chapter-1').click()
        chapter = _frame_with_heading(page, '第二章 归来')
        assert chapter.evaluate('() => window.__legacyChapterNumber') == 1
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
        assert '原版章节 102 段落 159' in chapter.locator('body').inner_text()
        chapter.wait_for_function('() => window.__nativeIsolation?.accountBlocked === true')
        assert chapter.evaluate('() => window.__nativeIsolation') == {
            'parentBlocked': True, 'cookieBlocked': True, 'accountBlocked': True}

        # The outer reader must command the actual nested reading document.
        page.locator('#reader-progress-range').evaluate(
            "e=>{e.value='650';e.dispatchEvent(new Event('change',{bubbles:true}));}")
        _wait_for_scroll(page, chapter)
        page.wait_for_function("() => document.querySelector('#reader-progress-label').textContent !== '0%'")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        progress_url = '/api/reader/progress/' + book['documentId']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = page.evaluate('async url => (await fetch(url)).json()', progress_url)
            if (saved.get('position') and 'Chapter1.htm' in json.dumps(saved['position'])
                    and saved['position'].get('scrollTop', 0) > 500):
                break
            page.wait_for_timeout(50)
        assert 'Chapter1.htm' in json.dumps(saved.get('position'))
        assert saved['position']['scrollTop'] > 500
        assert saved['position']['storage']['local']['skytxt'] == '1'

        page.reload()
        chapter = _frame_with_heading(page, '第二章 归来')
        assert chapter.evaluate('() => window.__legacyChapterNumber') == 1
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
        assert page.locator('.reader-error').count() == 0
        _wait_for_scroll(page, chapter)
        chapter.wait_for_function('(top) => Math.abs(window.scrollY - top) < 4',
                                  arg=saved['position']['scrollTop'])
        render_path = urlsplit(manifest['renderBaseUrl']).path
        resources = [request for request in live_site.requests if request['path'].startswith(render_path)
                     and request['path'].endswith(('Cover1.htm', 'List1.js', 'txt/102.txt'))]
        assert resources and any(not request['cookie'] for request in resources), resources
        if remote_http:
            assert all(not request['destination'] for request in resources), resources
            assert any(request['path'].endswith('txt/102.txt') for request in resources), resources
        else:
            assert any(request['path'].endswith('txt/102.txt') and request['destination'] == 'script'
                       for request in resources), resources
        files_root = Path(site.module.get_chm_cache_paths(str(native_chm.source))['files_root'])
        assert {str(path.relative_to(files_root)) for path in files_root.rglob('*') if path.is_file()} == set(native_chm.originals)
        for name, original in native_chm.originals.items():
            assert (files_root / name).read_bytes() == original
        assert not external_requests, external_requests
        assert not [request for request in live_site.requests
                    if request['path'] == '/api/account' and not request['cookie']]
        assert not errors, errors
        browser.close()


def test_native_chm_works_on_plain_http_remote_host(native_chm, live_site):
    """Remote HTTP omits Fetch Metadata headers, unlike trustworthy localhost."""
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Chromium is required for native CHM browser checks')
    remote_url = live_site.url.replace('127.0.0.1', 'chm-native.test')
    responses, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=[
            '--no-sandbox', '--no-proxy-server', '--host-resolver-rules=MAP chm-native.test 127.0.0.1'])
        context = browser.new_context()
        context.route('https://chm-external.invalid/**', lambda route: route.abort())
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def inspect_response(response):
            path = urlsplit(response.url).path
            if '/chm-render/' in path:
                responses.append({'entry': path.rsplit('/content/', 1)[-1], 'status': response.status,
                                  'policy': response.headers.get('content-security-policy', ''),
                                  'type': response.headers.get('content-type', '')})

        page.on('response', inspect_response)
        page.goto(remote_url + '/login')
        assert not page.evaluate('window.isSecureContext')
        page.locator('input[name=username]').fill('alice')
        page.locator('input[name=password]').fill(PASSWORD)
        with page.expect_navigation():
            page.locator('button[type=submit]').click()
        # Browser fetch uses Chromium's hostname mapping; APIRequestContext does not.
        book = page.evaluate("""async id => (await (await fetch('/api/reader/book?document_id=' +
            encodeURIComponent(id))).json()).book""", native_chm.document['id'])
        page.goto(remote_url + '/reader?document_id=' + book['documentId'])
        try:
            cover = _frame_with_heading(page, '原版电子书目录', timeout=5)
        except pytest.fail.Exception as error:
            pytest.fail(f'Plain HTTP native reader failed: {error}; responses={responses}; errors={errors}')
        cover.locator('#chapter-1').click()
        chapter = _frame_with_heading(page, '第二章 归来')
        assert chapter.evaluate('() => window.__legacyChapterNumber') == 1
        assert '原版章节 102 段落 159' in chapter.locator('body').inner_text()
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
        native_requests = [request for request in live_site.requests if '/chm-render/' in request['path']]
        assert native_requests and all(not request['destination'] for request in native_requests)
        assert not errors, errors
        browser.close()


def test_native_chm_direct_plain_http_document_stays_inert_without_bridge(native_chm, live_site):
    """No-header top-level visits must be inert even when the bridge cannot load."""
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Chromium is required for native CHM browser checks')
    remote_url = live_site.url.replace('127.0.0.1', 'chm-native.test')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=[
            '--no-sandbox', '--no-proxy-server', '--host-resolver-rules=MAP chm-native.test 127.0.0.1'])
        context = browser.new_context()
        context.route('**/static/reader/chm-bridge.js', lambda route: route.abort())
        context.route('https://chm-external.invalid/**', lambda route: route.abort())
        page = context.new_page()
        page.goto(remote_url + '/login')
        page.locator('input[name=username]').fill('alice')
        page.locator('input[name=password]').fill(PASSWORD)
        with page.expect_navigation():
            page.locator('button[type=submit]').click()
        manifest = page.evaluate("""async id => {
            const {book} = await (await fetch('/api/reader/book?document_id=' + encodeURIComponent(id))).json();
            return (await fetch(book.manifestUrl)).json();
        }""", native_chm.document['id'])
        target = remote_url + urlsplit(manifest['renderBaseUrl']).path + 'Cover1.htm'
        page.goto(target)
        page.get_by_role('heading', name='原版电子书目录', exact=True).wait_for()
        page.wait_for_timeout(150)
        assert page.locator('#chapter-0, #chapter-1').count() == 0
        assert page.evaluate('() => typeof window.pages') == 'undefined'
        assert page.evaluate('() => typeof window.__nativeIsolation') == 'undefined'
        assert "script-src 'none'" in page.locator('meta[http-equiv="Content-Security-Policy"]').get_attribute('content')
        direct = [request for request in live_site.requests if '/chm-render/' in request['path']]
        # Chromium's preload scanner may fetch List1.js before the synchronous
        # guard installs its meta policy. The execution checks above still hold.
        assert sum(request['path'].endswith('/Cover1.htm') for request in direct) == 1
        assert direct and all(not request['destination'] for request in direct), direct
        browser.close()
