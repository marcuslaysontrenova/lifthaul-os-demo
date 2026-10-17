# LiftHaul production recovery and rollback runbook

This runbook closes repository-side preparation for MVP Gate 6. It does **not**
claim that Railway backup/PITR, an isolated restore, alert routing, or a hosted
rollback has occurred. Those controls require retained provider evidence.

## Required production ownership

Configure these GitHub `production` environment variables with accountable,
monitored GitHub users or team aliases (not placeholders). The incident owner
must be an assignable GitHub username so the monitor can route a failed probe:

- `LH_PROD_OPERATIONS_OWNER`
- `LH_PROD_INCIDENT_OWNER`
- `LH_PROD_RECOVERY_OWNER`

Configure these protected secrets only when the isolated recovery target exists:

- `LH_PROD_DATABASE_URL` — production PostgreSQL, used read-only by the drill
- `LH_RECOVERY_DATABASE_URL` — a different, temporary restored database

The monitoring and recovery workflows fail closed while ownership or credentials
are missing. Secret values must never be committed or uploaded as evidence.

## Managed backup/PITR acceptance

1. In Railway, capture the PostgreSQL backup/PITR policy, retention period and a
   non-secret provider backup reference.
2. Restore the chosen point into a new isolated database. Never rehearse a
   destructive restore against production.
3. Record the recovery point, synthetic incident time, restore start and finish in
   timezone-qualified ISO-8601 format.
4. Run **LiftHaul Managed PostgreSQL Recovery Drill** with those values and tick
   `pitr_enabled` only after confirming it in Railway. The job:
   - refuses an identical source and target database;
   - opens the source transaction read-only;
   - reconciles critical table counts and schema stamp;
   - calculates RTO and RPO;
   - retains a 90-day evidence artifact without credentials or row data.
5. Attach the workflow URL and Railway policy screenshot/export to the Gate 6
   release record, then delete the temporary restore database after evidence is
   independently reviewed.

## Controlled application rollback rehearsal

The release manager must authorize the maintenance window and identify both the
previous successful deployment and the current approved deployment.

1. Record the current `/readyz` SHA, deployment ID and schema version.
2. In Railway, redeploy the immediately previous successful deployment. Do not
   change database state.
3. Run `scripts/go_live/rollback_probe.py` with `EVIDENCE_PHASE=rollback`, the
   expected previous SHA/deployment ID and a unique evidence path.
4. Exercise a read-only smoke journey and confirm that additive schema migrations
   remain compatible.
5. Redeploy the approved current SHA.
6. Run the probe with `EVIDENCE_PHASE=forward_restore`, the current SHA/deployment
   ID and a second evidence path.
7. Attach both JSON files, Railway deployment links, start/end timestamps, owner,
   decision and any incident notes. A rollback is not proven by documentation or
   a recorded target alone.

Example (values intentionally non-secret):

```bash
BASE_URL=https://www.lifthaul.com.ph \
EXPECTED_SHA=<40-character-sha> \
EXPECTED_DEPLOYMENT_ID=<railway-deployment-id> \
EVIDENCE_PHASE=rollback \
EVIDENCE_PATH=evidence/rollback.json \
python scripts/go_live/rollback_probe.py
```

## Monitoring and alert acceptance

The scheduled production health workflow checks the public surface, health,
readiness, schema, release identity and PSGC manifest. Gate 6 additionally requires:

- the named operations and incident owners above;
- a successful scheduled run;
- one controlled alert rehearsal (for example, a temporary invalid canary URL);
- evidence that the alert reached the monitored owner and was acknowledged;
- a linked incident/communications route and escalation response time.

GitHub Actions failure alone is not evidence that a human receives or owns alerts.

## Gate 6 closeout record

Record all of the following before changing Gate 6 to `COMPLETE`:

- managed backup/PITR provider reference and retention;
- isolated restore workflow URL and reconciliation artifact;
- measured RTO and RPO;
- rollback and forward-restore SHA/deployment evidence;
- successful scheduled monitor and controlled-alert run URLs;
- named operations, incident and recovery owners;
- reviewer, date and residual risks.

If any field is absent, Gate 6 remains `OPEN`.
