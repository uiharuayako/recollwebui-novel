%include('header', title=' / 请求未完成')
<main class="account-page account-narrow">
  <h1>请求未完成 · {{status}}</h1>
  <p role="alert">{{message}}</p>
  <p><a href="{{app_url('/account')}}">个人账号</a> · <a href="{{app_url('/login')}}">登录</a> · <a href="{{app_url()}}">返回首页</a></p>
</main>
%include('footer')
