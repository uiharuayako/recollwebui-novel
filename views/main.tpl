%include("header", title="")
%from urllib.parse import urlencode
%home_return_query = urlencode({'return_to': app_url('/')})
<main id="main-content" class="app-main app-home" tabindex="-1">
  <section class="app-hero" aria-labelledby="home-title">
    <p class="app-eyebrow"><span aria-hidden="true"></span> 你的个人阅读空间</p>
    <h1 id="home-title">从这里，<em>读下去。</em></h1>
    <p class="app-hero-description">接着上次的那一页，也发现下一本想读的书。</p>
  </section>
  %include("search", query=query, dirs=dirs, sorts=sorts, config=config)
  <div class="app-search-tips"><span>搜索小提示</span><span>完整短语加英文引号 <code>"关键词"</code></span><span>按 <kbd>/</kbd> 快速搜索</span></div>
  <section class="app-reading-section" aria-labelledby="continue-heading">
    <div class="app-section-heading"><div><p class="app-eyebrow">回到书页之间</p><h2 id="continue-heading">继续阅读</h2></div><a class="app-text-link" href="{{app_url('/history?tab=reading')}}">全部阅读记录 <span aria-hidden="true">↗</span></a></div>
    %if recent_readings:
    <div class="app-continue-grid">
      %for index, item in enumerate(recent_readings):
      %progress = item.get('progress')
      <a class="app-continue-card" href="{{app_url('/reader')}}?document_id={{item['document_id']}}">
        <div class="app-book-spine app-book-tone-{{index % 4}}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 4h13a1 1 0 0 1 1 1v16H6a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2Zm0 0v12m-2 2a2 2 0 0 1 2-2h14M9 8h6m-6 4h4"/></svg></div>
        <div class="app-continue-content"><div class="app-document-meta"><span class="app-format-tag">{{item.get('format', '').upper() or '文档'}}</span><span class="app-folder-name" title="{{item.get('folder_name', '')}}">{{item.get('folder_name', '')}}</span></div><h3>{{item.get('title_snapshot') or item.get('title') or '未命名文档'}}</h3><p class="app-reading-date"><time data-utc="{{item['last_opened_at']}}">{{item['last_opened_at']}}</time></p>
        %if progress is not None:
          <div class="app-reading-progress"><progress max="100" value="{{progress}}" aria-label="已读 {{round(progress)}}%"></progress><span>{{round(progress)}}%</span></div>
        %else:
          <p class="app-reading-date">最近打开</p>
        %end
          <span class="app-continue-action">继续阅读 <span aria-hidden="true">→</span></span>
        </div>
      </a>
      %end
    </div>
    %else:
    <div class="app-welcome-state"><div class="app-welcome-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"/></svg></div><div><h3>下一段阅读，从一次发现开始</h3><p>搜索文库并打开文档，最近阅读会出现在这里。</p></div><a class="app-button app-button-secondary" href="{{app_url('/bookshelf')}}">前往我的书架 →</a></div>
    %end
  </section>
  %if recommendation_notice:
  <p class="account-notice" role="status">{{recommendation_notice}}</p>
  %end
  <section class="app-recommendation-section" id="search-recommendations" aria-labelledby="search-recommendations-heading">
    <div class="app-section-heading"><div><p class="app-eyebrow">沿着搜索，发现好书</p><h2 id="search-recommendations-heading">搜索推荐 <span class="app-section-count" aria-label="{{recommendation_count}} 条候选">{{recommendation_count}}</span></h2></div><form method="get" action="{{app_url('/')}}#search-recommendations" class="app-recommendation-refresh-form"><button type="submit" name="refresh" value="{{recommendation_refresh}}" class="app-text-link app-recommendation-refresh" aria-label="换一批搜索推荐"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5m-4-4a8 8 0 0 1 13-2l2 2M4 16l2 2a8 8 0 0 0 13-2"/></svg>换一批</button></form></div>
    <p class="app-section-description">从最近的搜索中留存，每次随机相遇。最多保留 60 条，也可以自己挑选。</p>
    %if search_recommendations:
    <div class="app-recommendation-grid">
      %for index, item in enumerate(search_recommendations):
      <article class="app-recommendation-card" data-document-id="{{item['document_id']}}">
        <div class="app-recommendation-meta"><span class="app-format-tag">{{item['format'].upper()}}</span><span class="app-folder-name" title="{{item.get('folder_name', '')}}">{{item.get('folder_name', '')}}</span><span class="app-recommendation-origin">来自搜索</span></div>
        <h3><a href="{{app_url('/reader')}}?document_id={{item['document_id']}}&{{home_return_query}}">{{item['title'] or '未命名文档'}}</a></h3>
        <p class="app-recommendation-source" title="{{item.get('source_query') or '手动加入'}}">{{'“' + item['source_query'] + '”' if item.get('source_query') else '手动加入，留给下次阅读'}}</p>
        <div class="app-recommendation-actions"><a href="{{app_url('/reader')}}?document_id={{item['document_id']}}&{{home_return_query}}">开始阅读 <span aria-hidden="true">→</span></a><form method="post" action="{{app_url('/recommendations/remove')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><input type="hidden" name="next" value="{{app_url('/')}}#search-recommendations"><button type="submit" class="app-recommendation-remove" aria-label="移出搜索推荐：{{item['title']}}" title="移出搜索推荐"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M7 17 17 7"/></svg></button></form></div>
      </article>
      %end
    </div>
    %else:
    <div class="app-recommendation-empty"><span class="app-discovery-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M10 7v6M7 10h6"/></svg></span><div><h3>让每次搜索，都留下新的发现</h3><p>搜索后会自动留存少量结果，也可以在结果中选择「加入搜索推荐」。</p></div><a class="app-text-link" href="#library-query">开始搜索 <span aria-hidden="true">↑</span></a></div>
    %end
  </section>
  <section class="app-recommendation-section app-daily-section" id="daily-recommendations" aria-labelledby="daily-recommendations-heading">
    <div class="app-section-heading"><div><p class="app-eyebrow">给阅读一点偶然</p><h2 id="daily-recommendations-heading">每日推荐</h2></div><span class="app-daily-date"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v15H5zM8 3v4m8-4v4M5 10h14"/></svg><time datetime="{{daily_date}}">{{daily_date}}</time></span></div>
    <p class="app-section-description">从整个文库随机挑选，每天更新，不受搜索偏好影响。</p>
    %if daily_recommendations:
    <div class="app-recommendation-grid">
      %for index, item in enumerate(daily_recommendations):
      <article class="app-recommendation-card" data-document-id="{{item['document_id']}}">
        <div class="app-recommendation-meta"><span class="app-format-tag">{{item['format'].upper()}}</span><span class="app-folder-name" title="{{item.get('folder_name', '')}}">{{item.get('folder_name', '')}}</span><span class="app-daily-number">今日偶遇 · {{'%02d' % (index + 1)}}</span></div>
        <h3><a href="{{app_url('/reader')}}?document_id={{item['document_id']}}&{{home_return_query}}">{{item['title'] or '未命名文档'}}</a></h3>
        <div class="app-recommendation-actions"><a href="{{app_url('/reader')}}?document_id={{item['document_id']}}&{{home_return_query}}">开始阅读 <span aria-hidden="true">→</span></a><form method="post" action="{{app_url('/recommendations/remove' if item.get('on_recommendation') else '/recommendations/add')}}" ><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{item['document_id']}}"><input type="hidden" name="handle" value="{{item['recommendation_handle']}}"><input type="hidden" name="source_query" value=""><input type="hidden" name="next" value="{{app_url('/')}}#daily-recommendations"><button type="submit" class="app-recommendation-toggle" aria-pressed="{{'true' if item.get('on_recommendation') else 'false'}}">{{'移出搜索推荐' if item.get('on_recommendation') else '加入搜索推荐'}}</button></form></div>
      </article>
      %end
    </div>
    %else:
    <div class="app-recommendation-empty"><span class="app-discovery-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"/></svg></span><div><h3>等待下一本好书</h3><p>文库中有可阅读的文档后，每日推荐会出现在这里。</p></div></div>
    %end
  </section>
  <div class="app-home-links"><a href="{{app_url('/bookshelf')}}"><span class="app-home-link-icon" aria-hidden="true">▤</span><div><strong>留给下一次阅读</strong><p>把想读的文档放进我的书架</p></div><span aria-hidden="true">→</span></a><a href="{{app_url('/account')}}"><span class="app-home-link-icon" aria-hidden="true">Aa</span><div><strong>找到舒服的阅读方式</strong><p>管理阅读偏好与进度同步</p></div><span aria-hidden="true">→</span></a></div>
  <footer class="app-home-footer">RECOLL <span>·</span> 让收藏的文字，重新被阅读</footer>
</main>
%include("footer")
