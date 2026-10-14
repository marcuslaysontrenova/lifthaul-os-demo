# LiftHaul acceptance and AI task allocation (source: GitHub issue #20)

**Status: UNVERIFIED FOR MVP GO.** This document is a planning mirror, not sign-off. Always consult the latest issue #20 and PR status.

| Gate | Verified status per issue #20 | Next action | Evidence needed |
|---|---|---|---|
| CI, deployed release identity, health and geography manifest | Reported passing for release in issue #20 | Re-check exact deployed SHA on next release | CI run URLs, Railway deploy ID, read-only probes |
| End-to-end PH booking into operator queue | OPEN | Verify cross-region quote, weight/cargo, booking and tracking with strictly labeled synthetic records | Booking reference and authorized operator queue trace |
| Authenticated role UAT (7 personas) | OPEN | Provision least-privilege protected QA access outside chat; run owner/tenant negative tests | Signed matrix by persona/device and denied-access evidence |
| Backup/restore/rollback/monitoring | OPEN | Nonproduction restore drill and verified alert delivery | Restore reconciliation, RTO/RPO, rollback drill, incident owner |
| Public support/privacy/DPO contacts | OPEN | Obtain founder-approved actual contact details | Published URLs and accountable approval |
| Tax / 10% admin / subscription policies | OPEN | Confirm written policy and test disclosure/fee calculations | Sign-off and pricing tests |
| Payment/PSP and funds-flow certification | SEPARATE NO-GO | Keep real-money flags disabled | Independent provider/legal/finance approval |

## Active PR scope to avoid
- PR #28: client booking details, ownership/tenant isolation, tracking, UI.
- PR #29: driver GPS validation and recipient delivery code / POD.
- PR #23: synthetic production-write smoke; do not run as part of generic AI automation.
- PR #32: AI instructions and test-only handover policy.

## Agent work assignment policy
- Codex: prioritize one unmet issue #20 acceptance gate at a time; deliver code and exact CI evidence.
- Claude: review precise diff/risks and implement independently assigned *nonoverlapping* tasks only when authorized access exists.
- Gemini: free-tier read-only code review or isolated gap work with verifiable tests; no claim of unattended CLI until authenticated.
- No model judges its own work as production accepted. Human accountable acceptance and evidence required.

## Guardrails
- A PR passing local tests does not satisfy hosted production UAT.
- Do not fabricate verification credentials, customer records, location data, or certifications.
- No automatic merge, deployment, production writes, DNS/email provider changes or live-money activity.
- Fail closed on unknown quota/CI/authentication errors and prevent circular retries.
