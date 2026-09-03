# Prioritized product-improvement backlog

Items are ordered by release risk, not visual preference. Dates are proposed control dates from the defect register and require accountable-owner acceptance.

## P0 — release blockers

1. **Make cargo weight and complete route mandatory at the backend boundary.** Remove `LEGACY_CAPACITY_ONLY` from public/new bookings; reject unparseable numbers; require the canonical address/map-confirmation contract. Add regression tests for omitted, zero, negative, text, unit confusion, exact capacity, +1 kg, dimension-only oversize, and conflicting location hierarchy.
2. **Use one canonical money calculator and ledger projection.** Delete independent client-demo arithmetic. Enforce `transport + 10% fee + applicable tax` and keep processing/pass-through expenses out. Add an invariant test that every screen/export reconciles.
3. **Make booking idempotency atomic.** Add a database unique constraint scoped correctly, transaction/upsert behavior, request-hash conflict detection, and contract-equivalent replay. Stress with repeated concurrent runs on the production database engine.
4. **Enforce payment state/action invariants.** A protected payment cannot be Payment Required or show Pay Now; a disputed/held transaction cannot expose release/confirmation actions that bypass the hold.
5. **Deploy a production-equivalent API only after security review.** Separate demo and production configuration; HTTPS, secrets, CORS/CSP, observability, rate limiting, backups, migrations, and rollback must be tested before connecting public pages.
6. **Complete the regulated payment operating model.** Select/approve provider, finalize legal classification/contracts, implement signed webhooks + status verification, settlement/reconciliation, dual control, refund/payout failure handling, and finance audit.
7. **Complete real verification and document handling.** Official-source SOP, secure object storage, content/signature validation, malware quarantine, authorized download, retention/deletion, expiry, and reviewer workflow.
8. **Publish approved policies and support operations.** Counsel-approved terms/privacy/payment disclosures; DPO; phone/email/ticket intake; payment/safety escalation; complaint/refund/dispute SLAs and on-call ownership.

## P1 — required for controlled pilot

1. Live GPS and durable trip status with offline driver outbox, deduplication, timestamp/source, stale-location indication, and consent.
2. Pickup/POD evidence with secure upload, recipient/OTP/signature policy, dispute hold, audit trail, and retry.
3. Driver-specific registration, login recovery, dispatch, navigation, incident, breakdown, reassignment, and support journeys.
4. Provider/fleet portal: bulk vehicles/drivers, pairing, availability, maintenance, expiry, assignment collision control, earnings/payout reconciliation, and exports.
5. Client invoices/receipts, amendments, cancellation/refund preview, downloadable transaction history, and retained filters.
6. Real notification centre backed by email/SMS/push/in-app providers with delivery state, preferences, retries, and audit.
7. Production monitoring: SLOs, traces, structured logs, fraud/payment alerts, queues/dead letters, reconciliation dashboard, incident playbooks.
8. Independent penetration test; remediate Critical/High results and retest.
9. Load/soak/chaos/recovery campaign on production-equivalent infrastructure and data volumes.
10. Moderated end-to-end pilot with real bookers, independent owners, fleets, drivers, heavy-equipment operators, operations, finance, compliance, and support.

## P2 — competitive readiness

1. Contextual handling questions instead of showing every specialist option.
2. Quote validity, capacity reason, price-change explanation, no-supply alternatives, and manual-assessment SLA.
3. Corporate roles, budgets, approval policies, cost centres, statements, APIs/webhooks, and reconciliation exports.
4. Fleet performance, document-expiry planning, vehicle utilization, driver safety/quality, and auditable rating appeals.
5. Multi-stop route optimization, inter-island planning, serviceability confidence, and route-source transparency.
6. Consistent “Book a Service” terminology and removal of internal/demo setup text from public pages.
7. Native/mobile strategy only after the offline web and driver operating model is proven.

## Definition of done for every item

- Business invariant and threat model documented.
- Role/tenant permissions enforced server-side.
- Happy, abnormal, interrupted, concurrent, duplicate, and malicious cases tested.
- Loading, empty, error, retry, offline, and recovery states designed.
- Logs, audit events, metrics, alerts, and support runbook exist.
- Evidence includes actual result, not only screenshots or “tests passed.”
- Critical/High findings are independently retested before closure.

