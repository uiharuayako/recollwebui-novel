%include('header', title=' / 修改密码')
<main class="account-page account-narrow">
  <h1>修改密码</h1>
  %if forced:
  <p class="account-notice" role="status">请先更换初始密码，再访问文档库。</p>
  %end
  <p class="account-muted">密码长度为 15–128 个字符。更改成功后，所有设备都需要重新登录。</p>
  <form method="post" action="{{app_url('/account/password')}}" class="account-form">
    <input type="hidden" name="csrf" value="{{account_csrf()}}">
    <label>当前密码<input name="old_password" type="password" autocomplete="current-password" required maxlength="128"></label>
    <label>新密码<input name="password" type="password" autocomplete="new-password" required minlength="15" maxlength="128"></label>
    <label>再次输入新密码<input name="password_confirm" type="password" autocomplete="new-password" required minlength="15" maxlength="128"></label>
    <button type="submit">保存并退出所有设备</button>
  </form>
</main>
%include('footer')
