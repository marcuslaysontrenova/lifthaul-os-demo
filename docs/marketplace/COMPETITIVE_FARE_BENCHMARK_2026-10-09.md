# LiftHaul Philippine Fare Benchmark and Rate-Control Record

Evidence review date: **9 October 2026 (Asia/Manila)**
Candidate rate version: `LH-PH-2026-10-09-BENCHMARK-01`

## Scope and evidence rule

This review uses public, official Philippine pages only. It does not use account misuse, private
APIs, automated extraction that bypasses access controls, or competitor confidential information.
Published prices are point-in-time benchmark evidence, not a permanent competitor tariff and not
permission to copy a proprietary algorithm. App prices, demand, location, promotions, tolls and
booking details can change the final price.

Official evidence:

- Lalamove Philippines delivery pricing: https://www.lalamove.com/en-ph/all-delivery-pricing-detail
- Transportify local-delivery vehicle and base-price table (updated 12 May 2026):
  https://www.transportify.com.ph/delivery/business/local-delivery-service-philippines/
- Transportify driver requirements and payout destinations (updated 15 May 2026):
  https://www.transportify.com.ph/transportify-rates/driver/requirements-private-car-delivery-jobs/
- Lalamove onboarding, deposit and payout controls:
  https://www.lalamove.com/en-ph/driver-onboarding
  https://www.lalamove.com/en-ph/driver-security-deposit
  https://www.lalamove.com/en-ph/blog/cash-out-process

## Public benchmark snapshot

| Vehicle | Lalamove public guidance | Transportify public guidance | LiftHaul control conclusion |
|---|---:|---:|---|
| Motorcycle | ₱49 base; ₱6/km through 5 km then ₱5/km; 20 kg | Not listed in the cited Transportify table | Keep a dedicated small-item class; disclose demand and route effects |
| Sedan | ₱100 base; ₱18/km through 5 km then ₱15/km; 200 kg | Base ₱220 Metro Manila, ₱190 outside, ₱140 Visayas/Mindanao; 200 kg | Treat region and distance bands as configurable inputs |
| Small SUV | ₱115 base; ₱20/km through 30 km then ₱17/km; 300 kg | Included with MPV/SUV base ₱240/₱210/₱160; 200 kg | Do not conflate SUV, MPV and passenger service; match on verified cargo capacity |
| MPV / small van | ₱200 base; ₱20/km through 30 km then ₱17/km; 600 kg | Light Van base ₱375/₱292/₱275; 600 kg | Mini Van and L300 remain separate canonical categories |
| Pickup | ₱240 base plus ₱20/km; 800 kg | Base ₱418/₱338/₱325; 1,000 kg | Validate the actual registered payload and open/canopy body |
| L300 / cargo van | ₱280 base plus ₱20/km; 1,000 kg | Base ₱415/₱374/₱335; 1,000 kg | Enclosed-body dimensions and verified unit rating govern eligibility |
| 6-wheel class | Public variants range from 3,000 kg to 7,000 kg with different bases and per-km rates | 6W forward base ₱4,850; 7,000 kg | Never price all 6-wheel bodies as one vehicle; body and payload must be explicit |
| Wing van | ₱7,200 base plus ₱85/km; published 12,000 kg category | Base ₱7,000 Metro Manila or ₱6,500 elsewhere; published 12,000–28,000 kg range | Use verified body length and GVW/payload; no generic heavy-truck substitution |

Lalamove also publishes add-stop amounts, waiting charges, rental rates and a possible high-demand
surcharge. Those items are not silently copied. LiftHaul represents each approved add-on as a
separate versioned component and shows it before confirmation.

## LiftHaul rate governance

1. `public_booking.quote` is the only public pricing authority. The Fare Calculator, booking
   preview and booking creation call the same server method.
2. Client-submitted totals are ignored. The server computes transport, the approved 10%
   administration fee and the configured tax policy.
3. Every confirmed public booking stores its rate version, effective date and itemized component
   snapshot. Later rate changes cannot rewrite historical bookings.
4. Calculator previews create no booking, payment or payout record.
5. Tolls, parking, ferry/RoRo, port, permits and other unverified third-party expenses remain
   separately disclosed and are not guessed into the protected-payment amount.
6. Vehicle suitability is evaluated before price. Weight, dimensions, volume, handling,
   refrigeration and route restrictions can block a category or require manual assessment.
7. Heavy haul, cranes, hazardous cargo and other engineered work never receive a fabricated
   instant price.
8. Only an authorized pricing administrator may publish a future approved matrix. Effective dates,
   approval evidence and old versions must remain auditable.

## Commercial approval status

The candidate matrix is a **benchmark-informed planning matrix**, not evidence that either
competitor approved LiftHaul's fares. Before public activation, LiftHaul commercial and finance
owners must approve contribution margin, provider share, payment-provider cost, tax treatment,
region/distance bands, waiting allowance, cancellation rules, peak pricing limits and minimum
charges. Production must remain on the exact tested version until that approval is recorded.

## Payout and deposit conclusion

The benchmark does not support a claim that every platform requires a deposit. Lalamove publishes
a driver security-deposit model; Transportify expressly publishes “No Initial Cash Deposit.”
LiftHaul therefore supports a conditional risk-based refundable liability, but its seeded policy is
zero-value and inactive. No deposit may be collected until legal, PSP-contract, accounting/tax,
accepted-terms, refund-SLA and risk-amount evidence passes maker/checker approval. Payouts likewise
remain evidence-based: available is not paid, beneficiary accounts must be verified, and `PAID`
requires a payment-provider reference and reconciliation.
