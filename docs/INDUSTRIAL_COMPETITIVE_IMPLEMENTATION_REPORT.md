# LiftHaul Industrial Competitive-Gap Implementation Report

## 1. Executive result

LiftHaul now has an evidence-backed P0 foundation for **Managed Projects** alongside the existing
cargo-first Instant Book flow. This increment did not create a second booking system: the governed
survey, specialized-resource, package-plan, reservation and stage-history records all reference the
canonical `mkt_bookings` record.

Directly verified outcomes:

- complex, uncertain, hazardous, oversized, engineered-lift and multi-resource work cannot be forced
  through Instant Book;
- a free-text note cannot clear a required site survey;
- site surveys are versioned and require structured measurements, findings, private evidence references
  and independent approval;
- cranes, forklifts, lowbeds, rigging crews, equipment operators, helpers, escorts and safety officers
  have a governed submitted/verified registry;
- a resource package checks the vehicle's verified legality, payload safety margin and availability,
  the driver's licence/qualification/availability, and specialized-resource certification, inspection,
  maintenance, capacity and schedule conflicts;
- the entire package is reserved atomically or not at all;
- resource-plan approval is separated from dispatch creation; and
- public tracking exposes survey/plan status but not private findings or evidence references.

This is **not yet a production-ready industrial execution platform**. Real media storage/scanning,
specialized-equipment load-chart evidence, the full control-room lifecycle, field execution forms and
live external providers remain release blockers.

## 2. System inspected

- Repository: `marcuslaysontrenova/lifthaul-os-demo`
- Local branch: `codex/hostile-product-audit`
- Inspected baseline head: `a81f84a`
- Stack: static HTML/CSS/JavaScript surfaces; Python standard-library HTTP/service layer; SQLite test
  runtime with PostgreSQL portability adapter; pytest; GitHub Pages frontend; separately deployable
  backend configuration.
- Primary inspected modules: public booking, cargo matching, marketplace onboarding/matching/trips,
  availability, vehicle legality/operator qualifications, quotations/rate cards, protected payments,
  delivery verification, notifications, billing/reporting, tenancy, RBAC, audit and deployment.
- Existing uncommitted hostile-audit work was preserved and extended. No production deployment or live
  financial operation was performed.

## 3. Competitor gap matrix

The benchmark was refreshed against official public product pages on 23 September 2026. Marketing
claims are treated as claims, not independently verified service outcomes.

| Capability | Lalamove PH | Transportify PH | TEXTS | LiftHaul evidence/status |
| --- | --- | --- | --- | --- |
| Instant/scheduled delivery | Strong | Strong | Claimed | Present for governed standard cargo; not the launch differentiator |
| Multi-stop / bulk | Up to 19 API stops and business bulk tools | Up to 15 destinations and high-volume tools | Not verified in equivalent depth | Multi-stop present; bulk intake present; parity remains partial |
| Corporate billing/API | Corporate wallet, statements, postpaid and API/webhooks | Business dashboard, prepaid/postpaid, API/SDK | Partner-payment and app claims | API, billing and reporting foundations exist; live integrations remain gated |
| GPS/POD | Real-time tracking, ShareLink and POD | GPS, ETA and delivery confirmation | GPS/milestone tracking claimed | Trip/POD domain exists; public live-map provider remains placeholder |
| Fleet management | Driver/vehicle approval and pairing | Screened providers and dedicated/favourite arrangements | Fleet-provider network | Strong onboarding, compliance, pairing and availability foundation |
| Goods protection | Publicly advertises protection/insurance products | Publicly advertises cargo insurance | Insurance/security claims | Workflow exists but no unlicensed coverage promise; live binding gated |
| Site survey | Not central to public delivery proposition | Business process mapping is advertised | Project curation is claimed | Versioned, scheduled, evidence-based and independently approved survey is now core |
| Truck + equipment + crew | Not central | Dedicated fleet/special services, not a public combined industrial package | Not publicly verified as one governed package | Atomic verified package is a core differentiator |
| Capacity/safety gate | Vehicle limits published | Vehicle limits published | Training/vetting claims | Deterministic weight/dimension/volume/cargo/route gates plus unit legality and capacity |
| Industrial safety workflow | Not central | Transport-focused | Training/safety claims | Partial: risk/survey/resource gates strong; lift plan/toolbox/pre-start still incomplete |
| Industrial profitability | Not central publicly | Not central publicly | Not verified publicly | Existing quotation cost/margin and settlement controls; managed-project commercial UI incomplete |
| Repeat industrial jobs | Not central | Favourite/dedicated arrangements | Not verified | Missing governed repeat-project template |

