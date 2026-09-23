# LiftHaul audit refresh — 23 September 2026

This refresh reconciles the owner's complete hostile-product and performance mandates against the current repository and deployed topology.

- [Complete devil's-advocate checklist](DEVILS_ADVOCATE_COMPLETE_CHECKLIST.md)
- Baseline evidence remains in [`audit/2026-09-03`](../2026-09-03/README.md).

The checklist does not convert code presence into a production pass. It explicitly separates existing/tested, newly added, partial, missing, external-gated and unproven requirements.

## Payment integration answer

**Xendit is not complete for live production.** The adapter, server-side verification, idempotency/reconciliation controls and activation gates exist. Production activation is designed to fail closed until credentials, certified channels, controlled-pilot approval, reconciliation automation, regulatory role, safeguarded-funds structure, independent security test and DR restore approval are all present.

**Wise is not complete for live production.** The mock and governed adapter seam exist, but the current real adapter intentionally reports `LIVE WISE BLOCKED` until the owner provides and validates the authorized Wise Business credentials/profile and executes sandbox transfer/status/reconciliation evidence.

Neither integration should be described to users as live until those external gates are evidenced.
