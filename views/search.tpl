%import re
<div id="fade"></div>
<div id="searchbox">
<form action="results" method="get">
<table id="form">
<tr>
    <td id="query-cell">
        <b>Query</b><br>
        <input tabindex="0" type="search" name="query" value="{{query['query']}}" autofocus>
        <br class="qspacer">
        <br class="qspacer">
        <div class="button-row">
            <input type="submit" value="Search">
            <a href="./" tabindex="-1"><input type="button" value="Reset"></a>
            %if not config['rclc_nosettings']:
                <a href="settings" tabindex="-1"><input type="button" value="Settings"></a>
            %end
        </div>
    </td>
    <td id="folder-cell">
        <b>Folder</b><br>
        <select id="folders" name="dir">
        %for d in sorted(dirs, key=str.lower):
            %space = "&nbsp;" * (4 * d.count('/'))
            %if d in query['dir']:
            %selected = "selected"
            %else:
            %selected = ""
            %end
            <option {{selected}} value="{{d}}">{{!space}}{{re.sub('.+/','', d)}}</option>
        %end
        </select><br>
        <b>Dates</b> <small class="gray">YYYY[-MM][-DD]</small><br>
        <div class="date-range"><input name="after" value="{{query['after']}}" autocomplete="off"> &mdash; <input name="before" value="{{query['before']}}" autocomplete="off"></div>
    </td>
    <td id="sort-cell">
        <b>Sort by</b><br>
        <select name="sort">
        %for s in sorts:
            %if query['sort'] == s[0]:
                <option selected value="{{s[0]}}">{{s[1]}}</option>
            %else:
                <option value="{{s[0]}}">{{s[1]}}</option>
            %end
        %end
        </select><br>
        <b>Order</b><br>
        <select name="ascending">
            %if int(query['ascending']) == 1:
                <option value="0">Descending</option>
                <option value="1" selected>Ascending</option>
            %else:
                <option value="0" selected>Descending</option>
                <option value="1">Ascending</option>
            %end
        </select>
    </td>
</tr>
</table>
<input type="hidden" name="page" value="1" />
</form>
</div>
<!-- vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et:ai
-->
