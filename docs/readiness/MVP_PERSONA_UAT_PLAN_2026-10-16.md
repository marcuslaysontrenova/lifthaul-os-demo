# LiftHaul MVP persona UAT plan — 2026-10-16

## Scope and release rule

The controlled automated matrix exercises the required personas with synthetic
data in an isolated database. It never sends messages, dispatches providers or
moves funds. A passing local or CI run is a prerequisite for production UAT, but
does **not** close MVP Gate 5.

Gate 5 closes only when each production journey below is witnessed on the exact
deployed commit with a dedicated account, timestamp, result and redacted evidence.
No shared credential may be used. Live funds remain disabled.

## Persona checklist

| Persona | Automated acceptance | Production evidence still required |
|---|---|---|
| Booker | Authenticated workspace, owned booking list/detail and cross-booker isolation | Dedicated booker login; create/continue booking; estimate; status retained after sign-out/sign-in |
| Driver applicant | Submit, contact verification, login remains blocked pending sponsorship | Submit production application using synthetic identity; confirm status and missing requirements |
| Accredited driver | Sponsor, independent verify/activate, authenticated driver role cannot self-verify | Dedicated accredited test driver login; own job visibility; forbidden admin route |
| Motorcycle owner | Canonical motorcycle registration lands DRAFT; self-verification denied | Owner login; register synthetic motorcycle; verify correct requirements/status |
| Light-vehicle owner | Mini Van remains a distinct canonical category and lands DRAFT | Owner login; register synthetic Mini Van; verify category survives refresh/back navigation |
| Truck owner | Specs classify to canonical 6-wheel closed van and land DRAFT | Owner login; register synthetic truck; verify dimensions/documents/status |
| Fleet owner | Multiple owned vehicles and drivers; all remain in governed review state | Fleet login; add at least two units and drivers; assign one eligible pair; verify ownership isolation |
| Specialized provider | Crane capability plus engineering specs recorded without self-approval | Provider login; register capability/equipment; confirm manual-review routing |
| Operations | Authenticated queue read/review; finance denied operations action | Operations login; find exact synthetic booking; execute allowed review; denied out-of-role action |
| Finance | Protected-payment queue; licensed-rail/live-fund gate remains fail-closed | Finance login; inspect synthetic record and reconciliation; denied operational review; no real payment |
| Administrator | Create scoped user, suspend revokes session, reactivate restores sign-in | Administrator login; create dedicated UAT principal; validate audit event and role boundaries |

## Automated execution

From the repository root:

```powershell
python ci/run_mvp_persona_uat.py --output audit/mvp-persona-uat.json
```

Expected result: **11/11 PASS**, `failed: 0`, and
`production_gate_closed: false` in the generated evidence.

## Verified automated result — 2026-10-16

- Controlled persona matrix: **11/11 PASS**.
- Adjacent authentication, RBAC, public-intake QA and Protected Payment regression:
  **66/66 PASS**.
- Machine-readable evidence: `audit/mvp-persona-uat.json`.
- A real least-privilege gap was corrected: Finance and Finance Administrator
  roles can now read Protected Payment, reconciliation and exception queues, but
  still cannot manage marketplace Protected Payments, approve releases or
  approve payouts.
- MVP Gate 5 remains **OPEN** pending witnessed production sign-ins and journeys
  for every persona on the exact deployed commit.

## Production evidence schema

For each persona, record:

- exact deployed commit SHA and deployment ID;
- production URL and UTC/Asia-Manila timestamp;
- dedicated synthetic account identifier (redacted in shared reports);
- steps executed and expected/actual result;
- screenshot or video evidence with personal data and secrets removed;
- linked defect number for any failure;
- retest result and approver;
- confirmation that no real customer data, dispatch or money was used.

## Stop conditions

Stop the affected journey and record a defect if any account can see another
principal's data, a driver/provider can self-approve, an out-of-role action is
allowed, a booking loses state, or a live-fund control becomes enabled. Any such
failure is P0/P1 and Gate 5 remains open.
