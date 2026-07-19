FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BROWSER_USE_HEADLESS=1
WORKDIR /opt/gateway

RUN pip install --no-cache-dir "browser-use==0.12.6" \
 && python -m playwright install --with-deps chromium
COPY websitebench/services/browser-gateway/pyproject.toml ./
COPY websitebench/services/browser-gateway/benchbrowser ./benchbrowser
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 --create-home gateway \
 && mkdir -p /artifacts/browser /tmp/browser-use \
 && chown -R gateway:gateway /artifacts /tmp/browser-use

USER gateway
EXPOSE 7000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7000/healthz', timeout=2)"]
CMD ["uvicorn", "benchbrowser.app:app", "--host", "0.0.0.0", "--port", "7000"]

