"""Account-scoped search, document, bookshelf and reading integration."""
import hashlib
import json
import mimetypes
import os
import random
import re
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlencode, quote, urlsplit

import bottle
import account_web as auth
from accounts import ConflictError, NotFound, ValidationError, folder_identity, folder_name


SEARCH_FIELDS = ('query', 'dir', 'before', 'after', 'sort', 'ascending')
SEARCH_SETTINGS = ('stem', 'collapsedups', 'highlight', 'snippets')
FORMAT_MIME = {
    'txt': 'text/plain', 'md': 'text/markdown', 'html': 'text/html', 'htm': 'text/html',
    'xhtml': 'application/xhtml+xml', 'xml': 'application/xml', 'mhtml': 'multipart/related',
    'epub': 'application/epub+zip', 'pdf': 'application/pdf',
    'fb2': 'application/x-fictionbook+xml', 'chm': 'application/vnd.ms-htmlhelp',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'mobi': 'application/x-mobipocket-ebook', 'azw': 'application/vnd.amazon.ebook',
    'azw3': 'application/vnd.amazon.ebook', 'cbz': 'application/x-cbz',
    'cbr': 'application/x-cbr', 'cbt': 'application/x-cbt', 'cb7': 'application/x-cb7',
}


def json_response(value, status=200):
    bottle.response.status = status
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def request_json(limit=32768):
    if bottle.request.content_length > limit:
        bottle.abort(413, '请求过大')
    raw = bottle.request.body.read(limit + 1)
    if len(raw) > limit:
        bottle.abort(413, '请求过大')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        bottle.abort(400, '无效 JSON')
    if not isinstance(data, dict):
        bottle.abort(400, '请求必须是 JSON 对象')
    return data


