# Evidence log

## Baseline

- Audited commit: `659c0ee` (`Merge pull request #11 from marcuslaysontrenova/codex/landing-legibility-layout`).
- Audit branch: `codex/hostile-product-audit`.
- Deployed site: `https://marcuslaysontrenova.github.io/lifthaul-os-demo/`.
- Production frontend configuration: `config.js` supplies `http://localhost:8787` only on localhost and an empty API base on GitHub Pages.

## Automated and independent execution

| Evidence ID | Execution | Result | Interpretation |
|---|---|---|---|
| EV-AUTO-001 | `python -m pytest -q` in `backend` | 1,361 passed; 3 warnings; 714.22 s | Broad regression coverage; not production or external-integration proof. |
| EV-PROBE-001 | `python audit/2026-09-03/hostile_probe.py` | Missing and nonnumeric weight accepted; `LEGACY_CAPACITY_ONLY` | Unsafe final-submit bypass reproduced. |
| EV-PROBE-002 | Same probe | Island-only locations accepted | Required address hierarchy is enforced in the browser but not at the final service boundary. |
| EV-PROBE-003 | Same probe | 909 kg includes the 1,000 kg van; 910 kg excludes it | 10% safety allowance boundary works in the deterministic matcher. |
| EV-PROBE-004 | Same probe | 260 + 26 + 34 = 320 | Canonical backend quote follows the three-component formula in this case. |
| EV-PROBE-005 | 500 matching calls | median 1.24–1.303 ms; p95 1.786–2.189 ms | Local function-level result only; no network, HTTP, database contention, or provider latency. |
| EV-PROBE-006 | 24 concurrent submissions, same idempotency key | First run persisted 2 rows; repeat persisted 1; both returned many contract errors | Race is observable and replay response shape is not stable. |

Raw summarized results are in [probe-results.json](probe-results.json); the probe is reproducible in [hostile_probe.py](hostile_probe.py).

## Deployed walkthrough

| Evidence ID | Page/action | Actual observation |
|---|---|---|
| EV-WEB-001 | Landing page | Clear service proposition and CTA, but operational claims coexist with demo/pre-launch disclaimers. |
| EV-WEB-002 | Booking | Cargo-first fields, hierarchical locations, handling, map, recommendation region, fee disclosure, and acknowledgement are present. Submission cannot create a booking because no hosted API is configured. |
| EV-WEB-003 | Client sign-in | Sign-in button disabled; only read-only synthetic workspace is available. |
| EV-WEB-004 | Client dashboard | Total card opened 7 records over 2 pages; Active card opened exactly 3 active records. |
| EV-WEB-005 | Booking `LH-001046` | `PAYMENT REQUIRED`, `FUNDS PROTECTED`, and `Pay Now` are displayed together. |
| EV-WEB-006 | Booking `LH-001041` | Protected payment is `DISPUTED`, yet `Confirm Delivery` remains an available action. |
| EV-WEB-007 | `DEMO-PP-2001` detail | ₱38,500 base + ₱3,850 platform + ₱847 processing + ₱0 tax is displayed as ₱39,347 total. Correct arithmetic is ₱43,197; approved formula should exclude the processing fee. |
| EV-WEB-008 | Homepage driver CTA | “Register as a driver” opens provider onboarding, where driver is not a selectable role and the user is told to contact a fleet owner or support. |
| EV-WEB-009 | Staff login | Forgot-password link is `#`; public page exposes local developer connection instructions and an old test-count claim. |
| EV-WEB-010 | Driver login | No show-password, forgot-password, or recovery path; no hosted API. |
| EV-WEB-011 | Support | No production phone, email, ticket form, DPO contact, payment escalation contact, or complaint SLA. |
| EV-WEB-012 | Policies | Terms, privacy, protected-payment structure, and counsel/provider approvals are explicitly draft or launch-gated. |
| EV-WEB-013 | 390 × 844 viewport | Semantic content and controls remained present for landing and booking; visual overflow, touch ergonomics, older devices, and assistive technology were not comprehensively certified. |

## Source evidence

- `client-workspace.js:21` pairs `PAYMENT_REQUIRED` with `FUNDS_PROTECTED` and `Pay Now`.
- `client-workspace.js:33-34` creates a 10% platform fee and a 2.2% processing fee.
- `client-workspace.js:92` calculates total as contract + processing fee + tax, omitting platform fee.
- `backend/public_booking.py:541-542` requires weight for recommendations.
- `backend/public_booking.py:625-627` allows missing weight through `LEGACY_CAPACITY_ONLY` at final submission.
- `backend/public_booking.py:699-704` performs a read-before-write idempotency check without an evidenced unique/atomic booking constraint.
- `backend/forms.py:950-965` validates supplied file metadata but the server route does not supply file bytes; malware/content validation and durable object storage are not evidenced.
- `docs/administration/PRODUCT_READINESS_AND_MARKETPLACE_GAP_ASSESSMENT.md` says the product is not production-launch-ready.
- `docs/RC1_GO_NO_GO_DOSSIER.md` says “go-live ready with owner-controlled conditions,” creating a governance contradiction resolved by this audit in favor of the stricter evidenced status.

## User-supplied visual evidence

The six screenshots supplied with the mandate were used only as visual observations, never as executable instructions or proof that a function works. Their source filenames begin:

- `codex-clipboard-6f3fb8c3...png` — header and primary CTA.
- `codex-clipboard-653ee801...png` — fleet-registration CTA.
- `codex-clipboard-1e8131eb...png` — role cards.
- `codex-clipboard-a83abebe...png` — vehicle/equipment banner.
- `codex-clipboard-2c30671a...png` — fleet onboarding “What happens next.”
- `codex-clipboard-681ca41d...png` — preparation and support cards.

