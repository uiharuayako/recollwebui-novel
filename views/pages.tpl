%from urllib.parse import urlencode
%import math
%q = dict(query)
%def page_href(page):
    %q['page'] = page
    %return app_url('/results') + '?' + urlencode(q)
%end
%if nres > 0:
    %npages = int(math.ceil(nres/float(config['perpage'])))
    %if npages > 1:
    <nav class="pages" aria-label="搜索结果分页">
      %if query['page'] > 1:
      <a title="第一页" aria-label="第一页" class="page" href="{{page_href(1)}}">«</a><a title="上一页" aria-label="上一页" class="page" href="{{page_href(query['page']-1)}}">‹</a>
      %end
      %for p in range(max(1, query['page']-2), min(npages, query['page']+2)+1):
      <a href="{{page_href(p)}}" class="page {{'current' if p == query['page'] else ''}}" aria-label="第 {{p}} 页" aria-current="{{'page' if p == query['page'] else 'false'}}">{{p}}</a>
      %end
      <span class="app-page-total">共 {{npages}} 页</span>
      %if query['page'] < npages:
      <a title="下一页" aria-label="下一页" class="page" href="{{page_href(query['page']+1)}}">›</a><a title="最后一页" aria-label="最后一页" class="page" href="{{page_href(npages)}}">»</a>
      %end
    </nav>
    %end
%end
