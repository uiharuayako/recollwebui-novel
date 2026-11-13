<!DOCTYPE html>
<html>
<head>
    <title>recoll{{title}}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" type="text/css" href="{{app_url('/static/style.css')}}?v=reader-mobile-4">
    <link rel="stylesheet" type="text/css" href="{{app_url('/static/accounts.css')}}?v=1">
    <script type="text/javascript" src="{{app_url('/static/jquery.js')}}"></script>
    <script type="text/javascript" src="{{app_url('/static/extra.js')}}"></script>

    <script type="text/javascript" src="{{app_url('/static/jdpicker.js')}}"></script>
    <link rel="stylesheet" type="text/css" href="{{app_url('/static/jdpicker.css')}}">
    <link rel="icon" type="image/png" href="{{app_url('/static/recoll.png')}}">
    <link rel="search" type="application/opensearchdescription+xml" title="recoll" href="{{app_url('/osd.xml')}}">
    <script id="recoll-account-data" type="application/json">{{!account_context()}}</script>
    <script defer src="{{app_url('/static/accounts.js')}}?v=1"></script>
</head>
<body>
%if account_user():
<nav class="account-nav" aria-label="账号导航">
    <a href="{{app_url('/')}}">搜索</a>
    <a href="{{app_url('/bookshelf')}}">我的书架</a>
    <a href="{{app_url('/history')}}">历史记录</a>
    <a href="{{app_url('/account')}}">{{account_user().get('display_name') or account_user().get('username')}}</a>
    %if account_user()['role'] == 'admin':
    <a href="{{app_url('/admin/users')}}">账号管理</a>
    %end
    <form method="post" action="{{app_url('/logout')}}">
        <input type="hidden" name="csrf" value="{{account_csrf()}}">
        <button type="submit">退出</button>
    </form>
</nav>
%end
