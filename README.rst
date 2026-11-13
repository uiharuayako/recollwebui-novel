============
Recoll WebUI
============

This (https://framagit.org/medoc92/recollwebui) is an updated clone of
Koniu's original version on GitHub (https://github.com/koniu/recoll-webui),
which has not been updated lately, and is now slightly obsolete. If you are currently
on github, you are viewing a mirror of the framagit repo (you're fine :) ).

As compared to the original, this version has an additional dependency when
running the webui-standalone.py server (i.e. not with apache): it uses the
python3-waitress module which must be installed. This makes
webui-standalone suitable for moderate multiaccess loads. Local accounts now
protect searches, documents and exports; every account shares the document
library and keeps its own bookshelf, history, preferences and reading progress.

An updated version of the original doc follows.


**Recoll WebUI** is a Python-based web interface for the **Recoll** text search
tool for Unix/Linux.

.. image:: http://i.imgur.com/n8qTnBg.png

* WebUI homepage: https://framagit.org/medoc92/recollwebui
* Recoll homepage: http://www.lesbonscomptes.com/recoll
* Original WebUI homepage: https://github.com/koniu/recoll-webui

Requirements
============

All you need to use the WebUI is:

* Python 3.
* The Python waitress package. You can remove this dependence and run with
  the internal bottle server by editing webui-standalone.py
* Recoll 1.20+ and the Recoll Python3 extension (e.g. the python3-recoll package on Debian-derived
  systems).
* ``argon2-cffi`` for password hashing (install ``requirements.txt``).
* A WEB browser

Installation and Usage on MS-Windows
====================================

We have a `separate Web page <https://www.lesbonscomptes.com/recoll/faqsandhowtos/webui-on-windows.html>`_ for this.

Installation and Usage on Linux
===============================

**Recoll WebUI** can be used as a standalone application or through a web server via
WSGI/CGI. Regardless of the mode of operation you need Recoll to be configured on your system as the
WebUI only provides a front-end for searching and does not handle index configuration etc.

Run standalone
--------------

Install dependencies and initialize the account database as the operating-system
user that will run the application::

    python3 -m pip install -r requirements.txt
    export RECOLL_WEBUI_DB_PATH=/absolute/private/directory/app.sqlite3
    python3 manage.py init --username admin

The management command prompts for a 15–128 character password. The first login
requires changing that initial password. The database and its ``.secret`` signing
key must persist between restarts and be shared by all local workers. An
uninitialized application returns a setup error instead of enabling anonymous
access. Recoll remains a system dependency; a virtual environment must be able to
import its Python extension.

Run ``webui-standalone.py`` and connect to ``http://localhost:8080``. Use the same
``RECOLL_WEBUI_DB_PATH`` for the management commands and the server.

See `account, bookshelf and history operations <docs/accounts-and-history.md>`_
for setup, backup, recovery, retention and verification details.

There's some optional command-line arguments available::

    -h, --help            show this help message and exit
    -a ADDR, --addr ADDR  address to bind to [127.0.0.1]
    -p PORT, --port PORT  port to listen on [8080]
    -t THREADS, --threads THREADS
                          number of server worker threads [8]. Building the result
                          snippets is cpu intensive, so a few extra workers keep
                          concurrent requests (page assets, second tab) from being
                          served only when a search has finished
    -c CONFDIR, --config CONFDIR Recoll configuration directory to use

The standalone application can be configured to run automatically using systemd. See the file
`README-systemd.rst <README-systemd.rst>`_.

Environment variables:

- `RECOLL_CONFDIR` the recoll configuration directory. This is overriden by a -c option.
- `RECOLL_EXTRACONFDIRS` a space-separated list of external indexes to query in addition to the main
  one.
- ``RECOLL_WEBUI_DB_PATH`` the local persistent SQLite database; defaults to
  ``~/.local/share/recoll-webui/app.sqlite3`` for the service user.
- ``RECOLL_WEBUI_TRUSTED_PROXIES`` explicitly trusted proxy IP addresses/CIDRs
  (space or comma separated). Only these proxies may supply forwarded address and
  HTTPS information.
- ``RECOLL_WEBUI_COOKIE_SECURE`` defaults to ``auto``; set ``true`` for an
  HTTPS-only deployment.


Run as WSGI/CGI
---------------

See the following link for a complete run-through:

https://www.lesbonscomptes.com/recoll/pages/recoll-webui-install-wsgi.html

Example WSGI/Apache2 config, assuming that the code is in /var/recoll-webui-master::

        WSGIDaemonProcess recoll user=recoll group=recoll threads=5 display-name=%{GROUP} python-path=/var/recoll-webui-master
        WSGIScriptAlias /recoll /var/recoll-webui-master/webui-wsgi.py
        <Directory /var/recoll-webui-master>
                WSGIProcessGroup recoll
                Order allow,deny
                allow from all
        </Directory>

Remarks:

* Without "python-path=" you might see errors that it can't import webui 
* Run the WSGIDaemonProcess run under the username (user=xyz) of the user
  that you want to have exposed via web.


User configuration defaults
---------------------------

New on 2022-06-15.

There are a number of parameters with initial defaults which the user can change through a Web
interface page (by clicking the `Settings` button), and which are persisted in a cookie.

Some of the initial defaults may not be appropriate for your configuration. For example the default
depth of 2 for building the directory selection tree may be too much on a big data set (and cause
initialisation errors).

The initial defaults can be changed by setting values in the main recoll configuration file
($RECOLL_CONFDIR/recoll.conf):

- webui_context (30) the size of the abstract snippets in words.
- webui_maxchars (500) total maximum size for the abstract shown with each result.
- webui_stem (1) queries will use stemming (or not).
- webui_timefmt ("%c") format of the time display.
- webui_dirdepth (2) depth of the directory selection tree. Beware if your data set has many
  directories.
- webui_maxresults (500) default limit on the total number of results. Users can change this in
  Settings (0 means no limit).
- webui_perpage (500) number of results per page.
- webui_csvfields ("filename title author size time mtype url") fields extracted in CSV or JSON dumps.
- webui_title_link ("download") action performed if you click the result title (or 'open',
  'preview').
- webui_mount_MOUNTNAME (``file://MOUNTNAME``) location replacement for mount points. e.g.::

    webui_mount_/server/path = file:///local/client/path

  This is only really usable if ``webui_nosettings`` is also set, because, otherwise, the cookie
  value will override the server setting.
  
The following are not changeable from the user interface:

- webui_nojsoncsv (0) If set, disable downloading results as JSON or CSV.
- webui_maxperpage (0) If set to non-zero, limits the maximum value of results per page settable
  through the UI.
- webui_nosettings (0) do not show settings options to users.
- webui_defaultsort ("Relevancy") default sort field.
- webui_synonyms ("") absolute path to a Recoll synonyms file. Note that the optional Recoll
  `idxsynonyms` value should be the same string if it is to be used.
- webui_shortenpaths (1) shorten the folder paths shown in the result list by removing the common
  prefix. The default is to do it, set to 0 to show complete paths.
- webui_permlinks (0) add the Recoll `rcludi` unique identifier to Preview and Download links so that
  they become stable and bookmarkable.
- webui_res_permlink (0) add a stable link to the result itself (right of `Preview` and `Download`).
- webui_queryfrag query fragment to append to all user queries (e.g.: -mime:inode/directory to not
  show results which are actually folders).

Reading documents in the browser
--------------------------------

The reader keeps TXT, EPUB, HTML and PDF documents in a continuous vertical
scrolling view, including on phones. The compact toolbar provides a collapsible
navigation panel with separate contents, book list and full-book search tabs.
PDF pages retain selectable text, links, outline navigation, highlighted search,
zoom and rotation through the locally bundled Mozilla PDF.js viewer components.

Reading settings include light, warm-paper, dark and system themes, font family
and size, line height, paragraph spacing, content width, alignment and indentation.
Publisher typography can be retained. Settings belong to the signed-in account.
Text anchors preserve the visible passage when the layout changes; older saved
positions remain supported. Images open in an enlarged view and footnotes open
in a small dialog without leaving the current passage.

Search results offer separate actions to inspect a match or resume reading.
Inspecting a match temporarily preserves the original reading position; use
the return action or explicitly continue from the inspected position. Ctrl/Cmd+F
opens full-book search, Page Up/Down or Space scrolls, and H toggles the toolbar.
TXT headings are indexed when the text cache is built, so opening the contents
does not download the entire book to the browser. EPUB and TXT continue to mount
only the surrounding chapters or chunks.

The interface takes inspiration from `Readest <https://github.com/readest/readest>`_
(quiet reading surface, separate navigation and typography controls) and
`Koodo Reader <https://github.com/koodo-reader/koodo-reader>`_ (reading controls
and document navigation). The layout and reader integration
are implemented for this application. PDF rendering uses the pinned upstream
`PDF.js <https://github.com/mozilla/pdf.js>`_ components with their bundled license;
see ``vendor-build/pdfjs/README.md`` for reproducible asset builds.

Running the indexer
-------------------

Example user Crontab entry to have the indexer at least once a day::

        22 5    * * *   /usr/bin/recollindex



Issues
======

Can't open files when Recoll WebUI is running on a server
---------------------------------------------------------
By default links to files in the result list correspond to the file's
physical location on the server. If you have access to the file tree
via a local mountpoint or eg. ftp/http you can provide replacement
URLs in the WebUI settings. If in doubt, ask your network administrator.

Opening files via local links
-----------------------------
For security reasons modern browsers prevent linking to local content from
'remote' pages. As a result URLs starting with file:// will not, by default,
be opened when linked from anything else than pages in file:// or when
accessed directly from the address bar. Here's ways of working around it:

Firefox
~~~~~~~
1. Insert contents of ``examples/firefox-user.js`` into
   ``~/.mozilla/firefox/<profile>/user.js``
2. Restart Firefox

Chrom{e,ium}
~~~~~~~~~~~~
Install *LocalLinks* extension:

* http://code.google.com/p/locallinks/
* https://chrome.google.com/webstore/detail/locallinks/jllpkdkcdjndhggodimiphkghogcpida

Opera
~~~~~
1. Copy ``examples/opera-open.sh`` into your PATH (eg. ``/usr/local/bin``)
2. Go to ``Tools > Preferences > Advanced > Programs > Add``
3. In ``Protocol`` field enter ``local-file``
4. Select ``Open with other application`` and enter ``opera-open.sh``
5. In WebUI settings replace all ``file://`` with ``local-file://``
