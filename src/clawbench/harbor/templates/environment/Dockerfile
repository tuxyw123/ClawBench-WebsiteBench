FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_ROOT_USER_ACTION=ignore \
    ANONYMIZED_TELEMETRY=false \
    IN_DOCKER=true \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git nodejs npm procps ripgrep util-linux \
    && rm -rf /var/lib/apt/lists/*

# Browser Use CLI is the Agent's exploration/self-check path. It is deliberately
# not the source of formal scores; the separate verifier image owns Playwright.
RUN pip install --no-cache-dir \
      browser-use==0.12.6 \
      fastapi \
      httpx \
      playwright>=1.50 \
      uvicorn \
    && playwright install --with-deps chromium

COPY seed/ /app/repo/

RUN if [ -f /app/repo/.clawbench/site/install-agent.sh ]; then \
      bash /app/repo/.clawbench/site/install-agent.sh; \
    fi \
    && if [ -f /app/repo/install-agent.sh ]; then \
      bash /app/repo/install-agent.sh; \
    fi

RUN cd /app/repo \
    && rm -rf .git \
    && git init -q -b main . \
    && git -c user.email=env@local -c user.name=env add -Af . \
    && GIT_AUTHOR_DATE=2000-01-01T00:00:00Z \
       GIT_COMMITTER_DATE=2000-01-01T00:00:00Z \
       git -c user.email=env@local -c user.name=env commit -q \
         -m "base reconstruction environment" \
    && test "$(git rev-list --all --count)" = "1"

WORKDIR /app/repo
