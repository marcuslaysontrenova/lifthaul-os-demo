# 13 — Incident Response

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

## Scope

Payment-affecting incidents: provider outage, reconciliation break, suspected fraud, data exposure,
unauthorized access, webhook tampering.

## Roles

| Role | Responsibility |
|---|---|
| Owner / operator | Incident commander; external notifications |
| Finance | Payment/reconciliation containment; freeze transactions |
| Provider | Fund-side containment; regulated reporting |
| Counsel | Regulatory + legal notification obligations |

## Procedure

1. **Detect** — reconciliation exception, fraud flag, monitoring alert, provider notice.
2. **Contain** — `FROZEN` / `RECONCILIATION_HOLD` / `LEGAL_HOLD` on affected transactions; no releases.
3. **Assess** — scope, funds at risk, data affected; correlation-id trace in audit ledger.
4. **Notify** — provider, affected customers/carriers, and regulator/DPA **as counsel directs**.
5. **Remediate** — fix, reconcile, resume only after `difference == 0`.
6. **Review** — post-incident record appended to audit trail.

## Controls that support IR

- Immutable ledger + correlation ids (full traceability).
- State machine holds (`FROZEN`, `RECONCILIATION_HOLD`, `LEGAL_HOLD`) stop money movement instantly.
- Idempotent webhooks prevent replay-driven double effects.

## To be completed with the owner

- 24/7 contact tree, provider escalation SLA, regulator notification templates + timelines (counsel).
