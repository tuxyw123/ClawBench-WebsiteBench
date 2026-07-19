FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/northstar

COPY websitebench/northstar-market/reference/pyproject.toml ./
COPY websitebench/northstar-market/reference/northstar ./northstar
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 --create-home northstar \
 && mkdir -p /data /bench-fixtures /bench-schemas \
 && chown northstar:northstar /data

COPY websitebench/schemas/fixture.schema.json /bench-schemas/fixture.schema.json
COPY websitebench/northstar-market/public/fixtures/ /bench-fixtures/
COPY websitebench/northstar-market/judge/fixtures/ /bench-fixtures/

USER northstar
EXPOSE 8080 8081
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["python", "-m", "northstar.run"]

