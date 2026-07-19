FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/clawbench/src
WORKDIR /opt/clawbench

RUN pip install --no-cache-dir \
    "httpx==0.28.1" \
    "jsonschema==4.26.0" \
    "numpy==1.26.4" \
    "pillow==11.1.0" \
    "pyyaml==6.0.2" \
    "scikit-image==0.26.0"

COPY src ./src
COPY websitebench/northstar-market/judge/northstar_judge.py ./northstar_judge.py
RUN useradd --uid 10001 --create-home evaluator \
 && mkdir -p /artifacts/eval \
 && chown -R evaluator:evaluator /artifacts

USER evaluator
CMD ["python", "/opt/clawbench/northstar_judge.py"]

