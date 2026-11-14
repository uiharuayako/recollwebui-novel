%include('header', title=' / 书架')
%count_unit = '项' if any(item.get('format') == 'folder' for item in records['items']) else '本'
<main id="main-content" tabindex="-1" class="account-page" data-library data-library-count-unit="{{count_unit}}">
  <header class="account-heading">
    <div>
      <p class="account-eyebrow">YOUR READING SPACE</p>
      <h1>我的书架</h1>
      <p class="account-muted">收藏想读的文档和文件夹，随时接着上次的进度。</p>
    </div>
    <a class="account-button account-secondary" href="{{app_url('/')}}">查找文档 <span aria-hidden="true">↗</span></a>
  </header>
  %if not records['items']:
  <section class="account-empty">
    <div class="account-empty-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 4h6a4 4 0 0 1 3 1.5A4 4 0 0 1 15 4h6v16h-6a4 4 0 0 0-3 1 4 4 0 0 0-3-1H3zM12 6v15"/></svg></div>
    <h2>从第一本文档开始</h2>
    <p>书架还是空的。搜索并打开文档，在阅读器中收藏单本或整个文件夹，下次就能在这里找到。</p>
    <a class="account-button" href="{{app_url('/')}}">去搜索文档</a>
  </section>
  %else:
  <div class="library-toolbar" hidden>
    <div class="library-filter"><label for="library-filter">筛选本页</label><input type="search" id="library-filter" placeholder="输入文档或文件夹标题…" autocomplete="off" aria-controls="library-books" aria-describedby="library-count"></div>
    <div class="library-toolbar-side">
      <span id="library-count" class="library-count" role="status" aria-live="polite">本页 {{len(records['items'])}} {{count_unit}}</span>
      <div class="library-view" role="group" aria-label="书架显示方式">
        <button type="button" data-library-view="grid" aria-label="网格视图" title="网格视图" aria-pressed="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></button>
        <button type="button" data-library-view="list" aria-label="列表视图" title="列表视图" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M9 5h12M9 12h12M9 19h12M3 5h2M3 12h2M3 19h2"/></svg></button>
      </div>
    </div>
  </div>
  <section class="account-empty library-no-results" id="library-no-results" hidden>
    <h2>本页没有匹配的收藏</h2>
    <p>试试其他标题关键词，或清除筛选查看本页全部收藏。</p>
    <button type="button" class="account-secondary" data-library-clear>清除筛选</button>
  </section>
  %end
  <ul class="account-bookshelf" id="library-books" data-view="grid">
  %for item in records['items']:
    %is_folder = item.get('format') == 'folder'
    %item_title = item.get('title') or ('未命名文件夹' if is_folder else '未命名文档')
    %reader_url = app_url(('/reader?mode=folder&folder_id=' if is_folder else '/reader?document_id=') + item['document_id'])
    <li class="account-card" data-book-title="{{item_title}}">
      <div class="account-book-cover" data-format="{{item.get('format', '').lower()}}">
        %if is_folder:
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><path d="M3 7V4h6l3 3h9v13H3z"/></svg>
        %else:
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><path d="M3 4h6a4 4 0 0 1 3 1.5A4 4 0 0 1 15 4h6v16h-6a4 4 0 0 0-3 1 4 4 0 0 0-3-1H3zM12 6v15"/></svg>
        %end
      </div>
      <div class="account-book-body">
        <div class="app-document-meta"><span class="app-format-tag">{{'文件夹' if is_folder else item.get('format', '').upper() or '文档'}}</span><span class="app-folder-name" title="{{item.get('folder_name', '')}}">{{item.get('folder_name', '')}}</span></div>
        <h2><a href="{{reader_url}}">{{item_title}}</a></h2>
        <p class="account-muted">加入于 <time data-utc="{{item['added_at']}}">{{item['added_at']}}</time></p>
        %if is_folder and item.get('resume_title'):
        <p class="account-muted">上次阅读：{{item['resume_title']}}</p>
        %end
        %if item.get('progress') is not None:
        %progress_label = '上次阅读的文件进度' if is_folder else ('已读完' if item['progress'] >= 100 else '阅读进度')
        %progress_aria = ('上次阅读的文件已读 ' if is_folder else '已读 ') + str(round(item['progress'])) + '%'
        <div class="account-book-progress"><span>{{progress_label}}</span><span>{{round(item['progress'])}}%</span><progress max="100" value="{{item['progress']}}" aria-label="{{progress_aria}}"></progress></div>
        %else:
        <div class="account-book-progress"><span>暂无同步进度</span></div>
        %end
        <div class="account-actions"><a class="account-button" href="{{reader_url}}">继续阅读 <span aria-hidden="true">→</span></a><a class="account-button account-secondary" href="{{app_url('/shares/new')}}?document_id={{item['document_id']}}">分享</a><form method="post" action="{{app_url('/bookshelf/remove')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><button type="submit" class="account-secondary">移出书架</button></form></div>
      </div>
    </li>
  %end
  </ul>
  %if next_url:
  <nav class="account-pagination" aria-label="书架分页"><a class="account-button account-secondary" href="{{next_url}}">下一页 <span aria-hidden="true">→</span></a></nav>
  %end
  <p class="account-muted account-footnote">按加入时间排序 · 书架仅对当前账号显示，清空历史不会移除书架中的收藏。</p>
</main>
<script defer src="{{app_url('/static/library.js')}}?v=2"></script>
%include('footer')
