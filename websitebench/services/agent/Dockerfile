FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/agent

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install --global @openai/codex@0.120.0 \
 && pip3 install --break-system-packages --no-cache-dir "mcp>=1.27,<2" "httpx==0.28.1"

COPY websitebench/services/agent/gateway_mcp.py ./gateway_mcp.py
COPY websitebench/services/agent/run_agent.py ./run_agent.py
COPY websitebench/services/agent/AGENT_PROMPT.md ./AGENT_PROMPT.md
RUN useradd --uid 10001 --create-home agent \
 && mkdir -p /home/agent/.codex /workspace/candidate /artifacts/agent \
 && printf '%s\n' '[mcp_servers.websitebench]' 'command = "python3"' 'args = ["/opt/agent/gateway_mcp.py"]' 'required = true' > /home/agent/.codex/config.toml \
 && chown -R agent:agent /home/agent /workspace /artifacts

USER agent
WORKDIR /workspace/candidate
CMD ["python3", "/opt/agent/run_agent.py"]
