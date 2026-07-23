from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = (
    ROOT
    / "websitebench"
    / "corpora"
    / "claw-bench-v2"
    / "live-site-inventory.json"
)


def load_inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def test_live_site_inventory_is_self_consistent() -> None:
    inventory = load_inventory()
    summary = inventory["summary"]
    tasks = inventory["tasks"]
    platforms = inventory["platforms"]
    batches = inventory["batches"]

    assert inventory["schema_version"] == "clawbench.live-site-inventory.v1"
    assert summary == {
        "task_count": 129,
        "platform_count": 61,
        "first_party_origin_count": 62,
    }
    assert len(tasks) == summary["task_count"]
    assert len(platforms) == summary["platform_count"]
    assert len(batches) == 8
    assert len({task["task_id"] for task in tasks}) == len(tasks)
    assert len({task["directory"] for task in tasks}) == len(tasks)
    assert len({platform["platform_key"] for platform in platforms}) == len(platforms)

    task_ids_by_platform: dict[str, set[int]] = {}
    for task in tasks:
        task_ids_by_platform.setdefault(task["platform_key"], set()).add(task["task_id"])
        assert task["instruction"].strip()
        assert task["eval_schema"]["method"] in {"GET", "POST", "PUT"}
        assert task["eval_schema"]["url_pattern"]

    platform_keys = {platform["platform_key"] for platform in platforms}
    assert set(task_ids_by_platform) == platform_keys
    for platform in platforms:
        assert set(platform["task_ids"]) == task_ids_by_platform[platform["platform_key"]]
        assert len(platform["task_ids"]) == len(platform["task_directories"])
        for origin in platform["origins"]:
            parsed = urlsplit(origin)
            assert parsed.scheme == "https"
            assert parsed.hostname
            assert not parsed.username
            assert not parsed.password

    origins = {
        origin
        for platform in platforms
        for origin in platform["origins"]
    }
    assert len(origins) == summary["first_party_origin_count"]

    batched_platforms = [
        platform_key
        for batch in batches
        for platform_key in batch["platforms"]
    ]
    assert len(batched_platforms) == len(set(batched_platforms))
    assert set(batched_platforms) == platform_keys
    assert sum(batch["task_count"] for batch in batches) == summary["task_count"]


def test_live_site_inventory_preserves_upstream_count_warning() -> None:
    provenance = load_inventory()["provenance"]

    assert provenance["documented_task_count"] == 130
    assert provenance["discovered_task_count"] == 129
    assert provenance["discovery_warnings"] == [
        "documented V2 count is 130, but 129 task.json files were discovered"
    ]