Official references:

- [Lalamove for Business](https://www.lalamove.com/en-ph/business)
- [Lalamove API Solutions](https://www.lalamove.com/en-ph/business/api-solutions)
- [Lalamove Fleet Management](https://www.lalamove.com/en-ph/fleet-management)
- [Transportify API for Business](https://www.transportify.com.ph/api-for-business-teams/)
- [Transportify on-demand and multi-destination service](https://www.transportify.com.ph/delivery/business/on-demand-last-mile-delivery-app/)
- [TEXTS official site](https://thaumazoexpresstransportsolutions.com/)
- [TEXTS public company/product description](https://ourshop.thaumazoexpresstransportsolutions.com/about)

## 4. Before-and-after checklist

Statuses use only: **Verified implemented**, **Partial**, **Missing**, **Placeholder only**, **Broken**,
or **Not applicable**.

| ID | Before | After | Evidence / acceptance result |
| --- | --- | --- | --- |
| GOV-01 | Verified implemented | Verified implemented | `core.py`, `tenant.py`, negative access tests |
| GOV-02 | Partial | Partial | Existing granular roles plus new estimator/dispatcher/safety permissions; full industrial UI coverage remains |
| GOV-03 | Verified implemented | Verified implemented | `audit_logs`; new industrial survey/resource/stage events |
| CRM-01 | Verified implemented | Verified implemented | `crm_admin.py`; canonical public inquiry intake |
| CRM-02 | Partial | Partial | Pipeline/task foundations exist; industrial sales follow-up UI remains fragmented |
| SUR-01 | Placeholder only | Verified implemented | Versioned schedule/assignment/completion/approval API and staff actions |
| SUR-02 | Partial | Partial | Structured dimensions/access/clearance/findings and evidence refs; real media pipeline missing |
| EST-01 | Partial | Partial | Deterministic equipment/capacity assessment; full explainable industrial cost estimator remains human-led |
| EST-02 | Partial | Verified implemented | Unknown/unsafe data returns Site Survey Required and suppresses price |
| QUO-01 | Verified implemented | Verified implemented | Versioned quotations in `core.py` |
| QUO-02 | Partial | Partial | Vehicle/equipment/crew lines and margins supported; excluded toll/permit costs remain informational by product policy |
| QUO-03 | Partial | Partial | Approval/version/expiry foundations exist; public managed-project acceptance integration needs completion |
| BKG-01 | Partial | Partial | Structured address/coordinates/distance; live maps remain provider-gated |
| BKG-02 | Verified implemented | Verified implemented | Cargo-first eligible standard-book flow |
| BKG-03 | Partial | Partial | Managed intake, survey and resource gates now real; full job control room incomplete |
| BKG-04 | Partial | Partial | Multi-stop/scheduled supported; repeat template missing |
| AST-01 | Partial | Verified implemented | Vehicle registry plus governed specialized-resource registry |
| AST-02 | Partial | Partial | Typed cranes/forklifts/lowbeds/crews exist; detailed load-chart model still missing |
| AST-03 | Partial | Verified implemented | Availability plus atomic overlap-safe project reservations |
| AST-04 | Partial | Partial | Status/inspection/certification/maintenance gates exist; specialized maintenance history incomplete |
| PRV-01 | Verified implemented | Verified implemented | Provider/vehicle/driver onboarding modules and tests |
| PRV-02 | Verified implemented | Verified implemented | Document/qualification expiry engines and assignment gates |
| PRV-03 | Partial | Partial | Verification/accreditation gates exist; customer-facing Certified badge rules need consolidation |
| MAT-01 | Partial | Verified implemented | Category match plus unit legality/capacity/qualification/availability package validation |
| MAT-02 | Partial | Verified implemented | Managed work and resource plan require independent human approval |
| DSP-01 | Partial | Partial | Project stage history added; complete 23-state transition map is not yet enforced in one engine |
| DSP-02 | Partial | Partial | Combined package reserved atomically; final trip/crew assignment integration remains |
| DSP-03 | Placeholder only | Placeholder only | No false live GPS claim; provider adapter still required |
| SAFE-01 | Partial | Partial | Deterministic risk factors and survey gate; full JHA template incomplete |
| SAFE-02 | Partial | Partial | Safety approval hooks exist; lift plan/toolbox/pre-start evidence still incomplete |
| SAFE-03 | Verified implemented | Verified implemented | Trip exceptions, incidents/claims and payment-hold domains |
| POD-01 | Partial | Partial | Evidence metadata exists; signed upload/geotag pipeline missing |
| POD-02 | Verified implemented | Verified implemented | POD and one-time recipient verification |
| POD-03 | Partial | Partial | Trip/usage foundations; unified attendance/equipment-hours reconciliation incomplete |
| FIN-01 | Verified implemented | Verified implemented | Server pricing, rate cards and traceable snapshots |
| FIN-02 | Partial | Partial | Configurable 10% fee and settlement ledger; live payout provider remains gated |
| FIN-03 | Partial | Partial | Deposit/payment/invoice states exist; live custody/payment not activated |
| FIN-04 | Placeholder only | Placeholder only | Provider adapters fail closed without credentials |
| FIN-05 | Partial | Partial | Billing/statements and credit foundations; full corporate approval UX incomplete |
| FIN-06 | Verified implemented | Verified implemented | Quotation cost/margin, expenses, billing and settlement reconciliation |
| INS-01 | Partial | Partial | Controlled insurance/claim workflow; no live coverage promise without licensed partner |
| COM-01 | Partial | Partial | Notification abstraction/history exists; channels depend on configured providers |
| COM-02 | Partial | Partial | Support surface exists; full case/escalation lifecycle remains |
| REP-01 | Verified implemented | Verified implemented | Role-specific customer/provider/admin dashboards |
| REP-02 | Partial | Partial | Reporting foundation; industrial safety/utilization pack incomplete |
| REP-03 | Verified implemented | Verified implemented | Governed report/export framework with access checks |
| API-01 | Verified implemented | Verified implemented | Versioned client/webhook architecture and API reference |
| SEC-01 | Verified implemented | Verified implemented | Auth/session/RBAC/tenant negative tests |
| SEC-02 | Partial | Partial | Private metadata access enforced; real scanning/signed object storage missing |
| SEC-03 | Partial | Partial | Privacy/security documentation and controls exist; retention automation incomplete |
| QA-01 | Verified implemented | Verified implemented | New and existing regression tests; 87 targeted tests green |
| QA-02 | Partial | Partial | Responsive surfaces exist; full automated accessibility/device matrix incomplete |
| OPS-01 | Partial | Partial | Deployment/performance/monitoring plans exist; backup/failover proof remains environment-dependent |

## 5. Implemented changes

- Added `mkt_project_surveys`, a versioned governed survey record.
- Added explicit survey scheduling, completion and approval API operations.
- Added separation of duties between survey completion and approval.
- Replaced the legacy free-text survey completion shortcut with a hard structured-survey gate.
- Added `mkt_specialized_resources` with source-backed verification, capacity, certification, inspection,
  maintenance and active status.
- Added resource plans, plan items and time-bound reservations.
- Composed existing vehicle legality, payload, operator qualification and effective availability gates.
- Added all-or-nothing package reservation with overlap detection.
- Added independent resource-plan approval and stage history.
- Added customer-safe and staff-safe industrial control projections.
- Added staff-console survey sequencing and updated industrial intake status.
- Added server routes and database initialization.

## 6. Files/components changed

- `backend/industrial_projects.py` — industrial survey, resource registry, package validation/reservation,
  approval, audit and projections.
- `backend/db.py` — additive industrial schema initialization.
- `backend/server.py` — governed industrial admin API routes.
- `backend/public_booking.py` — customer/staff projections and structured-survey enforcement.
- `backend/test_industrial_projects.py` — P0 acceptance and failure-path tests.
- `backend/test_public_booking.py` — replaced note-based survey acceptance with the governed workflow.
- `console.html` — survey status and schedule/complete/approve actions.
- `docs/INDUSTRIAL_PRODUCT_GAP_AND_ROADMAP.md` — updated closed and remaining gaps.
- Existing working-tree files `book.html`, `cargo-booking.js`, delivery/goods-protection tests and related
  hostile-audit changes were preserved.

## 7. Tests and verification

Commands and observed results:

```text
python -m pytest backend/test_public_booking.py backend/test_availability.py \
  backend/test_quotation_pricing.py backend/test_delivery_verification.py \
  backend/test_security.py -q
155 passed

python -m pytest backend/test_industrial_projects.py backend/test_public_booking.py -q
87 passed

python -m py_compile backend/industrial_projects.py
passed
```

Proven scenarios include structured survey gating, independent approval, evidence redaction, verified
vehicle capacity, specialized-resource verification, maintenance blocking, incomplete-package rejection,
atomic rollback, conflict prevention and independent plan approval. Pytest emitted only environment/cache
warnings; no test failure remained.

## 8. Unimplemented items

| Priority | Gap | Blocker/risk | Acceptance target |
| --- | --- | --- | --- |
| P0 | Secure media pipeline | Object storage, malware scanner and retention configuration | Private upload, hash, scan, signed access and deletion tests |
| P0 | Specialized compliance wallet | Load charts, ownership/authority and maintenance-history schema/UI | Expired/missing evidence blocks resource validation |
| P0 | Full managed quotation bridge | Canonical public booking and enterprise quotation domains need a governed bridge | Approved versioned quote only; customer acceptance/expiry/revision tested |
| P0 | Lift plan/JHA/toolbox/pre-start | Safety templates and qualified approval model incomplete | High-risk start transition fails without every required approval |
| P0 | Field industrial execution | Arrival, crew attendance, equipment hours, overtime and before/after uploads incomplete | Mobile field flow reconciles job, evidence and billing |
| P1 | Industrial control room | Current staff UI emphasizes intake/survey, not full lifecycle | One board enforces all 23 stages and responsibilities |
| P1 | Repeat-project templates | No governed cloning/versioning | Approved template clones scope while revalidating expiry/availability |
| P1 | Corporate managed-project UX | Customer resource-plan and scope acceptance incomplete | Customer approves exact resources/scope/price/exclusions |

## 9. Security, safety and operational risks

- **Release blocker:** evidence references are records, not an upload service. Do not claim photographs or
  documents are securely stored until object storage, malware scanning and signed access are deployed.
- **Release blocker:** specialized-resource verification needs full documentary evidence and load charts,
  not only structured status fields.
- **Release blocker:** no high-risk industrial job should dispatch until lift-plan, JHA, toolbox and
  pre-start gates are implemented and tested.
- **Release blocker:** live protected funds remain off; no payment or insurance success may be fabricated.
- **High:** atomic overlap checks provide correct application behavior, but production PostgreSQL should
  add database-level locking/exclusion constraints and a concurrent transaction test.
- **High:** staff console prompts are a functional operator bridge, not the final high-volume control-room
  experience.
- **Medium:** full-suite runtime and Windows/OneDrive pytest cache permissions should be stabilized in CI;
  the tests passed despite cache warnings.

## 10. Recommended next build cycle

1. **Secure evidence service** — upload allow-list, size limits, malware scan, SHA-256, object storage,
   signed role-scoped access, retention and deletion; prove cross-tenant denial.
2. **Specialized compliance wallet** — load charts, OR/CR/authority where applicable, operator
   qualification, inspection and maintenance history; prove expiry and unsafe-status blocks.
3. **Managed quotation bridge** — versioned scope/resource/cost proposal, independent safety/commercial
   approval, customer accept/reject/revise/expire; retain tolls/permits as clearly separated client
   responsibility unless the owner changes that commercial policy.
4. **Safety execution pack** — JHA, lift/rigging plan, toolbox and pre-start forms; block dispatch/start
   until approvals and required evidence exist.
5. **Industrial control room** — enforce and visualize the full lifecycle from inquiry through settlement,
   including accountable owner, due date, evidence and stage history.
6. **Field execution and reconciliation** — mobile arrival, geotagged evidence, attendance, equipment
   hours, overtime, incidents, customer sign-off, final billing and provider settlement.
7. **Repeat-project templates** — clone approved operational knowledge but always re-run compliance,
   safety, capacity and availability gates.

The recommended market message remains: **One platform to survey, quote, schedule, transport, lift and
position industrial cargo using verified equipment and qualified crews.** Express delivery should remain
a later service line rather than the initial competitive battlefield.
