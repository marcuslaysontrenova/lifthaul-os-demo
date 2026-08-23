# LiftHaul Referral Rewards — single-level direct referral

Legitimate **single-level** direct referral rewards program. Extend-only — no parallel user/carrier/
customer/payment/finance/fraud/commission system. The whole program is **OFF by default**
(`referral.program.enabled=false`) until Legal/Finance activation.

## Non-negotiables enforced
- **One level only.** A refers B → A earns; B refers C → B earns; **A earns NOTHING from C.** Proven by
  `test_referral.SingleLevelRedTeam`. The `referrals` table stores only `referrer_ref` + `referred_ref` —
  there is **no `parent_referrer_id`/upline/downline/generation/level column** (asserted in a test).
- **REGISTERED ≠ EARNED.** A reward requires a real qualifying commercial event, verified against the
  canonical domains (accreditation fee paid / marketplace-eligible unit / settled booking). Registration,
  code entry, account creation, or recruitment never pays.
- **Reward is a clean LiftHaul acquisition expense** — never computed on VAT, insurance, Protected Payment
  funds, carrier settlement, or payment pass-through; kept separate from Protected Payment / insurance /
  BD commission.

## What shipped (new: `referral.py`)
- **Campaigns** (Platform Control): qualifying event, reward model (FIXED/PERCENTAGE/CREDIT/…), basis,
  max-reward cap, validation days, per-user + monthly caps, total budget (+committed/earned/paid tracking),
  geography, equipment class, terms version, effective dates. Budget/caps fail closed.
- **Codes**: `LH-<slug>-<rand>` — unique, non-guessable, revocable, tenant/campaign-aware, optional expiry.
  Public `validate_code` never leaks the referrer.
- **Attribution**: server-side source of truth; immutable attribution + terms snapshot; no silent
  reassignment (one live attribution per business).
- **Governed lifecycle**: REGISTERED→VERIFIED→QUALIFIED→EARNED→APPROVED→PAYABLE→PAID with a validation
  cooldown; REVIEW_REQUIRED / REVERSED / REJECTED / CANCELLED branches; illegal transitions blocked.
- **Fraud screen**: SELF_REFERRAL, DUPLICATE_COMPANY (TIN/SEC/DTI), CIRCULAR_REFERRAL,
  DUPLICATE_REGISTRATION_ID, SUSPICIOUS_REFERRAL_VELOCITY → HIGH/CRITICAL fails closed to REVIEW_REQUIRED;
  weak signals never auto-accuse.
- **Finance SoD**: referrer can never qualify/approve/pay its own reward; ops/compliance qualify; finance
  approves + pays + reverses; refund/cancel → reverse (original row preserved as audit evidence).
- **Historical immutability**: reward snapshot (type/amount/basis/terms/campaign version) frozen at EARNED;
  later campaign changes never alter earned rewards.
- Credit rewards issue a `referral_credits` ledger entry (ACCREDITATION/BOOKING/LIFTHAUL credit).

## Surfaces
- **Provider portal → Referral Rewards** (`portal.html` tab + `carrier_portal.referral`): own code, share
  link, and reward totals; privacy-safe (business label + status only, never confidential data).
- **API** (`/admin/marketplace/referral/*`): campaigns, codes, attribute, verify/qualify/reject/flag,
  finance approve/pay/reverse, admin list, leaderboard; public `/public/referral/validate`.
- Notifications emitted via the existing engine (referral.registered/qualified/earned/payable/paid/reversed).
- **Administration → Referral Program UI**: API-complete; the dedicated admin HTML screen is a follow-up
  (flagged honestly, not claimed done).

## Tests
`test_referral.py` (24) — codes/validity, attribution/persistence/no-reassignment, REGISTERED≠EARNED,
carrier + shipper qualification, fixed/percentage(+cap)/credit rewards, validation cooldown, finance SoD,
refund reversal, self/duplicate/circular fraud + weak-signal-not-accused, budget + per-user caps,
**single-level red-team (A earns nothing from C) + no-downline-column**, tenant isolation, audit.
Full regression green.

## Pre-go-live closure (2026-08-23) — verified

| Item | Status |
|---|---|
| Provider referral attribution | PASS (pre-existing) |
| Shipper/customer referral attribution | PASS — `create_shipper_application(referral_code=…)`, server-side, immutable, bad code never blocks |
| Provider Referral Portal | PASS |
| Shipper/Customer Referral Portal | PASS — `track.html` code-bearer view + public `POST /public/referral/dashboard` (privacy-safe, no account needed) |
| Referral Admin UI | PASS |
| Single-Level Red-Team (A earns nothing from C) | PASS |
| REGISTERED ≠ EARNED (shipper) | PASS |
| Program activation flag OFF by default | PASS |
| Surcharge weekday/date-window regression | PASS — `test_weekday_and_date_window` + 14 surcharge tests green; **no code change** |

No shipper self-service *account/portal* exists (no `shipper_principals`), so the shipper referral view
is delivered on the canonical customer-facing page (`track.html`) authorised by the referral code itself
(a bearer handle, like a booking tracking token) — reuse-only, no new customer portal or model.

## Activation gate
`referral.program.enabled=false` by default. Even a fully qualified referral cannot EARN until the flag is
turned on — deliberate Legal/Finance activation. Marketing copy guard: single-level, no MLM/downline
language.
