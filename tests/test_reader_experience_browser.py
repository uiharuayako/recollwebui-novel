"""End-to-end reading controls, windowed TXT search, and progress isolation."""
from pathlib import Path
from urllib.parse import urlsplit
import copy
import json
import os
import shutil

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'views/reader.tpl').read_text()
TEMPLATE = TEMPLATE[TEMPLATE.index('<div id="reader-app"'):TEMPLATE.index('<script>')]

VISIBLE_TXT_PARAGRAPH = """() => {
  const view=document.querySelector('#page-area').getBoundingClientRect();
  for(const frame of document.querySelectorAll('#page-area iframe')) {
    const bounds=frame.getBoundingClientRect(),doc=frame.contentDocument;
    if(bounds.bottom<=view.top || bounds.top>=view.bottom || !doc?.body) continue;
    const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT);
    for(let node;node=walker.nextNode();) {
      if(!/Chunk \\d+ paragraph \\d+/.test(node.textContent)) continue;
      const range=doc.createRange();range.selectNodeContents(node);
      const rect=range.getBoundingClientRect();
      if(rect.bottom+bounds.top>view.top+8 && rect.top+bounds.top<view.bottom) return node.textContent.match(/Chunk \\d+ paragraph \\d+/)[0];
    }
  }
  return null;
}"""


