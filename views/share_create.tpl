%include('header', title=' / 创建分享')
<link rel="stylesheet" href="{{app_url('/static/shares.css')}}?v=folder-label-1">
<main id="main-content" tabindex="-1" class="account-page account-narrow share-create">
  <p class="account-eyebrow">SHARE YOUR LIBRARY</p>
  <h1>{{'分享文件夹' if document['format'] == 'folder' else '分享此书'}}</h1>
  <p class="account-muted">创建链接后，持有链接的人无需登录即可阅读。</p>
  %if error:
  <p class="account-error" role="alert">{{error}}</p>
  %end
  <section class="account-card share-target" aria-label="分享内容">
    <div class="app-document-meta"><span class="share-kind">{{'文件夹' if document['format'] == 'folder' else document['format'].upper() or '文档'}}</span><span class="app-folder-name" title="{{document.get('folder_name', '')}}">{{document.get('folder_name', '')}}</span></div>
    <h2>{{document.get('title') or '未命名文档'}}</h2>
    %if document['format'] == 'folder':
    <p class="account-muted">分享范围包含此文件夹及子目录中的可阅读文件。文件夹内容更新后，链接中也会同步更新。</p>
    %else:
    <p class="account-muted">分享范围仅包含这一本书。</p>
    %end
  </section>
  <form method="post" action="{{app_url('/shares/create')}}" class="account-form">
    <input type="hidden" name="csrf" value="{{account_csrf()}}">
    <input type="hidden" name="document_id" value="{{document['id']}}">
    <label for="share-expiry">有效期（天）<input id="share-expiry" name="expires_days" type="number" min="1" max="3650" step="1" value="{{expires_days}}" required aria-describedby="share-expiry-help"></label>
    <p id="share-expiry-help" class="account-muted share-help">默认 7 天，可设为 1–3650 天。到期后链接自动失效；你也可以在「我的分享」中修改期限或随时取消。</p>
    <div class="account-actions"><button type="submit">创建分享链接</button><a class="account-button account-secondary" href="{{app_url('/shares')}}">我的分享</a></div>
  </form>
</main>
%include('footer')
