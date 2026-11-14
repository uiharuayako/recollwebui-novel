%import os
%from urllib.parse import quote as urlquote, urlencode, unquote, urlsplit
%from accounts import folder_name
%result_folder = folder_name(unquote(urlsplit(d.get('source_url') or d['url']).path))
%number = (query['page'] - 1)*config['perpage'] + i + 1
%reader_return_query = urlencode({'return_to': app_url('/results') + '?' + query_string})
%reader_hit_query = urlencode({'reader_search': d.get('reader_match_term', ''), 'reader_term': d.get('reader_match_terms', []), 'reader_page': d.get('reader_match_page', 0)}, doseq=True)
%if config["permlinks"] and query_string.find("&rcludi=") == -1:
    %query_string += "&rcludi=" + urlquote(d["rcludi"])
%end
%url = d['url'].replace('file://', '')
%reader_ext = os.path.splitext(d['filename'] or d['label'] or '')[1].lower().lstrip('.')
%reader_supported = d.get('reader_supported', reader_ext in ('txt', 'epub', 'mobi', 'azw', 'azw3', 'fb2', 'chm', 'docx', 'md', 'html', 'htm', 'xhtml', 'xml', 'mhtml', 'pdf', 'cbz', 'cbr', 'cbt', 'cb7') or (d.get('mtype') or '').lower() in ('application/vnd.ms-htmlhelp', 'application/x-chm'))
%for dr, prefix in config['mounts'].items():
    %url = url.replace(dr, prefix)
%end
%stable_reader = d.get('document_id') and not d.get('ipath')
%read_url = app_url('/reader') + '?mode=book&' + (urlencode({'document_id': d['document_id']}) + '&' + reader_return_query if stable_reader else 'resnum=' + str(number-1) + '&' + query_string)
%preview_url = app_url('/preview/' + str(number-1)) + '?' + query_string
%download_url = app_url('/download/' + str(number-1)) + '?' + query_string
%title_action = config.get('title_link', 'download')
%title_url = url if title_action == 'open' else read_url if title_action == 'reader' and reader_supported and stable_reader else download_url if title_action == 'download' else preview_url
<article class="search-result" aria-labelledby="r{{d['sha']}}">
  <div class="app-result-heading"><span class="app-format-tag">{{reader_ext.upper() or '文档'}}</span><span class="app-folder-name" title="{{result_folder}}">{{result_folder}}</span><span class="search-result-number"><a href="#r{{d['sha']}}" aria-label="第 {{number}} 条结果">#{{number}}</a></span><span class="search-result-date">{{d['time']}}</span></div>
  <h2 class="search-result-title" id="r{{d['sha']}}"><a href="{{title_url}}">{{d['label']}}</a></h2>
  %if d.get('author'):
  <div class="search-result-author">{{d['author']}}</div>
  %end
  %if d.get('ipath'):
  <div class="search-result-ipath">{{d['ipath']}}</div>
  %end
  %if d.get('collapsecount'):
  <div class="search-result-dups">{{d['collapsecount']}} 个重复文档已合并</div>
  %end
  <div class="search-result-snippet">{{!d['snippet']}}</div>
  %urllabel = d['url'].replace('file://', '')
  %if config['shortenpaths'] and len(config['commonprefix']) > 0 and len(urllabel) > len(config['commonprefix']):
      %urllabel = urllabel.replace(config['commonprefix'], '')
  %end
  %urllabel = os.path.dirname(urllabel) or config['commonprefix']
  <div class="search-result-url"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11H3V7Z"/></svg><a href="{{os.path.dirname(url)}}" title="{{urllabel}}">{{urllabel}}</a></div>
  %if not config.get('noresultlinks'):
  <div class="search-result-links">
    %if reader_supported:
      %if stable_reader:
      <a class="app-result-primary" href="{{app_url('/reader')}}?mode=folder&document_id={{d['document_id']}}&{{reader_return_query}}" target="_blank" rel="noopener" title="在新标签页阅读整个文件夹，可继续切换下一章">阅读当前文件夹</a>
      %if d.get('reader_match_term') or d.get('reader_match_page'):
      <a href="{{read_url}}&{{reader_hit_query}}" target="_blank" rel="noopener" title="在新标签页查看命中">查看命中</a>
      %end
      %elif d.get('reader_match_term') or d.get('reader_match_page'):
      <a class="app-result-primary" href="{{read_url}}&{{reader_hit_query}}" target="_blank" rel="noopener" title="在新标签页查看命中">查看命中</a>
      <a href="{{read_url}}" target="_blank" rel="noopener">继续阅读</a>
      %else:
      <a class="app-result-primary" href="{{read_url}}" target="_blank" rel="noopener" title="在新标签页继续阅读">继续阅读</a>
      %end
    %else:
      <a class="app-result-primary" href="{{preview_url}}" target="_blank" rel="noopener">预览文档 <span aria-hidden="true">↗</span></a>
    %end
    %if d.get('document_id'):
    <button type="button" class="shelf-toggle" data-document-id="{{d['document_id']}}" data-handle="{{d['shelf_handle']}}" data-on-shelf="{{'1' if d['on_shelf'] else '0'}}" aria-pressed="{{'true' if d['on_shelf'] else 'false'}}">{{'移出书架' if d['on_shelf'] else '加入书架'}}</button>
    %end
    %if reader_supported and stable_reader and d.get('recommendation_handle'):
    <form method="post" action="{{app_url('/recommendations/remove' if d.get('on_recommendation') else '/recommendations/add')}}" data-recommendation-form><input type="hidden" name="csrf" value="{{account_csrf()}}"><input type="hidden" name="document_id" value="{{d['document_id']}}"><input type="hidden" name="handle" value="{{d['recommendation_handle']}}"><input type="hidden" name="source_query" value="{{query['query']}}"><input type="hidden" name="next" value="{{app_url('/results') + '?' + query_string}}"><button type="submit" class="app-recommendation-toggle" aria-pressed="{{'true' if d.get('on_recommendation') else 'false'}}">{{'移出搜索推荐' if d.get('on_recommendation') else '加入搜索推荐'}}</button></form>
    %end
    <details class="app-result-more"><summary aria-label="{{d['label']}}的更多操作">更多 <span aria-hidden="true">⌄</span></summary><div class="app-result-menu">
      %if reader_supported and stable_reader:
      <a href="{{read_url}}" target="_blank" rel="noopener">单独阅读此文件</a>
      <a href="{{app_url('/shares/new')}}?document_id={{d['document_id']}}">分享此书</a>
      <a href="{{app_url('/shares/new')}}?document_id={{d['document_id']}}&amp;mode=folder">分享当前文件夹</a>
      %end
      <a href="{{download_url}}">下载文档</a><a href="{{preview_url}}" target="_blank" rel="noopener">预览文档</a><a href="{{url}}">打开源文件</a>
      %if config["permlinks"] and config["res_permlink"]:
      <a href="{{app_url('/results')}}?{{query_string}}">永久链接</a>
      %end
    </div></details>
  </div>
  %end
</article>
