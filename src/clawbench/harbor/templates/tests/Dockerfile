FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_ROOT_USER_ACTION=ignore \
    ANONYMIZED_TELEMETRY=false \
    IN_DOCKER=true \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl nodejs npm procps util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      httpx \
      pillow \
      playwright>=1.50 \
      pytest \
      scikit-image \
    && playwright install --with-deps chromium

RUN groupadd --system --gid 10001 verifier-runner \
    && useradd --system --uid 10001 --gid verifier-runner \
      --home-dir /run/verifier-untrusted --shell /usr/sbin/nologin verifier-runner

COPY . /tests
RUN if [ -f /tests/site/requirements.txt ]; then \
      pip install --no-cache-dir -r /tests/site/requirements.txt; \
    fi \
    && if [ -f /tests/site/install-verifier.sh ]; then \
      bash /tests/site/install-verifier.sh; \
    fi \
    && chown -R root:root /tests \
    && chmod -R u+rwX,go-rwx /tests \
    && chmod 755 /tests/test.sh

WORKDIR /tests
