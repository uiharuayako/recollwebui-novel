%include('header', title=' / 历史记录')
<main class="account-page">
  <h1>历史记录</h1>
  <nav class="account-tabs" aria-label="历史分类"><a href="{{app_url('/history')}}" aria-current="{{'page' if kind == 'search' else 'false'}}">搜索历史</a><a href="{{app_url('/history?tab=reading')}}" aria-current="{{'page' if kind == 'reading' else 'false'}}">最近阅读</a><a href="{{app_url('/bookshelf')}}">书架</a></nav>
  %if kind == 'search':
  <p class="account-muted">仅记录成功的主动搜索。重新搜索会创建新记录，结果可能随文档库更新而改变。</p>
  <form method="post" action="{{app_url('/history/search/delete')}}" data-confirm="清空当前账号全部搜索历史？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><button type="submit" class="account-danger">清空搜索历史</button></form>
  %else:
  <p class="account-muted">移除最近阅读会保留阅读进度。清空全部阅读数据同时清除进度和目录续读，书架继续保留。</p>
  <div class="account-actions">
    <form method="post" action="{{app_url('/history/reading/clear')}}" data-confirm="清空最近阅读列表？阅读进度仍会保留。"><input type="hidden" name="csrf" value="{{account_csrf()}}"><button class="account-secondary" type="submit">清空最近阅读</button></form>
    <form method="post" action="{{app_url('/history/reading/clear')}}" data-confirm="清空全部阅读数据，包括阅读进度和目录续读？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="include_progress" value="1"><button class="account-danger" type="submit">清空全部阅读数据</button></form>
  </div>
  %end
  %if not records['items']:
  <p class="account-empty">暂无记录。</p>
  %end
  <ul class="account-history-list">
  %for item in records['items']:
    <li class="account-card">
    %if kind == 'search':
      %query = item.get('query', {})
      <h2>{{query.get('query') or '按筛选条件搜索'}}</h2>
      <p class="account-muted"><time data-utc="{{item.get('completed_at', item.get('created_at', ''))}}">{{item.get('completed_at', '')}}</time> · {{item.get('result_count', 0)}} 个结果</p>
      <dl class="account-filters">
      %for key, label in [('dir', '目录'), ('after', '起始日期'), ('before', '截止日期'), ('sort', '排序')]:
        %if query.get(key):
        <div><dt>{{label}}</dt><dd>{{query[key]}}</dd></div>
        %end
      %end
      </dl>
      <div class="account-actions">
        <form method="post" action="{{app_url('/search')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}">
          <input type="hidden" name="replay_id" value="{{item['id']}}">
          %for key in ('query', 'dir', 'after', 'before', 'sort', 'ascending', 'stem', 'collapsedups'):
            %if key in query:
            <input type="hidden" name="{{key}}" value="{{query[key]}}">
            %end
          %end
          <button type="submit">重新搜索</button>
        </form>
        <form method="post" action="{{app_url('/history/search/delete')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="id" value="{{item['id']}}"><button class="account-secondary" type="submit">删除</button></form>
      </div>
    %else:
      <h2>{{item.get('title_snapshot') or item.get('title') or '未命名文档'}}</h2>
      <p class="account-muted">{{item.get('format', '').upper()}} · <time data-utc="{{item['last_opened_at']}}">{{item['last_opened_at']}}</time>{{' · 已读 ' + str(round(item['progress'])) + '%' if item.get('progress') is not None else ''}}</p>
      <div class="account-actions">
        <a class="account-button" href="{{app_url('/reader?document_id=' + item['document_id'])}}">继续阅读</a>
        <form method="post" action="{{app_url('/history/reading/delete')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><button class="account-secondary" type="submit">移除最近阅读</button></form>
        <form method="post" action="{{app_url('/history/progress/delete')}}" data-confirm="清除这本文档的阅读进度和目录续读？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><button class="account-secondary" type="submit">清除进度</button></form>
      </div>
    %end
    </li>
  %end
  </ul>
  %if next_url:
  <p><a class="account-button account-secondary" href="{{next_url}}">下一页</a></p>
  %end
  <p><a href="{{app_url('/account')}}">管理记录开关与阅读偏好</a></p>
</main>
%include('footer')
