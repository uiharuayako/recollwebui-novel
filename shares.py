"""Expiring, read-only links with a separate anonymous reader boundary."""
import functools
import json
import os
import re
import sqlite3
from urllib.parse import urlencode

import bottle

import account_web as auth
from accounts import NotFound, ValidationError, folder_name
from webui_data import FORMAT_MIME, json_response, request_json


TOKEN = r'[A-Za-z0-9_-]{64}'
DOCUMENT = r'[a-f0-9]{32}'
RESOURCE = r'share\.(' + TOKEN + r')\.(' + DOCUMENT + r')'


class ShareLinks:
    def __init__(self, app, store, webui, data):
        self.app, self.store, self.webui, self.data = app, store, webui, data
        self.install()

    def unavailable(self):
        if bottle.request.path.startswith('/api/') or '/api/' in bottle.request.path:
            raise bottle.HTTPResponse(json.dumps({'error': '分享链接不存在、已到期或已取消，或分享内容已不可用。'}),
                                      status=404, headers={'Content-Type': 'application/json; charset=utf-8'})
        raise bottle.HTTPResponse(auth.render('account_error', status=404,
                                  message='分享链接不存在、已到期或已取消，或分享内容已不可用。'), status=404)

    def public_share(self, token):
        share = self.store.get_public_share(token)
        if not share:
            raise NotFound('分享不可用')
        root = self.data.document(share['document_id'], allow_folder=True)
        # A replaced symlink must not move an existing capability to a new path.
        if os.path.realpath(root['source_path']) != root['source_path']:
            raise NotFound('分享内容已移动')
        return share, root

    def shared_document(self, token, document_id):
        share, root = self.public_share(token)
        doc = self.data.document(document_id)
        path = doc['source_path']
        if os.path.realpath(path) != path or doc['format'] not in self.webui.READER_SUPPORTED_FORMATS:
            raise NotFound('文档不可用')
        if root['format'] == 'folder':
            if os.path.commonpath([root['source_path'], path]) != root['source_path']:
                raise NotFound('文档不在分享范围内')
            if any(part.startswith('.') for part in os.path.relpath(path, root['source_path']).split(os.sep)):
                raise NotFound('文档不在分享范围内')
        elif doc['id'] != root['id']:
            raise NotFound('文档不在分享范围内')
        return share, doc

    def resource_payload(self, token, render=False):
        pattern = RESOURCE + (r'\.([A-Za-z0-9_-]{16,128})' if render else '')
        match = re.fullmatch(pattern, token or '')
        if not match:
            return None
        try:
            share, doc = self.shared_document(match[1], match[2])
        except (NotFound, ValidationError, OSError, ValueError):
            return None
        bottle.request.environ['recoll.share'] = share
        if render:
            return {'document_id': doc['id'], 'channel': match[3]}
        return {'path': doc['source_path'], 'name': os.path.basename(doc['source_path']),
                'mimetype': FORMAT_MIME.get(doc['format'], ''), 'document_id': doc['id']}

    def public_request(self):
        """Only these read-only routes may bypass the login boundary."""
        path = bottle.request.path
        page = re.fullmatch(r'/s/(' + TOKEN + r')(?:/api/(book|folder))?', path)
        resource = re.fullmatch(
            r'/api/reader/(?:file/(' + RESOURCE + r')|'
            r'(?:txt|epub|chm)/(' + RESOURCE + r')/(?:manifest|chunk/[0-9]+|content(?:/.*)?)|'
            r'chm-render/(' + RESOURCE + r'\.[A-Za-z0-9_-]{16,128})/content(?:/.*)?)', path)
        if not page and not resource:
            if path == '/s' or path.startswith('/s/') or re.match(r'/api/reader/[^/]+/share\.', path):
                self.unavailable()
            return False
        if bottle.request.method not in ('GET', 'HEAD'):
            bottle.abort(405, '分享只支持阅读')
        try:
            if page:
                share, root = self.public_share(page[1])
                if page[2] and page[2] != ('folder' if root['format'] == 'folder' else 'book'):
                    raise NotFound('分享类型不匹配')
            else:
                token = next(value for value in resource.groups() if value and value.startswith('share.'))
                payload = self.resource_payload(token, render='/chm-render/' in path)
                if not payload:
                    raise NotFound('资源不可用')
                share = bottle.request.environ['recoll.share']
            bottle.request.environ['recoll.share'] = share
            return True
        except (NotFound, ValidationError, OSError, ValueError):
            self.unavailable()

    def item(self, share, doc, root=None):
        token = 'share.' + share['token'] + '.' + doc['id']
        name = os.path.basename(doc['source_path'])
        item = {'documentId': doc['id'], 'contentVersion': doc['content_version'],
                'title': doc['title'] or name, 'name': name, 'format': doc['format'],
                'folder_name': folder_name(doc['source_path']),
                'path': os.path.relpath(doc['source_path'], root) if root else name,
                'author': '', 'size': os.path.getsize(doc['source_path']),
                'mtime': int(os.path.getmtime(doc['source_path'])), 'token': token,
                'url': auth.app_url('/api/reader/file/' + token), 'supported': True}
        if doc['format'] in ('txt', 'epub', 'chm'):
            item['manifestUrl'] = auth.app_url('/api/reader/' + doc['format'] + '/' + token + '/manifest')
        return item

    def public_reader(self, token):
        share, root = self.public_share(token)
        mode = 'folder' if root['format'] == 'folder' else 'book'
        config = {'mode': mode, 'shared': True, 'account': None,
                  'queryString': '', 'resultsQueryString': '',
                  'apiBase': auth.app_url('/s/' + token + '/api')}
        return auth.render('reader', mode=mode, shared=True, query_string='', results_query_string='',
                           results_url=auth.app_url('/s/' + token),
                           reader_config_json=json.dumps(config, ensure_ascii=True).replace('<', '\\u003c'))

    def public_book(self, token):
        share, root = self.public_share(token)
        if root['format'] == 'folder':
            raise NotFound('分享类型不匹配')
        _, doc = self.shared_document(token, root['id'])
        return json_response({'mode': 'book', 'book': self.item(share, doc)})

    def public_folder(self, token):
        share, root = self.public_share(token)
        if root['format'] != 'folder':
            raise NotFound('分享类型不匹配')
        folder, items, seen = root['source_path'], [], set()
        truncated = False
        for current, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not os.path.islink(os.path.join(current, d)))
            for name in sorted(files):
                path = os.path.realpath(os.path.join(current, name))
                format = self.webui.get_reader_format_from_name(name)
                if (name.startswith('.') or path in seen or format not in self.webui.READER_SUPPORTED_FORMATS
                        or os.path.commonpath([folder, path]) != folder or not os.path.isfile(path)):
                    continue
                if len(items) >= self.webui.READER_FOLDER_SCAN_LIMIT:
                    truncated = True
                    break
                try:
                    doc = self.data.register_path(path, title=os.path.splitext(name)[0], format=format)
                    _, doc = self.shared_document(token, doc['id'])
                    items.append(self.item(share, doc, folder))
                    seen.add(path)
                except (NotFound, ValidationError, OSError):
                    continue
            if truncated:
                break
        if not items:
            raise NotFound('文件夹内没有可阅读的文件')
        items.sort(key=lambda item: item['path'].lower())
        return json_response({'mode': 'folder', 'root': root['title'], 'items': items,
                              'count': len(items), 'currentIndex': 0, 'truncated': truncated,
                              'shelf': {'documentId': root['id'], 'title': root['title'], 'format': 'folder'}})

    def share_result(self, share):
        return dict(share, url=auth.app_url('/s/' + share['token']))

    def target(self, document_id, mode=None):
        if not isinstance(document_id, str) or len(document_id) > 128:
            raise ValidationError('无效文档标识')
        if mode not in (None, '', 'book', 'folder'):
            raise ValidationError('无效分享类型')
        doc = self.data.document(document_id, allow_folder=True)
        if mode == 'folder' and doc['format'] != 'folder':
            doc = self.data.register_folder(os.path.dirname(doc['source_path']))
        if doc['format'] != 'folder' and doc['format'] not in self.webui.READER_SUPPORTED_FORMATS:
            raise ValidationError('该格式暂不支持分享阅读')
        return doc

    @staticmethod
    def form_days(value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,4}', value):
            raise ValidationError('有效期请输入 1–3650 的整数天数')
        days = int(value)
        if not 1 <= days <= 3650:
            raise ValidationError('有效期请输入 1–3650 的整数天数')
        return days

    def install(self):
        def endpoint(fn):
            @functools.wraps(fn)
            def handler(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except NotFound:
                    if bottle.request.path.startswith('/s/'):
                        self.unavailable()
                    if bottle.request.path.startswith('/api/'):
                        return json_response({'error': '分享或文档不存在'}, 404)
                    bottle.abort(404, '分享或文档不存在')
                except ValidationError as error:
                    if bottle.request.path.startswith('/api/'):
                        return json_response({'error': str(error)}, 400)
                    raise bottle.HTTPResponse(auth.render('account_error', status=400, message=str(error)), status=400)
                except (sqlite3.Error, OSError):
                    if bottle.request.path.startswith('/api/'):
                        return json_response({'error': '暂时无法读取或保存分享，请稍后重试'}, 503)
                    bottle.abort(503, '暂时无法读取或保存分享，请稍后重试')
            return handler

        def create_page():
            doc = self.target(bottle.request.query.get('document_id', ''), bottle.request.query.get('mode'))
            return auth.render('share_create', document=doc, error='', expires_days=7)

        def manage():
            records = self.store.list_shares(auth.current_user()['id'], cursor=bottle.request.query.get('cursor') or None)
            next_url = auth.app_url('/shares?') + urlencode({'cursor': records['next_cursor']}) if records.get('next_cursor') else ''
            return auth.render('account_shares', shares=records, next_url=next_url,
                               created_id=bottle.request.query.get('created', ''))

        def create(api=False):
            body = request_json() if api else bottle.request.forms
            doc = self.target(body.get('document_id', ''), body.get('mode'))
            days = body.get('expires_days', 7) if api else self.form_days(body.get('expires_days', '7'))
            share = self.store.create_share(auth.current_user()['id'], doc['id'], days)
            if api:
                return json_response({'share': self.share_result(share)}, 201)
            bottle.redirect(auth.app_url('/shares?') + urlencode({'created': share['id']}), 303)

        def update(share_id, api=False):
            body = request_json() if api else bottle.request.forms
            days = body.get('expires_days', 7) if api else self.form_days(body.get('expires_days', '7'))
            share = self.store.update_share(auth.current_user()['id'], share_id, days)
            if api:
                return json_response({'share': self.share_result(share)})
            bottle.redirect(auth.app_url('/shares'), 303)

        def delete(share_id, api=False):
            result = self.store.delete_share(auth.current_user()['id'], share_id)
            if api:
                return json_response(result)
            bottle.redirect(auth.app_url('/shares'), 303)

        def list_api():
            result = self.store.list_shares(auth.current_user()['id'], cursor=bottle.request.query.get('cursor') or None)
            result['items'] = [self.share_result(share) for share in result['items']]
            return json_response(result)

        routes = [('/shares', 'GET', manage), ('/shares/new', 'GET', create_page),
                  ('/shares/create', 'POST', create), ('/shares/<share_id>/update', 'POST', update),
                  ('/shares/<share_id>/delete', 'POST', delete), ('/api/shares', 'GET', list_api),
                  ('/api/shares', 'POST', lambda: create(api=True)),
                  ('/api/shares/<share_id>/update', 'POST', lambda share_id: update(share_id, api=True)),
                  ('/api/shares/<share_id>/delete', 'POST', lambda share_id: delete(share_id, api=True)),
                  ('/s/<token>', 'GET', self.public_reader),
                  ('/s/<token>/api/book', 'GET', self.public_book),
                  ('/s/<token>/api/folder', 'GET', self.public_folder)]
        for path, method, callback in routes:
            self.app.route(path, method=method, callback=endpoint(callback))
