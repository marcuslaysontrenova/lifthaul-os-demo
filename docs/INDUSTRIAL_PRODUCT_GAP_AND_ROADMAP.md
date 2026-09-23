# LiftHaul Industrial Product Gap and Roadmap

## Product decision

LiftHaul's initial market position is **managed industrial, heavy and equipment transport**, not a
generic low-cost delivery marketplace. Standard cargo remains available through Instant Book only
when the request passes deterministic capacity, dimension, cargo, route and handling gates.

The primary customer promise is:

> Survey, estimate, schedule, transport, lift and position industrial cargo using verified equipment
> and qualified crews through one governed workflow.

## What already existed

| Capability | Existing implementation | Assessment |
|---|---|---|
| Cargo-first booking | Locations, cargo, weight, dimensions and handling precede vehicle selection | Strong foundation |
| Safe vehicle matching | Administrator-managed payload, volume, body dimensions, cargo compatibility, route and safety allowance | Strong foundation |
| Oversized/manual routing | Unsafe matches become customized operations assessments; clients cannot force an undersized vehicle | Strong foundation |
| Industrial assets | Lowbeds, cranes, forklifts, boom trucks and operator qualification records exist | Present, but fragmented across operational screens |
| Resource control | Reservations, conflict prevention, maintenance status and assignment controls exist | Present |
| Operations lifecycle | Planning, resource reservation, safety review, dispatch, on-site, execution, completion and acceptance states exist | Present |
| Completion evidence | Dispatch, on-site, completion and acceptance evidence gates exist | Present |
| Protected-payment pricing | Transport charge + 10% administration fee + applicable tax; excluded expenses remain separate | Present |
| Audit and role controls | Audited operator actions and permission checks exist | Present |

## Gap closed in this increment

1. Added first-class `INSTANT_BOOK` and `MANAGED_PROJECT` booking modes.
2. Added server-authoritative escalation: a user cannot force machinery, hazardous cargo, oversized
   loads, engineered lifts, multi-resource work or uncertain specifications through Instant Book.
3. Added Industrial, Heavy, Equipment and Cargo service lines.
4. Added combined-resource intake for transport, lowbed, crane, forklift, rigging crew, operator,
   helpers, escort, safety officer, permits, insurance review and inter-island work.
5. Added pickup/delivery site-condition and final-positioning requirements.
6. Added an explicit unknown-specification path; the system returns **Site Survey Required** instead
   of guessing equipment or price.
7. Persisted project stage, assessment status, site-survey requirement, human-approval requirement,
   risk level, risk factors, resources and photo count on the canonical booking record.
8. Suppressed instant pricing for all Managed Projects, even when a preliminary vehicle category fits.
9. Exposed project assessment in customer tracking and the staff intake queue.
10. Added an audited `COMPLETE_SURVEY` operator action. Survey-required jobs cannot be quoted or
    released to the marketplace until a staff member records survey evidence.
11. Repositioned the public booking message around managed industrial transport while retaining a
    governed Instant Book path for standardized work.
12. Added a versioned site-survey record with scheduling, assigned surveyor, structured measurements,
    findings, evidence references, completion and independent safety approval. A free-text booking note
    can no longer clear the survey gate.
13. Added a governed specialized-resource registry for cranes, forklifts, lowbeds, rigging crews,
    operators, helpers, escorts and safety officers. Submitted records are not usable until verified
    against a recorded source.
14. Added atomic combined-resource packages. Vehicle legality/capacity, driver qualifications,
    specialized-resource certification/inspection/maintenance, availability and schedule overlap are
    checked before any reservation is written; a single failure rolls back the entire package.
15. Added independent resource-plan approval and append-only project stage history. Customer tracking
    receives only safe survey/plan status and never private findings or evidence references.

## Remaining gaps, ordered by release priority

### P0 — required before real industrial transactions

- Real media upload pipeline for cargo/site photos and videos: object storage, malware scanning,
  signed URLs, retention, evidence hashes and role-scoped access. The current form records photo
  count only and must not imply the files were uploaded.
- Real survey evidence upload and checklist templates. The governed survey record now stores private
  evidence references and revisions, but object storage, malware scanning and signed access are not yet
  connected.
- Complete unit-level specialized-equipment evidence: load-chart documents, ownership/authority and
  maintenance work-order history. Vehicle legality, operator qualification, certification and
  inspection expiries are enforced today; specialized equipment still needs the richer evidence wallet.
- Operations UI for creating the entire resource package. The API now validates and reserves the package
  atomically; the staff console currently exposes the governed survey sequence and project status only.
- A human-approved industrial quotation model covering equipment hours, crew, mobilization,
  overtime and agreed inclusions while keeping tolls, permits and other excluded expenses outside
  Protected Payment unless commercial policy is deliberately changed.

### P1 — operational differentiators

- First-class industrial control-room UI using the canonical lifecycle: Inquiry, Survey Required,
  Estimation, Quotation Submitted, Customer Approval, Deposit/Payment Verified, Resources Reserved,
  Safety Review, Dispatched, On Site, In Execution, Completed, Customer Acceptance, Billing and
  Incident/Claim.
- Unified compliance wallet and proactive expiry dashboard for provider, vehicle, equipment and crew.
- Repeat-job templates and governed cloning of route, site, equipment and safety requirements.
- Customer-facing combined-resource plan and acceptance record.
- Incident/claim workflow linked to evidence, resource plan and payment hold.

### P2 — scale after industrial workflow validation

- Expand standard cargo only after the industrial workflow has real operational evidence.
- Add express/on-demand competition only where density and verified supply make the service reliable.
- Add customer and supplier scoring based on verified completion, cancellation, incident and evidence
  records rather than self-asserted ratings.

## Release gates

A Managed Project is not production-ready unless:

- uncertain specifications produce Site Survey Required;
- no instant price is shown;
- survey-required jobs cannot be quoted before survey evidence is recorded;
- only verified and unexpired assets/operators can be reserved;
- every combined resource is reserved without schedule conflicts;
- the customer accepts the final scope, price and exclusions;
- completion evidence and acceptance are present before payment release; and
- every stage, approval and override is auditable.
