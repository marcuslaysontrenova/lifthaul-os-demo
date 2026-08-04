# LiftHaul Nationwide Marketplace & Production Launch Program — Governance Charter

**Status:** DRAFT v0.1 · **Date:** 2026-08-03 · **Program type:** market-transformation +
production-release (NOT Phase 11 of the administration roadmap).

This charter governs the program that turns the CI-proven LiftHaul OS enterprise platform
(Phases 1–10) into a nationwide, protected-payment logistics marketplace. Documentation alone is
not delivery; every workstream must land as real, tested, CI-validated capability before it may be
called ready.

## 1. Decision-rights bodies

| Body | Accountable for | Chair |
|---|---|---|
| **Executive Steering Committee** | investment, regulatory posture, payment model, launch geography, marketplace policy, production approval, expansion approval | Executive Owner |
| **Product & Technology Council** | architecture, product scope, security, data, release, integrations, technical risk | CTO |
| **Marketplace Operations Council** | carrier supply, shipper demand, coverage, dispatch, control tower, incidents, claims, support | COO |
| **Go-Live Authority** | the single gate that releases anything to production | joint sign-off (below) |

## 2. Go-Live Authority — mandatory sign-off set

No launch (pilot, corridor, region, or nationwide) occurs without **all** of:
Product · Engineering · Security · Operations · Finance · Legal & Compliance · Customer Support ·
Executive Owner. A single withheld signature = HOLD. Sign-offs are recorded in
[`GO_LIVE_GATE_REGISTER.md`](GO_LIVE_GATE_REGISTER.md) with evidence links.

## 3. Escalation thresholds

| Severity | Trigger | Owner | Response |
|---|---|---|---|
| SEV-1 | payment mismatch, cross-tenant exposure, safety/cargo loss, data breach | CISO + COO | immediate freeze + Steering notify |
| SEV-2 | lane service failure, matching outage, settlement delay | Ops Council | same-day |
| SEV-3 | degraded UX, non-blocking defect | Product | next release decision |

## 4. Evidence requirements (non-negotiable)

- **No fabricated percentages.** Every status is `NOT READY / PARTIAL / VERIFIED` with an evidence link.
- Code capabilities require: real code + migration + tests + PostgreSQL runtime CI + browser E2E.
- Operational/regulatory/payment readiness requires named owner + artifact + external confirmation.
- "Nationwide" may not be claimed until **lane-level** operational capacity is verified per corridor.

## 5. Risk-acceptance authority

Critical/High security or regulatory findings must be **resolved** or **formally risk-accepted by the
Executive Steering Committee** (recorded, time-boxed) before the affected scope goes live. No implicit
acceptance.

## 6. Cadence

- Steering: weekly during pilot, at every go-live gate.
- Product & Tech Council: 2×/week.
- Ops Council: daily during pilot + hypercare.
- Program dashboard ([§24 of the blueprint](../administration/)) reviewed at every Steering meeting.

## 7. Owner-controlled blockers carried from Phases 1–10

| ID | Blocker | Why only the owner can clear it |
|---|---|---|
| B1 | Live Wise validation | requires a Wise **business** API token + authorized profile |
| B2 | Live AI provider validation | requires `OPENAI`/`ANTHROPIC_API_KEY` + approval |
| B3 | Licensed protected-payment / safeguarding partner | requires a contracted, regulated PH partner |
| B4 | Philippine legal operating-model determination | requires external PH legal counsel |
| B5 | Production infrastructure + security review | requires provisioning budget + independent pentest |

These are **owner actions**, tracked honestly; the engineering program builds fail-closed adapters
around them and never fabricates their outcome.
