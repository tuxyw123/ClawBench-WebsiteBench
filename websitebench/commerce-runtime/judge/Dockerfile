FROM mcr.microsoft.com/playwright/python:v1.60.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/evaluator
RUN pip install --no-cache-dir "httpx==0.28.1" "playwright==1.60.0" "pillow==11.1.0"
COPY websitebench/commerce-runtime/judge/commerce_judge.py ./commerce_judge.py
RUN useradd --uid 10001 --create-home evaluator \
 && mkdir -p /artifacts/eval \
 && chown -R evaluator:evaluator /artifacts
USER evaluator
CMD ["python", "/opt/evaluator/commerce_judge.py"]
