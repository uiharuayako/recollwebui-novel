"""Folder bookmarks keep their own identity while chapters retain reading progress."""

from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

import pytest

from test_webui_integration import (
    book_for,
    client_for,
    post_form,
    post_json,
    progress_body,
    recoll_index,
    site,
)


@pytest.fixture
def chapter_folder(site):
    """Use an isolated directory in the real library, without reindexing it."""
    client = client_for(site)
    indexed = book_for(client)
    library_key = site.store.get_document(indexed['documentId'])['library_key']
    root = Path(tempfile.mkdtemp(prefix='长篇小说-', dir=site.books))
    first = root / '01-opening.txt'
    nested = root / '第二卷' / '02-next.txt'
    nested.parent.mkdir()
    first.write_text('The first chapter.\n', encoding='utf-8')
    nested.write_text('The next chapter, inside a volume.\n', encoding='utf-8')
    ref = site.store.register_document(
        library_key, first.as_uri(), str(first), title='Opening chapter',
        format='txt', content_version='fixture')
    try:
        yield root, first, nested, ref['id']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def shelf_ids(site, user=None):
    return {item['document_id'] for item in
            site.store.list_bookshelf((user or site.alice)['id'])['items']}


@pytest.mark.parametrize('entry', ['document_id', 'query'])
def test_folder_reader_exposes_a_directory_bookmark_for_both_entry_points(site, entry):
    client = client_for(site)
    book = book_for(client)
    params = ({'document_id': book['documentId']} if entry == 'document_id'
              else {'query': 'alphatoken', 'resnum': 0})
    folder = client.get('/api/reader/folder', params).json
    shelf = folder['shelf']

    assert folder['mode'] == 'folder'
    assert shelf['format'] == 'folder'
    assert shelf['title'] == site.books.name
    assert shelf['documentId'] != book['documentId']
    assert shelf['openHandle']
    assert shelf['onShelf'] is False
    stored = site.store.get_document(shelf['documentId'])
    assert stored['format'] == 'folder'
    assert stored['source_path'] == str(site.books)

    post_json(client, '/api/bookshelf/add', {'handle': shelf['openHandle']})
    assert shelf_ids(site) == {shelf['documentId']}
    reopened = client.get('/api/reader/folder', {'folder_id': shelf['documentId']}).json
    assert reopened['root'] == str(site.books)
    assert reopened['shelf']['documentId'] == shelf['documentId']
    assert reopened['shelf']['onShelf'] is True
    # A directory is not a readable chapter, even though it has a stable ID.
    client.get('/api/reader/book', {'document_id': shelf['documentId']}, status=404)
    post_json(client, '/api/reader/open', {'handle': shelf['openHandle']}, status=403)


def test_adding_different_chapters_in_folder_mode_deduplicates_the_directory(site, chapter_folder):
    root, first, nested, first_id = chapter_folder
    client = client_for(site)
    folder = client.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    assert {item['path'] for item in folder['items']} == {str(first), str(nested)}

    for chapter in folder['items']:
        post_json(client, '/api/bookshelf/add', {'handle': chapter['openHandle']})
        assert shelf_ids(site) == {shelf_id}
        assert not site.store.is_on_shelf(site.alice['id'], chapter['documentId'])
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    assert shelf_ids(site) == {shelf_id}
    assert site.store.get_document(shelf_id)['source_path'] == str(root)
    assert site.store.list_readings(site.alice['id'])['items'] == []


