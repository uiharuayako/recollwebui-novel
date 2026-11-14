%import re
%active_filters = sum(bool(query.get(key)) for key in ('after', 'before')) + int(query.get('dir', '<all>') not in ('', '<all>'))
<section id="searchbox" aria-label="文库搜索">
<form id="library-search" action="{{app_url('/search')}}" method="post" role="search">
  <input type="hidden" name="csrf" value="{{account_csrf()}}">
  <label class="app-sr-only" for="library-query">搜索文档全文</label>
  <div class="app-search-field">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>
    <input id="library-query" type="search" name="query" value="{{query['query']}}" placeholder="搜索书名、作者或正文中的一句话…" autocomplete="off" enterkeyhint="search" autofocus>
    <kbd class="app-search-shortcut" title="按 / 聚焦搜索框">/</kbd>
    <details class="app-search-help">
      <summary role="button" aria-label="搜索语法帮助" title="搜索语法帮助" aria-controls="search-syntax-help"><span aria-hidden="true">?</span></summary>
      <div id="search-syntax-help" class="app-search-help-panel" role="region" aria-labelledby="search-syntax-title">
        <h2 id="search-syntax-title">搜索语法</h2>
        <p>在搜索框中直接输入以下写法：</p>
        <dl class="app-search-syntax">
          <dt><code>苹果 香蕉</code></dt><dd>同时包含两个词</dd>
          <dt><code>苹果 OR 香蕉</code></dt><dd>包含任意一个词</dd>
          <dt><code>"机器学习"</code></dt><dd>按完整短语匹配</dd>
          <dt><code>苹果 -香蕉</code></dt><dd>包含苹果，排除香蕉</dd>
          <dt><code>title:三体</code></dt><dd>只搜索标题字段</dd>
          <dt><code>comput*</code></dt><dd>匹配以 comput 开头的词</dd>
        </dl>
        <p><code>*</code> 匹配任意多个字符，<code>?</code> 匹配一个字符，<code>[abc]</code> 匹配其中一个字符。</p>
        <p class="app-search-help-note">通配符按索引词匹配，不能跨句匹配正文。不支持 <code>\d+</code>、<code>(甲|乙)</code> 等完整正则表达式。</p>
      </div>
    </details>
    <button type="submit" class="app-button app-search-submit">搜索文库<span aria-hidden="true"> →</span></button>
  </div>
  <div class="app-search-options">
    <details class="app-search-filters" {{'open' if active_filters else ''}}>
      <summary><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></svg>筛选与排序<span class="app-filter-count" {{'' if active_filters else 'hidden'}}>{{active_filters}}</span><span class="app-details-chevron" aria-hidden="true">⌄</span></summary>
      <div class="app-filter-fields">
        <label class="app-filter-folder" for="folders">所在文件夹<select id="folders" name="dir">
        %for d in sorted(dirs, key=str.lower):
          <option {{'selected' if d == query['dir'] else ''}} value="{{d}}">{{'全部文件夹' if d == '<all>' else '　' * d.count('/') + re.sub('.+/', '', d)}}</option>
        %end
        </select></label>
        <fieldset class="app-filter-dates"><legend>修改日期 <small>年 / 年-月 / 年-月-日</small></legend><div class="date-range"><label class="app-sr-only" for="search-after">起始日期</label><input id="search-after" name="after" value="{{query['after']}}" placeholder="起始日期" autocomplete="off"><span>至</span><label class="app-sr-only" for="search-before">截止日期</label><input id="search-before" name="before" value="{{query['before']}}" placeholder="截止日期" autocomplete="off"></div></fieldset>
        <label for="search-sort">排序方式<select id="search-sort" name="sort">
        %sort_labels = {'relevancyrating': '相关程度', 'mtime': '修改时间', 'url': '文件路径', 'filename': '文件名', 'fbytes': '文件大小', 'author': '作者'}
        %for s in sorts:
          <option {{'selected' if query['sort'] == s[0] else ''}} value="{{s[0]}}">{{sort_labels.get(s[0], s[1])}}</option>
        %end
        </select></label>
        <label for="search-order">排列顺序<select id="search-order" name="ascending"><option value="0" {{'selected' if int(query['ascending']) != 1 else ''}}>降序</option><option value="1" {{'selected' if int(query['ascending']) == 1 else ''}}>升序</option></select></label>
      </div>
    </details>
    <div class="app-search-links"><a href="{{app_url('/')}}">重置搜索</a>
    %if not config['rclc_nosettings']:
      <a href="{{app_url('/settings')}}">搜索设置</a>
    %end
    </div>
  </div>
  <input type="hidden" name="page" value="1">
</form>
</section>
