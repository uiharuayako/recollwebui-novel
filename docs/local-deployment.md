# 本机 8081 部署

2026-11-13 已完成账号、书架、历史及进度功能的部署初始化，并通过实际文库的浏览器验证。

## 当前配置

| 项目 | 值 |
| --- | --- |
| 本机入口 | http://localhost:8081/ |
| systemd 服务 | `recoll-webui.service`，已启用开机自启 |
| 服务文件 | `/etc/systemd/system/recoll-webui.service` |
| 运行账号 | `uiharu` |
| 工作目录 | `/home/uiharu/recoll-webui` |
| Python | `/usr/bin/python3`，当前为 Python 3.12 |
| Recoll 配置 | `/home/uiharu/.recoll-novel` |
| 默认搜索数量 | 默认最多返回 500 条，每页 500 条（`webui_maxresults = 500`、`webui_perpage = 500`） |
| SQLite | `/home/uiharu/.local/share/recoll-webui/app.sqlite3` |
| 签名密钥 | `/home/uiharu/.local/share/recoll-webui/app.sqlite3.secret` |

服务沿用原来的 `0.0.0.0:8081` 监听配置。数据库路径使用运行账号主目录下的默认值；维护命令应以 `uiharu` 执行，或显式指定同一个数据库路径。数据库位于本机文件系统，文档和 Recoll 索引保留原位置。

搜索默认最多返回前 500 条。本机 Recoll 配置同步使用该默认值，将原来的默认总结果数 200 条调整为 500 条。这不是不可修改的硬上限：用户随时可在 Settings 的 Max results 中调高、调低，或设为 0 取消限制；账号已保存的个人搜索偏好优先使用。Results per page 独立控制分页，默认也是 500 条。

管理员账号为 `admin`，首次登录必须修改密码。初始登录信息保存在仅本人可读的 `/home/uiharu/.local/share/recoll-webui/admin-initial-login.txt`；改密后可删除该文件。密码不写入仓库文档。

## 本次修复

旧服务进程仍使用升级前的 Python 代码，却读取到了新模板，导致访问返回 500。系统 Python 同时缺少 Argon2 密码哈希依赖，账号数据库也尚未初始化。

已将 `requirements.txt` 依赖安装到 `uiharu` 的 Python 用户目录 `/home/uiharu/.local/lib/python3.12/site-packages`，初始化私有 SQLite 数据库及管理员，并重载原 systemd 服务。服务继续使用系统 Recoll 扩展，运行不依赖临时测试环境。原 unit 配置和索引未改动。

## 日常维护

查看服务与错误：

```sh
systemctl status recoll-webui.service --no-pager
journalctl -u recoll-webui.service -n 80 --no-pager
curl -I http://127.0.0.1:8081/login
```

后续升级需要在停止服务后安装依赖、按版本说明处理数据，再启动。当前服务使用 Python 用户目录安装依赖，可在 `uiharu` 身份下使用：

```sh
cd /home/uiharu/recoll-webui
sudo systemctl stop recoll-webui.service
uv pip install --python /usr/bin/python3 \
  --target /home/uiharu/.local/lib/python3.12/site-packages \
  -r requirements.txt
sudo systemctl start recoll-webui.service
```

单纯重载已准备好的代码使用 `sudo systemctl restart recoll-webui.service`。系统级服务管理需要本机管理员权限。若升级系统 Python 主次版本，应重新为新解释器安装依赖并确认 Recoll 扩展兼容。

数据维护命令：

```sh
cd /home/uiharu/recoll-webui
/usr/bin/python3 manage.py cleanup
/usr/bin/python3 manage.py backup /private/backups/recoll-backup.sqlite3
```

备份目录应预先创建并仅本人可读写；备份目标必须不存在，并需另行备份签名密钥。保留规则、账号恢复及反向代理配置见 [账号与阅读功能说明](accounts-and-history.md)。

## 验证结果

- 登录页正常；管理员登录后进入首次改密页面。
- 临时普通账号可搜索真实索引，并生成一条搜索历史。
- 搜索结果可加入书架，书架列表正常。
- 实际 TXT 文档渲染后生成阅读历史，阅读位置保存到 SQLite，刷新阅读器后可恢复。
- 搜索结果的标题阅读、阅读本书及文件夹阅读保留原结果页地址；“返回结果”保留查询、筛选、排序和页码，返回时不会新增搜索历史。相关 38 项集成测试通过，8081 已完成浏览器实测。
- 8081 的设置页已实测默认总结果数 500 条、每页 500 条，并可保存为 1000、2、0 或 500；0 表示不限。
- 浏览器未出现 JavaScript 错误；验证结束后已删除临时账号及其私有记录。
