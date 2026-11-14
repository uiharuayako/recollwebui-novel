"""Search discovery uses real Recoll results and account-owned saved candidates."""
from urllib.parse import parse_qs, urlsplit

import pytest

from test_webui_integration import (
    recoll_index, site, book_for, changed_query, client_for, history_rows,
    identity, local_url, post_form, post_json, progress_body, search_for,
)


def candidates(site, user=None):
    return site.store.list_recommendations((user or site.alice)['id'])


def card_ids(response, section):
    return [card['data-document-id'] for card in response.html.select(
        f'#{section} .app-recommendation-card')]


def form_values(form):
    return {field['name']: field.get('value', '') for field in form.select('input[name]')}


def test_active_search_saves_once_and_later_search_rolls_candidates(site):
    client = client_for(site)
    target = search_for(client)
    assert candidates(site) == []
    first = client.get(target)
    saved = candidates(site)
    assert len(saved) == 1  # This site's configured result page contains one book.
    saved_id = saved[0]['document_id']
    assert saved[0]['source_query'] == 'sharedgalaxy'
    assert first.html.select_one('form[action="/recommendations/remove"]')
    completed = history_rows(site)[0]['completed_at']

    post_json(client, '/api/recommendations/remove', {'document_id': saved_id})
    client.get(target)
    client.get(changed_query(target, page=2))
    client.get(changed_query(target, page=0))
    assert candidates(site) == []
    assert history_rows(site)[0]['completed_at'] == completed

    client.get(search_for(client, 'betatoken'))
    saved = candidates(site)
    assert len(saved) == 1 and saved[0]['source_query'] == 'betatoken'
    assert saved[0]['document_id'] != saved_id
    assert candidates(site, site.bob) == []


def test_legacy_get_and_disabled_history_do_not_collect_but_manual_save_works(site):
    client = client_for(site)
    response = client.get('/results', {'query': 'alphatoken'})
    assert candidates(site) == []
    site.store.save_settings(site.alice['id'], flags={'search_history_enabled': False})
    response = client.get(search_for(client, 'betatoken'))
    assert candidates(site) == []
    form = response.html.select_one('form[action="/recommendations/add"]')
    result = post_json(client, '/api/recommendations/add', form_values(form)).json
    assert result['saved'] is True
    assert candidates(site)[0]['source_query'] == 'betatoken'
    assert history_rows(site) == []
    client.get(search_for(client, 'certainlymissingwordzz'))
    assert len(candidates(site)) == 1


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_native_recommendation_forms_keep_mount_and_escape_source(site, prefix):
    client = client_for(site, prefix=prefix)
    response = client.get('/results', {'query': 'alphatoken'})
    form = response.html.select_one(f'form[action="{prefix}/recommendations/add"]')
    assert form and form['method'].lower() == 'post'
    values = form_values(form)
    assert values['csrf'] == identity(client)['csrf']
    values['source_query'] = '<img src=x onerror=alert(1)>银河'
    destination = post_form(client, '/recommendations/add', values, status=303)
    assert local_url(destination.location) == values['next']
    home = client.get('/')
    search = home.html.select_one('#search-recommendations')
    assert search.select_one('.app-recommendation-source').text == '“' + values['source_query'] + '”'
    assert not search.select('img, script')
    link = search.select_one('h3 a')['href']
    assert urlsplit(link).path == prefix + '/reader'
    assert parse_qs(urlsplit(link).query)['return_to'] == [prefix + '/']
    removal = search.select_one(f'form[action="{prefix}/recommendations/remove"]')
    removed = post_form(client, '/recommendations/remove', form_values(removal), status=303)
    assert urlsplit(removed.location).path == prefix + '/'
    assert urlsplit(removed.location).fragment == 'search-recommendations'
    assert candidates(site) == []


