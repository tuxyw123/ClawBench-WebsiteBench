FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir '.[viewer]'

EXPOSE 8765
CMD ["clawbench-viewer", "--repo-root", "/app", "serve", "--profile", "internal", "--host", "0.0.0.0", "--port", "8765", "--reviews", "/data/reviews"]
