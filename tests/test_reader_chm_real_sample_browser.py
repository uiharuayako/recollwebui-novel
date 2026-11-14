"""Opt-in, read-only validation of a real legacy CHM on ordinary HTTP.

Set RECOLL_TEST_CHM_MANIFEST to an existing extracted CHM manifest.json.
The sourcePath archive and that cache are only read. Accounts and the HTTP
server are temporary; the browser uses a non-localhost hostname deliberately.
"""
import hashlib
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

import pytest

from test_webui_integration import PASSWORD, recoll_index, site

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def real_sample(site, monkeypatch):
    location = os.environ.get('RECOLL_TEST_CHM_MANIFEST')
    if not location:
        pytest.skip('Set RECOLL_TEST_CHM_MANIFEST for the read-only real CHM check')
    manifest_path = Path(location)
    manifest = json.loads(manifest_path.read_text())
    source = Path(manifest['sourcePath'])
    files = manifest_path.parent / 'files'
    assert source.is_file() and files.is_dir()
    before = {str(path): _digest(path) for path in [source, manifest_path, *files.rglob('*')] if path.is_file()}
    original_config = site.module.get_config

    def get_config():
        config = original_config()
        config['dirs'] = {**config['dirs'], str(source.parent): str(site.config)}
        return config

    def existing_cache(path):
        assert Path(path).resolve() == source.resolve()
        return str(files), manifest

    monkeypatch.setattr(site.module, 'get_config', get_config)
    monkeypatch.setattr(site.module, 'get_reader_chm_content_root', existing_cache)
    document = site.module.appdata.register_path(str(source), title=manifest.get('title') or source.stem,
                                               format='chm', config={'dirs': {str(site.books): str(site.config),
                                                                              str(source.parent): str(site.config)},
                                                                     'confdir': str(site.config)})
    yield SimpleNamespace(document=document, manifest=manifest)
    assert {path: _digest(Path(path)) for path in before} == before
    assert {str(path) for path in files.rglob('*') if path.is_file()} == {
        path for path in before if path not in (str(source), str(manifest_path))}


@pytest.fixture
def nonsecure_site(site):
    requests = []

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    def application(environ, start_response):
        # Keep capability tickets, session cookies, and full URLs out of logs.
        requests.append({'entry': environ.get('PATH_INFO', '').rsplit('/', 1)[-1],
                         'destination': environ.get('HTTP_SEC_FETCH_DEST', '')})
        return site.app(environ, start_response)

    server = make_server('127.0.0.1', 0, application, server_class=ThreadedServer, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f'http://recoll-chm.test:{server.server_port}', requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _reading_frame(page, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in page.frames:
            try:
                if predicate(frame):
                    return frame
            except Exception:
                pass  # A native link can replace a frame between two calls.
        page.wait_for_timeout(50)
    pytest.fail('The original CHM reading frame did not become ready')


def test_real_chm_nonsecure_http_original_navigation_restore_and_direct_open(site, real_sample, nonsecure_site):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:
        pytest.skip('Chromium is required for the real CHM check')
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=[
            '--no-sandbox', '--no-proxy-server', '--host-resolver-rules=MAP recoll-chm.test 127.0.0.1'])
        context = browser.new_context(viewport={'width': 1200, 'height': 900})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(nonsecure_site.url + '/login')
        assert page.evaluate('isSecureContext') is False
        assert page.evaluate('typeof crypto.getRandomValues') == 'function'
        assert page.evaluate('typeof structuredClone') == 'function'
        page.locator('input[name=username]').fill('alice')
        page.locator('input[name=password]').fill(PASSWORD)
        with page.expect_navigation():
            page.locator('button[type=submit]').click()

        try:
            with page.expect_response(lambda response: '/chm/' in urlsplit(response.url).path
                                      and urlsplit(response.url).path.endswith('/manifest'), timeout=8000) as result:
                page.goto(nonsecure_site.url + '/reader?mode=folder&document_id=' + real_sample.document['id'])
        except Exception:
            stage = page.locator('#page-area')
            pytest.fail('CHM manifest was not requested: ' + (stage.inner_text() if stage.count() else page.title())
                        + '; browser errors: ' + repr(errors))
        manifest = result.value.json()
        cover = _reading_frame(page, lambda frame: frame.locator('a[href*="Chapter"]').count() > 1)
        cover_entry = urlsplit(cover.url).path.rsplit('/content/', 1)[-1]
        cover.locator('a[href*="Chapter"]').nth(1).click()
        chapter = _reading_frame(page, lambda frame: frame.locator('#txt').count()
                                 and len(frame.locator('#txt').inner_text()) > 100)
        assert chapter.evaluate('localStorage.getItem("skytxt")') == '1'
        original_text = chapter.locator('#txt').inner_text()
        assert len(original_text) > 1000
        chapter.locator('a:has(img[alt="下页"])').click()
        chapter = _reading_frame(page, lambda frame: frame.locator('#txt').count()
                                 and frame.evaluate('localStorage.getItem("skytxt")') == '2'
                                 and len(frame.locator('#txt').inner_text()) > 100)
        assert chapter.locator('#txt').inner_text() != original_text
        chapter.locator('a:has(img[alt="上页"])').click()
        chapter = _reading_frame(page, lambda frame: frame.locator('#txt').count()
                                 and frame.evaluate('localStorage.getItem("skytxt")') == '1'
                                 and frame.locator('#txt').inner_text() == original_text)
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
        page.locator('#reader-progress-range').evaluate(
            "e=>{e.value='650';e.dispatchEvent(new Event('change',{bubbles:true}));}")
        chapter.wait_for_function('() => scrollY > 500')
        page.wait_for_function("() => document.querySelector('#reader-progress-label').textContent === '65%'")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        progress_url = nonsecure_site.url + '/api/reader/progress/' + real_sample.document['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = page.evaluate('async url => (await (await fetch(url)).json()).position', progress_url) or {}
            if saved.get('scrollTop', 0) > 500 and saved.get('storage', {}).get('local', {}).get('skytxt') == '1':
                break
            page.wait_for_timeout(50)
        assert saved.get('scrollTop', 0) > 500
        assert saved['storage']['local']['skytxt'] == '1'
        page.reload()
        chapter = _reading_frame(page, lambda frame: frame.locator('#txt').count()
                                 and frame.locator('#txt').inner_text() == original_text)
        chapter.wait_for_function('(top) => Math.abs(scrollY - top) < 4', arg=saved['scrollTop'])
        assert chapter.evaluate('localStorage.getItem("skytxt")') == '1'
        assert not errors, errors
        assert all(not request['destination'] for request in nonsecure_site.requests)

        # Direct opening on this insecure origin also lacks Fetch Metadata.
        # The earliest response guard must stop the book's own scripts/frames.
        direct = context.new_page()
        direct.goto(nonsecure_site.url + manifest['renderBaseUrl'] + cover_entry)
        direct.wait_for_timeout(150)
        assert direct.evaluate('typeof window.pages') == 'undefined'
        assert direct.locator('a[href*="Chapter"]').count() == 0
        assert not direct.evaluate('isSecureContext')
        browser.close()
