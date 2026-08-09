# 19 — BSP Application Field Mapping (Owner Data)  [Directive B]

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Maps the OPS/registration form fields to LiftHaul corporate information. **No owner-supplied fact is
invented.** Every value the owner must provide is marked `⛔ MISSING — OWNER MUST SUPPLY`. Engineering
fills only the technical/operational fields it can evidence from the codebase.

## A. Corporate identity (OWNER — do not invent)

| Form field | Value | Status |
|---|---|---|
| Registered company name | ⛔ MISSING — OWNER MUST SUPPLY | |
| SEC registration number | ⛔ MISSING — OWNER MUST SUPPLY (do NOT invent) | |
| SEC registration date | ⛔ MISSING — OWNER MUST SUPPLY (do NOT invent) | |
| TIN | ⛔ MISSING — OWNER MUST SUPPLY (do NOT invent) | |
| Business permit no. + LGU | ⛔ MISSING — OWNER MUST SUPPLY | |
| Principal business address | ⛔ MISSING — OWNER MUST SUPPLY (do NOT invent) | |
| Contact person / email / phone | ⛔ MISSING — OWNER MUST SUPPLY | |
| Company website | ⛔ MISSING — OWNER MUST SUPPLY (LiftHaul OS URL if applicable) | |

## B. Ownership & officers (OWNER — do not invent)

| Form field | Value | Status |
|---|---|---|
| Directors (names, nationality, IDs) | ⛔ MISSING — OWNER MUST SUPPLY (do NOT invent officers) | |
| Corporate officers (President, Treasurer, Corp. Sec.) | ⛔ MISSING — OWNER MUST SUPPLY | |
| Beneficial owners (>X%) | ⛔ MISSING — OWNER MUST SUPPLY | |
| Authorized/paid-up capital | ⛔ MISSING — OWNER MUST SUPPLY | |

## C. Regulatory answers (OWNER/COUNSEL — do not invent)

| Form field | Value | Status |
|---|---|---|
| Type of payment system / activity | ⛔ MISSING — COUNSEL determination (doc 01) | |
| OPS category applied for | ⛔ MISSING — COUNSEL determination | |
| AML/CFT officer designation | ⛔ MISSING — OWNER MUST SUPPLY | |
| DPO / NPC registration no. | ⛔ MISSING — OWNER MUST SUPPLY | |
| Prior/related BSP registrations | ⛔ MISSING — OWNER MUST SUPPLY | |
| Sworn certifications / attestations | ⛔ MISSING — OWNER MUST SIGN | |

## D. Operating model & technical (ENGINEERING — evidenced from repo)

| Form field | Value | Source |
|---|---|---|
| Operating-model description | Provided | docs 02, 03 |
| Fund custody structure | Provider holds; LiftHaul never custodies | docs 02, 04 |
| System architecture | Provided | doc 05 |
| Transaction lifecycle / state machine | 16 states + 11 exceptions | doc 07, `protected_payment.py` |
| Ledger & reconciliation | Immutable, append-only, recon-to-zero | doc 08 |
| Fraud / risk controls | Provided | doc 09 |
| KYB/KYC controls | Provided (KYC = provider) | doc 11 |
| Security controls | Provided | doc 12 |
| Incident response | Provided (contacts = owner) | doc 13 |
| BC/DR | Provided (host specifics = owner) | doc 14 |
| Privacy/DPA | Provided (DPO/NPC = owner) | doc 15 |
| Complaint handling | Provided (SLAs = owner) | doc 16 |
| Provider due diligence | Provided (signed contract = pending) | doc 17 |

## Summary

```
OWNER MUST SUPPLY:   Corporate identity (SEC no., SEC date, TIN, address, permit),
                     ownership & officers, capital, AML officer, DPO/NPC reg,
                     financial statements, board resolution, sworn certifications.
COUNSEL MUST DECIDE: OPS applicability/category, terminology, regulatory answers.
PROVIDER PENDING:    signed provider agreement + verified BSP-regulated status.
ENGINEERING DONE:    operating model, architecture, state machine, ledger, controls (docs 02–17).
```
