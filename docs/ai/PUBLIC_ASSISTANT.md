# LiftHaul public assistant pilot

Implemented on landing, booking, tracking and provider pages, preserving their design.
POST `/public/assistant/message` supports shipment preparation, canonical vehicle
suggestions, canonical indicative quotes, token-scoped customer status, registration
and payment/support guidance. An explicit booking-page handoff transfers cargo fields
using a single-use session draft that expires after 15 minutes. Complete geography
must still be selected in the existing canonical booking controls. No assistant
request creates a booking, sends a message to staff or changes authoritative records.

## Modes and activation

Default: `guided` (no model calls or API credentials needed). Free text routes common
English/Filipino intents; structured shipment inputs work independently of a model.
It does not pretend to understand arbitrary language without an AI connection.

Optional AI extracts intent and explicitly stated shipment fields using the OpenAI
Responses API with a strict JSON schema. Answers and quotations always come from
LiftHaul code and its existing customer-safe services. Provider output cannot add
functions, override amounts, widen vehicle eligibility or write records.

Configure server-side, after pilot review:

- `LIFTHAUL_ASSISTANT_LIVE=1` (kill switch: unset or set `0`)
- `OPENAI_API_KEY` (secret; never put in frontend config or Git)
- `LIFTHAUL_ASSISTANT_MODEL` (owner-selected structured-output-compatible model)
- `LIFTHAUL_ASSISTANT_CALL_CAP` (default 100 attempts **per process lifetime**,
  hard upper limit 1,000; not a durable monetary budget)

Two concurrent model requests maximum, eight-second network timeout, 600 output
tokens maximum, no provider retries. Attempts count toward the cap even on failure.
Missing credentials, budget exhaustion, invalid outputs and provider failures fall
back to guided mode. Buttons and structured inputs do not consume model calls.
External interpretation happens before database locking/checkout so AI latency
does not block normal booking/payment requests. Existing public request caps and
per-IP rate limits apply. Assistant traffic uses its own rate-limit bucket so it
cannot consume the booking/registration submission allowance. Set
`LIFTHAUL_TRUST_PROXY=1` only behind the controlled production reverse proxy so
per-client limits use its validated `X-Forwarded-For` value. UI timeout preserves
the current in-memory draft.

No production secret was configured and no paid live-provider test was performed.
Do not enable this public pilot at scale without durable cross-instance usage/cost
controls, provider-approved data handling and abuse protection. Session drafts contain
shipment fields only. Free text is sent to the configured model only in live mode;
common email, phone, token and credential patterns are redacted. This is best-effort
redaction, not a guarantee against arbitrary personal information. UI tells users to
keep credentials and contact details out of chat. `store=false` is sent to the API;
provider retention policies still apply.

## Deliberate boundaries

This is the first website assistant increment, not full Sophy parity. WhatsApp,
email, voice, unattended CRM follow-ups, persistent conversation analytics and staff
handoff queues are not implemented. Support opens the existing support page and
explicitly says the conversation has not been sent. No promise of payment verification,
refund approval, protected-fund release, accreditation, availability or dispatch.
Existing commercial readiness decisions remain separate from this feature.

## Verification

Run from `backend`:

`python -m unittest test_public_assistant test_public_booking test_phase9_ai test_static_frontend test_navigation_integrity`

Run frontend syntax checks from repository root:

`node --check lifthaul-assistant.js`

`node --check assistant-booking-prefill.js`

Assistant tests cover Filipino intent routing, canonical quote/vehicle equivalence,
missing fields, finite numeric limits, tracking isolation, forbidden actions,
no booking writes, redaction, provider failure/refusal/invalid JSON, call caps,
strict-schema request configuration, HTTP validation and provider calls outside
the database lock. Model transport is mocked. The public UI release gate covers
desktop/mobile assistant layout, keyboard open/close behavior and booking-draft
transfer. Paid inference still requires separately approved deployment-stage
verification. Official API format:
https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses
