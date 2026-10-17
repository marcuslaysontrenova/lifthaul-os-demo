# Claude development instructions for LiftHaul

Follow [AGENTS.md](AGENTS.md) as mandatory project-wide policy and [docs/ai/AGENT_HANDOVER.md](docs/ai/AGENT_HANDOVER.md) for handovers.

Claude's assigned role is a *backup coding agent* for GitHub issue #20. Claude Free chat is not an unattended Claude Code credential and must not be represented as such.

Before editing: inspect current main SHA, issue #20, active pull requests and newest acceptance evidence. Only implement a specific uncovered MVP acceptance gap; do not duplicate work in existing PRs. No production operations or real funds. Report BLOCKED if repo access, tests, or quota are unavailable.

When finishing: provide a reproducible diff/PR and exact test logs, not merely a narrative that tasks are complete.
