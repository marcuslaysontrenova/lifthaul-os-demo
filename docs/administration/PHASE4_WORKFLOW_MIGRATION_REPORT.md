# PHASE 4 — Workflow Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce governed, versioned Workflow Administration WITHOUT moving any existing
transaction onto a new workflow version and WITHOUT changing any financial value or operational
status.

## Migration strategy (additive, parallel, non-destructive)

Phase 4 adds a governance/versioning/approval/SLA layer **above** the existing hard-coded state
machines. The existing enforcement (`core._BOOKING_FLOW`, `ops.JOB_FLOW`, evidence/dispatch/safety
gates, `policy.evaluate_approval`, separation-of-duties) is **unchanged** and remains authoritative
for live operational transactions. Governed workflow *instances* are additive metadata bound to a
published version; they never rewrite an operational record's real status.

| Existing surface | Migration action | Impact |
|---|---|---|
| booking / quotation / job / payment / expense / invoice / change-order state machines | imported into governed workflow **definitions** that reproduce the same states + guards | none — the live transitions still run through the original code |
| quotation approval (amount/discount/credit-hold + SoD) | generalized into governed **approval matrices**; defaults reproduce the exact Phase-2 thresholds | none — same approval outcomes |
| open transactions (bookings not CONFIRMED, jobs not CLOSED) | **legacy retained** — NOT force-migrated onto any new version | none — current behavior preserved |
| historical transactions (CONFIRMED bookings, CLOSED jobs) | **excluded** from migration | none |

## Existing-transaction classification (read-only)

`workflow.classify_existing()` inspects live transactions and never moves them:

| Class | Meaning | Action |
|---|---|---|
| legacy retained | open bookings/jobs keep current behavior | not migrated (additive engine) |
| deterministically assignable | could bind to the imported version if the owner approves a governed migration | offered, not performed |
| requires manual remediation | none identified for go-live | — |
| historical / excluded | completed transactions | excluded |

## Migration results

| Metric | Result |
|---|---|
| Transactions analyzed | all open bookings + jobs (read-only) |
| Versions assigned (force-migrated) | **0** (additive engine; new work binds to the active version only) |
| Legacy retained | all open transactions |
| Ambiguous | 0 |
| Excluded (historical) | all completed transactions |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |

## Financial & operational invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
```

- **Financial:** nothing in the workflow engine recomputes a quotation/invoice value. Test
  `TestPermissionsAndSafety.test_workflow_does_not_change_financials` drives a full governed
  instance and asserts the quotation `tax`/`total` are unchanged (72000/672000).
- **Operational:** the real booking stage is untouched by a parallel governed instance
  (same test asserts `bookings.stage` unchanged); CI `pg_validate.py` re-checks on PostgreSQL.

## Reversibility

- No destructive DDL (only `CREATE TABLE IF NOT EXISTS`).
- Published/active versions are immutable but a definition can be retired; instances are
  additive rows that can be cancelled without touching operational data.
- The imported definitions are seeded at platform scope and can be retired without affecting the
  authoritative state machines.
