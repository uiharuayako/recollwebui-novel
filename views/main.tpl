%include("header",title="")
%include("search", query=query, dirs=dirs, sorts=sorts, config=config)
%if recent_readings:
<section class="account-panel">
  <h2>继续阅读</h2>
  %for item in recent_readings:
  <p><a href="{{app_url('/reader')}}?document_id={{item['document_id']}}">{{item['title_snapshot']}}</a></p>
  %end
</section>
%end
%include("footer")
