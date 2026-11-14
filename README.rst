=================
Recoll 文库 WebUI
=================

基于 Recoll 全文索引的私有文库与浏览器阅读应用，适合检索、收藏和连续阅读本地小说、电子书及文档。
后端使用 Python、Bottle、Waitress 和 SQLite，前端提供中文界面及手机、桌面布局。

本仓库是 `uiharuayako/recollwebui-novel <https://github.com/uiharuayako/recollwebui-novel>`_，
在 `medoc92/recollwebui <https://framagit.org/medoc92/recollwebui>`_ 的搜索界面基础上加入账号、书架和阅读功能。
所有账号共享部署者配置的文库，各自保存收藏、偏好、历史与阅读位置。
Recoll 负责索引与检索；本应用不创建或管理 Recoll 索引，也不提供文档上传。

主要功能
========

* **全文搜索**：支持 Recoll 查询语法、目录和日期筛选、排序、分页、命中摘要，以及 CSV / JSON 导出。
  搜索结果可查看命中、阅读整个文件夹、单独阅读、下载、加入书架或分享。
* **个人书架**：收藏单本或整个文件夹，切换网格与列表布局，显示已同步进度并继续阅读。
  文件夹收藏会恢复上次阅读的文件及位置；书架的标题筛选只作用于当前页。
* **连续阅读**：TXT、EPUB、HTML 和 PDF 提供连续纵向滚动；阅读器包含目录、书单、书内搜索、
  全屏、专注模式及快捷键帮助。可调整主题、字体、字号、行距、段距、宽度和缩进。
* **阅读同步**：按账号保存阅读位置与排版偏好，在换书或页面隐藏时补交位置。
  多设备位置冲突提供明确选择；临时查看搜索命中时保留原来的阅读位置。
* **首页发现**：展示继续阅读、个人搜索推荐和每日推荐。搜索推荐可手动管理，候选池最多 60 条；
  每日推荐从允许访问的文库抽取最多 6 本，按北京时间每天更新。
* **账号管理**：管理员可创建、停用、重置或删除账号，管理注册开关和可选的激活码。
  注册默认关闭，不需要邮箱。用户可改密、撤销登录设备并控制个人记录开关。
* **公开分享**：分享单本或文件夹，链接持有人无需登录即可阅读。默认有效期 7 天，可设为 1–3650 天，
  并在“我的分享”中续期或取消。匿名阅读不会写入账号的书架、历史或进度。

支持的阅读格式
==============

* 文本与网页：TXT、Markdown、HTML / HTM / XHTML、XML、MHTML。
* 电子书与文档：EPUB、MOBI、AZW / AZW3、FB2、CHM、DOCX、PDF。
* 漫画归档：CBZ、CBR、CBT、CB7。

TXT 使用服务端分块缓存与标题目录，EPUB 按章节加载；两者只挂载阅读位置附近的内容。
PDF 使用仓库内置的 Mozilla PDF.js，支持可选文本、目录、搜索高亮、缩放和旋转。
扫描 PDF 需要先有 OCR 文本层才能搜索文字。
CHM 需要系统提供 ``7z`` 命令，解包后在隔离沙箱内加载原页面，兼容书内脚本、目录及嵌套页面。
其他格式使用内置的 Kookit 阅读运行时，实际排版与定位能力随格式而异。

文件夹阅读最多扫描 1000 本可阅读文件，包含子目录。
Recoll 容器内部的 ``ipath`` 条目仍可使用搜索预览与下载，不支持直接进入阅读器。
文档移动后不会自动追踪；已失效或移出允许目录的文档需要重新检索。

安装与首次运行
==============

依赖
----

* Linux、Python 3，以及系统安装的 Recoll、``recollindex`` 和 Recoll Python 扩展。
  Recoll 不是 pip 依赖；运行应用的解释器必须能导入 ``recoll``、``rclextract``、``rclconfig``。
* ``requirements.txt`` 中的 ``argon2-cffi`` 和 ``waitress``；Bottle 已随仓库提供。
* 支持 ES modules 的现代浏览器；CHM 另需 ``7z``。
* Node.js 仅用于 JavaScript 测试和重新构建前端依赖，正常部署无需安装。

准备索引
--------

先通过 Recoll 配置文库根目录（``recoll.conf`` 中的 ``topdirs``），并建立索引。
以下命令中的路径应替换为自己的 Recoll 配置目录::

    recollindex -c /path/to/recoll-config

服务账号需要能够读取配置、索引与原始文档。索引更新也由部署者安排，例如定时运行同一命令。
搜索范围和可读取文件由这些 Recoll 配置共同决定，各账号使用相同文库范围。

