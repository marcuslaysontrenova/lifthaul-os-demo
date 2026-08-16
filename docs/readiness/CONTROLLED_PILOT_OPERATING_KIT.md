# LiftHaul — Controlled First-Customer Pilot Operating Kit

**Baseline:** `404dad9` (1,185 tests / 0 failed) · **Mode:** Controlled Pilot — *Application live,
**Live Payments OFF**, **LTFRB enforcement OFF (manual carrier vetting)***.
**Audience:** the operator running the first real customer + a small set of hand-vetted carriers.

> Purpose: validate real operations end to end with one friendly customer **before** the slow external
> gates (funds legal/provider, LTFRB CPC verification) resolve — instead of blocking launch on them. This
> kit is a runbook; it uses only capabilities already built and green. It touches no feature code.

---

## 0. Pilot mode — what is ON and OFF

| Capability | Pilot setting | Meaning |
|---|---|---|
| Application (booking→tracking→assignment→trip→OTP→POD) | **ON** | full stack, real data |
| Live Protected Payment | **OFF** (`payments.*` flags false) | money is **not** custodied; payment state is tracked, settlement is recorded, no live rail — operator-assisted |
| LTFRB CPC enforcement | **OFF** (`marketplace.ltfrb_enforcement_enabled=false`) | carriers are **manually vetted** by the operator before activation; the hard gate stays inert |
| Messaging / OTP delivery provider | OFF unless connected | OTP is issued + verified in-app; if no SMS provider, the operator relays the code to the recipient out-of-band (honest — nothing is faked) |
| Insurance / Goods Protection provider | OFF | coverage is not bound to a real insurer during pilot |
| Surcharge engine | OFF (default) | standard pricing only during pilot |

**Rule:** never flip a revenue/regulated flag to satisfy a pilot step. If a step needs live funds or real
CPC enforcement, it is **out of pilot scope** — record it and move on.

---

## 1. Pilot readiness gate (must be TRUE before inviting the first customer)

- [ ] **Application deployed** (Gate 1–2): hosted PostgreSQL + TLS domain; `GET /healthz` and `/readyz` green over HTTPS.
- [ ] **`prod_e2e.py` passes 8/8** against the hosted URL.
- [ ] **`preflight.py` = LAUNCH-SAFE** and confirms all four flags OFF.
- [ ] **Two-tenant isolation** re-verified on prod.
- [ ] **One backup taken** + restore drill run (`backup_restore.py --postgres`); RTO/RPO recorded.
- [ ] **Front-ends pointed** at the origin (`lifthaul_api_base`) for: `index/book/track/portal/driver/console`.
- [ ] **Named on-call** assigned (see §7) and reachable.
- [ ] **Rollback plan** confirmed (§8): how to take a fresh backup and how to pause new bookings.

If any box is unchecked, the pilot does not start.

---

## 2. Onboard the first CUSTOMER

The pilot customer books through the public flow — no account creation is required for a guest parcel
booking; a returning corporate customer can be set up as a billing account.

1. **Brief the customer** on pilot expectations: real delivery, operator-assisted payment (no online
   custody yet), and that they can track by code.
2. **Customer books** at `book.html` (or you place it for them): pickup + drop-off, vehicle (e.g.
   `motorcycle` for a parcel), contact details → they receive a **tracking token** + indicative estimate.
3. **Customer tracks** at `track.html` with the token.
4. *(Optional, for a repeat corporate customer)* create a **billing account** in the console (Corporate
   Billing tab) so their freight + rental activity consolidates onto one statement.

---

## 3. Onboard HAND-VETTED CARRIERS (LTFRB enforcement OFF → manual vetting)

Because CPC enforcement is off for the pilot, the operator is the compliance authority. For each pilot
carrier:

1. **Create the carrier** (console → onboarding): legal name, type, address, contacts.
2. **Collect + verify documents manually**, then upload and mark verified: Business Registration, Tax,
   Authority to Operate, Insurance. *(A carrier still cannot self-verify — you verify.)*
3. **Register the fleet** (Fleet Registration tab): enter each unit's specs → the engine classifies the
   canonical variant; for cranes/forklifts enter the required equipment fields. Units land **DRAFT**.
   Use **CSV import** for a carrier bringing several units.
4. **Verify + activate each vehicle** (reviewer action) after checking OR/CR + registration + insurance.
5. **Register + verify + activate drivers**; set **vehicle↔driver pairings** (PRIMARY/BACKUP).
6. **Set service areas + capabilities** for the carrier.
7. **Check per-unit readiness** (readiness checklist) — every item ✓ and MARKETPLACE STATUS = ELIGIBLE
   before the unit takes work. Fix any ✗ (e.g. INSURANCE_EXPIRED) rather than overriding.
8. **Bind a carrier-portal login** (Carrier Access tab) so the carrier self-serves in `portal.html`, and a
   **driver-app login** so the driver runs `driver.html`.

**Pilot vetting note:** manually confirm each carrier's LTFRB standing out-of-band and record the evidence
reference in the carrier notes. This is the manual stand-in for Gate 6 until real CPCs are loaded.

