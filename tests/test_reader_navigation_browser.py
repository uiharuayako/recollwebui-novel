"""Real DOM checks for heading navigation and matches spanning inline elements."""
from pathlib import Path
import os
import shutil

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]


def test_navigation_matches_inline_text_and_restores_temporary_location():
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        pytest.skip('Set RECOLL_TEST_CHROMIUM to run browser checks')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page()

        def route(request):
            if request.request.url.endswith('/navigation.js'):
                request.fulfill(content_type='text/javascript', body=(ROOT / 'static/reader/navigation.js').read_text())
            else:
                request.fulfill(content_type='text/html', body='''<!doctype html><html><body>
                  <button id="reader-tab-toc"></button><button id="reader-tab-books"></button><button id="reader-tab-search"></button>
                  <section id="reader-panel-toc"><div id="reader-toc-list"></div></section>
                  <section id="reader-panel-books"></section><section id="reader-panel-search">
                  <input id="reader-search-input"><button id="reader-search-submit"></button>
                  <div id="reader-search-status"></div><div id="reader-search-results"></div></section>
                  <div id="reader-return-banner" hidden><button id="reader-return-position"></button><button id="reader-keep-position"></button></div>
                  <div id="stage" style="height:200px;overflow:auto"><iframe id="content" style="height:1600px;width:600px;border:0"></iframe></div>
                  </body></html>''')

        page.route('http://recoll.test/**', route)
        page.goto('http://recoll.test/')
        result = page.evaluate('''async () => {
          const {ReaderNavigation, scrollToTextMatch} = await import('/navigation.js');
          const frame = document.getElementById('content'), stage = document.getElementById('stage');
          const doc = frame.contentDocument;
          doc.open(); doc.write('<html><body><h1>First heading</h1><p style="margin-top:500px">before <em>exact</em> phrase after</p><h2 style="margin-top:500px">Second heading</h2></body></html>'); doc.close();
          let temporary = false;
          const nav = new ReaderNavigation({getContext: () => ({format:'html',stage,documents:[{document:doc,index:0,frame}]}),
            onNavigate:(target,options) => {temporary=options.temporary;scrollToTextMatch(doc,target,stage,frame);},
            onReturn:() => {stage.scrollTop=0;}});
          await nav.refresh();
          const tocTab=document.getElementById('reader-tab-toc');tocTab.focus();
          tocTab.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
          const keyboardTab=document.activeElement.id;
          const tabStops=[...document.querySelectorAll('[id^="reader-tab-"]')].filter(node=>node.tabIndex===0).map(node=>node.id);
          const headings = [...document.querySelectorAll('#reader-toc-list button')].map(node=>node.textContent);
          const matches = await nav.search('exact phrase');
          await nav.navigate(matches[0], {temporary:true});
          const top = stage.scrollTop, canReturn = nav.canReturn;
          const highlighted = doc.defaultView.CSS?.highlights?.has('reader-search') || !!doc.querySelector('mark[data-reader-search]');
          await nav.returnToReading();
          const restored=stage.scrollTop,wasTemporary=temporary;
          await nav.navigate(nav.outline[1]);nav.setActive(0);
          const activeHeading=document.querySelector('#reader-toc-list [aria-current=location]')?.textContent;
          return {activeHeading,keyboardTab,tabStops,headings,count:matches.length,top,temporary:wasTemporary,canReturn,highlighted,restored,returned:!nav.canReturn,
            bannerHidden:document.getElementById('reader-return-banner').hidden};
        }''')
        assert result['headings'] == ['First heading', 'Second heading']
        assert result['keyboardTab'] == 'reader-tab-books'
        assert result['tabStops'] == ['reader-tab-books']
        assert result['activeHeading'] == 'Second heading'
        assert result['count'] == 1
        assert result['top'] > 400
        assert result['temporary'] and result['canReturn'] and result['highlighted']
        assert result['restored'] == 0 and result['returned'] and result['bannerHidden']
        browser.close()
