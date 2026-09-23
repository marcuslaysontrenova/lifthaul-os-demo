# Role-by-role hostile journey report

## Client booker — blocked

| Stage | Judgment | Evidence and abandonment risk |
|---|---|---|
| Understand service | Partial pass | Proposition is understandable and the service CTA is visible, but “matched in minutes,” verified carriers, real-time tracking, and protected-payment language exceed the live capability. |
| Register and authenticate | Fail | Deployed client sign-in is disabled because the hosted API is absent. Password recovery cannot be completed end to end. |
| Enter cargo and route | UI pass / service fail | Cargo-first fields, address hierarchy, map, special handling, and acknowledgement exist. The backend nevertheless accepts no weight, nonnumeric weight, and island-only locations. |
| Select a safe vehicle | Critical fail | The deterministic matcher honors weight, dimensions, volume, handling, and a 10% safety allowance, but final submit can bypass it through `LEGACY_CAPACITY_ONLY`. |
| Understand price | Critical fail | Canonical quote logic produced the approved three-line total in the probe, but the client ledger displays irreconcilable arithmetic and an excluded processing fee. |
| Pay and know funds are protected | Fail | No live payment partner is active. Synthetic records contain impossible combinations such as Payment Required + Funds Protected + Pay Now. |
| Track and communicate | Fail | No live GPS, messaging, or operational notifications are connected. |
| Cancel, dispute, refund, obtain invoice | Fail | Defensive backend tests exist, but no live journey or downloadable statutory invoice is available; the demo allows a delivery-confirm action on a disputed transaction. |
| Obtain support | Fail | No usable public support channel, escalation path, DPO contact, or complaint SLA. |

**Hostile-user conclusion:** a first-time client can understand the concept but cannot complete a trustworthy transaction. A high-value shipper should abandon before sharing data or money.

## Independent truck owner — blocked

| Expectation | Judgment |
|---|---|
| Self-service registration and correction | Provider intake UI exists; deployed submission and verification are unavailable without an API. Rejection/correction cannot be proven live. |
| Verified vehicle ownership and duplicate-plate protection | Backend controls are tested, but no production LTFRB/registration verification integration or real operational evidence exists. |
| See funded status before work | Synthetic UI states are contradictory, so providers cannot safely infer that funds are real or protected. |
| See accurate cargo, route, and exclusions | UI collects details and disclosures, but incomplete and weightless bookings are accepted at the service boundary. |
| Predict net earnings and payout timing | Not demonstrated. The client ledger is wrong, live payout is disabled, and no end-to-end reconciliation with a bank/payment partner exists. |
| Defend against unsafe cargo, delays, breakdowns, and false ratings | Backend modules/tests suggest workflow intentions; no complete provider-facing live controls or support operations were demonstrated. |

**Hostile-user conclusion:** an owner cannot predict job safety, funded status, net settlement, or support outcome. They should not mobilize a vehicle from this release.

## Fleet owner — blocked

The repository contains substantial fleet, driver, vehicle, document, role, assignment, and reporting code with automated tests. That is not the same as a deployable fleet portal.

- Public onboarding does not reach a hosted fleet workspace.
- Bulk registration, vehicle-driver pairing, availability, assignment conflict handling, expiry enforcement, settlement reconciliation, and exports were not demonstrated in the deployed system.
- Backend RBAC and isolation tests are positive evidence, but no production SSO/MFA/offboarding or real organization-administrator workflow was observed.
- A 1/10/100-vehicle usability study and a thousands-of-bookings performance test do not exist.

**Hostile-user conclusion:** architecture exists; operational fleet management does not yet exist as a customer-usable service.

## Driver — blocked

- The landing page promises “Register as a driver,” but the destination has no driver option.
- The driver login lacks show-password and recovery controls and cannot authenticate against a hosted API.
- No live dispatch, navigation, offline status queue, pickup/POD capture, messaging, incident, breakdown, or reassignment journey was demonstrated.
- No evidence shows safe retry behavior if the driver loses signal while changing status or uploading delivery proof.

**Hostile-user conclusion:** the role most exposed to poor connectivity and operational pressure has the least complete public workflow.

## Other service provider — blocked

Crane/heavy-equipment and logistics-service roles appear during onboarding, and engineered work is correctly routed away from fabricated instant pricing in backend tests. However:

- Licensing, operator certification, rigging plans, permits, equipment availability, mobilization, multi-crew coordination, evidence, and settlement cannot be completed live.
- “Matched in minutes” is unsuitable for engineered lifts requiring assessment.
- No live exception path covers site access, ground conditions, lifting plan revisions, weather, or safety stoppage.

**Hostile-user conclusion:** heavy-equipment breadth is a potential differentiator, but marketing currently outruns the operational product.

## LiftHaul operations, compliance, finance, support — blocked

| Team | Positive evidence | Release gap |
|---|---|---|
| Operations | Booking/assignment modules and tests | No production queue, partner supply, GPS/POD integration, incident rota, or measured service levels. |
| Compliance | Document and approval models; role checks | No official LTFRB/identity integration, binary document pipeline, malware scanning, reviewer SLA, or production audit exercise. |
| Finance | Webhook, idempotency, refund, release, and reconciliation tests | No live provider, incorrect client ledger, no bank settlement reconciliation, tax sign-off, or dual-control production exercise. |
| Support | Dispute and notification concepts | No public contact, case intake, escalation matrix, complaint SLA, staffed operating hours, or user-visible case history. |
| Security/admin | RBAC and tenant-isolation tests | No external pentest, production secrets/identity review, offboarding exercise, monitoring, or incident drill. |

## Most judgmental answers

- **Why trust a ₱1 million shipment?** There is not yet enough evidence. Verification, tracking, support, insurance/protection, and settlement are not live.
- **Why list 100 vehicles?** There is no measured supply, workflow, payout, or fleet-scale operating advantage yet.
- **What is materially better?** Cargo-first heavy-haul matching, engineered-review routing, and evidence-gated protected settlement could differentiate LiftHaul if made real and independently validated.
- **Is 10% justified?** Not yet. A client cannot receive the promised operational value, and the displayed ledger is wrong.
- **Is protected payment real protection?** Not in this deployment. It is a fail-closed design and synthetic demonstration, not a licensed live transaction.
- **Can disputes be evidence-led?** The model intends this, but no real moderated case proves it.
- **Can the platform survive fraud, outages, and 100× traffic?** Unknown; no production-equivalent security, chaos, or load evidence exists.
- **Would the audit approve a ₱1 million transaction?** No.