---

## 4. The end-to-end pilot transaction (per role)

| Step | Who | Where | Pilot behavior |
|---|---|---|---|
| Book | Customer | `book.html` | parcel/vehicle + contact → tracking token |
| Review + quote | Operator | console (public-booking queue) | review, price, send indicative → firm quote |
| Payment state | Operator | console | mark payment received **out-of-band** (funds MOCK; no live custody) |
| Match + assign | Operator | console (marketplace) | generate candidates → broadcast → select offer → create assignment |
| Accept | Carrier | `portal.html` | accept the assignment; availability must show the unit AVAILABLE |
| Execute | Driver | `driver.html` | start trip → advance status → GPS → arrive |
| Secure delivery | Driver + Recipient | `driver.html` | recipient reads their OTP to the driver; driver verifies (driver never sees the code) |
| POD | Driver | `driver.html` | submit proof of delivery |
| Settlement | Operator | console | record settlement (no live money moves; recorded for reconciliation) |

Run **one full loop** before onboarding a second customer. Confirm each stage transitions and the customer
sees it on `track.html`.

---

## 5. Daily operator checklist (during pilot)

- [ ] `/healthz` + `/readyz` green (or your monitor is green).
- [ ] Public-booking queue reviewed; no booking stuck > agreed SLA.
- [ ] Every active carrier unit's readiness still ELIGIBLE (watch for INSURANCE/REGISTRATION expiry).
- [ ] Availability board accurate (no “AVAILABLE” unit that's actually out).
- [ ] Any reassignment needed handled via the governed path (funds never moved).
- [ ] Nightly PostgreSQL backup succeeded.
- [ ] Audit ledger reviewed for anomalies.

---

## 6. Go / No-Go checklist (the gate to actually START the pilot)

**GO only when all are true:** §1 readiness gate green · at least **1 customer briefed** · at least **1
carrier ELIGIBLE with a paired, eligible driver** · on-call reachable · backup taken · rollback rehearsed.
**NO-GO** if any Critical/High defect is open, probes are red, or no eligible carrier exists.

---

## 7. Hypercare — incident severity matrix (fill the [brackets])

| Sev | Definition | Response | Owner |
|---|---|---|---|
| **S1** | data loss, security/privacy breach, any hint of unintended fund movement | **15-min** page; pause new bookings; take a backup | [owner] + [eng] · [phone] |
| **S2** | booking/matching/assignment/tracking broken for all | **1-hour** | [eng] · [phone] |
| **S3** | one tenant/feature degraded; workaround exists | **next business day** | [ops] |
| **S4** | cosmetic / minor | backlog | [ops] |

- **On-call:** [name] / [number] · **Backup on-call:** [name] / [number]
- **Monitoring/alerting:** [APM/uptime tool] on `/healthz`, `/readyz`, and the audit ledger.
- **Support hours (pilot):** [hours / timezone]. **Status comms channel:** [channel].

---

## 8. Abort / rollback criteria

Pause or roll back the pilot if any occur:
- S1 incident, or any sign of unintended fund movement (there should be none — funds are OFF).
- `/readyz` red > [X] minutes with no quick fix.
- Data-integrity anomaly in the audit ledger or reconciliation.

**Rollback:** take a fresh backup → stop accepting new public bookings (front-end config / maintenance)
→ if needed, restore the last good PostgreSQL backup (`backup_restore.py --postgres` playbook) → reconcile
→ post an incident note. Because live funds are OFF, no financial unwind is required.

---

## 9. Success metrics + graduation (exit criteria)

Pilot is a success and can **graduate** when, over [2 weeks / N transactions]:
- [ ] ≥ [N] complete transactions booking→POD→settlement with **zero Critical/High** defects.
- [ ] Restore drill executed once with acceptable RTO/RPO.
- [ ] Tenant isolation held (no cross-tenant incident).
- [ ] Customer + carrier satisfaction acceptable ([survey / thumbs-up]).

**Then unlock the next decisions (already gated in code, owner-flipped):**
- **LIVE PAYMENTS** — only after legal operating model approved + licensed provider certified → flip the
  three `payments.*` flags → one low-value controlled live transaction first.
- **MARKETPLACE at scale** — after real CPC/unit/area data loaded + verified → flip
  `marketplace.ltfrb_enforcement_enabled=true` (removes the manual-vetting requirement).
- **Providers** — connect messaging (then OTP auto-delivery), maps/tracking, insurance as selected.

---

## 10. Cadence

- **Day 1:** run the first full transaction with the operator watching every stage; debrief same day.
- **Week 1:** daily checklist (§5); one incident-response dry run; confirm backups.
- **Day 30:** review against §9; decide graduation to funds-ON / LTFRB-ON / scale, or extend the pilot.

---

*This kit assumes the go-live cutover (`GO_LIVE_CUTOVER_RUNBOOK.md`) is complete through the Application
gates. It deliberately keeps Live Payments and LTFRB enforcement OFF so a real pilot can run while those
external gates resolve. Nothing here fakes a provider, moves real money, or bypasses a compliance gate.*
