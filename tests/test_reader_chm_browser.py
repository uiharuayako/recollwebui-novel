"""Real CHM iframe scrolling, semantic anchors, and delayed search returns."""
from pathlib import Path
from urllib.parse import urlsplit
import copy
import json
import os
import shutil
import time

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'views/reader.tpl').read_text()
TEMPLATE = TEMPLATE[TEMPLATE.index('<div id="reader-app"'):TEMPLATE.index('<script>')]

VISIBLE_PARAGRAPH = r"""() => {
  const frame=document.querySelector('#reader-chm-iframe'),doc=frame?.contentDocument;
  if(!doc?.body) return null;
  for(const paragraph of doc.querySelectorAll('p')) {
    const rect=paragraph.getBoundingClientRect();
    if(rect.bottom>8 && rect.top<frame.clientHeight) return paragraph.textContent.match(/Chapter \d paragraph \d+/)?.[0];
  }
  return null;
}"""
INNER_POSITION = """() => {
  const frame=document.querySelector('#reader-chm-iframe'),win=frame.contentWindow;
  return {top:win.scrollY,max:win.document.documentElement.scrollHeight-win.innerHeight,
    href:win.location.pathname.split('/').pop()};
}"""


@pytest.mark.parametrize('mobile', [False, True])
def test_chm_scroll_controls_anchor_and_delayed_search_return(mobile):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    settings = {'progress_epoch': 3, 'reading_epoch': 4, 'progress_sync_enabled': True,
                'preferences': {'font_size': 20}}
    saved = {'position': None, 'revision': 7, 'content_version': 'v1'}
    calls, errors, returned_documents = [], [], []
    first_document_loads = 0
    hold_next_head, held_head = False, None
    prefix = '/library'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1200, 'height': 900},
                                      is_mobile=mobile, has_touch=mobile)

        def handler(route):
            nonlocal first_document_loads, hold_next_head, held_head
            request = route.request
            path = urlsplit(request.url).path.removeprefix(prefix)

            def send(value, mime='application/json', status=200):
                route.fulfill(status=status, content_type=mime,
                              body=value if isinstance(value, (str, bytes)) else json.dumps(value))

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
                return send(source.read_bytes(), 'text/javascript' if source.suffix in ('.js', '.mjs') else 'text/css')
            if path == '/reader':
                config = {'mode': 'book', 'apiBase': prefix + '/api/reader', 'account': {
                    'userId': 'a', 'sessionId': 's', 'csrf': 'c', 'settings': settings, 'baseUrl': prefix}}
                return send('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                            f'<link rel="stylesheet" href="{prefix}/static/style.css"><link rel="stylesheet" href="{prefix}/static/reader/reader-ui.css">'
                            '</head><body>' + TEMPLATE + '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                            f'<script type="module" src="{prefix}/static/reader/app.js"></script></body></html>', 'text/html')
            if path == '/api/reader/book':
                return send({'book': {'documentId': 'chm-doc', 'contentVersion': 'v1', 'openHandle': 'handle', 'format': 'chm',
                                     'path': '/fixture.chm', 'name': 'CHM fixture', 'manifestUrl': prefix + '/chm/manifest'}})
            if path == '/api/reader/progress/chm-doc':
                return send({**saved, 'settings': settings})
            if path == '/api/account':
                return send({'user': {'id': 'a'}, 'sessionId': 's', 'settings': settings})
            if path == '/chm/manifest':
                return send({'startUrl': prefix + '/chm/chapter1.html', 'startPath': 'chapter1.html', 'contentBaseUrl': prefix + '/chm/',
                             'toc': [{'href': f'chapter{index}.html', 'label': f'Chapter {index}',
                                      'url': prefix + f'/chm/chapter{index}.html'} for index in [1, 2]]})
            if path in ['/chm/chapter1.html', '/chm/chapter2.html']:
                if request.method == 'HEAD':
                    if hold_next_head and path.endswith('chapter2.html'):
                        hold_next_head = False
                        held_head = route
                        return
                    return send('', 'text/html')
                chapter = 1 if path.endswith('chapter1.html') else 2
                if request.resource_type == 'document':
                    returned_documents.append(chapter)
                    if chapter == 1:
                        first_document_loads += 1
                        if first_document_loads > 1:
                            # HEAD succeeds immediately; the replacement document
                            # remains pending while the old search page is visible.
                            time.sleep(.15)
                text = ''.join(f'<p>Chapter {chapter} paragraph {index:03d} reading content. '
                               'A long sentence keeps the same paragraph visible when its lines reflow after changing text size.'
                               + (' needle-on-second-page' if chapter == 2 and index == 90 else '') + '</p>'
                               for index in range(140))
                return send(f'<!doctype html><html><head><meta charset="utf-8"></head><body><h1>Chapter {chapter}</h1>{text}</body></html>', 'text/html')
            return send({}, status=404)

        context.route('**/*', handler)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://recoll.test' + prefix + '/reader')
        page.frame_locator('#reader-chm-iframe').get_by_role('heading', name='Chapter 1', exact=True).wait_for()
        page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
        page.wait_for_function("document.querySelectorAll('#reader-toc-list button').length === 2")

        # Dragging the visible footer must scroll the CHM document's own window.
        page.locator('#reader-progress-range').evaluate("input=>{input.value='650';input.dispatchEvent(new Event('change',{bubbles:true}));}")
        page.wait_for_function("() => document.querySelector('#reader-chm-iframe').contentWindow.scrollY > 1500")
        position = page.evaluate(INNER_POSITION)
        assert abs(position['top'] / position['max'] - .65) < .02, position
        assert page.locator('#reader-progress-label').inner_text() != '0%'
        page.evaluate("""() => {
            const win = document.querySelector('#reader-chm-iframe').contentWindow;
            win.scrollTo(0, (win.document.documentElement.scrollHeight - win.innerHeight) * .45);
        }""")
        # Ordinary iframe scrolling updates the footer without waiting for the
        # six-second persistence interval.
        page.wait_for_function("document.querySelector('#reader-progress-label').textContent === '45%'", timeout=2000)

        page.locator('#reader-toggle-settings').click()
        visible_before = page.evaluate(VISIBLE_PARAGRAPH)
        assert visible_before
        page.locator('#reader-font-increase').click()
        page.wait_for_function("document.querySelector('#reader-font-size').textContent === '22px'")
        page.wait_for_timeout(250)
        assert page.evaluate(VISIBLE_PARAGRAPH) == visible_before
        page.locator('#reader-close-settings').click()
        original = page.evaluate(INNER_POSITION)
        original_text = page.evaluate(VISIBLE_PARAGRAPH)
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(250)
        assert saved['position']['href'] == 'chapter1.html'
        assert abs(saved['position']['scrollTop'] - original['top']) < 2

        if mobile:
            page.locator('#reader-toggle-toc').click()
        page.locator('#reader-tab-search').click()
        page.locator('#reader-search-input').fill('needle-on-second-page')
        page.locator('#reader-search-submit').click()
        page.locator('#reader-search-results button').wait_for()
        assert page.locator('#reader-search-results button').count() == 1
        page.locator('#reader-search-results button').click()
        page.locator('#reader-return-position').wait_for(state='visible')
        page.wait_for_function("document.querySelector('#reader-chm-iframe').contentWindow.location.pathname.endsWith('chapter2.html')")
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(200)
        assert saved['position']['href'] == 'chapter1.html'
        assert abs(saved['position']['scrollTop'] - original['top']) < 2

        page.locator('#reader-return-position').click()
        page.locator('#reader-return-position').wait_for(state='hidden')
        page.frame_locator('#reader-chm-iframe').get_by_role('heading', name='Chapter 1', exact=True).wait_for()
        page.wait_for_function("() => Math.abs(document.querySelector('#reader-chm-iframe').contentWindow.scrollY - "
                               + str(original['top']) + ") < 2")
        assert page.evaluate(VISIBLE_PARAGRAPH) == original_text
        page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(250)
        assert saved['position']['href'] == 'chapter1.html'
        assert abs(saved['position']['scrollTop'] - original['top']) < 2
        assert returned_documents == [1, 2, 1]
        assert len([path for path, _ in calls if path.endswith('/open')]) == 1

        if not mobile:
            # An old HEAD response must not override the later user choice,
            # either in the iframe or the active outline item.
            page.locator('#reader-tab-toc').click()
            hold_next_head = True
            page.locator('#reader-toc-list button').nth(1).click()
            page.wait_for_timeout(50)
            assert held_head is not None
            with page.expect_response(lambda response: response.request.resource_type == 'document'
                                      and urlsplit(response.url).path.endswith('/chapter1.html')):
                page.locator('#reader-toc-list button').nth(0).click()
            page.wait_for_function("document.querySelector('#reader-chm-iframe').dataset.readerContentLoaded === 'yes'")
            held_head.fulfill(status=200, content_type='text/html', body='')
            page.wait_for_timeout(300)
            assert page.evaluate(INNER_POSITION)['href'] == 'chapter1.html'
            assert returned_documents == [1, 2, 1, 1]
            assert page.locator('#reader-toc-list button').nth(0).get_attribute('aria-current') == 'location'
            assert page.locator('#reader-toc-list button').nth(1).get_attribute('aria-current') != 'location'
        assert not errors, errors
        context.close()
        browser.close()
