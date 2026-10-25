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

The finance endpoint retains only its protected-funds contract. The updated
supplement additionally reads `GET /admin/security/operational-safety` using the
administrator persona and proves finance/external persona denial. The administrator
must have a security-authorized administrative role (the existing manifest's
`super_admin` option supports this; `user_administrator` alone is insufficient).

The endpoint reads the same process interlock enforced before Xendit/Wise network
calls, non-MOCK protected-payment adapter selection, assignment creation, trip
activation/progression and legacy dispatch. It reports all eight capabilities,
release/deployment identity and an observation timestamp, without credentials.
`LH_LIVE_OPERATIONS=off` explicitly locks live operations. `on` removes this
interlock only; existing provider/legal/certification/authorization gates remain.
Missing/invalid policy is UNKNOWN and denies live operations. Local synthetic
execution cannot certify OFF. No endpoint writes the policy.

**Separately approved production action required:** after independent review and
exact-SHA release authorization, the authorized infrastructure operator sets the
non-secret application variable `LH_LIVE_OPERATIONS` to literal `off` in the
verified production project/environment/service before deploying the reviewed
candidate. Record owner approval, window, target and rollback; never set `on` for
this gate. This packet does not authorize a configuration change. Missing policy
must remain BLOCKED rather than auto-provisioned by UAT. Recovery also requires
this policy, disabled external callbacks and its independently approved isolation.

The supplement requires eight literal false values, a PASS observation and the
same SHA/deployment as `/readyz`. UNKNOWN is BLOCKED; enabled or invalid values
FAIL. No constant boolean injection or absence-of-mutations inference is permitted.
No schema migration is introduced. Rollback to an older release removes this new
interlock: the operator must independently verify that release's existing financial
and dispatch controls remain OFF; variable presence alone cannot certify rollback.
Full browser landing-page UAT, administrator tenant-scope certification and
independent sign-offs remain mandatory. This supplement cannot close either gate.


## Operator-local certification integration (2026-10-25, development-only)

These steps update the existing packet. They are not authorization to run hosted tests or restoration.

1. Confirm independently reviewed and explicitly approved deployed SHA and UAT change reference. Run `python scripts/go_live/interactive_persona_uat.py` in safe mode first. Authorized operator executes with `--execute --expected-sha <approved-deployed-sha> --approval-reference <approved-uat-reference>` only from a private terminal. Primary and supplementary authorization checks use private identity/object prompts and write a sanitized combined artifact.
2. Browser landing/navigation, administrator tenant scope, protected audit/QA identities, auth-email delivery or valid approved limitation, and human sign-offs are deliberately **BLOCKED** in that artifact until independently witnessed. HTTP status alone cannot establish browser acceptance.
3. The recovery reconciliation helper remains read-only and **cannot restore**. It now requires `RECOVERY_ISOLATION_APPROVAL_REF` and `RECOVERY_TIMESTAMP_PROVENANCE` (provider-exported, tool-observed or operator-supplied) in addition to its existing inputs. Distinct database URLs are not proof of infrastructure isolation. Counts and supplied timestamps cannot certify a witnessed restore, real RPO/RTO, integrity or rollback.
4. After approved hosted evidence and independently verifiable sign-offs, create a sanitized evidence index and run `python scripts/go_live/validate_gate_closure.py --evidence <sanitized-index.json> --release-sha <approved-deployed-sha>`. This index verifier conservatively leaves human-controlled criteria **BLOCKED** until independently checked against their real authority source. It cannot authorize a deployment or close either gate.
5. Do not upload credentials, fixture markers/paths, customer records, connection strings, session tokens, or raw browser traces. Retain verified provider/reviewer/incident links; stop only the failed or missing stage and preserve earlier valid evidence. Issues #36 and #35 remain OPEN until accountable verification confirms all requirements.


### 2026-10-25 engineering supplement — safety-contract CI and structural reconciliation

- `python -m unittest scripts.go_live.test_validate_gate_closure scripts.go_live.test_validate_browser_evidence scripts.go_live.test_managed_recovery_provenance scripts.go_live.test_recovery_schema_catalog -v` runs the offline fail-closed checks. GitHub workflow `gate5-gate6-evidence-contract.yml` exercises the same suite on changed source.
- `validate_browser_evidence.py` only evaluates a sanitized witness record; **it does not launch a browser or authenticate a persona**, and editable evidence references cannot substitute for verification of an actual Playwright run or witnessed browser session. Until such an authenticated browser runner/evidence is provided, browser criteria remain BLOCKED.
- `managed_postgres_recovery.py` now performs a read-only structural catalog digest comparison of columns/indexes/constraints as well as counts. This is a compatibility signal, not proof of historical data equality or row-level referential integrity. If the historical recovery point differs from current source, the operator must use an approved matching baseline rather than comparing the changing live database. Never run against an unapproved restore target.
- Closure validator is conservative: external human reviews and attestations remain BLOCKED unless individually verified by an accountable operator against the original source. A text field or named approver does not establish verification.
