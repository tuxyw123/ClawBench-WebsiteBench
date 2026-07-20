# Triage labels

The engineering skills use five canonical triage roles. This table maps those
roles to the labels configured in this repository.

| Canonical role | GitHub label | Meaning |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | A maintainer still needs to evaluate the issue. |
| `needs-info` | `needs-info` | Work is waiting for information from the reporter. |
| `ready-for-agent` | `ready-for-agent` | The issue is fully specified and can be completed without live human context. |
| `ready-for-human` | `ready-for-human` | The issue requires human implementation or interaction. |
| `wontfix` | `wontfix` | The issue will not be actioned. |

When a skill refers to an AFK-ready or HITL-ready role, use
`ready-for-agent` or `ready-for-human`, respectively.
