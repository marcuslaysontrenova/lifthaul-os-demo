# LiftHaul authentication continuation — 13 October 2026

## Production evidence observed in this session

- Canonical Railway project `76f8ba71-2e56-4af4-95e9-000dd57ba275`, service
  `5941df7f-bd5a-42f5-a4fa-b7cddee94527`, environment
  `2a222da2-c291-40b0-a8d6-921de07ca46a`.
- Deployment `190c904b-4350-4061-b5b6-515c124ca846`: SUCCESS; one running replica;
  database also online; no staged changes or pending rollout.
- Canonical `/healthz`, `/readyz` and `/data/psgc/manifest.json`: HTTP 200.
- `/readyz`: ready, schema 27, exact SHA
  `695478d48513ecd4bbb5efe5f61d3f2994174311`.
- Historical failed rollout remains visible in Railway; it is not the current
  live deployment. No production mutation occurred during this continuation.

## Changes prepared locally

Branch `codex/auth-email-delivery-20261013`, code commit `f12616e`:

- Authenticated TLS SMTP adapter for provider/driver codes and client recovery.
- Replaced configuration-only OTP success with real SMTP acceptance.
- Missing or unknown APP_ENV cannot reveal reset tokens or seed demo clients.
- Client recovery form accepts emailed tokens in production and allows retry
  after an invalid/expired token without destroying the inputs.
- Mail configuration guide; no provider credentials or settings provisioned.

## Validation

- 44 tests passed: auth email transport, client auth, provider and driver signup.
- 35 tests passed: client portal, intake QA lifecycle, navigation integrity and
  notification engine.
- JavaScript syntax and git whitespace checks passed.
- Offline execution of the real client JavaScript handlers passed: production
  token form, request payload, retry after rejection and success feedback.
- Browser layout and actual inbox delivery are not established by these checks.
- Full repository CI has not run for this local candidate.

## Outstanding gates and next actions

1. Publish the candidate for review and full CI under the owner's subsequent
   explicit authorization to proceed toward MVP. Local git HTTPS authentication
   is unavailable; publication uses the connected GitHub API.
2. Configure an approved email provider through protected administration and
   obtain real operator-inbox registration/recovery evidence. Canonical service
   inspection currently lists no auth-email SMTP variable names.
3. Run PR #24's protected authenticated QA probe using a dedicated read-only
   principal, then complete remaining persona and operational sign-offs in #20.

**Decision: authenticated production MVP GO remains unverified.** Public runtime
health does not establish authenticated acceptance. Live funds remain a separate
gate. This checkpoint does not alter Issue #20 or any external sign-off.
