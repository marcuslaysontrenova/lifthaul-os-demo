# PHASE 7 — Integration Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed Integration Administration + Wise payments (mock/sandbox), webhooks,
reconciliation, dead-letter/replay, provider monitoring, and failure recovery.
**Method:** full inspection of `core.py` (PaymentProvider/MockWiseProvider, payment_requests,
verify_payment, confirm_job), `settings.py` (Phase-6 secret references), `wfgov.py` (notification
events), `security.py` (SecretManager), `server.py` (routes).

> Guiding rules (directive): **do not hardcode provider credentials**; **do not store raw secrets in
> ordinary tables** (Phase-6 secret-reference boundary); **a 200 HTTP response is NOT settlement** —
> verification requires reconciled provider settlement evidence; the internal payment workflow stays
> **provider-independent**; the payment amount comes from the **stored accepted-quotation downpayment
> snapshot** (Phase-2 historical reproducibility), never recomputed.

## Classification legend

`VERIFIED ACTIVE` real provider comms proven · `IMPLEMENTED BUT UNVALIDATED` · `PARTIALLY IMPLEMENTED` ·
`CONFIGURATION ONLY` · `MOCK ONLY` · `PLANNED` · `UNSUPPORTED` · `DEPRECATED` · `NOT APPLICABLE`.

---

## A. Payments (Wise — highest priority)

| Item | Provider | Code location | Auth | Secret ref | Webhook | Idempotency | Reconciliation | Status | Class |
|---|---|---|---|---|---|---|---|---|---|
| PaymentProvider interface | — | `core.py:310` | — | — | — | — | — | present | PARTIALLY IMPLEMENTED |
| MockWiseProvider | wise_mock | `core.py:320` | none | — | no | no | no | create_payment_link + get_status=UNKNOWN | MOCK ONLY |
| payment_requests domain | — | `core.py:124,598` | — | — | no | booking-unique | manual verify only | derives amount from stored dp snapshot | IMPLEMENTED (provider-independent) |
| verify_payment | — | `core.py:657` | finance perm + tenant guard + idempotent | — | — | — | manual `amount_received` | VERIFIED for manual flow | IMPLEMENTED |
| confirm_job gate | — | `core.py:684` | requires VERIFIED payment | — | — | idempotent (job_id) | — | control gate present | VERIFIED |
| **Wise real API** | wise | — | API token | Phase-6 secret ref | TBD | required | required | **no real comms** | **BLOCKED (no credentials)** |

**Finding:** the provider abstraction + payment-request domain + verification gate already exist and
are provider-independent. Phase 7 adds a governed integration layer (definitions/profiles/idempotency/
webhooks/reconciliation/dead-letter/health/circuit-breaker) and a real `WiseProvider` adapter alongside
a **deterministic `MockWiseAdapter`** proving every non-secret capability. Live Wise stays **BLOCKED**
until owner-controlled credentials validate (§27).

## B. Other external integration candidates

| Domain | Provider | Code location | Status | Class | Phase-7 action |
|---|---|---|---|---|---|
| Secret manager | env / store | `security.py:110` | env-backed MVP | IMPLEMENTED | reused via Phase-6 secret references |
| Email | — | `wfgov.notification_events` (queued, never auto-sent) | queue only | CONFIGURATION ONLY | governed provider boundary (mock/sandbox) |
| SMS | — | `wfgov.notification_events` | queue only | CONFIGURATION ONLY | governed provider boundary (mock) |
| Maps / geocoding | — | absent | none | PLANNED | provider-neutral boundary + secret ref |
| Accounting export | — | absent | none | PLANNED | provider-neutral boundary (chart/customer/invoice mapping) |
| Exchange rate (FX) | — | Phase-6 deferred | none | PLANNED | governed FX-rate boundary; distinct from Wise quote rate |
| File storage | local | `forms.upload_file` (Phase 5) | local refs | IMPLEMENTED | not external in Phase 7 |

## C. Webhook / retry / idempotency surfaces (current)

| Concern | Present today? | Notes |
|---|---|---|
| Webhook routes | NO | none → **new** governed webhook ingress (signature verify / dedup / replay-safe) |
| Idempotency | partial (booking-unique payment request; verify idempotent) | **new** first-class idempotency keys for external ops |
| Retry / backoff | NO | **new** failure classification + backoff + circuit breaker |
| Dead-letter | NO | **new** governed DLQ + governed replay |
| Reconciliation | manual only | **new** reconciliation engine (match/partial/over/under/duplicate/mismatch→manual review) |
| Provider health | NO | **new** health + circuit breaker + kill switch |

## Summary counts

- **Provider-independent payment domain (reused):** payment_requests, verify_payment (SoD/tenant),
  confirm_job gate, PaymentProvider interface, Phase-2 dp snapshot.
- **Wise:** MOCK ONLY today → deterministic MockWiseAdapter (all scenarios) + real WiseProvider adapter
  ready; **live BLOCKED** pending owner credentials.
- **NEW governance objects:** integration definitions, connection profiles, idempotency keys, webhook
  endpoints + events, polling jobs, reconciliation items, dead-letter queue, replay, provider health,
  circuit breaker, FX/email/sms/maps/accounting boundaries.

## Financial & control safety commitments

1. Payment amount always from the **stored accepted-quotation downpayment snapshot** — never recomputed
   from current config → **UNEXPECTED FINANCIAL DIFFERENCES = 0**.
2. A provider `CREATED`/`200` is **never** treated as verified; verification requires reconciled
   settlement evidence + authorized verifier + separation of duties → **UNEXPECTED PAYMENT-STATUS
   CHANGES = 0** from integration wiring alone.
3. Mock settlement **cannot** activate real jobs in production (mock/prod boundary) → **UNEXPECTED
   JOB-STATUS CHANGES = 0**.
4. Idempotency: a repeated key with the same payload returns the original result; a repeated key with a
   different payload is rejected — no duplicate transfers.
5. Secrets are Phase-6 references only; raw tokens/keys are never stored, logged, audited, or exported.
6. Live Wise production readiness is reported **separately** from mock validation and is not claimed
   until a real provider test succeeds.