def test_recommendation_handles_csrf_and_removals_are_account_scoped(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    response = alice.get('/results', {'query': 'alphatoken'})
    values = form_values(response.html.select_one('form[action="/recommendations/add"]'))
    for client, data in ((bob, values), (alice, dict(values, handle=values['handle'] + 'x'))):
        post_json(client, '/api/recommendations/add', data, status=403)
    alice.post_json('/api/recommendations/add', values, status=403)
    added = post_json(alice, '/api/recommendations/add', values).json
    assert added == {'saved': True, 'document_id': values['document_id']}
    post_json(bob, '/api/recommendations/remove', {
        'document_id': added['document_id'], 'user_id': site.alice['id']}, status=404)
    alice.post_json('/api/recommendations/remove', {'document_id': added['document_id']}, status=403)
    assert len(candidates(site)) == 1
    assert candidates(site, site.bob) == []
    removed = post_json(alice, '/api/recommendations/remove', {'document_id': added['document_id']}).json
    assert removed == {'saved': False, 'document_id': added['document_id']}


def test_daily_draw_is_stable_independent_of_search_and_creates_no_reading(site):
    alice, bob = client_for(site), client_for(site, 'bob')
    first = alice.get('/')
    daily = card_ids(first, 'daily-recommendations')
    assert len(daily) == 5  # All readable indexed files, including extensionless README.
    assert len(set(daily)) == len(daily)
    assert card_ids(first, 'search-recommendations') == []
    assert card_ids(alice.get('/'), 'daily-recommendations') == daily
    assert card_ids(bob.get('/'), 'daily-recommendations') == daily
    site.store.save_settings(site.alice['id'], preferences={
        'stem': 1, 'collapsedups': 1, 'maxresults': 1, 'perpage': 1,
    })
    assert card_ids(alice.get('/'), 'daily-recommendations') == daily
    assert card_ids(bob.get('/'), 'daily-recommendations') == daily
    assert history_rows(site) == [] and history_rows(site, site.bob) == []

    alice.get(search_for(alice, 'alphatoken'))
    assert card_ids(alice.get('/'), 'daily-recommendations') == daily
    assert card_ids(bob.get('/'), 'search-recommendations') == []
    assert len(history_rows(site)) == 1
    for user in (site.alice, site.bob):
        assert site.store.list_readings(user['id'])['items'] == []
        assert site.store.list_bookshelf(user['id'])['items'] == []
        with site.store._connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM reading_progress WHERE user_id=?',
                                      (user['id'],)).fetchone()[0] == 0


def test_home_combines_reading_progress_with_recommendations(site):
    client = client_for(site, prefix='/library')
    book = book_for(client)
    post_json(client, '/api/reader/open', {'handle': book['openHandle']})
    post_json(client, '/api/reader/progress/' + book['documentId'], progress_body(client, book))
    target = search_for(client, 'betatoken').removeprefix('/library')
    client.get(target)
    home = client.get('/')
    assert home.html.select_one('.app-reading-progress progress')['value'] == '37.5'
    assert book['documentId'] in home.html.select_one('.app-continue-card')['href']
    assert len(card_ids(home, 'search-recommendations')) == 1
    assert card_ids(home, 'daily-recommendations')
    assert {label.get_text() for label in home.html.select('.app-folder-name')} == {site.books.name}
    history = client.get('/history?tab=reading')
    assert history.html.select_one('.app-document-meta .app-folder-name').get_text() == site.books.name
    assert 'source_path' not in site.store.list_readings(site.alice['id'])['items'][0]


def test_home_displays_six_candidates_and_omits_unavailable_or_foreign_scope(site):
    client = client_for(site)
    book = book_for(client)
    scope = site.store.get_document(book['documentId'])['library_key']
    documents = []
    paths = []
    try:
        for number in range(8):
            path = site.books / f'recommendation-only-{number}.txt'
            path.write_text('Readable, deliberately not indexed.', encoding='utf-8')
            paths.append(path)
            documents.append(site.store.register_document(scope, path.as_uri(), str(path),
                                                         title=f'Candidate {number}', format='txt')['id'])
        missing = site.books / 'no-such-recommendation.txt'
        stale = site.store.register_document(scope, missing.as_uri(), str(missing),
                                            title='Missing candidate', format='txt')
        foreign = site.store.register_document('other-library', paths[0].as_uri(), str(paths[0]),
                                              title='Other library candidate', format='txt')
        site.store.add_recommendations(site.alice['id'], documents + [stale['id'], foreign['id']], 'recent query')
        home = client.get('/')
        drawn = card_ids(home, 'search-recommendations')
        assert len(drawn) == 6 and len(set(drawn)) == 6
        assert set(drawn) <= set(documents)
        section = home.html.select_one('#search-recommendations')
        assert section.select_one('.app-section-count').text == '8'
        assert 'Missing candidate' not in section.text and 'Other library candidate' not in section.text
        assert len(card_ids(client.get('/?refresh=recommendations'), 'search-recommendations')) == 6
    finally:
        for path in paths:
            path.unlink()