安装并初始化账号
----------------

在已安装系统 Recoll Python 扩展的解释器上创建虚拟环境，保留系统包访问::

    git clone https://github.com/uiharuayako/recollwebui-novel.git
    cd recollwebui-novel
    python3 -m venv --system-site-packages .venv
    . .venv/bin/activate
    python -m pip install -r requirements.txt
    python -c "from recoll import recoll, rclextract, rclconfig"

以实际运行服务的操作系统账号初始化数据库::

    export RECOLL_WEBUI_DB_PATH="$HOME/.local/share/recoll-webui/app.sqlite3"
    python manage.py init --username admin

命令会交互式要求输入 15–128 个字符的密码。首次登录管理员账号后必须改密，再重新登录。
数据库未初始化时，应用返回设置错误，不会开放匿名文库访问。

数据库应位于本机私有持久目录，放在源码、静态资源和文库目录之外。
初始化会生成数据库和同目录的 ``app.sqlite3.secret`` 签名密钥；所有本机工作进程需要共享同一组文件。
后续管理命令和服务始终使用相同的 ``RECOLL_WEBUI_DB_PATH``。

启动
----

在同一虚拟环境和环境变量下运行::

    python webui-standalone.py -c /path/to/recoll-config

默认访问 http://127.0.0.1:8080/。常用参数：

* ``-a / --addr``：监听地址，默认 ``127.0.0.1``；局域网部署可设为 ``0.0.0.0``。
* ``-p / --port``：端口，默认 ``8080``。
* ``-t / --threads``：Waitress 工作线程数，默认 ``8``。
* ``-c / --config``：Recoll 配置目录，优先于 ``RECOLL_CONFDIR``。

更多选项可运行 ``python webui-standalone.py --help``。
长期运行可使用 `systemd 部署说明 <README-systemd.rst>`_ 和 ``examples/recoll-webui.service``；
部署前调整账号、解释器、代码路径、Recoll 配置和数据库路径。
WSGI 入口为 ``webui-wsgi.py`` 中的 ``application``，同样需要预先初始化账号并配置这些路径。

配置
====

环境变量
--------

``RECOLL_CONFDIR``
    主 Recoll 配置目录；不指定时由 Recoll 使用默认配置。
``RECOLL_EXTRACONFDIRS``
    同时查询的其他 Recoll 配置目录，以空格分隔；包含空格的路径需在变量值中加引号。
``RECOLL_WEBUI_DB_PATH``
    SQLite 数据库绝对路径，默认 ``~/.local/share/recoll-webui/app.sqlite3``，其中 ``~`` 属于服务账号。
``RECOLL_WEBUI_TRUSTED_PROXIES``
    可信反向代理的 IP / CIDR，以空格或逗号分隔。只有这些代理可提供客户端地址和 HTTPS 转发信息。
``RECOLL_WEBUI_COOKIE_SECURE``
    默认 ``auto``，根据请求协议设置 Secure Cookie；HTTPS-only 部署可设为 ``true``。
``RECOLL_TMPDIR``
    临时文件及阅读缓存根目录，未设置时使用 ``TMPDIR`` 或系统临时目录；需给服务账号写权限并预留空间。

搜索设置
--------

用户在“搜索设置”中保存个人偏好；部署者可在主 ``recoll.conf`` 中设置初始值，例如::

    webui_maxresults = 500
    webui_perpage = 500
    webui_dirdepth = 2
    webui_context = 30
    webui_maxchars = 500

``webui_maxresults`` 是总结果数，``0`` 表示不限；``webui_perpage`` 单独控制分页。
已有个人偏好优先于初始值。目录特别多时，可降低 ``webui_dirdepth``，减少目录树构建开销。
常用的其他配置包括 ``webui_stem``、``webui_collapsedups``、``webui_defaultsort``、
``webui_csvfields``、``webui_synonyms`` 和追加到全部查询的 ``webui_queryfrag``。

服务端限制使用 ``webui_nosettings = 1`` 锁定搜索展示偏好，
``webui_maxperpage`` 限制每页数量，``webui_nojsoncsv = 1`` 关闭 CSV / JSON 导出。
这些设置不影响改密、设备撤销和个人历史删除。
原文件链接可通过 ``webui_mount_/server/path`` 替换路径；浏览器阅读和下载无需配置客户端本地挂载。

HTTPS 与子路径
--------------

