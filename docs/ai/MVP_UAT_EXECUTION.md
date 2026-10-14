# LiftHaul authenticated MVP UAT execution record

**Status: BLOCKED / NOT EXECUTED.** Template only. Source of truth: [MVP Issue #20](https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/20). Do not represent unfilled rows as passing evidence.

## Hard preconditions
- Confirm exact deployed release SHA from `/readyz` matches approved release, with Railway deployment ID captured.
- Obtain least-privilege **synthetic** accounts for all seven personas in a protected environment; no credentials in GitHub comments or logs.
- Use staging or explicitly authorized production read-only requests by default. Any booking submission creates a production record and needs a distinct, explicit authorization and cleanup plan.
- No real customer identities, funds, card numbers, custody, refunds, payouts, or dispatch.
- Record tester name, timestamp (timezone), desktop/tablet/mobile viewport, browser, trace IDs, and sanitized logs for each step.
- Inability to log in or access a protected environment = BLOCKED, not PASS.

## UAT case register

| ID | Persona / surface | Acceptance assertion | Status | Evidence link |
|---|---|---|---|---|
| BOOK-01 | Booker web/mobile | Quote uses real cargo weight/dimensions, routes and eligible vehicle | NOT RUN | — |
| BOOK-02 | Booker | Valid PH region pickup/drop-off; generated reference and tracking | NOT RUN | — |
| BOOK-03 | Staff/operator | Authorized queue sees persisted synthetic booking | NOT RUN | — |
| BOOK-04 | Other tenant/booker | Cannot view or modify another booker's booking | NOT RUN | — |
| DRIVER-01 | Driver | Can only access assigned trip; invalid GPS rejected | NOT RUN | — |
| DRIVER-02 | Driver/recipient | POD requires verified recipient code, no fake signature/photo | NOT RUN | — |
| FLEET-01 | Fleet owner | Fleet and booking visibility isolated by ownership | NOT RUN | — |
| OPS-01 | Staff/operations | Authorized workflow changes and audit trace | NOT RUN | — |
| FIN-01 | Finance | Read-only approved financial view, no live payout or refund | NOT RUN | — |
| ADMIN-01 | Admin | Admin-only access and unauthorized access denied | NOT RUN | — |
| FOUND-01 | Founder | Founder role access permitted, other roles denied | NOT RUN | — |
| AUTH-01 | Anonymous / expired login | Protected routes reject unauthorized requests | NOT RUN | — |
| PRICE-01 | Booker and operations | Display taxes + approved 10% admin fee only; external government charges excluded | NOT RUN | — |
| REC-01 | Operations | Restore to isolated environment with reconciliation and timing | NOT RUN | — |
| REC-02 | Operations | Rollback and alert/incident owner rehearsal | NOT RUN | — |
| WEB-01 | Public website | Real support, privacy and DPO contacts; no fake claims | NOT RUN | — |

## Evidence record per case
Case ID:
Approved SHA / deployment ID:
Environment:
Tester and accountable sign-off:
Timestamp/timezone:
Input (synthetic and redacted):
Expected result:
Actual result:
Test command / browser / viewport:
Artifact URL and trace ID:
Status: PASS / FAIL / BLOCKED / NOT RUN
Issue / remediation PR:

## Go decision
Issue #20 may be declared **MVP GO — no live funds** only when its mandatory acceptance gates are evidenced and signed by an accountable owner. A local test, agent report, or successful HTTP health check is insufficient. The separate payment certification remains NO-GO until its own approvals are documented.
