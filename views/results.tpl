%include('header', title=": " + query['query'] + " (" + str(nres) + ")")
<main id="main-content" class="app-main app-results-page" tabindex="-1">
<div class="app-results-heading"><p class="app-eyebrow">在文字中发现</p><h1>搜索文库</h1></div>
%include('search', query=query, dirs=dirs, sorts=sorts, config=config)
%if notice:
<p class="account-notice" role="status">{{notice}}</p>
%end
<div id="status">
    <div id="found">找到 <b>{{nres}}</b> 个结果<span class="app-query-summary"> · {{query['query'] or '按筛选条件搜索'}}</span><small class="gray">{{"%.3f" % time.total_seconds()}} 秒</small></div>
    %if len(res) > 0 and not config['rclc_nojsoncsv']:
    <details id="downloads" class="app-export"><summary>导出结果 ↓</summary><div><a href="{{app_url('/json')}}?{{query_string}}&page=0">JSON</a><a href="{{app_url('/csv')}}?{{query_string}}&page=0">CSV</a></div></details>
    %end
</div>
%include('pages', query=query, config=config, nres=nres)
<div id="results">
%for i in range(0, len(res)):
    %include('result', d=res[i], i=i, query=query, config=config, query_string=query_string)
%end
%if not res:
<section class="app-no-results" aria-labelledby="no-results-title"><span class="app-empty-symbol" aria-hidden="true">⌕</span><h2 id="no-results-title">{{'这一页没有更多结果' if nres else '还没有找到匹配的文档'}}</h2><p>{{'试着返回前面的结果页，或重新搜索。' if nres else '试试更短的关键词，检查拼写，或放宽文件夹和日期范围。'}}</p><a class="app-button app-button-secondary" href="{{app_url('/')}}">重新搜索</a></section>
%end
</div>
%include('pages', query=query, config=config, nres=nres)
</main>
%include('footer')
