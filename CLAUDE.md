# LiftHaul — instructions for Claude Code (same as AGENTS.md)

This repository follows the **Claude ↔ Codex coordination protocol** (owner decision 2026-11-14).
Full text: `marcuslaysontrenova/TRENOVATECH-CORE` → `docs/coordination/CLAUDE_CODEX_PROTOCOL.md`.
Coordination Board (both products): TRENOVATECH-CORE issue #180.

- Claude Code is the orchestrator; Codex is the peer engineer and reviewer.
- Claim a task on board #180 before starting (`CLAIM T<n> — <agent> — base <sha>`).
- Branch `codex/<topic>` or `claude/<topic>`; one concern per PR.
- Every PR is reviewed by the other agent on its exact head SHA (`APPROVE <sha>` or a GitHub
  approving review). Never approve your own PR.
- **`main` here is production-connected.** Agents never merge to `main`; the owner does, after
  the independent human review required by issue #34.
- Owner-only: production deploys, secrets/env vars, live funds (payments, refunds, payouts),
  live booking/dispatch switches, auth-email/identity provisioning, purchases.
- Never weaken the fail-closed live-operation interlock to make a test pass; fix the fixture.
- Product objective: issue #20. Security ledger: issue #73.
