# Amazon clone permanent demo

This directory packages the full Python/SQLite Amazon clone as a single,
long-running container. It is a shared demonstration surface only. Formal
WebsiteBench scoring must continue to use isolated, resettable Harbor
reference instances and must block this public host from the Agent and
candidate networks.

## One-click deployment

[Deploy the current `main` branch to Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Ftuxyw123%2FClawBench-WebsiteBench)

The Blueprint creates one paid Starter web service in Singapore and attaches a
1 GB persistent disk. The owner must review and approve that paid resource in
their Render account. A free or diskless service is not the permanent profile:
its filesystem is ephemeral and would lose the SQLite database after a restart
or deployment.

The Blueprint fixes the non-secret HTTP Basic username to `bench` and asks the
owner for `AMAZON_BASIC_AUTH_PASSWORD` during approval. Use a new, strong demo
password and share it separately; never commit it. Every storefront route
except `/healthz` requires this credential.

After approval, Render assigns a stable `*.onrender.com` origin. Keep that URL
until a separately owned custom domain is ready. The recommended custom host is
`amazon.website-bench.com`; the clone cannot safely run below
`website-bench.com/amazon/` because its links, forms, redirects, assets, and
cookies intentionally use root-relative paths.

## Runtime boundary

- Public storefront: `0.0.0.0:$PORT`
- Health check: public `GET` or `HEAD /healthz`, with no session or journal write
- Admin/reset: `127.0.0.1:8154`, protected by a generated secret and never
  published
- Database: `/data/amazon.sqlite3` on the single attached disk
- SMTP: unconfigured, therefore `LOCAL_ONLY`
- Cookies: `Secure; HttpOnly; SameSite=Lax`
- Indexing: `X-Robots-Tag: noindex, nofollow, noarchive`

The image starts as root only long enough to make the attached disk writable,
then uses `gosu` to run the clone as the unprivileged `amazon-clone` user. Do
not add a public route to the admin listener or copy the generated admin token
into logs, docs, Viewer data, or URLs. Do not configure real payment,
fulfillment, identity, or Amazon services.

## Operating policy

Visitors must use fictional names, email addresses, phone numbers, addresses,
credentials, and payment scenarios. This demo is shared mutable state and is
not a production commerce or identity service.

Before any destructive reset, create an application-consistent SQLite backup
from the service's private shell:

```bash
gosu amazon-clone:amazon-clone python /app/clone/backup_db.py
```

The command uses SQLite's online backup API, verifies the copy with
`PRAGMA integrity_check`, and prints its path, byte count, and SHA-256. Copy
important backups off the service disk. Render disk snapshots are a recovery
aid, not a replacement for an application-level database backup. Run a reset
only from the service's private shell against the loopback admin listener;
never expose reset through the public port.

For every scored benchmark run:

1. start a separate isolated Harbor reference;
2. block both the `onrender.com` origin and `amazon.website-bench.com` from the
   Agent and candidate networks;
3. keep reference source, hidden fixtures, verifier code, and oracle data out
   of the Agent filesystem;
4. destroy or reset the per-run reference independently of this public demo.

## Verification

The path-filtered GitHub workflow runs the complete clone test suite, builds the
container, verifies Basic Auth, the read-only health endpoint and HTTPS
headers, creates an online SQLite backup, restarts the container with the same
Docker volume, and checks that both database and backup survive. When the
service owner connects this Render service to the repository through Render's
GitHub integration, `checksPass` deploys `main` only after repository checks
pass. A service created only from the public repository URL must instead use
**Manual Deploy → Deploy latest commit** after CI, or configure a deploy hook
that is called only after CI succeeds.

This deployment package does not declare the benchmark release-ready. Harbor
NOP/oracle calibration, isolation audit, human browser review, and
redistribution review remain separate release gates in `project/plan.json`.
