# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues at
`tuxyw123/ClawBench-WebsiteBench`. Use the `gh` CLI for all operations and infer
the repository from the configured Git remote when running inside this clone.

## Conventions

- **Create an issue:** `gh issue create --title "..." --body "..."`.
- **Read an issue:** `gh issue view <number> --comments` and include labels when
  inspecting through structured output.
- **List issues:** use `gh issue list` with the appropriate state and label
  filters; request JSON fields when the result will be processed by an agent.
- **Comment on an issue:** `gh issue comment <number> --body "..."`.
- **Apply or remove labels:** `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`.
- **Close an issue:** `gh issue close <number> --comment "..."`.

## Skill terminology

When an engineering skill says to publish to the issue tracker, create a GitHub
issue. When it says to fetch a ticket, read the full GitHub issue body, labels,
and comments before acting.
