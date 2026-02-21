%import shlex, unicodedata, os, sys
<div class="search-result">
    %number = (query['page'] - 1)*config['perpage'] + i + 1
    %if config["permlinks"]:
        %if query_string.find("&rcludi=") == -1:
            %query_string += "&rcludi=" + d["rcludi"]
        %end
    %end
    <div class="search-result-number"><a href="#r{{d['sha']}}">#{{number}}</a></div>
    %url = d['url'].replace('file://', '')
    %for dr, prefix in config['mounts'].items():
        %url = url.replace(dr, prefix)
    %end
    <div class="search-result-title" id="r{{d['sha']}}" title="{{d['abstract']}}">
    %if 'title_link' in config and config['title_link'] != 'download':
        %if config['title_link'] == 'open':
            <a href="{{url}}">{{d['label']}}</a>
        %elif config['title_link'] == 'preview':
            <a href="preview/{{number-1}}?{{query_string}}">{{d['label']}}</a>
        %end
    %else:
        <a href="download/{{number-1}}?{{query_string}}">{{d['label']}}</a>
    %end
    </div>
    %if len(d['ipath']) > 0:
        <div class="search-result-ipath">[{{d['ipath']}}]</div>
    %end
    %if "collapsecount" in d and d["collapsecount"]:
        <div class="search-result-dups">&nbsp;&nbsp;(duplicates: {{d["collapsecount"]}})</div>
    %end
    %if 'author' in d and len(d['author']) > 0:
        <div class="search-result-author">{{d['author']}}</div>
    %end
    <div class="search-result-url">
        %urllabel = d['url'].replace('file://', '')
        %if config['shortenpaths']:
            %if len(config['commonprefix']) > 0 and len(urllabel) > len(config['commonprefix']):
                %urllabel = urllabel.replace(config['commonprefix'], "")
            %end
        %end
        %urllabel = os.path.dirname(urllabel)
        %if len(urllabel) == 0:
            %urllabel = config['commonprefix']
        %end
        <a href="{{os.path.dirname(url)}}">{{urllabel}}</a>
    </div>
    %if not "noresultlinks" in config or not config["noresultlinks"]:
    <div class="search-result-links">
        <a href="{{url}}">Open</a>
        <a href="download/{{number-1}}?{{query_string}}">Download</a>
        <a href="preview/{{number-1}}?{{query_string}}" target="_blank">Preview</a>
        %if config["permlinks"] and config["res_permlink"]:
            <a href="results?{{query_string}}">Link</a>
        %end
    </div>
    %end
    <div class="search-result-date">{{d['time']}}</div>
    <div class="search-result-snippet">{{!d['snippet']}}</div>
</div>
<!-- vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et:ai
-->
