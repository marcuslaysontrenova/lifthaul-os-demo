# Philippine Regulatory Readiness Register

**Status:** DRAFT v0.1 · **Date:** 2026-08-03 · **Owner:** Philippine Regulatory & Compliance Lead
(requires external PH legal counsel — see B4).

> **The legal operating model is NOT assumed.** Whether LiftHaul operates as a technology
> marketplace, freight broker, logistics service provider, carrier, managed transportation provider,
> or a governed hybrid is an **open determination pending formal Philippine legal review**. Every row
> below is an open item until an owner + evidence + regulator interpretation closes it.

## Legal operating-model determination (BLOCKING — B4)

| Option | Implication if chosen | Status |
|---|---|---|
| Technology marketplace | least liability; must avoid acting as carrier/broker of record | OPEN |
| Freight broker | broker licensing + carrier-liability contracts | OPEN |
| Logistics service provider | operational liability, insurance obligations | OPEN |
| Carrier | full carrier liability + fleet regulation | OPEN |
| Managed transportation provider | contractual custody + SLA liability | OPEN |
| Governed hybrid | per-service classification | OPEN |

**Do not launch revenue-bearing service until this is determined and documented.**

## Regulatory issue register

| # | Issue | Authority / regime | Required action | Owner | Evidence | Status | Launch impact |
|---|---|---|---|---|---|---|---|
| R-01 | Business registration + permits | SEC / DTI / LGU / BIR | confirm entity + permits for marketplace ops | Legal | — | OPEN | blocks go-live |
| R-02 | Data privacy | Data Privacy Act (NPC) | DPA registration, DPO, PIA, consent, retention | DPO | — | OPEN | blocks go-live |
| R-03 | Payment facilitation / protected funds | BSP (payment systems), AMLA | licensed partner for holding/releasing funds; **not** a plain bank/Wise account | Payments Architect | — | OPEN (B3) | blocks payments |
| R-04 | E-invoicing / tax | BIR | invoice + VAT treatment for marketplace fees + carrier payouts | Finance | — | OPEN | blocks billing |
| R-05 | Carrier/driver verification | LTO / LTFRB (as applicable) | vehicle registration, authority to operate, licences | Carrier Lead | — | OPEN | blocks onboarding |
| R-06 | Cargo insurance + claims | Insurance Commission | cargo insurance, liability, claims process | Ops | — | OPEN | blocks claims |
| R-07 | Consumer protection | DTI | terms, dispute rights for individual shippers | Legal | — | OPEN | blocks public launch |
| R-08 | Regulated / hazardous cargo | DOTr / DENR / DOH (per cargo) | permit workflow; deferred in pilot | Compliance | taxonomy flags `regulated`/`hazardous`/`prohibited` in `marketplace.py` | PARTIAL | pilot defers these |

## Payment & escrow terminology control

Per blueprint §3, the platform uses **"Protected Payment and Conditional Release"** — **never**
"escrow" — until a licensed and contractually valid escrow/safeguarding arrangement is confirmed
(R-03 / B3). Wise may be used only for supported collection, transfer, FX, account-detail, or payout
functions, and must not be the sole rail.

## Closure rule

A row is CLOSED only with: named regulator/authority interpretation + registration/licence evidence +
owner sign-off. Until then it stays OPEN or PARTIAL and constrains the corresponding go-live gate.
