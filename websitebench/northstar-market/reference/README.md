# Northstar Market private reference

This directory is private corpus-builder material. It must never be mounted into
an Agent, BrowserUse gateway, candidate builder, or candidate runtime.

The app exposes public HTTP on 8080 and authenticated benchmark administration
on 8081. SQLite data lives under `/data`; fixtures and the fixture schema are
read-only at `/bench-fixtures` and `/bench-schemas`.

From the repository root, generate fixtures with:

```bash
websitebench/northstar-market/reference/scripts/seed
```

Start the corpus services with a populated environment file:

```bash
docker compose --env-file <run-dir>/secrets.env \
  -f websitebench/northstar-market/docker-compose.yml \
  --profile corpus up --build --wait
```

The public health endpoint is `/healthz`. Reference reset, controlled clock, and
normalized state use the authenticated 8081 API frozen in the public candidate
contract. The mailbox has a separate authenticated reset endpoint on port 8026.

