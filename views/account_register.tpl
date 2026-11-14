%include('header', title=' / 注册')
<main id="main-content" tabindex="-1" class="account-page account-narrow">
  <p class="account-eyebrow">JOIN YOUR LIBRARY</p>
  <h1>创建阅读账号</h1>
  <p class="account-muted">填写用户名和密码即可注册，无需邮箱验证。</p>
  %if error:
  <p class="account-error" role="alert">{{error}}</p>
  %end
  <form method="post" action="{{app_url('/register')}}" class="account-form">
    <input type="hidden" name="csrf" value="{{registration_csrf}}">
    <input type="hidden" name="next" value="{{next_path}}">
    <label>用户名<input name="username" value="{{username}}" autocomplete="username" autocapitalize="none" spellcheck="false" required pattern="[A-Za-z0-9._\-]{3,32}" minlength="3" maxlength="32" autofocus><small>3–32 位英文字母、数字或 . _ -，不区分大小写。</small></label>
    <label>密码<input name="password" type="password" autocomplete="new-password" required minlength="15" maxlength="128"><small>15–128 个字符。</small></label>
    %if registration['activation_required']:
    <label>激活码<input name="activation_code" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" required maxlength="128"><small>请向管理员获取激活码。</small></label>
    %end
    <button type="submit">注册账号 <span aria-hidden="true">→</span></button>
  </form>
  <p class="account-footnote">已有账号？<a href="{{login_url}}">返回登录</a></p>
</main>
%include('footer')
