FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/model-proxy

COPY websitebench/services/model-proxy/model_proxy.py ./model_proxy.py
RUN useradd --system --uid 10001 --create-home modelproxy

USER modelproxy
EXPOSE 7200
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1',7200),2).close()"]
CMD ["python", "/opt/model-proxy/model_proxy.py"]
