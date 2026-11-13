# 账号、书架、历史与阅读进度

所有账号共享 Recoll 文档库，分别保存个人书架、搜索历史、最近阅读、阅读位置和偏好。管理员管理账号；普通用户与管理员的文档范围相同。SQLite 只保存应用数据，正文和全文索引仍由原文件及 Recoll 管理。

## 初始化与启动

需要系统 Recoll Python 扩展、`recollindex`、Python 3 和 `requirements.txt` 中的依赖。Bottle 已随仓库提供。若用虚拟环境，请让它能导入系统 Recoll 扩展，例如创建时使用 `--system-site-packages`。

以实际服务账号执行，管理命令和服务使用同一个绝对数据库路径：

```sh
python3 -m pip install -r requirements.txt
export RECOLL_WEBUI_DB_PATH=/var/lib/recoll-webui/app.sqlite3
python3 manage.py init --username admin
python3 webui-standalone.py -c /path/to/recoll-config -a 127.0.0.1 -p 8080
```

部署者先创建并授权 `/var/lib/recoll-webui`，或改用服务账号有写权限的私有持久目录。默认路径为服务账号的 `~/.local/share/recoll-webui/app.sqlite3`。不要放入源码、`static`、文档库、Recoll 临时目录或 NFS/SMB。初始化生成数据库、`app.sqlite3.secret` 持久签名密钥及维护锁文件；目录使用 0700，新数据库和密钥使用 0600。全部本机 WSGI worker 使用相同路径与密钥。

密码在终端提示中输入，长度为 15–128 个字符。非交互管理支持 `--password-stdin` 从标准输入读一行，不支持密码命令行参数。首管理员和新账号首次登录须改密；改密后重新登录。数据库未初始化、版本不兼容或认证存储不可用时，服务返回错误，不开放匿名访问。

已有站点升级时，先停止工作进程、安装依赖并完成初始化，再启动新代码。旧匿名浏览器 Cookie 不会变成账号，旧未绑定会话的阅读令牌失效。首次启用无需迁移 Recoll 索引。

## 使用

- **账号**：登录后从导航进入个人设置，修改字号、记录开关、密码并撤销登录设备。管理员从“账号管理”创建、停用、重置、调整角色或删除账号。最后一个可用管理员受事务保护。
- **搜索数量**：默认最多返回前 500 条；用户可在搜索设置的 Max results 中修改，设为 0 表示不限。Results per page 独立控制每页数量，个人设置优先于默认值。
- **书架**：搜索结果或阅读器点击“加入书架”；“我的书架”显示标题、格式和已同步的阅读百分比，点击“继续阅读”按稳定文档 ID 打开。移出书架保留历史和位置，清空历史保留书架。
- **搜索历史**：通过页面主动提交且执行成功的搜索生成一条记录；刷新、翻页、预览、下载和导出不重复记录。零结果是成功搜索；失败搜索不显示在历史中。旧 GET 书签/OpenSearch 可查询，但不自动写历史。历史中“重新搜索”产生新事件。
- **最近阅读**：只有正文实际渲染成功后才提交打开回执；目录扫描、manifest 和预取不会产生已读记录。同一文档聚合为一项，可从首页或最近阅读继续。
- **进度**：TXT 保存块和块内位置，EPUB 保存章节及章节内位置，CHM 保存内部章节和滚动位置，PDF 保存页号和页内位置；其他格式取决于阅读引擎提供的定位能力。文件版本改变时提示用户选择，不自动用初始化的 0% 覆盖旧位置。移动屏幕或字号变化可能使章节内恢复位置近似。
- **并发**：位置变化约每 6 秒合并保存，换书及页面隐藏时补交。多个设备冲突时可以“采用已保存位置”或明确从当前设备继续；不会以最大百分比覆盖回读位置。同步失败时显示状态并保留当前页面内待同步位置，页面关闭瞬间的请求不保证送达，重载后以内库已提交进度为准。
- **目录阅读**：按文档身份恢复该目录最后一本，不依赖列表下标。扫描最多 1000 本；目标在截断范围外时单独验证并纳入列表，目标缺失时显示提示。
- **旧浏览器数据**：个人设置提供主动选择导入已知旧 Cookie、字号和本地阅读位置的入口。默认保留已有服务端进度；成功导入的旧键才删除，不伪造旧阅读时间，不导入旧目录下标。切号后不读取旧账号缓存。

书架可保存目前阅读器不支持的文档引用；容器内部 `ipath` 文档保留原搜索预览/下载方式，直接继续阅读会明确拒绝。文件移动不自动跟踪，文件消失或移出允许根目录后不会用同一搜索序号的另一文档替代。

## 保留与清理

