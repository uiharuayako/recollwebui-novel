%include("header", title=" Reader")
<div id="reader-app" class="reader-shell" data-mode="{{mode}}" data-query="{{query_string}}">
  <div class="reader-toolbar" id="reader-toolbar">
    <div class="reader-toolbar-left">
      <a class="reader-button" href="./results?{{results_query_string}}">返回结果</a>
      <button class="reader-button" id="reader-prev-page" type="button">上一页</button>
      <button class="reader-button" id="reader-next-page" type="button">下一页</button>
      <button class="reader-button" id="reader-prev-book" type="button">上一册</button>
      <button class="reader-button" id="reader-next-book" type="button">下一册</button>
    </div>
    <div class="reader-toolbar-center">
      <div class="reader-title" id="reader-title">加载中</div>
      <div class="reader-meta" id="reader-meta"></div>
    </div>
    <div class="reader-toolbar-right">
      <button class="reader-button" id="reader-toggle-toc" type="button">目录</button>
      <button class="reader-button" id="reader-toggle-toolbar" type="button">收起顶栏</button>
      <div class="reader-font-controls" role="group" aria-label="阅读字号">
        <span class="reader-font-label">字号</span>
        <button class="reader-button reader-font-button" id="reader-font-decrease" type="button" aria-label="减小阅读字号">A-</button>
        <output class="reader-font-value" id="reader-font-size" for="reader-font-decrease reader-font-increase">20px</output>
        <button class="reader-button reader-font-button" id="reader-font-increase" type="button" aria-label="增大阅读字号">A+</button>
      </div>
      <label class="reader-label">TXT 规则</label>
      <input class="reader-input" id="reader-parser-regex" placeholder="可选" />
      <button class="reader-button" id="reader-save-regex" type="button">保存</button>
    </div>
  </div>
  <button class="reader-toolbar-peek" id="reader-show-toolbar" type="button" aria-label="展开顶栏">展开</button>
  <div class="reader-body">
    <aside class="reader-sidebar" id="reader-sidebar">
      <div class="reader-sidebar-header">书单</div>
      <div id="reader-booklist" class="reader-booklist"></div>
    </aside>
    <button class="reader-sidebar-backdrop" id="reader-sidebar-backdrop" type="button" aria-label="关闭书单"></button>
    <main class="reader-main">
      <div class="reader-stage-shell" id="reader-stage-shell">
        <div id="page-area" class="reader-stage">
          <div class="reader-loading">正在加载阅读器…</div>
        </div>
      </div>
    </main>
  </div>
</div>
<script>
window.RECOLL_READER = {
  mode: "{{mode}}",
  queryString: "{{query_string}}",
  resultsQueryString: "{{results_query_string}}",
};
</script>
<script type="module" src="/static/reader/kookit.bundle.js"></script>
<script type="module" src="/static/reader/app.js"></script>
%include("footer")
