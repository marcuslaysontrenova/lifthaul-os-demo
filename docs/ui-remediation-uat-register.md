# LiftHaul UI Remediation — Defect and UAT Register

Release candidate: pending  
Decision owner: Product Manager with CTO, QA Manager, and business acceptance  
Current decision: **NO-GO until every P0/P1 and user-confusing P2 item passes retest**

| ID | Page / evidence | Defect | Severity | Owner | Target | Retest result | Evidence / release decision |
|---|---|---|---|---|---|---|---|
| UI-001 | Landing header | Duplicate `Book a Service` competed with the hero action. | P2 | Frontend | Current revision | Pending full suite | Header action removed; hero is the only booking CTA. |
| UI-002 | Vehicle catalogue | Default view hid categories and truncated results. | P1 | Frontend | Current revision | Pending browser UAT | `All Vehicles` now renders every active canonical record in governed sections. |
| UI-003 | Provider registration | A second hard-coded four-vehicle catalogue conflicted with the canonical catalogue. | P1 | Architecture | Current revision | Pending integration retest | Provider now consumes `vehicle-catalogue.json` by vehicle code. |
| UI-004 | Dark cards | Dark La Salle-green links failed readable normal-text contrast on black. | P2 | Design system | Current revision | Pending accessibility scan | White, `#D1D5DB`, and `#9BE33D` dark-surface tokens introduced. |
| UI-005 | Secondary journeys | Home/back routes were inconsistent or visually unclear. | P1 | Frontend | Current revision | Pending link gate | Driver and provider routes now expose visible breadcrumb-style exits. |
| UI-006 | Root HTML pages | Placeholder `href="#"` controls created false navigation targets. | P1 | QA | Current revision | Pending automated gate | Placeholder social controls and password action changed to non-link controls. |
| UI-007 | Shared shell | Navigation markup remains page-specific even though routes and tokens are aligned. | P2 | CTO | Post-remediation architecture sprint | Open | Extract a versioned shared public shell without breaking static deployment. |
| UI-008 | Responsive UAT | Desktop, tablet, mobile, loading, error, and success screenshots require fresh baselines. | P1 | QA Manager | Before deployment | Open | Capture after automated functional gates pass. |
| UI-009 | Role UAT | Ten requested personas have not yet completed documented end-to-end acceptance. | P1 | Product / Operations | Before public launch | Open | Execute the matrix below without developer assistance. |

## Required UAT matrix

Each persona must complete its journey on desktop and mobile, retain data through Back/refresh where applicable, understand the next action, and reach an explicit success or governed blocked state:

- Individual customer
- Small-business booker
- Corporate booker
- Motorcycle rider
- Light-vehicle driver
- Truck owner
- Fleet operator
- Heavy-equipment operator
- Staff member
- Administrator

## Release gates

- Zero open P0 and P1 defects.
- Zero P2 defects that could cause misunderstanding, wrong vehicle selection, or an incorrect booking.
- Canonical catalogue, link, navigation, registration, booking, payment-state, accessibility, responsive, and performance suites pass.
- QA Manager attaches automated results and visual evidence.
- CTO confirms shared data contracts, saved-data compatibility, and no duplicate vehicle business logic.
- Product Manager records **GO** only after signed business UAT; otherwise the decision remains **NO-GO**.
