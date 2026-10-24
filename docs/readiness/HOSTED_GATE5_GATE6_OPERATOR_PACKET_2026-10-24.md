# Gate 5/6 hosted operator execution packet — 2026-10-24

Canonical trackers: [#20](https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/20), [#36](https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/36), [#35](https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/35). Candidate evidence commit: `bb26f20bb155960f079048a355ea6958d147162d` (nonproduction candidate, **NOT a verified hosted SHA**). All commands below must be run by a separately authorized operator. This is a plan, **not** sign-off.

## 1. Read-only release identity — no Railway OAuth required
Use a trusted operator machine with outbound HTTPS:
```bash
curl --fail --silent --show-error --max-time 15 https://www.lifthaul.com.ph/readyz | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ("status","release_sha","deployment_id","schema_version","version")},sort_keys=True))'
```
Operator privately cross-checks release SHA and deployment ID against Railway > project > production > application service > Deployments and current health. Record environment name/id from Railway metadata. Record UTC and Manila timestamps. If SHA differs from formally approved SHA, record `BLOCKED_RELEASE_IDENTITY_MISMATCH`; do not run hosted acceptance. Avoid publishing internal IDs if classified sensitive.

## 2. Gate 5: one human-only protected-identity operation
Owner: production security/GitHub environment administrator. Prepare **eleven independent approved synthetic accounts and scoped marker fixtures** using a separately approved, governed process; driver applicant remains pending. Do not run provisioning scripts as a hosted UAT prerequisite or modify Railway variables. In GitHub repo > Settings > Environments > production > Environment secrets, set **one** protected secret named `LH_PROD_PERSONA_CREDENTIALS_JSON`; structure (PLACEHOLDERS ONLY, NEVER FILL IN A PR):
```json
{
  "booker": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "driver_applicant": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "accredited_driver": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "motorcycle_owner": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "light_vehicle_owner": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "truck_owner": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "fleet_owner": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "specialized_provider": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "operations": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "finance": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"},
  "administrator": {"email":"<private>","password":"<private>","marker":"<synthetic-private>"}
}
```
All 11 emails, passwords, and markers must be unique. Passwords must be valid for their accounts. Maintain mandatory protected-environment reviewers. Confirm secret presence and scope **without reading its value**. On exact approved deployed SHA only, navigate GitHub > Actions > LiftHaul Gate 5 production persona UAT > Run workflow; input the 40-character deployed SHA. Select an authorized workflow branch that contains the read-only implementation; verify checkout script is anchored to deployed SHA and that no production provisioning steps execute. If the approved deployed commit lacks the hardened validation script, do not run; raise `BLOCKED_RELEASE_IDENTITY_MISMATCH` for separately approved release. Redacted artifact must report 11/11, 10 revoked sessions, pending denial, RBAC/tenant denials, funds/dispatch off and nonleakage. Independent QA and security sign-off required. No credential values in chat, issues, logs, or screenshots.

## 3. Gate 6: one separately authorized witnessed isolated restoration
Owner: infrastructure/database operator and an independent security/operations witness. Confirm isolation by recording distinct production vs recovery project ID, environment ID, DB service ID, network endpoints, and credential boundaries; recovery cannot receive production traffic. If unavailable record `G6_BLOCKED_ENVIRONMENT_ISOLATION`. Obtain approved current recoverable backup metadata (backup ID, method, encryption, retention, backup timestamp, checksum/integrity fingerprint), without exposing production records. Restore **only into an authorized isolated and empty disposable target** under a controlled window. Record start time and ready time in UTC; calculate RTO = recovery-ready - restore-start. Record recovery-point timestamp and backup data timestamp; calculate RPO = recovery-point - backup data timestamp; compare with approved targets. Reconcile schema, required indexes, permitted synthetic sentinel integrity, app readiness and auth with isolated credentials. Ensure payment, dispatch, callbacks, email, webhooks remain disabled.

Test app rollback in isolated environment only to a known-good compatible SHA and back; capture deployments, health, schema compatibility, smoke/authorization checks and redacted monitoring outcomes. Use a non-disruptive monitor/incident drill and record trigger, delivery, acknowledgment and resolution timestamps, owner and witness. Mark `hosted_recovery_verified=false` until actual witnessed run completes; do not use synthetic CI as proof.

## 4. Sign-off record
Store redacted run URLs/artifact IDs and privately approved witness attestations against **the exact release SHA**. Update #36 and #35 only after all checks pass and independent owners sign; then update #20 Gate 7 entry. Do not mark MVP GO until all mandatory MVP criteria pass. Gate 5 and Gate 6 status: OPEN pending hosted proof.

## 5. Supplemental direct-object acceptance (PR #77)
The same protected persona manifest additionally needs an approved `object_path`
on four existing rows: booker `/portal/client/bookings/<id>`, accredited_driver
`/driver/trips/<id>`, motorcycle_owner and truck_owner
`/admin/carrier-portal/overview/<id>` (distinct carrier IDs). Use only existing
authorized synthetic objects whose response contains the owner's unique marker.
Do not publish IDs or create fixtures through UAT. The supplementary runner first
proves authorized owner access, then verifies direct-object cross-role denial and
same-role carrier isolation. Carrier HTTP 200 is acceptable only if foreign IDs
are ignored and only the requesting carrier's marker is returned. Protected API
URLs are tested directly; public HTML shells are not authorization evidence.

The workflow runs `supplemental_persona_uat.py` and uploads
`production-persona-security.json`. It performs only GET and authentication
login/logout calls, verifies its own six session revocations and pins release and
deployment before/after. These six sessions are separate from the primary ten.
No credentials, markers, paths, object IDs or response bodies enter its artifact.

The existing finance status endpoint proves only its protected-funds master
switch. It currently lacks authoritative booleans for live dispatch, collection,
refunds, payouts and settlement. Their absence produces
`BLOCKED_GATE5_SAFETY_OBSERVABILITY`, never a presumed OFF result. Do not inject
constant booleans or treat absence of test mutations as proof of runtime policy.
Actual runtime-enforced safety status or separately witnessed exact-release
evidence remains required. This supplement alone is not full browser landing-page
UAT, administrator tenant-scope certification, independent review or gate closure.