def test_single_book_and_its_folder_can_be_added_and_removed_independently(site, chapter_folder):
    _, _, _, first_id = chapter_folder
    client = client_for(site)
    book = client.get('/api/reader/book', {'document_id': first_id}).json['book']
    folder = client.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']

    post_json(client, '/api/bookshelf/add', {'handle': book['openHandle']})
    assert shelf_ids(site) == {first_id}
    assert client.get('/api/reader/folder', {'folder_id': shelf_id}).json['shelf']['onShelf'] is False
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    assert shelf_ids(site) == {first_id, shelf_id}

    post_json(client, '/api/bookshelf/remove', {'document_id': shelf_id})
    assert shelf_ids(site) == {first_id}
    assert client.get('/api/reader/book', {'document_id': first_id}).json['book']['onShelf'] is True
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    post_json(client, '/api/bookshelf/remove', {'document_id': first_id})
    assert shelf_ids(site) == {shelf_id}
    assert client.get('/api/reader/book', {'document_id': first_id}).json['book']['onShelf'] is False
    assert client.get('/api/reader/folder', {'folder_id': shelf_id}).json['shelf']['onShelf'] is True


def test_folder_bookmarks_and_handles_are_account_scoped(site, chapter_folder):
    _, _, _, first_id = chapter_folder
    alice, bob = client_for(site), client_for(site, 'bob')
    folder = alice.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    post_json(alice, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})

    for handle in [folder['shelf']['openHandle'], folder['items'][0]['openHandle']]:
        post_json(bob, '/api/bookshelf/add', {'handle': handle}, status=403)
    post_json(bob, '/api/bookshelf/remove', {'document_id': shelf_id}, status=404)
    assert shelf_ids(site, site.bob) == set()
    own = bob.get('/api/reader/folder', {'folder_id': shelf_id}).json
    assert own['shelf']['documentId'] == shelf_id
    assert own['shelf']['onShelf'] is False

    post_json(bob, '/api/bookshelf/add', {'handle': own['shelf']['openHandle']})
    post_json(alice, '/api/bookshelf/remove', {'document_id': shelf_id})
    assert shelf_ids(site) == set()
    assert shelf_ids(site, site.bob) == {shelf_id}


def test_folder_reopening_keeps_original_root_after_nested_resume_and_seed_deletion(site, chapter_folder):
    root, first, nested, first_id = chapter_folder
    alice, bob = client_for(site), client_for(site, 'bob')
    folder = alice.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    chapter = next(item for item in folder['items'] if item['path'] == str(nested))
    post_json(alice, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    post_json(alice, '/api/reader/open', {'handle': chapter['openHandle']})

    resumed = alice.get('/api/reader/folder', {'folder_id': shelf_id}).json
    assert resumed['root'] == str(root)
    assert resumed['shelf']['documentId'] == shelf_id
    assert resumed['items'][resumed['currentIndex']]['documentId'] == chapter['documentId']
    assert {item['path'] for item in resumed['items']} == {str(first), str(nested)}
    other = bob.get('/api/reader/folder', {'folder_id': shelf_id}).json
    assert other['items'][other['currentIndex']]['documentId'] == first_id
    assert other['shelf']['onShelf'] is False

    first.unlink()
    reopened = alice.get('/api/reader/folder', {'folder_id': shelf_id}).json
    assert reopened['root'] == str(root)
    assert reopened['shelf']['title'] == root.name
    assert reopened['shelf']['documentId'] == shelf_id
    assert reopened['shelf']['onShelf'] is True
    assert [item['path'] for item in reopened['items']] == [str(nested)]
    assert reopened['items'][reopened['currentIndex']]['documentId'] == chapter['documentId']
    post_json(alice, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    assert shelf_ids(site) == {shelf_id}


def test_folder_shelf_labels_completed_chapter_progress_and_survives_clearing_it(site, chapter_folder):
    root, _, nested, first_id = chapter_folder
    client = client_for(site)
    folder = client.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    chapter = next(item for item in folder['items'] if item['path'] == str(nested))
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    post_json(client, '/api/reader/open', {'handle': chapter['openHandle']})
    post_json(client, '/api/reader/progress/' + chapter['documentId'],
              progress_body(client, chapter, value=100))

    record = site.store.list_bookshelf(site.alice['id'])['items'][0]
    assert record['document_id'] == shelf_id
    assert record['resume_document_id'] == chapter['documentId']
    assert record['resume_title'] == chapter['title']
    assert record['progress'] == 100
    card = client.get('/bookshelf').html.select_one('.account-card')
    assert card.select_one('h2').get_text(strip=True) == root.name
    assert '上次阅读：' + record['resume_title'] in card.get_text()
    progress = card.select_one('.account-book-progress')
    assert '上次阅读的文件进度' in progress.get_text()
    assert '100%' in progress.get_text()
    assert '已读完' not in card.get_text()

    post_form(client, '/history/reading/clear', {'include_progress': '1'})
    assert shelf_ids(site) == {shelf_id}
    assert site.store.list_readings(site.alice['id'])['items'] == []
    assert site.store.get_progress(site.alice['id'], chapter['documentId']) is None
    assert site.store.get_folder_resume(site.alice['id'], folder['folderKey']) is None
    cleared_card = client.get('/bookshelf').html.select_one('.account-card')
    assert cleared_card.select_one('h2').get_text(strip=True) == root.name
    assert cleared_card.select_one('progress') is None

    reopened = client.get('/api/reader/folder', {'folder_id': shelf_id}).json
    assert reopened['root'] == str(root)
    assert reopened['shelf']['onShelf'] is True
    current = reopened['items'][reopened['currentIndex']]
    opened = post_json(client, '/api/reader/open', {'handle': current['openHandle']}).json
    assert opened['reading_recorded'] is True
    assert shelf_ids(site) == {shelf_id}


@pytest.mark.parametrize('unavailable', ['empty', 'deleted', 'forbidden'])
def test_unavailable_folder_bookmark_returns_a_clear_404(site, chapter_folder, unavailable):
    root, first, nested, first_id = chapter_folder
    client = client_for(site)
    folder = client.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})
    if unavailable == 'empty':
        first.unlink()
        nested.unlink()
    elif unavailable == 'deleted':
        shutil.rmtree(root)
    else:
        allowed = site.root / 'other-library'
        allowed.mkdir()
        (site.config / 'recoll.conf').write_text(
            site.base_config.replace(f'topdirs = {site.books}', f'topdirs = {allowed}'),
            encoding='utf-8')

    response = client.get('/api/reader/folder', {'folder_id': shelf_id}, status=404)
    assert '不可用' in response.text or '没有可阅读' in response.text
    assert shelf_ids(site) == {shelf_id}


