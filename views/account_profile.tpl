%include('header', title=' / 个人账号')
<main id="main-content" tabindex="-1" class="account-page">
  <p class="account-eyebrow">MAKE IT YOURS</p>
  <h1>个人账号</h1>
  <p>{{user.get('display_name') or user['username']}} <span class="account-muted">@{{user['username']}} · {{'管理员' if user['role'] == 'admin' else '普通用户'}}</span></p>
  %if saved:
  <p class="account-notice" role="status" data-account-notify="settings-changed">设置已保存。</p>
  %end
  <section class="account-card">
    <h2>记录与阅读偏好</h2>
    <p class="account-muted">关闭记录会保留已有数据。清除历史与进度可在历史记录页单独操作；书架独立保留。</p>
    <form method="post" action="{{app_url('/account/preferences')}}" class="account-form">
      <input type="hidden" name="csrf" value="{{account_csrf()}}">
      <label class="account-check"><input type="checkbox" name="search_history_enabled" value="1" {{'checked' if settings.get('search_history_enabled', True) else ''}}>记录主动搜索（最多 90 天、500 条）</label>
      <label class="account-check"><input type="checkbox" name="reading_history_enabled" value="1" {{'checked' if settings.get('reading_history_enabled', True) else ''}}>记录最近阅读（最多 180 天、1000 本）</label>
      <label class="account-check"><input type="checkbox" name="progress_sync_enabled" value="1" {{'checked' if settings.get('progress_sync_enabled', True) else ''}}>同步阅读进度（非书架文档最多 365 天、1000 本）</label>
      <label>阅读字号<input type="number" min="16" max="32" step="1" name="font_size" value="{{settings.get('preferences', {}).get('font_size', 20)}}"></label>
      <button type="submit">保存偏好</button>
    </form>
    <p><a href="{{app_url('/settings')}}">搜索显示设置</a> · <a href="{{app_url('/account/password')}}">修改密码</a></p>
  </section>
  <section class="account-card" id="legacy-import" hidden>
    <h2>本浏览器的旧数据</h2>
    <p id="legacy-summary"></p>
    <p class="account-muted">这些匿名数据不会自动归入当前账号。导入仅验证可访问文档，不补写阅读历史，也不覆盖已有云端进度。</p>
    <label class="account-check"><input type="checkbox" id="legacy-import-preferences">导入旧搜索偏好和阅读字号</label>
    <label class="account-check"><input type="checkbox" id="legacy-import-positions">导入旧阅读位置</label>
    <div class="account-actions"><button type="button" id="legacy-import-submit">导入选中数据</button><button type="button" class="account-secondary" id="legacy-delete">删除本浏览器旧数据</button></div>
    <p id="legacy-import-status" role="status"></p>
  </section>
  <section class="account-card">
    <h2>登录设备</h2>
    <p class="account-muted">设备名称来自浏览器，供识别参考。</p>
    <ul class="account-list">
    %for item in sessions:
      <li>
        <div><strong>{{'当前设备' if item['id'] == session['id'] else '其他设备'}}</strong><div class="account-device">{{item.get('device_label') or '未知浏览器'}}</div><span class="account-muted">最近活动：<time data-utc="{{item['last_seen_at']}}">{{item['last_seen_at']}}</time></span></div>
        <form method="post" action="{{app_url('/account/sessions/revoke')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="session_id" value="{{item['id']}}"><button type="submit" class="account-secondary">撤销</button></form>
      </li>
    %end
    </ul>
    <form method="post" action="{{app_url('/account/sessions/revoke')}}" data-confirm="确定退出所有设备？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><button class="account-danger" type="submit">退出所有设备</button></form>
  </section>
</main>
%include('footer')
