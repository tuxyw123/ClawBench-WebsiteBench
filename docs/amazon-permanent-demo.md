# Amazon clone 长期公开演示站

## 定位

该服务用于让维护者和评审者长期浏览、交互当前 Amazon 离线 clone。它是共享
demo，不是 Harbor 正式评分时的 reference。正式实验必须继续创建隔离、可
reset 的 reference，并阻断 Agent 与 candidate 对公开 demo 域名的网络访问。

Viewer 的 `/amazon/` 继续展示数据、截图和未来评分；完整动态 clone 使用独立
origin。当前实现包含大量根路径链接、表单、重定向和 `Path=/` Cookie，因此
推荐最终域名为 `amazon.website-bench.com`，不要把它反向代理到
`website-bench.com/amazon/`。

## 一键创建长期实例

1. 确保 GitHub 仓库为 public，且 `main` 包含根目录 `render.yaml`。
2. 打开
   [Deploy to Render](https://render.com/deploy?repo=https://github.com/tuxyw123/ClawBench-WebsiteBench)。
3. 在 Blueprint 审核页为 `AMAZON_BASIC_AUTH_PASSWORD` 填入单独提供的
   demo 密码。用户名已经固定为 `bench`；密码不会写入 Git 或 Docker 镜像。
4. 确认付费 Starter web service 和 1 GB persistent disk，再批准部署。
5. 等待 `/healthz` 通过。Render 会先分配稳定的 `onrender.com` HTTPS URL。

Blueprint 只运行一个实例，SQLite、WAL 和 SHM 都位于 `/data` 持久卷；admin
仍仅监听 `127.0.0.1:8154`，且 token 由 Render 生成。公网容器不会运行本地
SMTP catcher/inbox，也不会配置真实 SMTP、支付、履约或 Amazon API。

## 访问控制与数据边界

- `/healthz` 无需认证，仅做只读数据库检查，不创建 session。
- 其余页面、静态资源和 POST 均使用 HTTP Basic Auth。
- 生产 profile 启用 Secure/HttpOnly/SameSite Cookie、HSTS 和
  `noindex, nofollow, noarchive`。
- 只能输入虚构账号、邮箱、电话、地址、密码和支付场景数据；不要输入任何真实
  个人信息、真实凭据或真实支付资料。
- SMTP 保持 `LOCAL_ONLY`，因此公开访客无法通过真实邮箱完成验证流程；这属于
  安全边界，而不是生产身份系统。

若出现真实个人信息，先停止公开访问，再通过 loopback admin 或一致的 SQLite
备份/重置流程删除数据；不得把运行数据库、日志或 admin token 提交到 Git。

## 持久化、备份与更新

Render 的持久卷只保留 `/data` 下的文件，并让该服务保持单实例。每天的磁盘
snapshot 是灾难恢复辅助；对正在写入的 SQLite，应在 reset 或高风险升级前使用
SQLite backup API 生成一致备份，不要只复制运行中的 `.sqlite3` 主文件。

在 Render 的私有 Shell 中执行项目自带的一致备份命令：

```bash
gosu amazon-clone:amazon-clone python /app/clone/backup_db.py
```

若服务所有者通过 Render 的 GitHub integration 连接本仓库，
`checksPass` 会在 CI 通过后自动部署 `main`。仅通过 public repository URL
创建的第三方服务不会自动获得后续部署：CI 通过后需要使用
**Manual Deploy → Deploy latest commit**，或配置只在 CI 成功后调用的 deploy
hook。已有卷不会因为 fixture 改动而自动重置：

- 兼容更新通过 schema migration 保留共享 demo 状态；
- 不兼容更新先备份，再由维护者通过 loopback admin 受控 reset；
- admin 端口、token、state、journal、outbox 和 reset 路由永远不经过公网。

## 绑定域名

基础 `onrender.com` URL 验证通过后，在 Render 服务中添加
`amazon.website-bench.com`。由 `website-bench.com` 的 Cloudflare 所有者
完成 Render 显示的域名验证步骤；验证成功前保留 `onrender.com` 作为永久
origin。不要覆盖 Viewer 已使用的根域名或 `/amazon/` 路由。

## 上线前门禁

公网访问前仍需记录 Amazon 商标、图片、字体和其他素材的所有权、许可证及
再分发边界。公开 demo 上线不改变项目 release gates，也不能替代真实 Harbor
NOP/oracle 校准、隔离审核和人工浏览器验收。