对外访问时，在 Waitress / WSGI 前配置 HTTPS 反向代理，并把实际代理地址加入
``RECOLL_WEBUI_TRUSTED_PROXIES``，由代理传递 ``X-Forwarded-Proto`` 和 ``X-Forwarded-For``。
WSGI 服务器也必须信任该代理，并将外部 HTTPS 正确传递为 ``wsgi.url_scheme=https``。
当前 standalone 使用的 Waitress 默认清理未受信的转发头，仅设置应用的可信代理变量不足以完成 HTTPS 反代配置；
此类部署应使用在服务器层配置了代理信任的 WSGI 入口。
子路径部署使用 WSGI ``SCRIPT_NAME``，页面、API、静态资源与 Cookie 会使用相应前缀。

退出、改密、停用、密码重置和角色调整会撤销相关登录会话。
公开分享链接独立于创建者的登录会话；到期、取消或停用所有者后，后续正文与资源请求也会失效。
链接持有人拥有对应单本或文件夹的阅读权限，分享文件夹时其可阅读内容会随目录更新。

账号、数据与维护
================

管理员可在“账号管理”中开放注册、要求激活码或更换激活码。
注册配置保存在数据库旁的 ``app.sqlite3.registration.json``，只保存激活码哈希；配置不存在时注册关闭。
数据库、签名密钥和注册配置都应保留在部署环境中，实际密码和激活码不要写入源码。

默认保留规则：搜索历史 90 天 / 500 次，最近阅读 180 天 / 1000 篇，
非书架阅读进度与目录续读 365 天 / 1000 项。
书架内容及其对应进度受收藏保护；移出书架保留历史和位置，清空历史保留书架。
搜索历史、最近阅读与进度同步可分别开关。

以服务账号、相同数据库路径执行维护命令::

    python manage.py create-user reader --display-name 读者
    python manage.py reset-password admin --enable --admin
    python manage.py cleanup
    python manage.py backup /private/backups/recoll-backup.sqlite3

备份目标必须尚不存在，另行备份签名密钥和注册配置；原文档与 Recoll 索引分别备份。
恢复前停止全部 standalone / WSGI 工作进程，再执行::

    python manage.py restore /private/backups/recoll-backup.sqlite3 --workers-stopped

已有部署升级前先备份并停止工作进程，再安装依赖和更新代码。
当前 schema 为 v1，分享和搜索推荐表在首次使用时增量创建，已有数据库无需重新初始化。
详细的数据保留、恢复、注册、代理和权限说明见 `账号与阅读功能文档 <docs/accounts-and-history.md>`_。

开发与验证
==========

在能导入系统 Recoll 扩展的虚拟环境中运行::

    python -m pip install -r requirements-dev.txt
    python -m pytest -q tests
    node --test tests/*.test.mjs

集成测试建立临时 Recoll 索引和 SQLite 数据库，不连接正在运行的服务。
浏览器测试使用 Playwright 和 Chromium，未找到浏览器时跳过；可通过 ``RECOLL_TEST_CHROMIUM`` 指定可执行文件。
真实 CHM 样本检查另需 ``RECOLL_TEST_CHM_MANIFEST`` 指向已有解包缓存的 ``manifest.json``。
运行全部 CHM 检查还需要 ``7z``。

主要代码位置：

* ``webui.py``：Recoll 搜索、文档预览与下载、阅读器内容及缓存。
* ``accounts.py`` / ``account_web.py``：SQLite、认证、个人设置和账号管理。
* ``webui_data.py`` / ``shares.py``：书架、历史、进度、推荐与公开分享。
* ``views/`` / ``static/``：Bottle 模板、文库交互及阅读器。
* ``manage.py`` / ``examples/``：初始化、备份恢复、清理和 systemd 示例。

阅读器静态依赖已提交到仓库，运行时不从 CDN 下载。
PDF.js 的版本、许可证与构建命令见 `PDF.js 运行时说明 <vendor-build/pdfjs/README.md>`_；
界面设计参考与验证范围见 `UI/UX 调研记录 <docs/reader-ui-research.md>`_。
``docs/local-deployment.md`` 记录维护者本机部署，通用部署请使用上面的步骤。

项目来源
========

* `Recoll <https://www.recoll.org/>`_ 提供全文索引与检索。
* `medoc92/recollwebui <https://framagit.org/medoc92/recollwebui>`_ 是本项目的直接上游，
  更早源自 `koniu/recoll-webui <https://github.com/koniu/recoll-webui>`_。
* 多格式阅读使用 `Koodo Reader / Kookit <https://github.com/koodo-reader/koodo-reader>`_ 运行时，
  PDF 渲染使用 `Mozilla PDF.js <https://github.com/mozilla/pdf.js>`_；随附资源保留各自许可信息。
* 界面与交互参考 Readest、Koodo Reader 等开源阅读器，具体来源见上述调研记录。
