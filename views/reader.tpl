%include("header", title=" Reader", shared=get('shared', False))
<link rel="stylesheet" href="{{app_url('/static/reader/reader-ui.css')}}?v=reader-ui-4">
<div id="reader-app" class="reader-shell" data-mode="{{mode}}" data-query="{{query_string}}" data-theme="sepia">
  <header class="reader-toolbar" id="reader-toolbar" aria-label="阅读工具栏">
    <div class="reader-toolbar-left">
      <a class="reader-button reader-icon-button" href="{{results_url}}" title="{{'分享内容' if get('shared', False) else '返回搜索结果'}}" aria-label="{{'分享内容' if get('shared', False) else '返回搜索结果'}}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6M8 12h12"/></svg></a>
      <span class="reader-toolbar-divider" aria-hidden="true"></span>
      <button class="reader-button reader-icon-button" id="reader-toggle-toc" type="button" title="目录、书单与搜索（T）" aria-label="打开或收起阅读导航" aria-controls="reader-sidebar" aria-expanded="true" aria-keyshortcuts="T"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m4-11h4m-4 4h4"/></svg></button>
    </div>
    <div class="reader-toolbar-center">
      <div class="reader-meta reader-document-meta" id="reader-meta"></div>
      <div class="reader-title" id="reader-title">正在打开文档</div>
    </div>
    <div class="reader-toolbar-right">
      <button class="reader-button reader-focus-button" id="reader-toggle-toolbar" type="button" title="专注阅读：收起工具栏（H）" aria-label="收起阅读工具栏" aria-keyshortcuts="H"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 16V5h16v11M8 21l4-4 4 4M4 10h16"/></svg><span>专注</span></button>
      <button class="reader-button reader-icon-button reader-desktop-action" id="reader-fullscreen" data-reader-fullscreen type="button" title="全屏阅读（F）" aria-label="全屏阅读" aria-pressed="false" aria-keyshortcuts="F"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg></button>
      <button class="reader-button reader-icon-button reader-desktop-action" id="reader-help" data-reader-help type="button" title="阅读帮助与快捷键（?）" aria-label="阅读帮助与快捷键" aria-haspopup="dialog" aria-controls="reader-help-dialog" aria-keyshortcuts="Shift+/"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 8.5a2.5 2.5 0 0 1 5 .5c0 1.5-2.5 2-2.5 4M12 16h.01"/></svg></button>
      <button class="reader-button reader-icon-button" id="reader-toggle-settings" type="button" title="阅读设置" aria-label="阅读设置" aria-controls="reader-settings-panel" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></svg></button>
    </div>
  </header>
  <button class="reader-toolbar-peek" id="reader-show-toolbar" type="button" aria-label="展开阅读工具栏" title="展开阅读工具栏"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg><span>工具栏</span></button>
  <div class="reader-body">
    <aside class="reader-sidebar" id="reader-sidebar" aria-label="阅读导航">
      <div class="reader-sidebar-heading"><span class="reader-sidebar-header">阅读导航</span><span class="reader-sidebar-caption">CONTENTS</span><button id="reader-close-sidebar" class="reader-button reader-icon-button" type="button" title="关闭导航" aria-label="关闭阅读导航"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>
      <div class="reader-sidebar-tabs" role="tablist" aria-label="阅读导航分类">
        <button id="reader-tab-toc" class="reader-tab is-active" type="button" role="tab" aria-selected="true" aria-controls="reader-panel-toc">目录</button>
        <button id="reader-tab-books" class="reader-tab" type="button" role="tab" aria-selected="false" aria-controls="reader-panel-books" tabindex="-1">书单</button>
        <button id="reader-tab-search" class="reader-tab" type="button" role="tab" aria-selected="false" aria-controls="reader-panel-search" tabindex="-1">搜索</button>
      </div>
      <section id="reader-panel-toc" class="reader-sidebar-panel" role="tabpanel" aria-labelledby="reader-tab-toc">
        <nav id="reader-toc-list" class="reader-toc-list" aria-label="文档目录"><p class="reader-empty-state">正在读取目录…</p></nav>
      </section>
      <section id="reader-panel-books" class="reader-sidebar-panel" role="tabpanel" aria-labelledby="reader-tab-books" hidden>
        <div id="reader-booklist" class="reader-booklist"></div>
        <div class="reader-book-navigation" role="group" aria-label="切换文档">
          <button class="reader-button" id="reader-prev-book" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>上一册</button>
          <button class="reader-button" id="reader-next-book" type="button">下一册<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg></button>
        </div>
      </section>
      <section id="reader-panel-search" class="reader-sidebar-panel" role="tabpanel" aria-labelledby="reader-tab-search" hidden>
        <div class="reader-search-form" role="search" aria-label="在当前文档中搜索">
          <label class="reader-sr-only" for="reader-search-input">搜索书中内容</label>
          <input id="reader-search-input" class="reader-input" type="search" placeholder="搜索书中内容" autocomplete="off" enterkeyhint="search">
          <button id="reader-search-submit" class="reader-button reader-icon-button" type="button" title="搜索" aria-label="搜索书中内容"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg></button>
        </div>
        <p id="reader-search-status" class="reader-search-status" role="status" aria-live="polite">输入关键词，在当前文档中查找。</p>
        <div id="reader-search-results" class="reader-search-results" aria-label="书内搜索结果"></div>
      </section>
    </aside>
    <button class="reader-sidebar-backdrop" id="reader-sidebar-backdrop" type="button" aria-label="关闭阅读导航"></button>
    <main class="reader-main" aria-label="阅读正文">
      <div id="reader-return-banner" class="reader-return-banner" hidden>
        <span>正在查看跳转内容</span>
        <button id="reader-return-position" class="reader-button reader-button-accent" type="button" hidden>返回阅读处</button>
        <button id="reader-keep-position" class="reader-button" type="button" hidden>从此处继续</button>
      </div>
      <div class="reader-pdf-controls" id="reader-pdf-controls" role="group" aria-label="PDF 显示设置" hidden>
        <button id="reader-pdf-zoom-out" class="reader-button reader-icon-button" type="button" aria-label="缩小 PDF" title="缩小"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button>
        <output id="reader-pdf-zoom-value" class="reader-pdf-zoom-value" aria-label="当前缩放比例">100%</output>
        <button id="reader-pdf-zoom-in" class="reader-button reader-icon-button" type="button" aria-label="放大 PDF" title="放大"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg></button>
        <span class="reader-toolbar-divider" aria-hidden="true"></span>
        <button id="reader-pdf-fit" class="reader-button" type="button">适合宽度</button>
        <button id="reader-pdf-rotate" class="reader-button reader-icon-button" type="button" aria-label="顺时针旋转 PDF" title="旋转"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 9a8 8 0 1 0 0 6M20 4v5h-5"/></svg></button>
      </div>
      <div class="reader-stage-shell" id="reader-stage-shell">
        <div id="page-area" class="reader-stage" tabindex="0" aria-label="文档内容，向下滚动继续阅读"><div class="reader-loading"><span class="reader-loading-mark" aria-hidden="true"></span>正在加载阅读器…</div></div>
      </div>
      <footer class="reader-progress-footer" aria-label="阅读进度">
        <button class="reader-button reader-icon-button reader-progress-step" id="reader-progress-prev-book" type="button" title="书单：上一册" aria-label="书单：上一册" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6-6 6 6M12 4v16"/></svg></button>
        <button class="reader-button reader-icon-button reader-progress-step" id="reader-prev-section" type="button" title="目录：上一文本区块" aria-label="目录：上一文本区块" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6-6 6 6 6M4 12h16"/></svg></button>
        <div class="reader-progress-track">
          <label class="reader-sr-only" for="reader-progress-range">阅读进度</label>
          <input id="reader-progress-range" class="reader-progress-range" type="range" min="0" max="1000" step="1" value="0" aria-label="阅读进度" disabled>
          <output id="reader-progress-label" class="reader-progress-label" for="reader-progress-range">0%</output>
        </div>
        <button class="reader-button reader-icon-button reader-progress-step" id="reader-next-section" type="button" title="目录：下一文本区块" aria-label="目录：下一文本区块" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6 6 6-6 6M4 12h16"/></svg></button>
        <button class="reader-button reader-icon-button reader-progress-step" id="reader-progress-next-book" type="button" title="书单：下一册" aria-label="书单：下一册" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6 6 6-6M12 4v16"/></svg></button>
      </footer>
    </main>
  </div>
  <button id="reader-settings-backdrop" class="reader-settings-backdrop" type="button" aria-label="关闭阅读设置" tabindex="-1" hidden></button>
  <aside id="reader-settings-panel" class="reader-settings-panel" role="dialog" aria-modal="true" aria-labelledby="reader-settings-title" hidden>
    <div class="reader-panel-heading"><div><span class="reader-eyebrow">让阅读更舒适</span><h2 id="reader-settings-title">阅读设置</h2></div><button id="reader-close-settings" class="reader-button reader-icon-button" type="button" title="关闭设置" aria-label="关闭阅读设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>
    <div class="reader-settings-content">
      <div class="reader-setting"><label for="reader-theme">阅读主题</label><select id="reader-theme"><option value="light">明亮</option><option value="sepia" selected>暖纸</option><option value="dark">深色</option><option value="auto">跟随系统</option></select></div>
      <div data-reader-text-setting class="reader-setting"><label for="reader-font-family">正文字体</label><select id="reader-font-family"><option value="serif" selected>衬线 · 宋体</option><option value="sans">无衬线 · 黑体</option><option value="system">系统字体</option></select></div>
      <div data-reader-text-setting class="reader-setting"><span id="reader-font-controls-label">文字大小</span><div class="reader-font-controls" role="group" aria-labelledby="reader-font-controls-label"><button class="reader-button reader-font-button" id="reader-font-decrease" type="button" aria-label="减小阅读字号">A−</button><output class="reader-font-value" id="reader-font-size" for="reader-font-decrease reader-font-increase">20px</output><button class="reader-button reader-font-button" id="reader-font-increase" type="button" aria-label="增大阅读字号">A+</button></div></div>
      <div data-reader-text-setting class="reader-setting reader-setting-range"><label for="reader-line-height">行距</label><output id="reader-line-height-value" for="reader-line-height">1.80</output><input id="reader-line-height" type="range" min="140" max="220" step="5" value="180"></div>
      <div data-reader-text-setting class="reader-setting reader-setting-range"><label for="reader-paragraph-spacing">段间距</label><output id="reader-paragraph-spacing-value" for="reader-paragraph-spacing">12px</output><input id="reader-paragraph-spacing" type="range" min="0" max="30" step="2" value="12"></div>
      <div data-reader-text-setting class="reader-setting reader-setting-range"><label for="reader-content-width">正文宽度</label><output id="reader-content-width-value" for="reader-content-width">760px</output><input id="reader-content-width" type="range" min="480" max="1040" step="40" value="760"></div>
      <div data-reader-text-setting class="reader-setting"><label for="reader-text-align">文字对齐</label><select id="reader-text-align"><option value="left" selected>左侧对齐</option><option value="justify">两端对齐</option></select></div>
      <div data-reader-text-setting class="reader-setting reader-setting-check"><label for="reader-indent"><span>首行缩进</span><small>每段开头空两个字</small></label><input id="reader-indent" type="checkbox" role="switch"></div>
      <div data-reader-text-setting class="reader-setting reader-setting-check"><label for="reader-preserve-publisher"><span>保留原书排版</span><small>优先使用文档自带的文字样式</small></label><input id="reader-preserve-publisher" type="checkbox" role="switch"></div>
      <p class="reader-setting-note">排版设置适用于文字文档。PDF 保留原版式，可单独缩放。</p>
      <div class="reader-settings-actions" aria-label="阅读工具">
        <button class="reader-button" data-reader-fullscreen type="button" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg><span data-fullscreen-label>全屏阅读</span></button>
        <button class="reader-button" data-reader-help type="button" aria-haspopup="dialog" aria-controls="reader-help-dialog"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 8.5a2.5 2.5 0 0 1 5 .5c0 1.5-2.5 2-2.5 4M12 16h.01"/></svg>阅读帮助与快捷键</button>
      </div>
      <p id="reader-experience-status" class="reader-setting-note" role="status" aria-live="polite"></p>
      <label class="reader-txt-rule-hidden" for="reader-parser-regex" hidden>TXT 章节规则</label>
      <input class="reader-input reader-txt-rule-hidden" id="reader-parser-regex" placeholder="可选" hidden>
      <button class="reader-button reader-txt-rule-hidden" id="reader-save-regex" type="button" hidden>保存</button>
    </div>
  </aside>
  <dialog id="reader-help-dialog" class="reader-dialog reader-help-dialog" aria-labelledby="reader-help-title">
    <div class="reader-panel-heading"><div><span class="reader-eyebrow">自在阅读，随手掌控</span><h2 id="reader-help-title">阅读帮助与快捷键</h2></div><button id="reader-close-help" class="reader-button reader-icon-button" type="button" aria-label="关闭阅读帮助" autofocus><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>
    <div class="reader-help-content">
      <p>打开左侧导航，可切换目录、书单与书内搜索。搜索跳转后，点击「返回阅读处」即可回到原来的位置。</p>
      <dl class="reader-shortcuts">
        <div><dt>书内搜索</dt><dd><kbd>Ctrl</kbd> / <kbd>⌘</kbd> + <kbd>F</kbd> 或 <kbd>/</kbd></dd></div>
        <div><dt>打开 / 收起导航</dt><dd><kbd>T</kbd></dd></div>
        <div><dt>显示 / 收起工具栏</dt><dd><kbd>H</kbd></dd></div>
        <div><dt>进入 / 退出全屏</dt><dd><kbd>F</kbd></dd></div>
        <div><dt>向后阅读</dt><dd><kbd>Page Down</kbd> / <kbd>空格</kbd></dd></div>
        <div><dt>向前阅读</dt><dd><kbd>Page Up</kbd></dd></div>
        <div><dt>关闭弹层</dt><dd><kbd>Esc</kbd></dd></div>
        <div><dt>显示阅读帮助</dt><dd><kbd>?</kbd></dd></div>
      </dl>
      <p class="reader-help-note">在正文区域使用快捷键；输入文字、调整设置时不会触发。手机上可从阅读设置打开全屏与帮助。点击正文图片可放大，点击脚注可查看注释。</p>
    </div>
  </dialog>
  <dialog id="reader-image-dialog" class="reader-dialog reader-image-dialog" aria-label="查看文档图片">
    <button id="reader-close-image" class="reader-button reader-icon-button reader-dialog-close" type="button" aria-label="关闭图片" autofocus><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button>
    <div class="reader-image-viewport"><img id="reader-image-preview" alt="文档图片"></div>
  </dialog>
  <dialog id="reader-footnote-dialog" class="reader-dialog reader-footnote-dialog" aria-labelledby="reader-footnote-title">
    <div class="reader-panel-heading"><h2 id="reader-footnote-title">注释</h2><button id="reader-close-footnote" class="reader-button reader-icon-button" type="button" aria-label="关闭注释" autofocus><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>
    <div id="reader-footnote-content" class="reader-footnote-content" tabindex="0"></div>
  </dialog>
</div>
<script>
window.RECOLL_READER = {{!reader_config_json}};
</script>
<script type="module" src="{{app_url('/static/reader/app.js')}}?v=reader-ui-5"></script>
%include("footer")