class ApplicationData:
    def __init__(self, app, store, webui):
        self.app, self.store, self.webui = app, store, webui
        self.install()

    def scope(self, config=None):
        config = config or self.webui.get_config()
        roots = sorted(os.path.realpath(x) for x in config['dirs'])
        configs = sorted({os.path.realpath(config['confdir'])} |
                         {os.path.realpath(x) for x in config.get('extraconfdirs') or []})
        return hashlib.sha256(json.dumps([configs, roots]).encode()).hexdigest()

    def scope_version(self, config):
        return hashlib.sha256(json.dumps([
            self.scope(config), config.get('queryfrag', ''), config.get('synonyms', ''),
            config.get('stemlang', ''), config.get('locked_search_options', {}),
        ], sort_keys=True).encode()).hexdigest()

    def validate_query(self, query):
        if len(query['query']) > 4096:
            raise ValidationError('检索表达式最多 4096 个字符')
        for field in ('before', 'after'):
            if query[field] and not re.fullmatch(r'\d{4}(?:-\d{2})?(?:-\d{2})?', query[field]):
                raise ValidationError('日期格式应为 YYYY、YYYY-MM 或 YYYY-MM-DD')
        if query['sort'] not in {x[0] for x in self.webui.SORTS}:
            raise ValidationError('无效排序字段')
        if len(query['dir']) > 4096 or '\x00' in query['dir']:
            raise ValidationError('无效目录')
        query['ascending'] = int(bool(query['ascending']))
        return query

    def search_event(self, config, query, event_id=None):
        """Restore only this user's matching event; foreign IDs are inert."""
        if event_id is None:
            event_id = bottle.request.query.get('search_id', '')
        if not event_id:
            return None
        try:
            event = self.store.get_search(auth.current_user()['id'], event_id)
        except NotFound:
            return None
        if not event:
            return None
        saved = event.get('query') or event.get('query_json') or {}
        if isinstance(saved, str):
            saved = json.loads(saved)
        if any(str(saved.get(k, '')) != str(query.get(k, '')) for k in SEARCH_FIELDS):
            return None
        settings = event.get('settings_snapshot') or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        if event.get('scope_version') != self.scope_version(config):
            bottle.request.environ['recoll.notice'] = '检索配置已变化，结果按当前配置生成。'
            return None
        # Retained successful events remain readable after another event is
        # deleted or recording is paused. Only pending writes require a live epoch.
        if event.get('status') != 'success' and (
                not auth.current_settings()['search_history_enabled'] or
                event.get('history_epoch') != auth.current_settings()['search_epoch']):
            return None
        for key in ('stem', 'collapsedups'):
            if key in settings:
                config[key] = settings[key]
        for key in ('highlight', 'snippets'):
            if key in settings:
                query[key] = settings[key]
        return event

    def begin_search(self):
        config = self.webui.get_config()
        query = self.validate_query(self.webui.get_query(config, bottle.request.forms))
        query['page'] = 1
        query.pop('search_id', None)
        replay_id = bottle.request.forms.get('replay_id')
        replay_event = None
        if replay_id:
            replay_event = self.search_event(config, query, event_id=replay_id)
        event_id = None
        try:
            if query['query'].strip() or query['dir'] != '<all>' or query['before'] or query['after']:
                event_id = self.store.begin_search(
                    auth.current_user()['id'], {k: query[k] for k in SEARCH_FIELDS},
                    {k: config[k] if k in config else query[k] for k in SEARCH_SETTINGS},
                    self.scope_version(config))
        except sqlite3.Error:
            query['history_unsaved'] = '1'
        if event_id:
            query['search_id'] = event_id
        elif replay_event and replay_event.get('status') == 'success':
            # Recording may be paused. The retained event is a read-only
            # snapshot reference across POST/redirect/GET, never a new event.
            query['search_id'] = replay_event['id']
        bottle.redirect(auth.app_url('/results') + '?' + urlencode(query), 303)

    def finish_search(self, event, query, config, result_count, recommendation_ids=None):
        if not event:
            return
        try:
            self.store.complete_search(
                auth.current_user()['id'], event['id'],
                {k: query[k] for k in SEARCH_FIELDS},
                {k: config[k] if k in config else query[k] for k in SEARCH_SETTINGS}, result_count,
                recommendation_ids=recommendation_ids)
        except sqlite3.Error:
            bottle.request.environ['recoll.notice'] = '搜索已完成，本次历史与搜索推荐尚未保存。'

    def recommendation_card(self, document, source_query=''):
        return {
            'document_id': document['id'], 'title': document['title'],
            'format': document['format'], 'source_query': source_query,
            'folder_name': folder_name(document['source_path']),
            'recommendation_handle': auth.sign_payload({'document_id': document['id']}, 'recommendation-add'),
        }

    def search_recommendations(self, config):
        uid = auth.current_user()['id']
        candidates = []
        for item in self.store.list_recommendations(uid, library_key=self.scope(config)):
            try:
                document = self.document(item['document_id'], config)
            except (NotFound, ValidationError, OSError):
                continue
            if document['format'] in self.webui.READER_SUPPORTED_FORMATS:
                candidates.append(self.recommendation_card(document, item['source_query']))
        return candidates

    def daily_recommendations(self, config, day):
        # Seek randomly into the complete index, without generating excerpts or
        # using the user's search result/page limits or personal history.
        mime_types = sorted({FORMAT_MIME[fmt] for fmt in self.webui.READER_SUPPORTED_FORMATS if fmt in FORMAT_MIME})
        query_spec = {
            'query': '(' + ' OR '.join('mime:"%s"' % mime for mime in mime_types) + ')',
            'dir': '<all>', 'before': '', 'after': '', 'sort': 'relevancyrating', 'ascending': 0,
        }
        # Relevance order avoids sorting the whole library by file path. Fixed
        # search options keep the daily draw independent of account preferences.
        daily_config = dict(config, stem=0, collapsedups=0)
        query, database, _ = self.webui.recoll_initsearch(query_spec, daily_config)
        generator = random.Random(day + ':' + self.scope_version(config))
        offsets = generator.sample(range(query.rowcount), min(query.rowcount, 48))
        items, seen = [], set()
        for offset in offsets:
            if type(query.next) == int:
                query.next = offset
            else:
                query.scroll(offset, mode='absolute')
            doc = query.fetchone()
            if doc is None or getattr(doc, 'ipath', ''):
                continue
            try:
                document = self.register_doc(doc, config)
            except (NotFound, ValidationError, OSError):
                continue
            if document['format'] not in self.webui.READER_SUPPORTED_FORMATS or document['id'] in seen:
                continue
            seen.add(document['id'])
            items.append(self.recommendation_card(document))
            if len(items) == 6:
                break
        return items

    def change_recommendation(self, saved, api=False):
        data = request_json() if api else bottle.request.forms.decode('utf-8')
        uid = auth.current_user()['id']
        if saved:
            context = auth.read_payload(data.get('handle', ''), 'recommendation-add')
            if not context:
                bottle.abort(403, '推荐操作已失效，请刷新后重试')
            document = self.document(context['document_id'])
            if document['format'] not in self.webui.READER_SUPPORTED_FORMATS:
                raise ValidationError('该文档暂不支持推荐阅读')
            document_id = document['id']
            self.store.add_recommendations(uid, [document_id], data.get('source_query', ''))
        else:
            document_id = data.get('document_id', '')
            self.store.remove_recommendation(uid, document_id)
        if api:
            return json_response({'saved': saved, 'document_id': document_id})
        bottle.redirect(auth.safe_next(data.get('next', auth.app_url('/#search-recommendations'))), 303)

    def version(self, path):
        stat = os.stat(path)
        return '%s:%s' % (stat.st_mtime_ns, stat.st_size)

    def register_path(self, path, title='', format='', config=None, ipath='', rcludi=''):
        config = config or self.webui.get_config()
        path = os.path.realpath(path)
        if not self.webui.is_allowed_reader_path(path, config) or not os.path.isfile(path):
            raise NotFound('文档不可用')
        return self.store.register_document(
            self.scope(config), 'file://' + quote(path, safe='/'), path,
            ipath=ipath, rcludi=rcludi, title=title or os.path.basename(path),
            format=format or self.webui.get_reader_format_from_name(path),
            content_version=self.version(path))

    def register_doc(self, doc, config=None):
        config = config or self.webui.get_config()
        url = getattr(doc, 'url', '') or ''
        if urlsplit(url).scheme not in ('', 'file'):
            raise NotFound('该来源不支持书架与继续阅读')
        return self.register_path(
            self.webui.normalize_file_path(url),
            title=getattr(doc, 'title', '') or getattr(doc, 'filename', ''),
            format=self.webui.get_reader_format_from_name(
                getattr(doc, 'filename', ''), getattr(doc, 'mtype', '')),
            config=config, ipath=getattr(doc, 'ipath', '') or '',
            rcludi=getattr(doc, 'rcludi', '') or '')

    def register_folder(self, root, config=None):
        config = config or self.webui.get_config()
        root = os.path.realpath(root)
        if not self.webui.is_allowed_reader_path(root, config) or not os.path.isdir(root):
            raise NotFound('文件夹不可用')
        ref = self.store.register_document(
            self.scope(config), 'file://' + quote(root, safe='/'), root,
            title=os.path.basename(root) or root, format='folder')
        ref['folder_name'] = folder_name(root)
        return ref

    def document(self, document_id, config=None, allow_container=False, allow_folder=False):
        config = config or self.webui.get_config()
        doc = self.store.get_document(document_id)
        is_folder = doc['format'] == 'folder'
        if (doc['library_key'] != self.scope(config) or
                not self.webui.is_allowed_reader_path(doc['source_path'], config) or
                (is_folder and not allow_folder) or
                not (os.path.isdir(doc['source_path']) if is_folder else os.path.isfile(doc['source_path']))):
            raise NotFound('文件夹不可用' if is_folder else '文档不可用')
        if doc.get('ipath') and not allow_container:
            raise ValidationError('该容器内文档暂不支持继续阅读，请通过搜索结果预览或下载')
        doc['content_version'] = '' if is_folder else self.version(doc['source_path'])
        doc['folder_name'] = folder_name(doc['source_path'])
        return doc

    def folder(self, document_id, config=None):
        doc = self.document(document_id, config, allow_folder=True)
        if doc['format'] != 'folder':
            raise NotFound('文件夹不可用')
        return doc

    def as_recoll_doc(self, document_id):
        doc = self.document(document_id)
        path = doc['source_path']
        return SimpleNamespace(
            url=doc['source_url'], ipath='', filename=os.path.basename(path),
            title=doc['title'], author='',
            mtype=FORMAT_MIME.get(doc['format']) or mimetypes.guess_type(path)[0] or '',
            size=os.stat(path).st_size, fbytes=os.stat(path).st_size,
            mtime=int(os.stat(path).st_mtime), rcludi=doc.get('rcludi', ''))

    def folder_key(self, root, config=None):
        return folder_identity(self.scope(config), os.path.realpath(root))

    def folder_shelf_metadata(self, root, config=None):
        ref = self.register_folder(root, config)
        return {
            'documentId': ref['id'], 'title': ref['title'], 'format': 'folder',
            'openHandle': auth.sign_payload({'document_id': ref['id']}, 'shelf-add'),
            'onShelf': self.store.is_on_shelf(auth.current_user()['id'], ref['id']),
        }

    def reader_metadata(self, item, doc=None, config=None, folder_key=None, shelf_document_id=None):
        ref = self.register_doc(doc, config) if doc is not None else self.register_path(
            item['path'], item.get('title', ''), item.get('format', ''), config)
        item['documentId'] = ref['id']
        item['folder_name'] = folder_name(ref['source_path'])
        item['contentVersion'] = ref['content_version']
        settings = auth.current_settings()
        item['openHandle'] = auth.sign_payload({
            'document_id': ref['id'], 'reading_epoch': settings['reading_epoch'],
            'progress_epoch': settings['progress_epoch'], 'folder_key': folder_key,
            'shelf_document_id': shelf_document_id or ref['id'],
        }, 'reader-open')
        item['onShelf'] = self.store.is_on_shelf(auth.current_user()['id'], ref['id'])
        return item

    def token_for_path(self, path, mimetype='', name='', document_id=None):
        ref = self.document(document_id) if document_id else self.register_path(
            path, name, self.webui.get_reader_format_from_name(name or path, mimetype))
        return auth.sign_payload({'document_id': ref['id']}, 'reader')

    def token_payload(self, token):
        try:
            signed = auth.read_payload(token, 'reader')
            if not signed:
                return None
            doc = self.document(signed['document_id'])
            return {'path': doc['source_path'], 'name': os.path.basename(doc['source_path']),
                    'mimetype': FORMAT_MIME.get(doc['format']) or mimetypes.guess_type(doc['source_path'])[0] or '',
                    'document_id': doc['id']}
        except (NotFound, ValidationError, KeyError):
            return None

    def progress_response(self, document_id):
        doc = self.document(document_id)
        row = self.store.get_progress(auth.current_user()['id'], document_id)
        result = dict(row or {'position': None, 'revision': 0})
        if 'position_json' in result:
            result['position'] = json.loads(result['position_json']) if isinstance(result['position_json'], str) else result['position_json']
        if row and row.get('content_version') != doc['content_version']:
            result['contentChanged'] = True
            result['current_content_version'] = doc['content_version']
        result['settings'] = auth.current_settings()
        result.setdefault('content_version', doc['content_version'])
        return result

    def open_reading(self):
        data = request_json()
        context = auth.read_payload(data.get('handle', ''), 'reader-open')
        if not context:
            bottle.abort(403, '阅读上下文已失效，请重新打开')
        doc = self.document(context['document_id'])
        if context['progress_epoch'] != auth.current_settings()['progress_epoch']:
            return json_response({'error': '阅读进度已清理或设置已变化，请重新打开', 'code': 'stale_epoch'}, 409)
        result = self.store.open_reading(
            auth.current_user()['id'], doc['id'], context['reading_epoch'],
            context['progress_epoch'], folder_key=context.get('folder_key'))
        result['progress'] = self.progress_response(doc['id'])
        return json_response(result)

    def save_progress(self, document_id):
        data = request_json()
        context = auth.read_payload(data.get('handle', ''), 'reader-open')
        if not context or context.get('document_id') != document_id:
            bottle.abort(403, '阅读上下文已失效，请重新打开')
        doc = self.document(document_id)
        if (data.get('epoch') != context['progress_epoch'] or
                context['progress_epoch'] != auth.current_settings()['progress_epoch'] or
                not auth.current_settings()['progress_sync_enabled']):
            return json_response({'error': '阅读进度上下文已失效，请重新打开', 'code': 'stale_epoch'}, 409)
        if data.get('content_version', '') != doc['content_version']:
            return json_response({'error': '文档内容已变化，请重新打开后选择阅读位置',
                                  'code': 'content_changed'}, 409)
        result = self.store.save_progress(
            auth.current_user()['id'], document_id, data.get('position'),
            data.get('revision'), data.get('writer_id', ''), data.get('writer_seq'),
            context['progress_epoch'], content_version=doc['content_version'],
            progress=data.get('progress'))
        return json_response(result)

    def add_shelf(self):
        data = request_json()
        context = auth.read_payload(data.get('handle', ''), 'reader-open')
        if context:
            document_id = context.get('shelf_document_id') or context['document_id']
        else:
            context = auth.read_payload(data.get('handle', ''), 'shelf-add')
            if not context:
                bottle.abort(403, '书籍上下文已失效，请刷新后重试')
            document_id = context['document_id']
        doc = self.document(document_id, allow_container=True, allow_folder=True)
        self.store.add_to_shelf(auth.current_user()['id'], doc['id'])
        return json_response({'onShelf': True})

    def import_legacy(self, data):
        if not isinstance(data, dict):
            raise ValidationError('无效导入数据')
        positions = data.get('positions', [])
        if not isinstance(positions, list) or len(positions) > 100:
            raise ValidationError('每次最多导入 100 项进度')
        prefs = data.get('preferences', {})
        if not isinstance(prefs, dict) or not isinstance(data.get('overwrite', False), bool):
            raise ValidationError('无效导入偏好或覆盖选项')
        if self.webui.get_config()['rclc_nosettings']:
            prefs = {k: v for k, v in prefs.items() if k == 'font_size'}
        # Validate the complete request before importing any rows.
        self.store._preferences(prefs)
        if positions and (data.get('progress_epoch') != auth.current_settings()['progress_epoch'] or
                          not auth.current_settings()['progress_sync_enabled']):
            raise ConflictError('阅读进度上下文已失效，请刷新后重试')
        imported, skipped = [], []
        for item in positions:
            if not isinstance(item, dict):
                skipped.append(None)
                continue
            path = item.get('path', '')
            try:
                if not isinstance(path, str) or not os.path.isabs(path):
                    raise ValidationError('无效文档路径')
                ref = self.register_path(path)
                self.store.import_progress(auth.current_user()['id'], ref['id'], item.get('position'),
                                           data.get('progress_epoch'), content_version=ref['content_version'],
                                           overwrite=data.get('overwrite', False))
                imported.append(path)
            except (ValidationError, NotFound, ConflictError, OSError, TypeError):
                skipped.append(path)
        if prefs:
            self.store.save_settings(auth.current_user()['id'], preferences=prefs)
        return {'imported': imported, 'skipped': skipped,
                'preferences_imported': bool(prefs)}

    def install(self):
        def endpoint(fn):
            def handler(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ConflictError as exc:
                    current = getattr(exc, 'current', None)
                    return json_response({'error': str(exc), 'code': 'stale_epoch' if '代际' in str(exc) or '上下文' in str(exc) else 'conflict',
                                          'current': current}, 409)
                except NotFound:
                    return json_response({'error': '文档或记录不可用'}, 404)
                except ValidationError as exc:
                    return json_response({'error': str(exc)}, 400)
                except sqlite3.Error:
                    return json_response({'error': '暂时无法保存，请稍后重试'}, 503)
            return handler

        self.app.route('/search', method='POST', callback=endpoint(self.begin_search))
        self.app.route('/recommendations/add', method='POST',
                       callback=endpoint(lambda: self.change_recommendation(True)))
        self.app.route('/recommendations/remove', method='POST',
                       callback=endpoint(lambda: self.change_recommendation(False)))
        self.app.route('/api/recommendations/add', method='POST',
                       callback=endpoint(lambda: self.change_recommendation(True, api=True)))
        self.app.route('/api/recommendations/remove', method='POST',
                       callback=endpoint(lambda: self.change_recommendation(False, api=True)))
        self.app.route('/api/reader/open', method='POST', callback=endpoint(self.open_reading))
        self.app.route('/api/reader/progress/<document_id>', method='GET',
                       callback=endpoint(lambda document_id: json_response(self.progress_response(document_id))))
        self.app.route('/api/reader/progress/<document_id>', method='POST', callback=endpoint(self.save_progress))
        self.app.route('/api/bookshelf/add', method='POST', callback=endpoint(self.add_shelf))

        def remove_shelf():
            data = request_json()
            self.store.remove_from_shelf(auth.current_user()['id'], data.get('document_id', ''))
            return json_response({'onShelf': False})

        self.app.route('/api/bookshelf/remove', method='POST', callback=endpoint(remove_shelf))
