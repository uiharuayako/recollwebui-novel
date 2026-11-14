"""Recommendation actions and layout through the real mounted application."""
import os
from pathlib import Path
import shutil

import pytest

from test_webui_integration import (
    recoll_index, site, client_for, book_for, post_json, progress_body,
)
from test_app_experience_browser import route_site, PREFIX

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width', [1280, 390, 320])
def test_recommendation_controls_and_refresh(site, width):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    client = client_for(site, prefix=PREFIX)
    book = book_for(client)
    post_json(client, '/api/reader/open', {'handle': book['openHandle']})
    post_json(client, '/api/reader/progress/' + book['documentId'], progress_body(client, book))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': width, 'height': 900}, is_mobile=width < 700, has_touch=width < 700)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        route_site(page, client)
        page.goto('http://recoll.test' + PREFIX + '/')
        assert page.locator('#daily-recommendations .app-recommendation-card').count() > 0
        page.locator('#library-query').fill('sharedgalaxy')
        page.get_by_role('button', name='搜索文库', exact=True).click()
        page.wait_for_url('**/results?**')
        toggle = page.locator('.app-recommendation-toggle')
        assert toggle.get_attribute('aria-pressed') == 'true'
        toggle.click()
        page.wait_for_function("document.querySelector('.app-recommendation-toggle').getAttribute('aria-pressed') === 'false'")
        assert site.store.list_recommendations(site.alice['id']) == []
        page.reload()
        assert toggle.get_attribute('aria-pressed') == 'false'
        toggle.click()
        page.wait_for_function("document.querySelector('.app-recommendation-toggle').getAttribute('aria-pressed') === 'true'")
        page.goto('http://recoll.test' + PREFIX + '/')
        assert page.locator('.app-reading-progress progress').get_attribute('value') == '37.5'
        assert page.locator('#search-recommendations .app-recommendation-card').count() == 1
        daily_ids = page.locator('#daily-recommendations .app-recommendation-card').evaluate_all('nodes => nodes.map(node => node.dataset.documentId)')
        for _ in range(2):
            with page.expect_request(lambda request: request.is_navigation_request() and '/library/?' in request.url):
                page.get_by_role('button', name='换一批搜索推荐').click()
            page.wait_for_load_state()
        assert page.locator('#daily-recommendations .app-recommendation-card').evaluate_all('nodes => nodes.map(node => node.dataset.documentId)') == daily_ids
        unsaved = page.locator('#daily-recommendations form').filter(has=page.locator('button[aria-pressed="false"]')).first
        document_id = unsaved.locator('[name="document_id"]').input_value()
        unsaved.get_by_role('button').click()
        page.wait_for_url('**/#daily-recommendations')
        card = page.locator(f'#search-recommendations .app-recommendation-card[data-document-id="{document_id}"]')
        assert card.count() == 1
        card.get_by_role('button').click()
        page.wait_for_url('**/#search-recommendations')
        assert page.locator(f'#search-recommendations .app-recommendation-card[data-document-id="{document_id}"]').count() == 0
        assert page.locator('.app-section-count').text_content() == '1'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        previews = ROOT / '.tmp/ux-previews'
        previews.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(previews / f'recommendations-{width}.png'), full_page=True)
        assert not errors, errors
        browser.close()


def test_recommendation_forms_without_javascript(site):
    chromium = os.environ.get('RECOLL_TEST_CHROMIUM') or shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    client = client_for(site, prefix=PREFIX)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        page = browser.new_page(java_script_enabled=False, viewport={'width': 390, 'height': 844})
        route_site(page, client)
        page.goto('http://recoll.test' + PREFIX + '/')
        page.locator('#library-query').fill('sharedgalaxy')
        page.get_by_role('button', name='搜索文库', exact=True).click()
        page.wait_for_url('**/results?**')
        page.get_by_role('button', name='移出搜索推荐', exact=True).click()
        page.wait_for_url('**/results?**')
        page.get_by_role('button', name='加入搜索推荐', exact=True).wait_for(state='visible')
        assert site.store.list_recommendations(site.alice['id']) == []
        page.get_by_role('button', name='加入搜索推荐', exact=True).click()
        page.wait_for_url('**/results?**')
        assert len(site.store.list_recommendations(site.alice['id'])) == 1
        browser.close()
