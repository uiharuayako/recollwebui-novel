%include("header", title=" / 搜索设置")
<main id="main-content" class="app-settings" tabindex="-1">
<div id="settings-box" class="app-settings">
  <h1>搜索设置</h1><p>调整搜索结果与文档展示方式，找到更适合你的文库浏览节奏。</p>
  <form action="{{app_url('/set')}}" method="post">
    <input type="hidden" name="csrf" value="{{account_csrf()}}">
    <fieldset class="app-settings-group"><legend>搜索与结果</legend><div class="app-settings-grid">
      <label>近义词形匹配<select name="stem"><option value="0" {{'selected' if not int(stem) else ''}}>关闭 · 精确词形</option><option value="1" {{'selected' if int(stem) else ''}}>开启 · 匹配相近词形</option></select><small>例如同时匹配英文单词的不同词形。</small></label>
      <label>合并重复文档<select name="collapsedups"><option value="1" {{'selected' if int(collapsedups) else ''}}>开启 · 相同内容只显示一次</option><option value="0" {{'selected' if not int(collapsedups) else ''}}>关闭 · 显示所有结果</option></select></label>
      <label>最多返回结果数<input name="maxresults" type="number" min="0" required value="{{maxresults}}"><small>0 表示不限；结果较多时可能需要更长时间。</small></label>
      <label>每页结果数<input name="perpage" type="number" min="0" required value="{{perpage}}"><small>0 表示不分页。</small></label>
      <label>点击文档标题时<select name="title_link"><option value="reader" {{'selected' if title_link == 'reader' else ''}}>在阅读器中打开</option><option value="preview" {{'selected' if title_link == 'preview' else ''}}>预览文档</option><option value="download" {{'selected' if title_link == 'download' else ''}}>下载文档</option><option value="open" {{'selected' if title_link == 'open' else ''}}>打开源文件</option></select></label>
      <label>文件夹显示层级<input name="dirdepth" type="number" min="0" required value="{{dirdepth}}"><small>决定搜索筛选中展开的文件夹深度。</small></label>
    </div></fieldset>
    <fieldset class="app-settings-group"><legend>摘要显示</legend><div class="app-settings-grid">
      <label>上下文词数<input name="context" type="number" min="0" required value="{{context}}"><small>命中词前后显示的上下文长度。</small></label>
      <label>摘要最多字符数<input name="maxchars" type="number" min="0" required value="{{maxchars}}"></label>
    </div></fieldset>
    <details class="app-settings-advanced"><summary>高级设置：时间格式、路径映射与导出</summary>
      <fieldset class="app-settings-group"><legend>高级设置</legend><div class="app-settings-grid">
        <label>时间格式<input name="timefmt" value="{{timefmt}}"><small>例如 %Y-%m-%d 显示年、月、日。</small></label>
        <label>CSV 导出字段<input name="csvfields" value="{{csvfields}}"><small>{{fields}}</small></label>
        %for d in dirs:
        <label class="app-settings-path">路径映射：{{d}}<input name="mount_{{d}}" value="{{mounts[d]}}"></label>
        %end
      </div><p><a href="#" onclick="addOpenSearch();return false">添加为浏览器搜索引擎 ↗</a></p></fieldset>
    </details>
    <div class="app-settings-actions"><button type="submit">保存设置</button><a href="{{app_url('/')}}">返回文库</a></div>
  </form>
</div>
</main>
%include("footer")
