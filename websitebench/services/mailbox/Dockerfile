FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mailbox

COPY websitebench/services/mailbox/pyproject.toml ./
COPY websitebench/services/mailbox/benchmail ./benchmail
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 --create-home benchmail \
 && mkdir -p /data \
 && chown benchmail:benchmail /data

USER benchmail
EXPOSE 8025 8026
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8025/healthz', timeout=2)"]
CMD ["python", "-m", "benchmail.run"]

