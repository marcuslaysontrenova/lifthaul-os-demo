# 01 — OPS Applicability Assessment

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

**This is a preparation document. It states the questions and the platform's design position; it does
NOT assert a legal conclusion.** The classification is for BSP and qualified PH counsel to determine.

## 1. Governing framework

- **R.A. 11127 — National Payment Systems Act (NPSA)** and its IRR.
- **BSP Operator of Payment System (OPS) registration** regime.
- Related: BSP rules on Electronic Money Issuers (EMI), Payment System Operators, and the
  Anti-Money-Laundering Act (AMLA) obligations that attach to regulated payment activity.

## 2. What LiftHaul does / does not do (design intent)

| Activity | LiftHaul | Licensed provider |
|---|---|---|
| Holds / custodies customer funds | **No** (design intent) | Yes |
| Commingles funds with operating cash | **No** | No (safeguarded/segregated) |
| Moves / settles funds | **No** — issues *instructions* only | Yes — executes |
| Decides release conditions (business logic) | Yes | Executes instruction |
| Stores payment references + evidence | Yes | Yes |
| Onboards/KYC's the payer | Shares data | Performs regulated KYC |

## 3. The classification question (for BSP + counsel)

1. Under the NPSA, does LiftHaul's role — orchestrating a payment held and settled by a **separate
   BSP-regulated provider** — constitute "operating a payment system" requiring OPS registration, or
   is it a technology/platform layer outside the registrable perimeter?
2. If OPS registration is required, which category and what are the capital, governance, reporting,
   and oversight obligations?
3. Does any part of the model (e.g., pooling, timing of release, holding references) risk being
   construed as e-money issuance or deposit-taking? If so, what redesign avoids it?
4. What AMLA/KYC obligations attach to LiftHaul vs. the provider?

## 4. Platform position pending determination

- LiftHaul is built so that **it never needs custody** — the licensed provider is the custodian.
- The live-funds path is **hard-disabled** (`payments.live_protected_funds_enabled=false`) and cannot
  be enabled without three documented approvals (see README §"Three mandatory prerequisites").
- If BSP determines OPS registration is required for LiftHaul itself, the application package (docs
  02–19) supplies the supporting material; the owner supplies corporate facts (doc 19).

## 5. Decision record (to be completed)

| Item | Determination | By | Date | Reference |
|---|---|---|---|---|
| OPS registration required for LiftHaul? | ☐ TBD | | | |
| Category / obligations | ☐ TBD | | | |
| Provider licence category (NPSA/EMI) | ☐ TBD | | | |
| Terminology ("Protected Payment" vs "Escrow") | ☐ TBD | | | |
| Go-live conditions signed | ☐ TBD | | | |

Until every row is completed and signed, `BSP OPS STATUS` remains
**REGULATORY CLASSIFICATION / APPLICATION PREPARATION**.
