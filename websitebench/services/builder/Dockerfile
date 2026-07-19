FROM docker:28.0.4-cli AS dockercli

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/builder

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY websitebench/services/builder/pyproject.toml ./
COPY websitebench/services/builder/benchbuilder ./benchbuilder
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 --create-home builder \
 && mkdir -p /workspace/candidate /artifacts/builds \
 && chown -R builder:builder /workspace /artifacts

USER builder
EXPOSE 7100
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7100/healthz', timeout=2)"]
CMD ["uvicorn", "benchbuilder.app:app", "--host", "0.0.0.0", "--port", "7100"]
