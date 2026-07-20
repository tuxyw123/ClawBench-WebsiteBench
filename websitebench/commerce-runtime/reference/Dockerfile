FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/clawbench/src
WORKDIR /opt/clawbench
COPY pyproject.toml ./
COPY src ./src
RUN mkdir -p /opt/reference
COPY websitebench/commerce-runtime/reference/app.py /opt/reference/app.py
RUN pip install --no-cache-dir "fastapi==0.115.12" "uvicorn==0.34.2" "pyyaml==6.0.2" "httpx==0.28.1" "python-multipart==0.0.20" \
 && useradd --uid 10001 --create-home reference \
 && mkdir -p /data \
 && chown -R reference:reference /data
USER reference
CMD ["python", "/opt/reference/app.py"]
