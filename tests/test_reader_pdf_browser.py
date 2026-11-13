"""Optional real-browser tests: install playwright and provide a Chromium executable."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import shutil
import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

root = Path(__file__).resolve().parents[1]
settings = {'progress_epoch':3,'reading_epoch':4,'progress_sync_enabled':True,'preferences':{'font_size':24}}
tpl = (root/'views/reader.tpl').read_text()
tpl = tpl[tpl.index('<div id="reader-app"'):tpl.index('<script>')]

PARENT_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' blob:; worker-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'"

def fixture_pdf():
    contents = [b'0.9 0.1 0.1 rg 20 900 360 80 re f BT /F1 28 Tf 30 840 Td (FIRST PDF PAGE) Tj ET', b'0.1 0.1 0.9 rg 20 900 360 80 re f BT /F1 28 Tf 30 840 Td (SECOND PDF PAGE) Tj ET']
    objects = [b'<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R >>', b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 1400] /Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 1400] /Resources << /Font << /F1 7 0 R >> >> /Contents 8 0 R >>', b'<< /Length '+str(len(contents[0])).encode()+b' >>\nstream\n'+contents[0]+b'\nendstream', rb'<< /S /JavaScript /JS (fetch\("/attack"\)) >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length '+str(len(contents[1])).encode()+b' >>\nstream\n'+contents[1]+b'\nendstream']
    out=b'%PDF-1.7\n'; offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(out));out+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(out);out+=b'xref\n0 9\n0000000000 65535 f \n'
    for offset in offsets[1:]:out+=f'{offset:010} 00000 n \n'.encode()
    return out+f'trailer\n<< /Size 9 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()

pdf=fixture_pdf()
@pytest.mark.parametrize("mobile", [False, True])
@pytest.mark.parametrize("mount_prefix", ["", "/library"])
def test_pdf_canvas_navigation_progress_and_session_cleanup(mobile, mount_prefix):
    chromium = os.environ.get("RECOLL_TEST_CHROMIUM") or shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        pytest.skip("Set RECOLL_TEST_CHROMIUM to run the real PDF browser checks")
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=chromium,args=['--no-sandbox'])
        saved={'position':{'version':1,'pageNumber':2,'pageOffsetRatio':0.15},'revision':7,'content_version':'v1'}
        calls=[];errors=[];attacks=[];requests=[]
        context=browser.new_context(viewport={'width':390,'height':844} if mobile else {'width':1200,'height':1000},is_mobile=mobile,has_touch=mobile)
        def handler(route):
            path=urlsplit(route.request.url).path
            if mount_prefix and path.startswith(mount_prefix + '/'):
                path = path[len(mount_prefix):]
            requests.append(path)
            def send(value,mime='application/json',status=200):route.fulfill(status=status,content_type=mime,body=value if isinstance(value,(str,bytes)) else json.dumps(value),headers={'Content-Security-Policy':PARENT_CSP} if path=='/reader' else {})
            if route.request.method=='POST':
                data=route.request.post_data_json;calls.append((path,data))
                if path.endswith('/open'):return send({'progress':saved,'settings':settings})
                if '/progress/' in path:
                    assert data['revision']==saved['revision'],(data,saved)
                    saved.update({'position':data['position'],'revision':saved['revision']+1})
                    return send(saved)
                if path.endswith('/add'):return send({'onShelf':True})
                return send({'settings':settings})
            if path.startswith('/static/'):
                f=root/path.lstrip('/')
                if not f.is_file():return send({},status=404)
                return send(f.read_bytes(),'text/javascript' if f.suffix in ('.js','.mjs') else 'text/css' if f.suffix=='.css' else 'application/wasm' if f.suffix=='.wasm' else 'application/octet-stream')
            if path=='/reader':
                config={'mode':'book','queryString':'fmt=pdf','apiBase':mount_prefix+'/api/reader','account':{'userId':'a','sessionId':'s','csrf':'c','settings':settings,'baseUrl':mount_prefix}}
                markup='<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta charset="utf-8"><link rel="stylesheet" href="/static/style.css"></head><body>'+tpl+'<script>window.RECOLL_READER='+json.dumps(config)+'</script><script type="module" src="/static/reader/app.js"></script></body></html>'
                return send(markup.replace('href="/static/', 'href="'+mount_prefix+'/static/').replace('src="/static/', 'src="'+mount_prefix+'/static/'),'text/html')
            if path=='/api/reader/book':return send({'book':{'documentId':'pdf-document','contentVersion':'v1','openHandle':'signed-pdf','path':'/test.pdf','name':'Two page PDF','title':'PDF','format':'pdf','url':mount_prefix+'/content/pdf'}})
            if path=='/api/reader/progress/pdf-document':return send({**saved,'settings':settings})
            if path=='/content/pdf':return send(pdf,'application/pdf')
            if path=='/api/account':return send({'user':{'id':'a'},'sessionId':'s','settings':settings})
            if path=='/attack':attacks.append(path);return send({})
            return send({},status=404)
        context.route('**/*',handler)
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://recoll.test'+mount_prefix+'/reader')
        try:page.locator('#page-area canvas').first.wait_for(timeout=30000)
        except Exception:
            print('DEBUG',page.locator('body').inner_text(),errors,requests[-20:],flush=True);raise
        page.wait_for_function('() => window.rendition?.getPosition?.().pageNumber === 2')
        page.wait_for_function("() => [...document.querySelectorAll('[role=status]')].some(e=>/已就绪|尚未同步|已同步/.test(e.textContent))")
        position=page.evaluate('window.rendition.getPosition()')
        assert abs(position['pageOffsetRatio']-0.15)<0.003,(mobile,position)
        assert len([c for c in calls if c[0].endswith('/open')])==1,(mobile,calls)
        second=page.locator('#page-area [data-page-number="2"] canvas').evaluate('(canvas)=>canvas.toDataURL()')
        page.evaluate("document.querySelector('#page-area').scrollTop = 0")
        page.wait_for_function('() => window.rendition?.getPosition?.().pageNumber === 1')
        first=page.locator('#page-area [data-page-number="1"] canvas').evaluate('(canvas)=>canvas.toDataURL()')
        assert first!=second
        assert page.locator('#page-area .textLayer').count() >= 1
        page.evaluate("const stage=document.querySelector('#page-area'); const second=stage.querySelector('[data-page-number=\"2\"]'); stage.scrollTop=second.getBoundingClientRect().top-stage.getBoundingClientRect().top+stage.scrollTop")
        page.wait_for_function('() => window.rendition?.getPosition?.().pageNumber === 2')
        page.evaluate("const stage=document.querySelector('#page-area');const second=stage.querySelector('[data-page-number=\"2\"]');stage.scrollTop=second.getBoundingClientRect().top-stage.getBoundingClientRect().top+stage.scrollTop+second.clientHeight*0.25;stage.dispatchEvent(new Event('scroll'));window.dispatchEvent(new Event('beforeunload'))")
        page.wait_for_timeout(400)
        assert saved['position']['pageNumber']==2 and abs(saved['position']['pageOffsetRatio']-0.25)<0.003,(mobile,saved,calls)
        page.close()
        reopened=context.new_page();reopened.on('pageerror',lambda error:errors.append(str(error)))
        reopened.goto('http://recoll.test'+mount_prefix+'/reader');reopened.locator('#page-area canvas').first.wait_for(timeout=30000)
        reopened.wait_for_function('() => window.rendition?.getPosition?.().pageNumber === 2')
        reopened.wait_for_timeout(100)
        restored=reopened.evaluate('window.rendition.getPosition()')
        assert abs(restored['pageOffsetRatio']-0.25)<0.003,(mobile,restored)
        assert not errors,errors
        assert not attacks,attacks
        assert any(path.endswith('pdf.worker.min.mjs') for path in requests),requests
        assert not any('sandbox' in path or 'renderer' in path for path in requests)
        reopened.evaluate("window.dispatchEvent(new CustomEvent('recoll-account-change',{detail:{type:'logout'}}))")
        assert reopened.locator('#page-area canvas').count()==0
        print('mobile' if mobile else 'desktop','PDF canvas renders two distinct pages; native continuous scroll; page/offset save and fresh-page restore; worker; no document JS; logout clears canvas OK',flush=True)
        context.close()
        browser.close()


def searchable_pdf(page_count=120):
    """Text, outline destinations and a real internal link in a long PDF."""
    objects = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'', b'', b'']
    page_ids = []
    for number in range(1, page_count + 1):
        page_id = len(objects) + 1
        page_ids.append(page_id)
        content = f'BT /F1 24 Tf 25 920 Td (Page {number} needle reading) Tj 0 -70 Td (Second line for selection) Tj ET'.encode()
        annotation = b' /Annots [6 0 R]' if number == 1 else b''
        objects.extend([
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 1000] /Resources << /Font << /F1 3 0 R >> >> /Contents ' + str(page_id + 1).encode() + b' 0 R' + annotation + b' >>',
            b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
        ])
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>'
    objects[1] = b'<< /Type /Pages /Count ' + str(page_count).encode() + b' /Kids [' + b' '.join(f'{n} 0 R'.encode() for n in page_ids) + b'] >>'
    objects[3] = b'<< /Type /Outlines /First 5 0 R /Last 5 0 R /Count 1 >>'
    objects[4] = f'<< /Title (Last chapter) /Parent 4 0 R /Dest [{page_ids[-1]} 0 R /Fit] >>'.encode()
    objects[5] = f'<< /Type /Annot /Subtype /Link /Rect [20 830 390 880] /Border [0 0 0] /Dest [{page_ids[-1]} 0 R /XYZ null 950 null] >>'.encode()
    out = b'%PDF-1.7\n'
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return out + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


@pytest.mark.parametrize('mobile', [False, True])
def test_pdf_long_continuous_text_search_outline_zoom_and_canvas_recycling(mobile):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run the PDF viewer checks')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width':390,'height':700} if mobile else {'width':1000,'height':800}, is_mobile=mobile, has_touch=mobile)
        errors = []
        data = searchable_pdf()
        document_requests = []
        def handler(route):
            path = urlsplit(route.request.url).path
            if path.startswith('/static/'):
                file = root / path.lstrip('/')
                if not file.is_file():
                    return route.fulfill(status=404, body='missing')
                mime = 'text/javascript' if file.suffix in ('.js', '.mjs') else 'text/css' if file.suffix == '.css' else 'image/svg+xml' if file.suffix == '.svg' else 'application/octet-stream'
                return route.fulfill(body=file.read_bytes(), content_type=mime)
            if path == '/document.pdf':
                document_requests.append(route.request.headers.get('range'))
                headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(len(data))}
                byte_range = route.request.headers.get('range')
                if byte_range:
                    start, end = byte_range.removeprefix('bytes=').split('-')
                    start, end = int(start), min(int(end) if end else len(data) - 1, len(data) - 1)
                    headers.update({'Content-Range': f'bytes {start}-{end}/{len(data)}', 'Content-Length': str(end - start + 1)})
                    return route.fulfill(status=206, body=data[start:end + 1], content_type='application/pdf', headers=headers)
                return route.fulfill(body=data, content_type='application/pdf', headers=headers)
            return route.fulfill(content_type='text/html', headers={'Content-Security-Policy': PARENT_CSP}, body='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}.reader-stage-shell{height:100vh;width:100%;position:relative}</style><div id="reader-app" data-theme="light"><div class="reader-stage-shell"><div id="stage" class="reader-stage"></div></div></div><script type="module">
              import { createPdfRendition } from '/static/reader/pdf-adapter.js';
              const source = location.search ? '/document.pdf' : await (await fetch('/document.pdf')).arrayBuffer();
              window.rendition = await createPdfRendition(source);
              await rendition.renderTo(document.querySelector('#stage'));
              window.ready = true;
            </script>''')
        context.route('**/*', handler)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://recoll.test/pdf-test' + ('?url=1' if mobile else ''))
        page.wait_for_function('() => window.ready === true', timeout=30000)
        assert document_requests
        assert page.locator('.pdfViewer > .page').count() == 120
        assert page.evaluate('rendition.readerMode') == 'scroll'
        # A native scroll crosses a physical page boundary without calling next().
        page.evaluate("const stage=document.querySelector('#stage');const second=stage.querySelector('[data-page-number=\"2\"]');stage.scrollTop=second.getBoundingClientRect().top+stage.scrollTop+20")
        page.wait_for_function('() => rendition.getPosition().pageNumber === 2')
        before = page.evaluate('rendition.getPosition()')
        hits = page.evaluate("rendition.search('needle')")
        assert len(hits) == 120
        after = page.evaluate('rendition.getPosition()')
        assert after['pageNumber'] == before['pageNumber']
        assert abs(after['pageOffsetRatio'] - before['pageOffsetRatio']) < .005
        assert hits[109]['pageNumber'] == 110 and 'needle' in hits[109]['excerpt']
        page.evaluate('(hit) => rendition.goToSearchResult(hit)', hits[109])
        assert page.locator('[data-page-number="110"] .highlight.selected').inner_text() == 'needle'
        assert 109 <= page.evaluate('rendition.getPosition().pageNumber') <= 110
        # The official text layer produces selectable real text, not just pixels.
        selected = page.evaluate('''() => { const node=document.querySelector('[data-page-number="110"] .textLayer span');const range=document.createRange();range.selectNodeContents(node);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);return selection.toString(); }''')
        assert 'Page 110' in selected
        assert page.evaluate("rendition.search('not-present-anywhere')") == []
        assert page.evaluate("rendition.search('')") == []
        assert len(page.evaluate("rendition.search('needle')")) == 120
        outline = page.evaluate('rendition.getOutline()')
        assert outline[0]['label'] == 'Last chapter' and outline[0]['pageNumber'] == 120
        page.evaluate('(dest) => rendition.goToDestination(dest)', outline[0]['dest'])
        page.wait_for_function('() => rendition.getPosition().pageNumber === 120')
        page.evaluate('rendition.goToPosition({pageNumber: 50, pageOffsetRatio: .3})')
        saved = page.evaluate('rendition.getPosition()')
        page.evaluate('rendition.setScale(1.5)')
        zoomed = page.evaluate('rendition.getPosition()')
        assert zoomed['pageNumber'] == 50 and abs(zoomed['pageOffsetRatio'] - saved['pageOffsetRatio']) < .005
        assert abs(page.evaluate('(async()=> (await rendition.getProgress()).scale)()') - 1.5) < .001
        page.evaluate('rendition.rotate(90)')
        assert page.evaluate('(async()=> (await rendition.getProgress()).rotation)()') == 90
        page.evaluate('rendition.setScale("page-width")')
        page.evaluate('rendition.rotate(-90)')
        page.evaluate('rendition.goToPosition({pageNumber: 50, pageOffsetRatio: .3})')
        page.set_viewport_size({'width':430,'height':720} if mobile else {'width':850,'height':740})
        page.evaluate('rendition.resize()')
        resized = page.evaluate('rendition.getPosition()')
        assert resized['pageNumber'] == 50 and abs(resized['pageOffsetRatio'] - .3) < .01
        # Visit enough far-apart pages to overflow the viewer's canvas cache.
        for number in range(1, 120, 9):
            page.evaluate('(pageNumber) => rendition.goToPosition({pageNumber})', number)
        assert page.locator('.pdfViewer canvas').count() <= 12
        assert page.locator('[data-page-number="1"] canvas').count() == 0
        page.evaluate('rendition.showPage(1)')
        link = page.locator('[data-page-number="1"] .annotationLayer a')
        link.wait_for()
        link.click()
        page.wait_for_function('() => rendition.getPosition().pageNumber === 120')
        page.evaluate('rendition.removeContent()')
        assert page.locator('canvas').count() == 0
        assert not errors, errors
        context.close()
        browser.close()
