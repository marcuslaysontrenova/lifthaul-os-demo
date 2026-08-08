# Protected-Transaction Attack Matrix — LiftHaul Enterprise

Adversarial validation of the protected-transaction system (Workstream 3). Every dangerous case
must **fail closed**. Evidence = automated tests in `backend/test_protected_transaction_redteam.py`
(23 tests, all passing) plus the release-gate / payout / webhook / legality / ledger engines.

| # | Attack | Expected control | Actual result | Evidence (test) | Severity | Status |
|---|---|---|---|---|---|---|
| 1 | Frontend sends `paid=true` | Only signed provider webhooks change funding; no trusted client "paid" path | No client path exists; funding needs a verified provider event | `verify_webhook` design + `test_forged_webhook_signature_quarantined` | Critical | CLOSED |
| 2 | Forged provider callback | HMAC signature check | Bad signature → QUARANTINED, not accepted | `test_forged_webhook_signature_quarantined` | Critical | CLOSED |
| 3 | Incorrect HMAC | Signature mismatch rejected | Not accepted, quarantined | same | Critical | CLOSED |
| 4 | Stale webhook | Timestamp tolerance | Outside window → rejected | `test_stale_webhook_rejected` | High | CLOSED |
| 5 | Replayed webhook | `(provider,event_id)` unique + dedup | Second delivery → `duplicate_or_replay` | `test_replayed_webhook_rejected` | Critical | CLOSED |
| 6 | Duplicate funding event | Idempotency (existing `mkt_payment_idempotency`) + webhook dedup | Deduplicated | payments idempotency + #5 | High | CLOSED |
| 7 | Duplicate release event | Idempotency + release-gate single-path | Deduplicated / re-gated | payments engine (Inc4) + release gate | High | CLOSED |
| 8 | Release before protection | Release gate requires `funds_protected` | DENIED | `test_release_before_protection` | Critical | CLOSED |
| 9 | Release before trip completion | Gate requires `milestone_verified` | DENIED | `test_release_before_trip_milestone` | Critical | CLOSED |
| 10 | Release without POD | Gate requires `pod_ok` | DENIED | `test_release_without_pod` | Critical | CLOSED |
| 11 | Release with open dispute | `dispute_blocks_release` | DENIED (`blocking_dispute_open`) | `test_release_with_open_dispute` | Critical | CLOSED |
| 12 | Release with critical fraud flag | Fraud block in gate | DENIED (`critical_fraud_flag`) | `test_release_with_critical_fraud` | Critical | CLOSED |
| 13 | Release during payout cooling | Cooling blocks high-value | DENIED (`cooling_period_high_value_blocked`) | `test_release_during_payout_cooling` | High | CLOSED |
| 14 | Payout to unverified beneficiary | `payout_allowed` verification check | DENIED (`beneficiary_unverified`) | `test_release_to_unverified_beneficiary` | Critical | CLOSED |
| 15 | Maker approves own payout account | SoD maker≠checker | ForbiddenError | `test_maker_cannot_self_approve` | Critical | CLOSED |
| 16 | Payment destination changed just before release | Cooling period on new account | High-value DENIED during cooling | `test_release_during_payout_cooling` | High | CLOSED |
| 17 | Carrier suspended after funding | Fraud/eligibility re-checked at release | DENIED on fraud flag / KYB status | #12 + eligibility gate | High | CLOSED |
| 18 | Expired vehicle before dispatch | `vehicle_legality_gate` | Not eligible (`registration_expired`) | `test_expired_vehicle_blocked` | High | CLOSED |
| 19 | Expired driver qualification | `driver_assignment_gate` | Blocked (`qualification_expired`) | `test_expired_driver_qualification_blocked` | High | CLOSED |
| 20 | Manipulated / duplicate POD | POD + geofence evidence (Inc5) required by gate | Release needs `pod_ok`; duplicate POD rejected by trips engine | gate `pod_ok` + Inc5 POD | High | CLOSED |
| 21 | Cross-tenant payment identifier | `tenant.guard` on every payment row | 404 no-leak (proven across the spine) | `test_tenant_isolation.py` | Critical | CLOSED |
| 22 | Ledger imbalance | `reconcile_ledger` | `LEDGER_IMBALANCE` flagged | `test_ledger_imbalance_flagged` | Critical | CLOSED |
| 23 | Refund exceeding protected balance | Reconciliation cannot balance | Imbalance flagged | `test_refund_exceeding_protected_is_imbalance` | Critical | CLOSED |
| 24 | Double refund | Idempotency + reconciliation | Deduped / imbalance flagged | payments idempotency + #22 | High | CLOSED |
| 25 | Out-of-order provider events | Webhook ordering + idempotency + gate state | Non-authoritative until gate conditions met | webhook + release gate | Medium | CLOSED |
| 26 | Exceed carrier progressive risk limit | `within_risk_limit` in gate | DENIED (`exceeds_carrier_risk_limit`) | `test_release_exceeding_risk_limit` | High | CLOSED |
| 27 | Payout approval without MFA | MFA required | ForbiddenError | `test_payout_approval_requires_mfa` | Critical | CLOSED |
| 28 | Account change during fraud review | Blocked while carrier under critical review | ForbiddenError | `test_account_change_blocked_during_fraud_review` | Critical | CLOSED |
| 29 | Engage a LIVE payment rail while disabled | `LIVE_PROTECTED_FUNDS_ENABLED=false` hard boundary | ForbiddenError; requires all 3 prerequisites | `test_live_funds_disabled_by_default`, `test_live_funds_requires_all_three_prerequisites` | Critical | CLOSED |

## Result

- **Critical vulnerabilities open: 0.**  High open: 0.
- Live fund custody stays technically **OFF** (`LIVE_PROTECTED_FUNDS_ENABLED=false`) until an approved
  PH legal operating model **and** an active licensed provider are both configured — enforced centrally,
  not just per-adapter.
