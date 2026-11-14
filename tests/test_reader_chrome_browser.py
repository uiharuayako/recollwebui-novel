"""Reader keyboard, focus, full screen, and narrow-screen regressions."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import shutil

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')
sync_playwright = playwright_api.sync_playwright
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'views/reader.tpl').read_text()
TEMPLATE = TEMPLATE[TEMPLATE.index('<div id="reader-app"'):TEMPLATE.index('<script>')]


@pytest.mark.parametrize('width', [1280, 390, 320])
def test_reader_chrome_keyboard_focus_and_fullscreen(width):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    settings = {'progress_epoch': 3, 'reading_epoch': 4, 'progress_sync_enabled': True, 'preferences': {'font_size': 20}}
    progress = {'position': None, 'revision': 7, 'content_version': 'v1'}
    errors = []
    mobile = width < 700
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': width, 'height': 844}, is_mobile=mobile, has_touch=mobile)

        def handler(route):
            request = route.request
            path = urlsplit(request.url).path

            def send(value, mime='application/json', status=200):
                route.fulfill(status=status, content_type=mime,
                              body=value if isinstance(value, (str, bytes)) else json.dumps(value))

            if request.method == 'POST':
                if path.endswith('/open'):
                    return send({'progress': progress, 'settings': settings})
                if '/progress/' in path:
                    progress.update(position=request.post_data_json['position'], revision=progress['revision'] + 1)
                    return send(progress)
                return send({'settings': settings})
            if path.startswith('/static/'):
                source = ROOT / path.lstrip('/')
                return send(source.read_bytes(), 'text/javascript' if source.suffix in ('.js', '.mjs') else 'text/css') if source.is_file() else send({}, status=404)
            if path == '/reader':
                config = {'mode': 'book', 'apiBase': '/api/reader', 'account': {'userId': 'a', 'sessionId': 's', 'csrf': 'c', 'settings': settings}}
                return send('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/reader/reader-ui.css">'
                            '</head><body>' + TEMPLATE + '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                            '<script type="module" src="/static/reader/app.js"></script></body></html>', 'text/html')
            if path == '/api/reader/book':
                return send({'book': {'documentId': 'chrome-doc', 'contentVersion': 'v1', 'openHandle': 'handle', 'format': 'html',
                                     'path': '/fixture.html', 'name': '在文字之间，留一点呼吸', 'url': '/content/html'}})
            if path.startswith('/api/reader/progress/'):
                return send({**progress, 'settings': settings})
            if path == '/api/account':
                return send({'user': {'id': 'a'}, 'sessionId': 's', 'settings': settings})
            if path == '/content/html':
                return send('<!doctype html><html><body><h1>在文字之间，留一点呼吸</h1><p>慢下来，让阅读成为日常。</p>'
                            + ''.join(f'<h2>第 {index + 1} 节 · 阅读的节奏</h2><p>一本好书，值得安静地读完。温暖的纸面、舒适的行距，让每一段文字都更清晰。</p>' for index in range(40))
                            + '</body></html>', 'text/html')
            return send({}, status=404)

        context.route('**/*', handler)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://recoll.test/reader')
        frame = page.frame_locator('#page-area iframe')
        frame.get_by_role('heading', name='在文字之间，留一点呼吸', exact=True).wait_for()
        page.wait_for_function('() => document.querySelectorAll("#reader-toc-list button").length > 1')

        # A 320px screen keeps navigation and controls in bounds and tappable.
        assert page.locator('#reader-toolbar').bounding_box()['height'] <= 100
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        for selector in ['#reader-toggle-toc', '#reader-toggle-settings', '#reader-toggle-toolbar',
                         '#reader-progress-prev-book', '#reader-prev-section',
                         '#reader-next-section', '#reader-progress-next-book']:
            bounds = page.locator(selector).bounding_box()
            assert bounds['width'] >= 44 and bounds['height'] >= 44, (selector, bounds)
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
        footer_order = page.locator('.reader-progress-footer button, .reader-progress-footer input').evaluate_all(
            'nodes => nodes.map(node => node.id)')
        assert footer_order == ['reader-progress-prev-book', 'reader-prev-section', 'reader-progress-range',
                                'reader-next-section', 'reader-progress-next-book']
        assert page.locator('#reader-progress-range').bounding_box()['width'] > 0
        for selector in ['#reader-progress-prev-book', '#reader-progress-next-book']:
            assert page.locator(selector).is_disabled()

        # Adjacent sections follow TOC entries within the same HTML document.
        current_heading = page.locator('#reader-toc-list [aria-current="location"]')
        previous_section, next_section = page.locator('#reader-prev-section'), page.locator('#reader-next-section')
        playwright_api.expect(previous_section).to_be_disabled()
        next_section.click()
        playwright_api.expect(current_heading).to_have_text('第 1 节 · 阅读的节奏')
        next_section.click()
        playwright_api.expect(current_heading).to_have_text('第 2 节 · 阅读的节奏')
        previous_section.click()
        playwright_api.expect(current_heading).to_have_text('第 1 节 · 阅读的节奏')

        # Scrolling changes the starting section for the next arrow click.
        page.locator('#page-area').evaluate('''stage => {
          const frame = stage.querySelector('iframe');
          const heading = frame.contentDocument.querySelectorAll('h2')[12];
          stage.scrollTop += frame.getBoundingClientRect().top + heading.getBoundingClientRect().top
            - stage.getBoundingClientRect().top - 40;
        }''')
        playwright_api.expect(current_heading).to_have_text('第 13 节 · 阅读的节奏')
        next_section.click()
        playwright_api.expect(current_heading).to_have_text('第 14 节 · 阅读的节奏')
        previous_section.click()
        playwright_api.expect(current_heading).to_have_text('第 13 节 · 阅读的节奏')
        page.locator('#page-area').evaluate('node => { node.scrollTop = 0; }')
        playwright_api.expect(previous_section).to_be_disabled()
        playwright_api.expect(page.locator('#reader-progress-label')).to_have_text('0%')

        screenshots = os.environ.get('RECOLL_UX_SCREENSHOT_DIR')
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(screenshots) / f'reader-{width}.png'))

        # All settings remain reachable, and Tab cannot fall behind the panel.
        page.locator('#reader-toggle-settings').click()
        page.locator('#reader-close-settings').wait_for(state='visible')
        page.wait_for_function('document.querySelector("#reader-toolbar").inert')
        page.locator('#reader-close-settings').focus()
        page.keyboard.press('Shift+Tab')
        assert page.evaluate('document.activeElement.matches("#reader-settings-panel [data-reader-help]")')
        page.keyboard.press('Enter')
        page.locator('#reader-help-dialog[open]').wait_for()
        position = page.locator('#page-area').evaluate('node => node.scrollTop')
        page.keyboard.press('PageDown')
        assert page.locator('#page-area').evaluate('node => node.scrollTop') == position
        page.keyboard.press('Escape')
        page.locator('#reader-help-dialog').wait_for(state='hidden')
        assert page.evaluate('document.activeElement.matches("#reader-settings-panel [data-reader-help]")')
        assert page.locator('#reader-settings-panel').is_visible()
        page.keyboard.press('Escape')
        page.wait_for_function('document.activeElement.id === "reader-toggle-settings"')

        # Fullscreen controls use the browser API and track both transitions.
        if mobile:
            page.locator('#reader-toggle-settings').click()
            fullscreen = page.locator('#reader-settings-panel [data-reader-fullscreen]')
        else:
            fullscreen = page.locator('#reader-fullscreen')
        fullscreen.click()
        page.wait_for_function('document.fullscreenElement?.id === "reader-app"')
        assert fullscreen.get_attribute('aria-pressed') == 'true'
        fullscreen.click()
        page.wait_for_function('!document.fullscreenElement')
        assert fullscreen.get_attribute('aria-pressed') == 'false'
        if mobile:
            page.keyboard.press('Escape')

        # Shortcuts also work from a same-origin, script-disabled book iframe.
        frame.get_by_role('heading', name='在文字之间，留一点呼吸', exact=True).click()
        page.keyboard.press('?')
        page.locator('#reader-help-dialog[open]').wait_for()
        page.keyboard.press('Escape')
        page.wait_for_function('document.activeElement?.tagName === "IFRAME"')
        frame.locator('body').press('h')
        page.wait_for_function('document.querySelector("#reader-app").classList.contains("reader-toolbar-hidden")')
        frame.locator('body').press('h')
        page.wait_for_function('!document.querySelector("#reader-app").classList.contains("reader-toolbar-hidden")')
        frame.locator('body').press('/')
        page.wait_for_function('document.activeElement.id === "reader-search-input"')
        page.locator('#reader-search-input').fill('fht/?')
        page.locator('#reader-search-input').press('h')
        assert page.locator('#reader-search-input').input_value() == 'fht/?h'
        assert not page.locator('#reader-help-dialog').is_visible()
        assert not page.evaluate('!!document.fullscreenElement')
        assert not page.locator('#reader-app').evaluate('node => node.classList.contains("reader-toolbar-hidden")')
        if mobile:
            page.keyboard.press('Escape')
            page.wait_for_function('document.activeElement.id === "reader-toggle-toc"')
            assert page.locator('#reader-sidebar').evaluate('node => node.inert')
            assert not page.locator('.reader-main').evaluate('node => node.inert')

        # Editable nodes inside book frames must keep ordinary keys and Ctrl+F.
        frame.locator('body').evaluate('node => {const input=node.ownerDocument.createElement("input");input.id="book-input";node.prepend(input)}')
        book_input = frame.locator('#book-input')
        book_input.fill('')
        book_input.press('?')
        book_input.press('f')
        book_input.press('h')
        book_input.press('/')
        assert book_input.input_value() == '?fh/'
        assert not page.locator('#reader-help-dialog').is_visible()
        assert not page.evaluate('!!document.fullscreenElement')
        assert book_input.evaluate('node => node.dispatchEvent(new KeyboardEvent("keydown", {key:"f",ctrlKey:true,bubbles:true,cancelable:true}))')

        # Browser denial leaves an understandable message and usable controls.
        page.evaluate('() => { document.querySelector("#reader-app").requestFullscreen = () => Promise.reject(new Error("denied")); }')
        if mobile:
            page.locator('#reader-toggle-settings').click()
        fullscreen.click()
        page.get_by_text('未能进入全屏。你仍可使用「专注」收起工具栏，或使用浏览器的全屏功能。', exact=True).wait_for()
        assert page.locator('#reader-settings-panel').is_visible()
        assert fullscreen.get_attribute('aria-pressed') == 'false'
        assert not errors, errors
        context.close()
        browser.close()
