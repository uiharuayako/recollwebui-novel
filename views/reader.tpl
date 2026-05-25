%include("header", title=" Reader")
<div id="reader-app" class="reader-shell" data-mode="{{mode}}" data-query="{{query_string}}">
  <div class="reader-toolbar">
    <div class="reader-toolbar-left">
      <a class="reader-button" href="./results?{{query_string}}">返回结果</a>
      <button class="reader-button" id="reader-prev-book" type="button">上一册</button>
      <button class="reader-button" id="reader-next-book" type="button">下一册</button>
    </div>
    <div class="reader-toolbar-center">
      <div class="reader-title" id="reader-title">加载中</div>
      <div class="reader-meta" id="reader-meta"></div>
    </div>
    <div class="reader-toolbar-right">
      <button class="reader-button" id="reader-toggle-toc" type="button">目录</button>
      <label class="reader-label">TXT 规则</label>
      <input class="reader-input" id="reader-parser-regex" placeholder="可选" />
      <button class="reader-button" id="reader-save-regex" type="button">保存</button>
    </div>
  </div>
  <div class="reader-body">
    <aside class="reader-sidebar" id="reader-sidebar">
      <div class="reader-sidebar-header">书单</div>
      <div id="reader-booklist" class="reader-booklist"></div>
    </aside>
    <main class="reader-main">
      <div id="page-area" class="reader-stage">
        <div class="reader-loading">正在加载阅读器…</div>
      </div>
    </main>
  </div>
</div>
<script>
window.RECOLL_READER = {
  mode: "{{mode}}",
  queryString: "{{query_string}}",
};
</script>
<script type="module" src="/static/reader/kookit.bundle.js"></script>
<script type="module" src="/static/reader/app.js"></script>
%include("footer")
