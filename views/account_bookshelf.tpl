%include('header', title=' / 书架')
<main class="account-page">
  <h1>我的书架</h1>
  <p class="account-muted">阅读时可将文档加入书架。书架仅对当前账号显示，清空历史不会移除书架中的书。</p>
  %if not records['items']:
  <p class="account-empty">书架还是空的。打开文档后，点击“加入书架”。</p>
  %end
  <ul class="account-bookshelf">
  %for item in records['items']:
    <li class="account-card">
      <div class="account-book-format" aria-hidden="true">{{item.get('format', '').upper() or '文档'}}</div>
      <h2>{{item.get('title') or '未命名文档'}}</h2>
      <p class="account-muted">加入于 <time data-utc="{{item['added_at']}}">{{item['added_at']}}</time></p>
      %if item.get('progress') is not None:
      <p>已读 {{round(item['progress'])}}%</p>
      %else:
      <p class="account-muted">暂无同步进度</p>
      %end
      <div class="account-actions"><a class="account-button" href="{{app_url('/reader?document_id=' + item['document_id'])}}">继续阅读</a><form method="post" action="{{app_url('/bookshelf/remove')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><button type="submit" class="account-secondary">移出书架</button></form></div>
    </li>
  %end
  </ul>
  %if next_url:
  <p><a class="account-button account-secondary" href="{{next_url}}">下一页</a></p>
  %end
</main>
%include('footer')
