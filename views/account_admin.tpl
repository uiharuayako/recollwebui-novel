%include('header', title=' / 账号管理')
<main class="account-page">
  <h1>账号管理</h1>
  <p class="account-muted">管理员管理账号，所有账号共享文档库。此处不提供其他账号的个人历史。</p>
  %if saved:
  <p class="account-notice" role="status">账号操作已完成；角色、状态或密码变更后，相关账号需重新登录。</p>
  %end
  <section class="account-card">
    <h2>创建账号</h2>
    <form method="post" action="{{app_url('/admin/users')}}" class="account-form">
      <input type="hidden" name="csrf" value="{{account_csrf()}}">
      <label>用户名<input name="username" required pattern="[A-Za-z0-9._-]{3,32}" maxlength="32" autocomplete="off"><small>3–32 位英文字母、数字或 . _ -，不区分大小写。</small></label>
      <label>显示名称<input name="display_name" maxlength="100"></label>
      <label>角色<select name="role"><option value="member">普通用户</option><option value="admin">管理员</option></select></label>
      <label>初始密码<input type="password" name="password" required minlength="15" maxlength="128" autocomplete="new-password"><small>用户首次登录时必须更换。</small></label>
      <button type="submit">创建账号</button>
    </form>
  </section>
  <section class="account-card"><h2>现有账号</h2>
  %for user in users:
    <article class="account-user-row">
      <h3>{{user.get('display_name') or user['username']}} <small>@{{user['username']}}</small></h3>
      <p>{{'管理员' if user['role'] == 'admin' else '普通用户'}} · {{'已启用' if user['status'] == 'active' else '已停用'}}{{' · 待更换初始密码' if user.get('must_change_password') else ''}}</p>
      <div class="account-actions">
        <form method="post" action="{{app_url('/admin/users/' + user['id'] + '/update')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="action" value="{{'disable' if user['status'] == 'active' else 'enable'}}"><button class="account-secondary" type="submit">{{'停用' if user['status'] == 'active' else '启用'}}</button></form>
        <form method="post" action="{{app_url('/admin/users/' + user['id'] + '/update')}}" data-confirm="修改角色将撤销该账号的登录会话，是否继续？"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="action" value="role"><input type="hidden" name="role" value="{{'member' if user['role'] == 'admin' else 'admin'}}"><button class="account-secondary" type="submit">{{'设为普通用户' if user['role'] == 'admin' else '设为管理员'}}</button></form>
        <form method="post" action="{{app_url('/admin/users/' + user['id'] + '/update')}}" data-confirm="永久删除此账号及其个人数据？此操作无法撤销。"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="action" value="delete"><button class="account-danger" type="submit">删除账号</button></form>
      </div>
      <details><summary>重置密码</summary><form method="post" action="{{app_url('/admin/users/' + user['id'] + '/update')}}" class="account-inline"><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="action" value="reset-password"><label>一次性初始密码<input type="password" name="password" required minlength="15" maxlength="128" autocomplete="new-password"></label><button type="submit">重置并撤销所有会话</button></form></details>
    </article>
  %end
  </section>
</main>
%include('footer')
