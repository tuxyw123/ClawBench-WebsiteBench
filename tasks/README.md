# Tasks

`clawbench/` contains the ClawBench development task paired with the historical
Amazon local replica. Its paths are rewritten for this standalone repository.

`dev/` contains the three compact legacy tasks used by the Viewer compatibility
adapters for Freshdesk, Greenhouse, and Idealist. Their local clones live under
`website-clone/`; runtime SQLite state is intentionally excluded.

Northstar Market uses the separate `websitebench.task.v1` envelope. The
`clawbench-web2code pilot` command creates that per run from the versioned
manifest and writes it inside `web2code-output/<run-id>/task.json`; generated
run IDs, timestamps, container URLs, and budgets therefore do not belong in a
static task file.
