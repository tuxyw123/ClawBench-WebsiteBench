"""Role-based validation of WebsiteBench Compose trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


ROLE_KEYS = frozenset(
    {
        "reference",
        "mailbox_query",
        "mailbox_delivery",
        "build_daemon",
        "candidate_builder",
        "browser_gateway",
        "model_proxy",
        "agent",
        "evaluator",
    }
)
NETWORK_KEYS = frozenset(
    {
        "agent_control",
        "reference_web",
        "candidate_web",
        "model_egress",
        "build_egress",
        "internet_egress",
    }
)


@dataclass(frozen=True)
class TopologyProblem:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class TopologyValidationError(ValueError):
    def __init__(self, problems: list[TopologyProblem]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid WebsiteBench topology:\n" + "\n".join(f"- {problem}" for problem in problems))


def _networks(service: Mapping[str, Any]) -> set[str]:
    value = service.get("networks", {})
    if isinstance(value, (list, dict)):
        return set(value)
    return set()


def _volume_text(service: Mapping[str, Any]) -> str:
    return "\n".join(str(item) for item in service.get("volumes", []))


def _mapping(
    document: Mapping[str, Any],
    roles: Mapping[str, str] | None,
    network_roles: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str], list[Any]]:
    extension = document.get("x-websitebench", {})
    configured_roles = dict(roles or extension.get("roles", {}))
    configured_networks = dict(network_roles or extension.get("networks", {}))
    public_fixtures = list(extension.get("public_fixtures", []))
    return configured_roles, configured_networks, public_fixtures


def validate_compose_topology(
    path: Path | str,
    *,
    roles: Mapping[str, str] | None = None,
    network_roles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate topology by semantic role, independent of concrete names."""

    path = Path(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TopologyValidationError([TopologyProblem(str(path), str(exc))]) from exc
    if not isinstance(document, dict):
        raise TopologyValidationError([TopologyProblem(str(path), "must contain a mapping")])
    problems: list[TopologyProblem] = []
    services = document.get("services", {})
    networks = document.get("networks", {})
    role_map, network_map, public_fixtures = _mapping(document, roles, network_roles)

    missing_roles = ROLE_KEYS - set(role_map)
    extra_roles = set(role_map) - ROLE_KEYS
    if missing_roles or extra_roles:
        problems.append(
            TopologyProblem("x-websitebench.roles", f"missing={sorted(missing_roles)}, extra={sorted(extra_roles)}")
        )
    missing_network_roles = NETWORK_KEYS - set(network_map)
    extra_network_roles = set(network_map) - NETWORK_KEYS
    if missing_network_roles or extra_network_roles:
        problems.append(
            TopologyProblem(
                "x-websitebench.networks",
                f"missing={sorted(missing_network_roles)}, extra={sorted(extra_network_roles)}",
            )
        )
    missing_services = set(role_map.values()) - set(services)
    if missing_services:
        problems.append(TopologyProblem("services", f"missing role targets {sorted(missing_services)}"))
    missing_networks = set(network_map.values()) - set(networks)
    if missing_networks:
        problems.append(TopologyProblem("networks", f"missing role targets {sorted(missing_networks)}"))
    if problems:
        raise TopologyValidationError(problems)

    def service(role: str) -> tuple[str, Mapping[str, Any]]:
        name = role_map[role]
        return name, services[name]

    def expected_networks(role: str, expected_roles: set[str]) -> None:
        name, value = service(role)
        expected = {network_map[item] for item in expected_roles}
        actual = _networks(value)
        if actual != expected:
            problems.append(
                TopologyProblem(
                    f"services.{name}.networks",
                    f"role {role} requires {sorted(expected)}, got {sorted(actual)}",
                )
            )

    for role in ("agent_control", "reference_web", "candidate_web", "model_egress"):
        name = network_map[role]
        if networks[name].get("internal") is not True:
            problems.append(TopologyProblem(f"networks.{name}", f"{role} must be internal"))

    expected_networks("agent", {"agent_control", "model_egress"})
    expected_networks("browser_gateway", {"agent_control", "reference_web", "candidate_web"})
    expected_networks("reference", {"reference_web"})
    expected_networks("mailbox_query", {"reference_web"})
    expected_networks("mailbox_delivery", {"reference_web", "candidate_web"})
    expected_networks("candidate_builder", {"agent_control", "build_egress"})
    expected_networks("model_proxy", {"model_egress", "internet_egress"})
    expected_networks("evaluator", {"reference_web", "candidate_web"})

    build_name, build_service = service("build_daemon")
    if network_map["reference_web"] in _networks(build_service):
        problems.append(TopologyProblem(f"services.{build_name}.networks", "build daemon can reach reference"))

    agent_name, agent_service = service("agent")
    agent_mounts = _volume_text(agent_service).casefold()
    for forbidden in ("reference/", "judge/", "bench-fixtures", "docker.sock", "secrets.env"):
        if forbidden in agent_mounts:
            problems.append(TopologyProblem(f"services.{agent_name}.volumes", f"contains {forbidden}"))

    browser_name, browser_service = service("browser_gateway")
    browser_mounts = _volume_text(browser_service).casefold()
    if "reference/" in browser_mounts or "judge/" in browser_mounts or "docker.sock" in browser_mounts:
        problems.append(TopologyProblem(f"services.{browser_name}.volumes", "leaks a private workspace"))

    evaluator_name, evaluator_service = service("evaluator")
    evaluator_mounts = _volume_text(evaluator_service)
    for fixture in public_fixtures:
        marker = f"/bench-fixtures/{fixture}.json"
        if marker not in evaluator_mounts:
            problems.append(
                TopologyProblem(
                    f"services.{evaluator_name}.volumes",
                    f"must mount declared public fixture {fixture}",
                )
            )

    for name, value in services.items():
        service_text = yaml.safe_dump(value).casefold()
        if value.get("privileged") is True:
            problems.append(TopologyProblem(f"services.{name}.privileged", "must not be privileged"))
        if value.get("network_mode") == "host":
            problems.append(TopologyProblem(f"services.{name}.network_mode", "host mode is forbidden"))
        if "/var/run/docker.sock" in service_text:
            problems.append(TopologyProblem(f"services.{name}", "host Docker socket is forbidden"))
    if problems:
        raise TopologyValidationError(problems)
    return document
