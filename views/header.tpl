<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>Recoll 文库{{title}}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="{{app_url('/static/style.css')}}?v=reader-mobile-4">
    <link rel="stylesheet" href="{{app_url('/static/accounts.css')}}?v=folder-label-1">
    <link rel="stylesheet" href="{{app_url('/static/app-ui.css')}}?v=folder-label-1">
    <script src="{{app_url('/static/jquery.js')}}"></script>
    <script src="{{app_url('/static/extra.js')}}?v=history-dates-1"></script>
    <script src="{{app_url('/static/jdpicker.js')}}"></script>
    <link rel="stylesheet" href="{{app_url('/static/jdpicker.css')}}">
    <link rel="icon" type="image/png" href="{{app_url('/static/recoll.png')}}">
    <link rel="search" type="application/opensearchdescription+xml" title="recoll" href="{{app_url('/osd.xml')}}">
    <script id="recoll-account-data" type="application/json">{{!account_context()}}</script>
    <script defer src="{{app_url('/static/accounts.js')}}?v=ux-1"></script>
    <script defer src="{{app_url('/static/app-ui.js')}}?v=home-recommendations-1"></script>
</head>
<body>
<a class="app-skip-link" href="#main-content">跳到主要内容</a>
<header class="app-header">
  <div class="app-header-inner">
    <a class="app-brand" href="{{app_url('/')}}" aria-label="Recoll 文库首页"><span class="app-brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"/></svg></span><span>Recoll <span class="app-brand-subtitle">文库</span></span></a>
%if account_user() and not get('shared', False):
%import bottle
%current_path = bottle.request.path
<nav class="account-nav" aria-label="主要导航">
    <a href="{{app_url('/')}}" aria-current="{{'page' if current_path in ('/', '/results', '/search') else 'false'}}">发现文档</a>
    <a href="{{app_url('/bookshelf')}}" aria-current="{{'page' if current_path == '/bookshelf' else 'false'}}">我的书架</a>
    <a href="{{app_url('/history')}}" aria-current="{{'page' if current_path == '/history' else 'false'}}">历史记录</a>
    <a href="{{app_url('/shares')}}" aria-current="{{'page' if current_path.startswith('/shares') else 'false'}}">我的分享</a>
</nav>
<details class="app-user-menu">
  %display_name = account_user().get('display_name') or account_user().get('username')
  <summary aria-label="账号菜单：{{display_name}}"><span class="app-avatar" aria-hidden="true">{{display_name[:1].upper()}}</span><span class="app-user-name">{{display_name}}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary>
  <div class="app-user-dropdown">
    <a href="{{app_url('/account')}}">账号与阅读偏好</a>
    %if account_user()['role'] == 'admin':
    <a href="{{app_url('/admin/users')}}">账号管理</a>
    %end
    <form method="post" action="{{app_url('/logout')}}"><input type="hidden" name="csrf" value="{{account_csrf()}}"><button type="submit">退出登录</button></form>
  </div>
</details>
%else:
<span class="app-header-note">{{'分享阅读 · 无需登录' if get('shared', False) else '一处文库，随时阅读'}}</span>
%end
  </div>
</header>
