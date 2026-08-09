# 14 — Business Continuity / Disaster Recovery

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

## Objectives (targets — to be ratified with the owner/host)

| Metric | Target |
|---|---|
| RPO (max data loss) | ≤ 24h (DB backup cadence; tighten with PITR) |
| RTO (max downtime) | ≤ 4h for core platform |
| Fund safety | Independent of LiftHaul uptime — funds held by the provider |

## Data

- Production DB: PostgreSQL. Backup/restore procedure documented in `docs/GO_LIVE_RUNBOOK.md`.
- Immutable ledger is reconstructable from append-only history; provider statements are the second
  source of truth for fund positions.

## Fund-continuity property

Because **LiftHaul never holds funds**, a LiftHaul outage does not put customer money at risk — funds
remain safeguarded with the regulated provider. Release simply pauses until service resumes; the state
machine resumes deterministically (idempotent, state persisted).

## Provider failure

If the **provider** fails, doc 17 (due diligence) requires a documented **fund-return scenario** and
contract termination terms. A second certified provider is the mitigation; the provider interface is
provider-neutral by design.

## DR drills

- Backup/restore rehearsal before live funds (owner + host).
- Reconciliation replay from ledger + provider statements.

## To be completed with the owner

- Host/region redundancy choice, backup retention window, and a scheduled restore-test cadence.