@pytest.mark.parametrize('mobile', [False, True])
def test_txt_reading_controls_full_search_and_temporary_progress(mobile):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    settings = {'progress_epoch': 3, 'reading_epoch': 4, 'progress_sync_enabled': True,
                'preferences': {'font_size': 20}}
    saved = {'position': {'chunkIndex': 1, 'chunkProgressRatio': .2}, 'revision': 7, 'content_version': 'v1'}
    chunks = [f'第{index + 1}章\n' + '\n'.join(f'Chunk {index:02d} paragraph {line:03d} ordinary reading content.' for line in range(100)) for index in range(8)]
    chunks[7] = chunks[7].replace('paragraph 030', 'paragraph 030 unique distant search needle')
    calls, fetched, errors = [], [], []
    prefix = '/library'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900},
                                      is_mobile=mobile, has_touch=mobile)

        def handler(route):
            request = route.request
            parsed = urlsplit(request.url)
            path = parsed.path.removeprefix(prefix)

            def send(value, mime='application/json', status=200):
                route.fulfill(status=status, content_type=mime, body=value if isinstance(value, (str, bytes)) else json.dumps(value))

            if request.method == 'POST':
                data = request.post_data_json
                calls.append((path, copy.deepcopy(data)))
                if path.endswith('/open'):
                    return send({'progress': saved, 'settings': settings})
                if '/progress/' in path:
                    assert data['revision'] == saved['revision']
                    saved.update(position=copy.deepcopy(data['position']), revision=saved['revision'] + 1)
                    return send(saved)
                if path == '/api/account/preferences':
                    settings['preferences'].update(data['preferences'])
                    return send({'settings': settings})
                return send({})
            if path.startswith('/static/'):
                source = ROOT / path.lstrip('/')
                if not source.is_file():
                    return send({}, status=404)
                mime = 'text/javascript' if source.suffix in ('.js', '.mjs') else 'text/css' if source.suffix == '.css' else 'application/octet-stream'
                return send(source.read_bytes(), mime)
            if path == '/reader':
                config = {'mode': 'book', 'apiBase': prefix + '/api/reader', 'account': {
                    'userId': 'a', 'sessionId': 's', 'csrf': 'c', 'settings': settings, 'baseUrl': prefix}}
                return send('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                            f'<link rel="stylesheet" href="{prefix}/static/style.css"><link rel="stylesheet" href="{prefix}/static/reader/reader-ui.css">'
                            '</head><body>' + TEMPLATE + '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                            f'<script type="module" src="{prefix}/static/reader/app.js"></script></body></html>', 'text/html')
            if path == '/api/reader/book':
                return send({'book': {'documentId': 'txt-doc', 'contentVersion': 'v1', 'openHandle': 'handle', 'format': 'txt',
                                     'path': '/fixture.txt', 'name': 'Windowed TXT fixture', 'manifestUrl': prefix + '/txt/manifest'}})
            if path == '/api/reader/progress/txt-doc':
                return send({**saved, 'settings': settings})
            if path == '/txt/manifest':
                return send({'format': 'txt', 'charset': 'utf-8', 'chunkCount': len(chunks), 'contentUrl': prefix + '/txt/chunk',
                             'chunks': [{'index': index, 'byteLength': len(text.encode()), 'start': index * 10000, 'end': (index + 1) * 10000}
                                        for index, text in enumerate(chunks)]})
            if path.startswith('/txt/chunk/'):
                index = int(path.rsplit('/', 1)[1]); fetched.append(index)
                return send({'index': index, 'text': chunks[index], 'hasPrev': index > 0, 'hasNext': index < len(chunks) - 1})
            if path == '/api/account':
                return send({'user': {'id': 'a'}, 'sessionId': 's', 'settings': settings})
            return send({}, status=404)

        context.route('**/*', handler)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://recoll.test' + prefix + '/reader')
        page.wait_for_function('() => window.rendition?.getPosition()?.chunkIndex === 1')
        page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
        page.wait_for_function("() => document.querySelector('[data-chunk-index=\"1\"] iframe')?.contentDocument?.body?.textContent.includes('Chunk 01')")

        # The sidebar must actually remain closed after its ResizeObserver runs.
        if not page.locator('#reader-sidebar').evaluate("node => node.classList.contains('is-hidden')"):
            page.locator('#reader-toggle-toc').click()
        page.wait_for_timeout(300)
        assert page.locator('#reader-sidebar').evaluate('node => node.getBoundingClientRect().right <= 1')
        if mobile:
            assert page.locator('#reader-toolbar').bounding_box()['height'] <= 100

        page.locator('#reader-toggle-settings').click()
        page.locator('#reader-theme').select_option('dark')
        page.wait_for_function("() => document.querySelector('#reader-app').dataset.theme === 'dark'")
        page.wait_for_timeout(150)
        visible_before_font_change = page.evaluate(VISIBLE_TXT_PARAGRAPH)
        assert visible_before_font_change
        page.locator('#reader-font-increase').click()
        page.wait_for_function("() => document.querySelector('#reader-font-size').textContent === '22px'")
        page.wait_for_function("() => {const f=document.querySelector('[data-chunk-index=\"1\"] iframe');return f && f.contentWindow.getComputedStyle(f.contentDocument.body).fontSize==='22px'}")
        page.locator('#reader-close-settings').click()
        page.wait_for_timeout(350)
        assert page.evaluate(VISIBLE_TXT_PARAGRAPH) == visible_before_font_change
        assert settings['preferences']['reader_theme'] == 'dark'
        assert settings['preferences']['font_size'] == 22

        page.locator('#reader-toggle-settings').click()
        page.locator('#reader-paragraph-spacing').evaluate("node=>{node.value='20';node.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.locator('#reader-text-align').select_option('justify')
        page.locator('#reader-indent').check()
        page.wait_for_function("() => {const f=document.querySelector('[data-chunk-index=\"1\"] iframe'),p=f?.contentDocument.querySelector('p[data-reader-txt-line]');if(!p)return false;const s=f.contentWindow.getComputedStyle(p);return s.marginBottom==='20px'&&s.textAlign==='justify'&&s.textIndent==='44px'}")
        page.locator('#reader-close-settings').click()

        page.locator('#reader-toggle-toc').click()
        page.locator('#reader-tab-search').click()
        page.evaluate("const slot=document.querySelector('[data-chunk-index=\"1\"]');document.querySelector('#page-area').scrollTop=slot.offsetTop+500")
        page.wait_for_function('() => window.rendition.getPosition().chunkIndex === 1')
        page.wait_for_timeout(250)
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(200)
        original = copy.deepcopy(saved['position'])
        assert original['chunkIndex'] == 1
        assert page.locator('[data-chunk-index="7"] iframe').count() == 0

        page.locator('#reader-search-input').fill('unique distant search needle')
        page.locator('#reader-search-submit').click()
        page.wait_for_function("() => document.querySelector('#reader-search-status').textContent === '找到 1 处命中'")
        assert 7 in fetched
        page.locator('#reader-search-results button').click()
        page.wait_for_function('() => window.rendition.getPosition().chunkIndex === 7')
        page.locator('#reader-return-position').wait_for(state='visible')
        page.wait_for_function("() => {const doc=document.querySelector('[data-chunk-index=\"7\"] iframe')?.contentDocument;return doc?.defaultView.CSS?.highlights?.has('reader-search') || !!doc?.querySelector('mark[data-reader-search]')}")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(250)
        assert saved['position'] == original, 'A temporary search jump must not replace the saved reading position'

        page.locator('#reader-return-position').click()
        page.wait_for_function('() => window.rendition.getPosition().chunkIndex === 1')
        page.wait_for_function("() => document.querySelector('#reader-return-banner').hidden")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(250)
        assert saved['position']['chunkIndex'] == original['chunkIndex'], {
            'original': original, 'saved': saved['position'], 'visible': page.evaluate(VISIBLE_TXT_PARAGRAPH),
            'position': page.evaluate('window.rendition.getPosition()'),
            'geometry': page.evaluate("() => {const s=document.querySelector('#page-area');return {top:s.scrollTop,height:s.scrollHeight,slots:[...s.querySelectorAll('[data-chunk-index]')].map(n=>({index:n.dataset.chunkIndex,top:n.offsetTop,height:n.offsetHeight,mounted:!!n.querySelector('iframe')}))}}"),
        }
        assert abs(saved['position']['chunkProgressRatio'] - original['chunkProgressRatio']) < .04

        # An explicit continue action commits the inspected position.
        if page.locator('#reader-sidebar').evaluate("node => node.classList.contains('is-hidden')"):
            page.locator('#reader-toggle-toc').click()
        page.locator('#reader-search-results button').click()
        page.wait_for_function('() => window.rendition.getPosition().chunkIndex === 7')
        page.locator('#reader-keep-position').click()
        page.wait_for_function("() => document.querySelector('#reader-return-banner').hidden")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(250)
        assert saved['position']['chunkIndex'] == 7
        page.evaluate("window.dispatchEvent(new CustomEvent('recoll-account-change',{detail:{type:'logout'}}))")
        assert page.locator('#reader-toc-list').text_content() == ''
        assert page.locator('#reader-search-results').text_content() == ''
        assert page.locator('#reader-search-input').input_value() == ''
        assert not errors, errors
        context.close(); browser.close()


@pytest.mark.parametrize('mobile', [False, True])
def test_epub_unmounted_search_return_and_progress_navigation(mobile):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    settings = {'progress_epoch': 3, 'reading_epoch': 4, 'progress_sync_enabled': True, 'preferences': {'font_size': 20}}
    saved = {'position': {'chapterDocIndex': 0, 'intraChapterOffset': 0}, 'revision': 7, 'content_version': 'v1'}
    prefix = '/library'
    chapters = [{'label': f'Chapter {index + 1}', 'href': f'chapter{index}.xhtml', 'url': f'{prefix}/epub/content/{index}', 'size': 8000}
                for index in range(8)]
    fetched, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900},
                                      is_mobile=mobile, has_touch=mobile)

        def handler(route):
            request = route.request
            path = urlsplit(request.url).path.removeprefix(prefix)

            def send(value, mime='application/json', status=200):
                route.fulfill(status=status, content_type=mime, body=value if isinstance(value, (str, bytes)) else json.dumps(value))

            if request.method == 'POST':
                data = request.post_data_json
                if path.endswith('/open'):
                    return send({'progress': saved, 'settings': settings})
                if '/progress/' in path:
                    assert data['revision'] == saved['revision']
                    saved.update(position=copy.deepcopy(data['position']), revision=saved['revision'] + 1)
                    return send(saved)
                return send({'settings': settings})
            if path.startswith('/static/'):
                source = ROOT / path.lstrip('/')
                if not source.is_file():
                    return send({}, status=404)
                return send(source.read_bytes(), 'text/javascript' if source.suffix in ('.js', '.mjs') else 'text/css')
            if path == '/reader':
                config = {'mode': 'book', 'apiBase': prefix + '/api/reader', 'account': {
                    'userId': 'a', 'sessionId': 's', 'csrf': 'c', 'settings': settings, 'baseUrl': prefix}}
                return send('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                            f'<link rel="stylesheet" href="{prefix}/static/style.css"><link rel="stylesheet" href="{prefix}/static/reader/reader-ui.css">'
                            '</head><body>' + TEMPLATE + '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                            f'<script type="module" src="{prefix}/static/reader/app.js"></script></body></html>', 'text/html')
            if path == '/api/reader/book':
                return send({'book': {'documentId': 'epub-doc', 'contentVersion': 'v1', 'openHandle': 'handle', 'format': 'epub',
                                     'path': '/fixture.epub', 'name': 'Windowed EPUB fixture', 'manifestUrl': prefix + '/epub/manifest'}})
            if path == '/api/reader/progress/epub-doc':
                return send({**saved, 'settings': settings})
            if path == '/epub/manifest':
                return send({'chapters': chapters, 'toc': [{'label': chapter['label'], 'href': chapter['href']} for chapter in chapters]})
            if path.startswith('/epub/content/'):
                index = int(path.rsplit('/', 1)[1]); fetched.append(index)
                paragraphs = ''.join(f'<p>Chunk {index:02d} paragraph {line:03d} ordinary reading content.</p>' for line in range(100))
                if index == 6:
                    paragraphs = paragraphs.replace('paragraph 020', 'paragraph 020 EPUB distant needle')
                return send(f'<!doctype html><html><head><meta charset="utf-8"></head><body><h1>Chapter {index + 1}</h1>{paragraphs}</body></html>', 'text/html')
            if path == '/api/account':
                return send({'user': {'id': 'a'}, 'sessionId': 's', 'settings': settings})
            return send({}, status=404)

        context.route('**/*', handler)
        page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://recoll.test' + prefix + '/reader')
        page.wait_for_function("() => document.querySelector('[data-chapter-index=\"0\"] iframe')?.dataset.readerContentLoaded === 'yes'")
        page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
        assert page.locator('#reader-toc-list button').count() == 8
        iframe = page.locator('[data-chapter-index="0"] iframe')
        first_height = iframe.evaluate('frame=>frame.offsetHeight')
        page.wait_for_timeout(250)
        assert abs(iframe.evaluate('frame=>frame.offsetHeight') - first_height) <= 1

        if page.locator('#reader-sidebar').evaluate("node=>node.classList.contains('is-hidden')"):
            page.locator('#reader-toggle-toc').click()
        page.locator('#reader-tab-search').click()
        page.locator('#page-area').evaluate('stage=>{stage.scrollTop=450}')
        page.wait_for_timeout(250)
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(150)
        original = copy.deepcopy(saved['position'])
        assert original['chapterDocIndex'] == 0
        assert page.locator('[data-chapter-index="6"] iframe').count() == 0
        page.locator('#reader-search-input').fill('EPUB distant needle')
        page.locator('#reader-search-submit').click()
        page.wait_for_function("() => document.querySelector('#reader-search-status').textContent === '找到 1 处命中'")
        assert 6 in fetched
        page.locator('#reader-search-results button').click()
        page.wait_for_function('() => window.rendition.getPosition().chapterDocIndex === 6')
        page.locator('#reader-return-position').wait_for(state='visible')
        page.wait_for_function("() => document.querySelector('[data-chapter-index=\"6\"] iframe')?.contentWindow.CSS?.highlights?.has('reader-search')")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(150)
        assert saved['position'] == original
        page.locator('#reader-return-position').click()
        page.wait_for_function('() => window.rendition.getPosition().chapterDocIndex === 0')
        page.wait_for_function("() => document.querySelector('#reader-return-banner').hidden")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(200)
        assert saved['position']['chapterDocIndex'] == 0
        assert abs(saved['position']['chapterProgressRatio'] - original['chapterProgressRatio']) < .04

        if not page.locator('#reader-sidebar').evaluate("node=>node.classList.contains('is-hidden')"):
            if mobile:
                page.locator('#reader-sidebar-backdrop').click(position={'x': 375, 'y': 400})
            else:
                page.locator('#reader-toggle-toc').click()
        page.locator('#reader-progress-range').evaluate("node=>{node.value='900';node.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function('() => window.rendition.getPosition().chapterDocIndex === 7')
        page.wait_for_timeout(250)
        assert page.locator('[data-chapter-index="7"] iframe').count() == 1
        height = page.locator('[data-chapter-index="7"] iframe').evaluate('frame=>frame.offsetHeight')
        page.wait_for_timeout(250)
        assert abs(page.locator('[data-chapter-index="7"] iframe').evaluate('frame=>frame.offsetHeight') - height) <= 1
        before_resize = page.evaluate(VISIBLE_TXT_PARAGRAPH)
        assert before_resize and before_resize.startswith('Chunk 07')
        page.set_viewport_size({'width': 844, 'height': 390} if mobile else {'width': 900, 'height': 900})
        page.wait_for_timeout(350)
        assert page.evaluate(VISIBLE_TXT_PARAGRAPH) == before_resize
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(150)
        assert saved['position']['chapterDocIndex'] == 7
        assert not errors, errors
        context.close(); browser.close()
