# Counsel Decision Checklist — Protected Payment (PH)

Questions requiring a **legal determination**, not an engineering assumption. Engineering has built
the controls to enforce whatever counsel decides; it does not decide the law. Until these are
answered and documented, `LIVE_PROTECTED_FUNDS_ENABLED` stays false and no live funds move.

1. **Operating model** — Under PH law, is the described model (customer → licensed provider custody
   → LiftHaul orchestration → provider-executed release) permissible without LiftHaul itself being a
   licensed payment/e-money/safeguarding entity?
2. **Terminology** — May the feature be called "Escrow" / "Escrow Account", or must it remain
   "Protected Payment" under the chosen model? (Engineering default: Protected Payment.)
3. **Custody boundary** — Confirm LiftHaul must never take custody or commingle funds under this
   model; confirm the provider's safeguarding arrangement satisfies the requirement.
4. **Provider licensing** — What BSP registration/licence category must the provider hold (NPSA)?
   Confirm the selected provider's status against the BSP listing.
5. **Release authority** — Is LiftHaul's role of issuing release *instructions* (not moving funds)
   legally sound? Any documentation/consent required from the customer?
6. **Dispute + refund** — Legal requirements for dispute windows, refund rights, and consumer
   protection disclosures?
7. **Fees** — Disclosure requirements for platform/provider fees and margins.
8. **Data privacy** — DPA obligations for storing payment references, evidence, and payout data.
9. **Cross-border / FX** — If multi-currency is ever enabled, applicable rules.
10. **Fund-return / wind-down** — Legal handling of protected funds if LiftHaul or the provider exits.
11. **Records / audit** — Retention and auditability requirements the immutable ledger must satisfy.
12. **Go-live conditions** — Sign-off that turns `LEGAL_OPERATING_MODEL_APPROVED=true` (one of the
    three mandatory prerequisites for live funds).

Output: a signed determination that (a) approves or amends the operating model, (b) fixes the
customer-facing terminology, and (c) authorizes the go-live conditions. Only then does engineering
flip the three flags and run the licensed-provider sandbox certification.
