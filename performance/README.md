# LiftHaul performance test harness

These tests are release gates for an isolated synthetic-data environment. They are not safe to aim
at production and a local pass is not proof of production capacity.

## Fast local diagnostic

Start the API with a disposable database, then run:

```powershell
python performance/local_http_probe.py --profile read --stages 1,25,100,500 --output performance/evidence/read.json
python performance/local_http_probe.py --profile mixed --stages 1,25,100 --output performance/evidence/mixed.json
python performance/local_http_probe.py --profile idempotency --stages 20 --output performance/evidence/idempotency.json
```

The read profile measures liveness, readiness, catalogs, and payment-channel reads. The mixed profile
exercises the mandated activity distribution with the demo's safely available endpoints. It also
shows whether the public per-IP limiter makes legitimate shared-network traffic fail. The idempotency
profile submits the same synthetic booking concurrently and requires one returned reference.

## Full performance environment

Install k6 and use `performance/k6/lifthaul-mixed.js`. The script defaults to the required progressive
100, 500, 1,000, 2,500, 5,000, and 10,000 virtual-user stages. It refuses booking, recommendation, or
webhook mutations unless `ALLOW_SYNTHETIC_MUTATIONS=true` is set explicitly.

The full gate requires dedicated synthetic identities, tracking tokens, payment fixtures, signed
webhooks, seeded fleets/trips, and observability exports. A response-time pass is invalid unless the
post-run reconciliation proves zero duplicate/lost bookings, payments, releases, refunds, and
assignments.
