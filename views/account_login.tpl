%include('header', title=' / 登录')
<main class="account-page account-narrow">
  <h1>登录</h1>
  <p class="account-muted">文档库由所有账号共享；搜索历史、阅读进度与偏好仅对当前账号显示。</p>
  %if error:
  <p class="account-error" role="alert">{{error}}</p>
  %end
  <form method="post" action="{{app_url('/login')}}" class="account-form">
    <input type="hidden" name="csrf" value="{{login_csrf}}">
    <input type="hidden" name="next" value="{{next_path}}">
    <label>用户名<input name="username" autocomplete="username" required maxlength="32" autofocus></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required maxlength="128"></label>
    <label class="account-check"><input name="remember" value="1" type="checkbox">保持登录（最长 30 天）</label>
    <button type="submit">登录</button>
  </form>
</main>
%include('footer')
