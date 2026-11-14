%include('header', title=' / 已退出')
<main id="main-content" tabindex="-1" class="account-page account-narrow" data-account-notify="{{action}}">
  <p class="account-eyebrow">SEE YOU NEXT CHAPTER</p>
  <h1>{{reason}}</h1>
  <p><a class="account-button" href="{{app_url('/login')}}">重新登录或切换账号</a></p>
</main>
%include('footer')
