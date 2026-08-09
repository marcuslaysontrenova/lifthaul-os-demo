# 03 — End-to-End Fund-Flow Diagram

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

**Funds never touch LiftHaul.** LiftHaul carries instructions + evidence; the licensed provider
carries money.

```
   CUSTOMER (shipper)                         LICENSED BSP-REGULATED PROVIDER
        |                                          (safeguarded / segregated funds)
        |  (1) pay contract amount  ───────────────────►  [ funds held ]
        |                                                       ▲   |
        |                                                       |   |
        v                                                       |   | (6) settle to carrier
   LiftHaul OS  ──(2) create tx / instruct──────────────────────┘   |    on release instruction
   (orchestration, no custody)                                      v
        |   • state machine (protected_payment.py)          CARRIER (payout account)
        |   • release gates: fraud / dispute /
        |     driver+vehicle legality / payout
        |     security / txn limits
        |   • immutable ledger + reconciliation
        |
        └──(3) release INSTRUCTION only (never moves funds) ──► provider executes (6)

   Money path:      CUSTOMER ──► PROVIDER (held) ──► CARRIER
   Instruction path: LiftHaul ──► PROVIDER (create / hold / release / refund / reconcile)
   Evidence path:   POD, milestones, KYB, LTFRB authority ──► LiftHaul (references stored)
```

## Legend of control points

| Point | Control | Enforced by |
|---|---|---|
| (1)→hold | Funds safeguarded by provider | Provider (BSP-regulated) |
| (2) | Transaction created only for a valid assignment | `protected_payment.create_transaction` |
| (3) | Release requires all gates green + maker/checker | `release_gate` composed at `RELEASE_*` |
| (6) | Provider settles only on a valid instruction; ledger must reconcile to 0 before `SETTLED` | `reconcile` |

Multi-currency / FX and any pooling are **out of scope** until counsel rules on them (doc 01 §3).
