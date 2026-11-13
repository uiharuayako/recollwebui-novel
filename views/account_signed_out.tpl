%include('header', title=' / 已退出')
<main class="account-page account-narrow" data-account-notify="{{action}}">
  <h1>{{reason}}</h1>
  <p><a href="{{app_url('/login')}}">重新登录或切换账号</a></p>
</main>
%include('footer')