@pytest.mark.parametrize('identifier', ['chapter', 'missing'])
def test_invalid_folder_identity_does_not_fall_back_to_search(site, chapter_folder, identifier):
    _, _, _, first_id = chapter_folder
    client = client_for(site)
    client.get('/api/reader/folder', {
        'folder_id': first_id if identifier == 'chapter' else 'missing-folder',
        'query': 'alphatoken', 'resnum': 0,
    }, status=404)


@pytest.mark.parametrize('prefix', ['', '/library'])
def test_bookshelf_displays_folder_title_and_reopens_folder_with_mount_prefix(site, chapter_folder, prefix):
    root, _, _, first_id = chapter_folder
    client = client_for(site, prefix=prefix)
    folder = client.get('/api/reader/folder', {'document_id': first_id}).json
    shelf_id = folder['shelf']['documentId']
    post_json(client, '/api/bookshelf/add', {'handle': folder['shelf']['openHandle']})

    page = client.get('/bookshelf')
    card = page.html.select_one('.account-card')
    assert card['data-book-title'] == root.name
    assert card.select_one('h2').get_text(strip=True) == root.name
    links = [link['href'] for link in card.select('a') if urlsplit(link['href']).path == prefix + '/reader']
    assert len(links) >= 2
    for link in links:
        target = urlsplit(link)
        assert target.path == prefix + '/reader'
        query = parse_qs(target.query)
        assert query['mode'] == ['folder']
        assert query['folder_id'] == [shelf_id]
        assert 'document_id' not in query
    assert card.select_one('input[name="document_id"]')['value'] == shelf_id
    reader = client.get(links[0][len(prefix):] if prefix else links[0])
    assert '"mode": "folder"' in reader.text
    assert f'"apiBase": "{prefix}/api/reader"' in reader.text
