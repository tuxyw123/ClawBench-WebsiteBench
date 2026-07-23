FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 amazon-clone \
    && useradd --system --uid 10001 --gid amazon-clone --home-dir /app amazon-clone \
    && mkdir -p /app/clone /data \
    && chown -R amazon-clone:amazon-clone /app /data

WORKDIR /app/clone
COPY --chown=amazon-clone:amazon-clone . .

EXPOSE 10000
STOPSIGNAL SIGTERM

CMD ["sh", "-c", "mkdir -p /data && chown amazon-clone:amazon-clone /data && exec gosu amazon-clone:amazon-clone python -u server.py"]
