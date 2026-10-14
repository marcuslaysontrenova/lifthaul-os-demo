# Gemini development instructions for LiftHaul

Follow [AGENTS.md](AGENTS.md) and [docs/ai/AGENT_HANDOVER.md](docs/ai/AGENT_HANDOVER.md).

Gemini CLI is a backup developer/reviewer only when its authentication method, terms, rate limits, and execution context are verified. A personal Google sign-in on a developer computer does not automatically authenticate unattended GitHub Actions.

Always begin from GitHub issue #20, inspect current main SHA and open PRs, and target one outstanding MVP gate. No hallucinated acceptance, shortcuts around CI, production-write tests, secrets exposure, or live-money activation. If verification is inaccessible, mark the result UNVERIFIED or BLOCKED.

Leave a complete cross-provider handover in the exact format described by docs/ai/AGENT_HANDOVER.md.
