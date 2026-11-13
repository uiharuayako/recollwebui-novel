"""Optional real Chromium regression for converted HTML rendering and isolation."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import shutil
import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {"progress_epoch": 3, "reading_epoch": 4, "progress_sync_enabled": True,
            "preferences": {"font_size": 24}}
PARENT_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
              "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
              "font-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' blob:; "
              "worker-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; "
              "base-uri 'self'; form-action 'self'")
TEMPLATE = (ROOT / "views/reader.tpl").read_text()
TEMPLATE = TEMPLATE[TEMPLATE.index('<div id="reader-app"'):TEMPLATE.index('<script>')]


@pytest.mark.parametrize("mobile,empty,legacy", [(False, False, False), (True, False, False), (True, True, False), (True, False, True)])
def test_html_content_progress_and_resource_isolation(mobile, empty, legacy):
    chromium = os.environ.get("RECOLL_TEST_CHROMIUM") or shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        pytest.skip("Set RECOLL_TEST_CHROMIUM to run the real HTML browser checks")
    saved = {"position": None, "revision": 7, "content_version": "v1"}
    if legacy:
        saved["position"] = {"chapterDocIndex": "0", "chapterHref": "title0", "chapterTitle": "HTML content",
                             "text": "Paragraph 038 content words", "count": "39", "page": "", "percentage": "0.25",
                             "xpath": "/body/DocFragment[1]/body/p[39]"}
    calls, external, errors = [], [], []
    prefix = "/library"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1200, "height": 1000},
                                      is_mobile=mobile, has_touch=mobile)

        def handler(route):
            request = route.request
            parsed = urlsplit(request.url)
            if parsed.hostname != "recoll.test":
                external.append(request.url)
                return route.fulfill(status=200, body="blocked test resource")
            path = parsed.path.removeprefix(prefix)

            def send(value, mime="application/json", status=200):
                route.fulfill(status=status, content_type=mime,
                              body=value if isinstance(value, (str, bytes)) else json.dumps(value),
                              headers={"Content-Security-Policy": PARENT_CSP} if path == "/reader" else {})

            if request.method == "POST":
                data = request.post_data_json
                calls.append((path, data))
                if path.endswith("/open"):
                    # A receipt must follow visible content, not an empty iframe.
                    assert not empty
                    return send({"progress": saved, "settings": SETTINGS})
                if "/progress/" in path:
                    assert data["revision"] == saved["revision"]
                    saved.update(position=data["position"], revision=saved["revision"] + 1)
                    return send(saved)
                return send({})
            if path.startswith("/static/"):
                source = ROOT / path.lstrip("/")
                if not source.is_file():
                    return send({}, status=404)
                return send(source.read_bytes(), "text/javascript" if source.suffix == ".js" else "text/css")
            if path == "/reader":
                config = {"mode": "book", "apiBase": prefix + "/api/reader", "account": {
                    "userId": "a", "sessionId": "s", "csrf": "c", "settings": SETTINGS, "baseUrl": prefix}}
                return send('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                            '<link rel="stylesheet" href="' + prefix + '/static/style.css"></head><body>' + TEMPLATE +
                            '<script>window.RECOLL_READER=' + json.dumps(config) + '</script>'
                            '<script type="module" src="' + prefix + '/static/reader/app.js"></script></body></html>', "text/html")
            if path == "/api/reader/book":
                return send({"book": {"documentId": "html-doc", "contentVersion": "v1", "openHandle": "handle", "format": "html",
                                      "path": "/test.html", "name": "HTML fixture", "url": prefix + "/content/html"}})
            if path == "/api/reader/progress/html-doc":
                return send({**saved, "settings": SETTINGS})
            if path == "/content/html":
                paragraphs = ''.join(
                    (f'<h2 id="section-{i}">Section {i:03d}</h2>' if i and i % 30 == 0 else '')
                    + '<p>Paragraph %03d content words</p>' % i for i in range(150))
                content = "" if empty else ('<h1>HTML content</h1>' + paragraphs +
                    f'<img id="reader-fixture-image" alt="A quiet mountain" src="{prefix}/content/illustration.svg" width="240" height="120">'
                    '<p><a id="reader-fixture-noteref" href="#reader-fixture-note" role="doc-noteref">Read the footnote</a></p>'
                    '<aside id="reader-fixture-note" role="doc-footnote"><p>Footnote <strong>safe emphasis</strong>.</p>'
                    '<a href="javascript:parent.fetch(\'/attack\')" onclick="parent.fetch(\'/attack\')">Untrusted link</a>'
                    '<img src="https://tracker.invalid/note" onerror="parent.fetch(\'/attack\')">'
                    '<iframe src="https://tracker.invalid/note-frame"></iframe>'
                    '<script>parent.fetch("/attack")</script></aside>' +
                    '<img src="https://tracker.invalid/image"><iframe src="https://tracker.invalid/frame"></iframe>'
                    '<object data="https://tracker.invalid/object"></object><style>body{background-image:url(https://tracker.invalid/css)}</style>'
                    '<script>parent.fetch("/attack")</script>')
                return send('<!doctype html><html><body>' + content + '</body></html>', "text/html")
            if path == "/content/illustration.svg":
                return send('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120" viewBox="0 0 240 120">'
                            '<rect width="240" height="120" fill="#dce8dc"/><path d="M0 120L80 20l70 100z" fill="#48735b"/></svg>',
                            "image/svg+xml")
            if path == "/api/account":
                return send({"user": {"id": "a"}, "sessionId": "s", "settings": SETTINGS})
            if path == "/attack":
                external.append(request.url)
            return send({}, status=404)

        context.route("**/*", handler)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://recoll.test" + prefix + "/reader")
        if empty:
            page.get_by_text("正文尚未成功加载，原阅读进度已保留", exact=True).wait_for(timeout=30000)
            assert not any(path.endswith("/open") or "/progress/" in path for path, _ in calls)
        else:
            page.frame_locator("#page-area iframe").get_by_text("HTML content", exact=True).wait_for(timeout=30000)
            page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
            assert len([path for path, _ in calls if path.endswith("/open")]) == 1
            assert page.frame_locator("#page-area iframe").get_by_text("Paragraph 100 content words", exact=True).count() == 1
            # Multiple heading levels must remain in the same scrolling document.
            # Kookit's former generic path rendered only the first heading section.
            assert page.frame_locator("#page-area iframe").get_by_text("Paragraph 149 content words", exact=True).count() == 1
            page.wait_for_function("document.querySelectorAll('#reader-toc-list button').length === 5")
            assert page.locator("#reader-toc-list").get_by_role("button", name="Section 120", exact=True).count() == 1
            assert page.locator("#page-area iframe").get_attribute("sandbox") == "allow-same-origin"
            assert page.locator("#page-area iframe").evaluate("f=>!!f.contentDocument.head.querySelector('meta[data-recoll-reader-csp]')")
            if legacy:
                page.wait_for_function("() => document.querySelector('#page-area').scrollTop > 1500")
            height = page.locator("#page-area iframe").evaluate("f=>f.clientHeight")
            page.wait_for_timeout(250)
            assert abs(page.locator("#page-area iframe").evaluate("f=>f.clientHeight") - height) < 2
            page.evaluate("const stage=document.querySelector('#page-area');stage.scrollTop=2200;stage.dispatchEvent(new Event('scroll'))")
            page.wait_for_function("() => window.rendition.getPosition().text?.startsWith('Paragraph')")
            page.wait_for_timeout(150)
            before = page.evaluate("window.rendition.getPosition()")
            assert before["text"].startswith("Paragraph")
            page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
            page.wait_for_timeout(250)
            assert saved["position"]["text"] == before["text"]
            assert saved["position"]["chapterDocIndex"] == "0"
            page.close()
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://recoll.test" + prefix + "/reader")
            page.frame_locator("#page-area iframe").get_by_text("HTML content", exact=True).wait_for(timeout=30000)
            page.wait_for_function("() => document.querySelector('#page-area').scrollTop > 1500")
            page.wait_for_timeout(200)
            restored = page.evaluate("window.rendition.getPosition()")
            assert restored["text"] == before["text"], (before, restored)
            assert abs(restored["scrollTop"] - before["scrollTop"]) < 2, (before, restored)

            # Search reaches text after several h2 sections and its temporary jump
            # must not replace the established reading position.
            if mobile:
                page.locator("#reader-toggle-toc").click()
            page.locator("#reader-tab-search").click()
            page.locator("#reader-search-input").fill("Paragraph 120 content words")
            page.locator("#reader-search-submit").click()
            page.locator("#reader-search-results button").wait_for()
            assert page.locator("#reader-search-results button").count() == 1
            page.locator("#reader-search-results button").click()
            page.locator("#reader-return-position").wait_for(state="visible")
            page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
            page.wait_for_timeout(200)
            assert saved["position"]["text"] == before["text"]
            page.locator("#reader-return-position").click()
            page.locator("#reader-return-position").wait_for(state="hidden")
            page.wait_for_function("() => Math.abs(document.querySelector('#page-area').scrollTop - "
                                   + str(restored["scrollTop"]) + ") < 2")

            # Book images and note markup cross into the main application's UI.
            # Keep the image preview useful and the note preview strictly inert.
            if mobile and page.locator("body").evaluate("e => e.classList.contains('reader-sidebar-open')"):
                page.locator("#reader-sidebar-backdrop").click(position={"x": 350, "y": 100})
            frame = page.frame_locator("#page-area iframe")
            frame.locator("#reader-fixture-image").click()
            page.locator("#reader-image-dialog[open]").wait_for()
            assert page.locator("#reader-image-preview").get_attribute("alt") == "A quiet mountain"
            page.wait_for_function("""() => {
                const image = document.querySelector('#reader-image-preview');
                return image.complete && image.naturalWidth > 0;
            }""")
            assert page.locator("#reader-image-preview").evaluate("image => image.complete && image.naturalWidth > 0")
            page.keyboard.press("Escape")
            assert page.locator("#reader-image-dialog[open]").count() == 0
            frame.locator("#reader-fixture-noteref").click()
            page.locator("#reader-footnote-dialog[open]").wait_for()
            assert page.locator("#reader-footnote-content strong").inner_text() == "safe emphasis"
            assert page.locator("#reader-footnote-content").get_by_text("Untrusted link", exact=False).count() == 1
            assert page.locator("#reader-footnote-content script, #reader-footnote-content iframe, "
                                "#reader-footnote-content img, #reader-footnote-content a, "
                                "#reader-footnote-content [onclick], #reader-footnote-content [onerror]").count() == 0
            page.locator("#reader-close-footnote").click()
            assert page.locator("#reader-footnote-dialog[open]").count() == 0
            assert not errors, errors
        page.wait_for_timeout(100)
        assert not external, external
        context.close()
        browser.close()
