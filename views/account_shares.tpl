%import time
%now = time.time()
%include('header', title=' / 我的分享')
<link rel="stylesheet" href="{{app_url('/static/shares.css')}}?v=folder-label-1">
<main id="main-content" tabindex="-1" class="account-page shares-page">
  <header class="account-heading"><div><p class="account-eyebrow">YOUR SHARED LINKS</p><h1>我的分享</h1><p class="account-muted">管理你分享的书籍和文件夹，随时修改有效期或取消分享。</p></div><a class="account-button account-secondary" href="{{app_url('/bookshelf')}}">从书架分享 <span aria-hidden="true">↗</span></a></header>
  %if created_id:
  <p class="account-notice" role="status">分享链接已创建。复制链接，即可分享给他人，无需登录。</p>
  %end
  %if not shares['items']:
  <section class="account-empty"><h2>还没有分享链接</h2><p>在书架、搜索结果或阅读器中选择「分享」，设置期限后即可创建链接。</p><a class="account-button" href="{{app_url('/bookshelf')}}">前往我的书架</a></section>
  %end
  <ul class="account-list share-list">
  %for item in shares['items']:
    %expired = item['expires_at'] <= now
    %share_url = app_url('/s/' + item['token'])
    <li class="account-card share-card" id="share-{{item['id']}}" data-share-card data-created="{{'true' if item['id'] == created_id else 'false'}}">
      <div class="share-card-heading"><div><div class="app-document-meta"><span class="share-kind">{{'文件夹' if item['format'] == 'folder' else item['format'].upper() or '文档'}}</span><span class="app-folder-name" title="{{item.get('folder_name', '')}}">{{item.get('folder_name', '')}}</span></div><h2>{{item.get('title') or '未命名文档'}}</h2></div><span class="share-status {{'share-status-expired' if expired else ''}}">{{'已过期' if expired else '分享中'}}</span></div>
      <p class="account-muted">创建于 <time data-utc="{{item['created_at']}}">{{item['created_at']}}</time> · 到期于 <time data-utc="{{item['expires_at']}}">{{item['expires_at']}}</time></p>
      <div class="share-link-row"><label class="share-link-label" for="share-url-{{item['id']}}">分享链接</label><input id="share-url-{{item['id']}}" class="share-url" type="text" value="{{share_url}}" data-share-url readonly><button type="button" class="account-secondary" data-share-copy hidden>复制链接</button><a class="account-button account-secondary" href="{{share_url}}" target="_blank" rel="noopener noreferrer">打开链接 <span aria-hidden="true">↗</span></a></div>
      <p class="share-copy-status account-muted" data-share-copy-status role="status" aria-live="polite"></p>
      <div class="share-card-actions"><details class="share-expiry-edit"><summary>{{'重新启用并设置期限' if expired else '修改有效期'}}</summary><form method="post" action="{{app_url('/shares/' + item['id'] + '/update')}}" class="account-inline"><input type="hidden" name="csrf" value="{{account_csrf()}}"><label>从现在起有效（天）<input name="expires_days" type="number" min="1" max="3650" step="1" value="7" required></label><button type="submit" class="account-secondary">保存期限</button></form></details><form method="post" action="{{app_url('/shares/' + item['id'] + '/delete')}}" data-confirm="取消后，此分享链接将立即失效。确定取消？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><button type="submit" class="account-danger">取消分享</button></form></div>
    </li>
  %end
  </ul>
  %if next_url:
  <nav class="account-pagination" aria-label="分享分页"><a class="account-button account-secondary" href="{{next_url}}">下一页 <span aria-hidden="true">→</span></a></nav>
  %end
  <p class="account-muted account-footnote">链接持有人可以阅读分享内容。到期或取消分享后，链接及其文件访问会立即失效。</p>
</main>
<script defer src="{{app_url('/static/shares.js')}}?v=1"></script>
%include('footer')
