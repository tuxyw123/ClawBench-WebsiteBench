"""Static validation of Web2Code2Web Compose trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TopologyProblem:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class TopologyValidationError(ValueError):
    def __init__(self, problems: list[TopologyProblem]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid WebsiteBench topology:\n" + "\n".join(f"- {p}" for p in problems))


def _networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", {})
    if isinstance(networks, list):
        return set(networks)
    if isinstance(networks, dict):
        return set(networks)
    return set()


def _volume_text(service: dict[str, Any]) -> str:
    return "\n".join(str(item) for item in service.get("volumes", []))


def validate_compose_topology(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    problems: list[TopologyProblem] = []
    if not isinstance(document, dict):
        raise TopologyValidationError([TopologyProblem(str(path), "must contain a mapping")])
    services = document.get("services", {})
    networks = document.get("networks", {})
    required_services = {
        "reference-app",
        "mailbox",
        "mailbox-delivery",
        "rootless-buildkit",
        "candidate-builder",
        "browser-gateway",
        "model-proxy",
        "agent",
        "evaluator",
    }
    missing_services = required_services - set(services)
    if missing_services:
        problems.append(TopologyProblem("services", f"missing {sorted(missing_services)}"))
    required_networks = {
        "agent-control",
        "reference-web",
        "candidate-web",
        "model-egress",
        "build-egress",
        "internet-egress",
    }
    missing_networks = required_networks - set(networks)
    if missing_networks:
        problems.append(TopologyProblem("networks", f"missing {sorted(missing_networks)}"))
    for name in ("agent-control", "reference-web", "candidate-web", "model-egress"):
        if name in networks and networks[name].get("internal") is not True:
            problems.append(TopologyProblem(f"networks.{name}", "must be internal"))
    if "agent" in services:
        agent_networks = _networks(services["agent"])
        if agent_networks != {"agent-control", "model-egress"}:
            problems.append(
                TopologyProblem(
                    "services.agent.networks",
                    f"must be agent-control + model-egress only, got {sorted(agent_networks)}",
                )
            )
        agent_mounts = _volume_text(services["agent"]).casefold()
        for forbidden in (
            "reference/",
            "judge/",
            "bench-fixtures",
            "browser",
            "docker.sock",
            "secrets.env",
            "required}:/artifacts",
        ):
            if forbidden in agent_mounts:
                problems.append(TopologyProblem("services.agent.volumes", f"contains {forbidden}"))
    if "browser-gateway" in services:
        gateway_networks = _networks(services["browser-gateway"])
        expected = {"agent-control", "reference-web", "candidate-web"}
        if gateway_networks != expected:
            problems.append(
                TopologyProblem(
                    "services.browser-gateway.networks",
                    f"must be {sorted(expected)}, got {sorted(gateway_networks)}",
                )
            )
        mounts = _volume_text(services["browser-gateway"]).casefold()
        if "candidate:/workspace" in mounts or "reference/" in mounts or "judge/" in mounts:
            problems.append(TopologyProblem("services.browser-gateway.volumes", "leaks source workspace"))
    if "reference-app" in services:
        reference_networks = _networks(services["reference-app"])
        if reference_networks != {"reference-web"}:
            problems.append(TopologyProblem("services.reference-app.networks", "crosses trust boundary"))
    if "mailbox" in services and _networks(services["mailbox"]) != {"reference-web"}:
        problems.append(
            TopologyProblem(
                "services.mailbox.networks",
                "query mailbox must be reachable only from reference-web",
            )
        )
    if "mailbox-delivery" in services:
        delivery_networks = _networks(services["mailbox-delivery"])
        if delivery_networks != {"reference-web", "candidate-web"}:
            problems.append(
                TopologyProblem(
                    "services.mailbox-delivery.networks",
                    "delivery mailbox must bridge reference-web + candidate-web only",
                )
            )
    if "rootless-buildkit" in services and "reference-web" in _networks(services["rootless-buildkit"]):
        problems.append(TopologyProblem("services.rootless-buildkit.networks", "can reach reference"))
    if "candidate-builder" in services:
        builder_networks = _networks(services["candidate-builder"])
        if builder_networks != {"agent-control", "build-egress"}:
            problems.append(
                TopologyProblem(
                    "services.candidate-builder.networks",
                    "must be agent-control + build-egress only",
                )
            )
    if "model-proxy" in services:
        proxy_networks = _networks(services["model-proxy"])
        if proxy_networks != {"model-egress", "internet-egress"}:
            problems.append(
                TopologyProblem(
                    "services.model-proxy.networks",
                    "must be model-egress + internet-egress only",
                )
            )
    if "evaluator" in services:
        evaluator_networks = _networks(services["evaluator"])
        if evaluator_networks != {"reference-web", "candidate-web"}:
            problems.append(
                TopologyProblem(
                    "services.evaluator.networks",
                    "must be reference-web + candidate-web only",
                )
            )
        evaluator_mounts = _volume_text(services["evaluator"])
        for seed in (1101, 1102):
            if f"/bench-fixtures/{seed}.json" not in evaluator_mounts:
                problems.append(
                    TopologyProblem(
                        "services.evaluator.volumes",
                        f"must mount public fixture {seed} for visual evaluation",
                    )
                )
    for name, service in services.items():
        service_text = yaml.safe_dump(service).casefold()
        if service.get("privileged") is True:
            problems.append(TopologyProblem(f"services.{name}.privileged", "must not be privileged"))
        if service.get("network_mode") == "host":
            problems.append(TopologyProblem(f"services.{name}.network_mode", "host mode is forbidden"))
        if "/var/run/docker.sock" in service_text:
            problems.append(TopologyProblem(f"services.{name}", "host Docker socket is forbidden"))
    if problems:
        raise TopologyValidationError(problems)
    return document
