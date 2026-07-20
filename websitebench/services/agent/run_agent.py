"""Configure the controlled MCP bridge and execute one Codex build run."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any


def run_turn(
    command: list[str],
    output: Any,
    *,
    timeout_seconds: float,
    token_budget_remaining: int,
) -> tuple[int, str | None, int, int, bool]:
    process = subprocess.Popen(
        command,
        cwd="/workspace/candidate",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    thread_id = None
    input_tokens = 0
    output_tokens = 0
    exhausted = False
    deadline = time.monotonic() + timeout_seconds
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    while True:
        if time.monotonic() >= deadline:
            process.terminate()
            exhausted = True
            break
        events = selector.select(timeout=0.2)
        line = process.stdout.readline() if events else ""
        if line:
            output.write(line)
            output.flush()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
                input_tokens = int(usage.get("input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", 0))
                if input_tokens + output_tokens >= token_budget_remaining:
                    process.terminate()
                    exhausted = True
                    break
        elif process.poll() is not None:
            break
    selector.close()
    try:
        return_code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        return_code = process.wait()
    return return_code, thread_id, input_tokens, output_tokens, exhausted


def main() -> int:
    task_path = Path(os.environ.get("TASK_PATH", "/task/task.json"))
    prompt_path = Path(os.environ.get("AGENT_PROMPT_PATH", "/opt/agent/AGENT_PROMPT.md"))
    artifacts = Path(os.environ.get("AGENT_ARTIFACT_DIR", "/artifacts/agent"))
    artifacts.mkdir(parents=True, exist_ok=True)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    prompt = prompt_path.read_text(encoding="utf-8") + "\n\nTask envelope:\n```json\n" + json.dumps(task, indent=2) + "\n```\n"
    base_command = [
        "codex",
        "exec",
        "--sandbox",
        "danger-full-access",
        "--skip-git-repo-check",
        "--model",
        task["agent"]["model"],
        "--config",
        f'model_reasoning_effort="{task["agent"]["thinking_level"]}"',
        "--json",
    ]
    started = time.monotonic()
    token_budget = int(task["budget"]["token_budget"])
    total_input_tokens = 0
    total_output_tokens = 0
    messages_path = artifacts / "agent-messages.jsonl"
    with messages_path.open("w", encoding="utf-8") as output:
        return_code, thread_id, used_input, used_output, exhausted = run_turn(
            [*base_command, prompt],
            output,
            timeout_seconds=task["budget"]["wall_time_seconds"],
            token_budget_remaining=token_budget,
        )
        total_input_tokens += used_input
        total_output_tokens += used_output
        if task["track"] == "hitl" and return_code == 0 and not exhausted and thread_id:
            intervention_path = artifacts.parent / "human-interventions.jsonl"
            processed = 0
            hitl_deadline = min(
                started + task["budget"]["wall_time_seconds"], time.monotonic() + 90 * 60
            )
            while time.monotonic() < hitl_deadline and processed < 12:
                records = []
                if intervention_path.exists():
                    records = [
                        json.loads(line)
                        for line in intervention_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                if processed >= len(records):
                    time.sleep(1)
                    continue
                intervention = records[processed]
                processed += 1
                remaining_time = max(1, task["budget"]["wall_time_seconds"] - (time.monotonic() - started))
                return_code, resumed_thread, used_input, used_output, exhausted = run_turn(
                    [
                        *base_command,
                        "resume",
                        thread_id,
                        f"Human intervention ({intervention['category']}): {intervention['message']}",
                    ],
                    output,
                    timeout_seconds=remaining_time,
                    token_budget_remaining=max(
                        1, token_budget - total_input_tokens - total_output_tokens
                    ),
                )
                total_input_tokens += used_input
                total_output_tokens += used_output
                thread_id = resumed_thread or thread_id
                if return_code or exhausted or intervention.get("final"):
                    break
    (artifacts / "exit.json").write_text(
        json.dumps(
            {
                "exit_code": return_code,
                "thread_id": thread_id,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "tokens": total_input_tokens + total_output_tokens,
                "budget_exhausted": exhausted,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