| 数据 | 默认保留规则 |
| --- | --- |
| 搜索历史 | 90 天，最多 500 次成功搜索 |
| 最近阅读 | 180 天，最多 1000 篇 |
| 非书架阅读进度 | 365 天，最多 1000 项；书架文档进度不受自动过期/数量淘汰影响 |
| 目录续读 | 365 天，最多 1000 项 |
| 书架 | 主动移除或删除账号前保留 |
| 登录会话 | 空闲 7 天、绝对 30 天；可主动撤销 |

三个记录开关相互独立：关闭最近阅读仍可同步进度，关闭同步保留已有服务端位置。移除最近阅读保留位置；清除该书进度或“清空全部阅读数据”删除相应位置和目录续读。删除或更改记录开关会推进代际，旧标签页迟到请求无法复活数据；重新打开页面后建立新上下文。书架没有自动完读移除行为。

列表读取即时过滤过期数据；定期物理清理使用小批事务和维护锁：

```sh
python3 manage.py cleanup
```

可安装 `examples/recoll-webui-cleanup.service` 与 `.timer` 每天运行；先调整用户、Python 和路径，保持单一调度。清理不会删除原文档或 Recoll 索引。反向代理日志、浏览器历史和已有备份独立于应用内历史删除。

## HTTPS 与 WSGI

`SCRIPT_NAME` 决定 WSGI 挂载前缀，页面、API、静态资源和 Cookie 使用对应路径。HTTPS 根据请求协议自动设置 Secure Cookie。反代部署需要在服务中设置 `RECOLL_WEBUI_TRUSTED_PROXIES` 为实际代理 IP/CIDR；仅受信代理的 `X-Forwarded-For` 和 `X-Forwarded-Proto` 生效。HTTPS-only 站点可设置 `RECOLL_WEBUI_COOKIE_SECURE=true`。不要把所有公网地址设置为可信代理。

退出、改密、停用、密码重置和角色调整会撤销相关服务端会话。阅读令牌绑定账号和会话，正文请求仍须登录并重新检查允许目录；同一个浏览器配置文件同时使用一个账号，其他设备可独立登录。HTML/EPUB/CHM 等正文通过沙箱及 CSP 禁止脚本和表单，不允许正文取得账号页面权限。

`webui_nosettings` 锁定搜索展示偏好，`webui_maxperpage` 和 `webui_nojsoncsv` 在服务端执行；这些限制不阻止改密、撤销会话或删除个人历史。SQL 参数绑定、Argon2id 密码哈希、写接口 CSRF 校验和登录限速默认启用。

## 备份、恢复与本地管理

在线备份使用 SQLite backup API，目标必须是尚不存在的新文件：

```sh
python3 manage.py backup /private/backups/recoll-2026-11-13.sqlite3
```

另行备份 `app.sqlite3.secret` 并保护其访问权限，建议每日轮换保留 7 份，升级前额外备份。不在服务运行时只复制数据库主文件。原文档与 Recoll 索引使用各自备份方案。

恢复前停止全部 standalone/WSGI 工作进程，以服务账号使用同一个数据库路径执行：

```sh
python3 manage.py restore /private/backups/recoll-2026-11-13.sqlite3 --workers-stopped
```

恢复检查 schema、完整性与外键，撤销恢复出来的会话并推进记录代际。保留当前持久签名密钥即可，若是新机器则恢复原密钥到 `app.sqlite3.secret` 并设置 0600；密钥遗失时在停止服务后运行 `init` 可生成新密钥，旧会话上下文全部失效。恢复后启动服务并重新登录。

本地应急管理同样需要服务账号/数据库文件权限：

```sh
python3 manage.py create-user reader --display-name 读者
python3 manage.py reset-password admin --enable --admin
```

数据库 schema 当前为 v1；初始化幂等，程序不会在网页请求内建表或隐式迁移。高于/低于支持版本的数据库均拒绝使用。初始化根据实际 SQLite 版本选择日志模式：已包含 WAL-reset 修复的已知版本使用 WAL，其他版本保守使用 DELETE；当前测试环境 SQLite 3.45.1 使用 DELETE。连接启用外键，写事务使用 3 秒有界等待，不跨线程复用连接。

## 验证与代码位置

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q tests
node --test tests/*.test.mjs
```

集成测试使用临时 Recoll 文档库、真实索引和 SQLite，不连接运行中的服务。浏览器测试使用 Playwright/Chromium，找不到浏览器时跳过，可用 `RECOLL_TEST_CHROMIUM` 指定可执行文件。PDF.js 运行时在本地提供，无 CDN 依赖；其版本、来源和更新步骤见 `vendor-build/pdfjs/README.md`。

`accounts.py` 管数据库与事务，`account_web.py` 管认证及账号页面，`webui_data.py` 接搜索事件、文档引用、书架和进度 API；`static/accounts.js` 管账号页面交互与跨标签页通知，`static/reader/account-sync.js` 管阅读位置同步。原规划和追加书架的数据约束保留在 `multi-account-and-history-plan.md`。
