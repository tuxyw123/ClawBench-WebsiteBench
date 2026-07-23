# WebsiteBench Viewer · one-click Cloudflare deployment

This self-contained directory in the `ClawBench-WebsiteBench` repository
contains a public-facing WebsiteBench Viewer snapshot and a small Cloudflare
Worker. It does not contain benchmark secrets, hidden fixtures, private run
artifacts, credentials, or writable services. Publishing this Viewer does not
mean the full benchmark is release-ready; benchmark-wide rights and
redistribution review remains pending.

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/tuxyw123/ClawBench-WebsiteBench/tree/main/deploy/websitebench-cloudflare-worker)

## 域名所有者操作

1. 点击上方 **Deploy to Cloudflare**。
2. 登录您自己的 GitHub 和 Cloudflare 账号。
3. 选择拥有 `website-bench.com` 的 Cloudflare 账号。
4. 确认创建仓库和 Worker，保持默认部署配置并完成部署。
5. 部署完成后打开：
   - `https://website-bench.com/`
   - `https://website-bench.com/amazon`

`wrangler.jsonc` 已将 `website-bench.com` 声明为 Worker Custom Domain。
Cloudflare 会在部署过程中创建域名记录并签发证书，不需要手工填写 DNS。
一键部署前，原始 `ClawBench-WebsiteBench` 仓库必须已经设为 Public。

如果 Cloudflare 提示同名 DNS 记录冲突，请先停止，不要随意删除邮件或其他
业务记录。只需让网站维护者检查 `website-bench.com` 根记录是否仍指向旧网站。

## How updates work

The Worker reads the canonical public snapshot from this repository's `main`
branch and keeps a bundled snapshot as a fallback. By deploying this template,
the domain owner explicitly authorizes the repository maintainer to update
content shown on `website-bench.com` without another Cloudflare approval. New
Viewer paths such as `/ebay` or `/reddit` can therefore be published upstream
without repeating domain setup. The owner can revoke that continuing trust at
any time by removing the Worker Custom Domain or by changing the Worker to use
only its bundled snapshot.

The Worker accepts only `GET` and `HEAD`. It strips upstream cookies, adds
basic browser security headers, and does not intentionally collect credentials
or user-submitted data. Standard Cloudflare platform metadata may still be
handled according to the deploying account's settings.

During scored benchmark runs, block evaluated agents from accessing this
public Viewer. It exposes task routes, journeys, and visualization metadata
for human inspection and must not become an evaluation side channel.

## Local verification

```bash
npm ci
npm run check
```

## Independent deployment

```bash
npm ci
npm run deploy
```

The deploying Cloudflare account must own an active `website-bench.com` zone.
The apex Custom Domain covers every URL path; `www.website-bench.com` is not
included unless it is added separately.

## Rights notice

WebsiteBench is a research benchmark viewer and is not affiliated with Amazon
or other referenced websites. Screenshots and marks remain subject to their
respective owners' rights. Their presence in this public-facing research
snapshot does not represent completion of the benchmark-wide redistribution
review.
