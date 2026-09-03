# Competitor benchmark

## Method

This is a capability benchmark, not a claim that every service is available in every Philippine city, vehicle class, or account tier. Official provider pages were checked on 3 September 2026. “LiftHaul status” evaluates the deployed product, not intended backend architecture.

Key sources:

- Transportify documents immediate/scheduled multi-drop bookings, up to 15 drop-offs, vehicle-specific charges, web/API booking, sandbox, status, ETA, cancellation, special requirements, and production onboarding: [multiple drops](https://www.transportify.com.ph/blog/multiple-drops/), [API for tech teams](https://www.transportify.com.ph/api-for-tech-teams/).
- Lalamove documents immediate/scheduled orders, up to 19 stops, POD, quotations, cancellation, webhooks, route/price enhancements, business tiers, corporate controls, and a fleet portal: [API solutions](https://www.lalamove.com/en-ph/business/api-solutions), [business services](https://www.lalamove.com/en-ph/business), [fleet management](https://www.lalamove.com/en-ph/fleet-management).
- Grab documents fixed upfront fares, live GPS, photo POD, stated delivery cover, business expense controls, employee linking, reports, and e-receipts: [GrabExpress](https://www.grab.com/ph/express/), [Grab for Business](https://forbusiness.grab.com/ph/services/express), [GrabExpress Web](https://www.grab.com/ph/community/optimizing-your-business-operations-with-grabexpress-web/).
- inDrive documents courier and freight as global verticals with peer-to-peer pricing, but this audit found no current official proof that inDrive Freight is operating in the Philippines: [company overview](https://indrive.com/company), [Philippines/global announcement](https://blog.indrive.com/fil-ph/article/ang-indrive-ay-pangalawa-sa-pinakamadalas-na-download-na-ride-hailing-app-sa-mundo-para-sa-ikatlong-magkakasunod-na-taon).

## Matrix

| Capability | Established-market evidence | LiftHaul status | Classification |
|---|---|---|---|
| Time to first booking | Competitors accept real orders through apps/web/API. | Deployed booking cannot submit. | **Missing** |
| Immediate and scheduled booking | Transportify/Lalamove document both. | UI exists; live completion does not. | **Inferior** |
| Multi-stop | Transportify up to 15 drop-offs; Lalamove API up to 19 stops. | Add-stop UI exists; no live end-to-end result. | **Inferior** |
| Vehicle range | Lalamove documents vehicles to 12,000 kg; Transportify publishes classes into large trucks. Grab focuses on smaller parcel delivery. | UI/catalog includes lowbed, cranes, specialized/heavy equipment. | **Potential differentiator** |
| Cargo-first safety matching | Competitors publish vehicle payload/dimension constraints. | Deterministic weight/dimension/volume/handling matcher exists, but final submit bypasses it when weight is absent/invalid. | **Regulatory concern** |
| Oversized/engineered review | General delivery competitors focus on standard vehicle menus. | Engineered work avoids fabricated instant prices and can route to assessment. | **Potential differentiator** |
| Upfront price | Grab fixed upfront; Lalamove instant quote; Transportify get-quote API. | Canonical quote engine exists; deployed transaction and client ledger are unreliable. | **Inferior** |
| Transparent fee/tax split | Competitors show fees/rates under their models. | Three-line formula is implemented in backend, but demo arithmetic contradicts it. | **Regulatory concern** |
| Live tracking and ETA | All three established benchmarks advertise real-time status/tracking. | Map is planning UI; no production GPS feed. | **Missing** |
| POD | Grab photo POD; Lalamove and Transportify API/POD features. | Evidence model/tests exist; no live POD journey. | **Missing** |
| Notifications/webhooks | Lalamove and Transportify document webhooks/status; consumer apps notify users. | Providers disabled; no live notification chain. | **Missing** |
| In-app communication/privacy | Established apps provide driver/order coordination. | No deployed masked communication channel. | **Missing** |
| Fleet management | Lalamove allows fleet owners to register/pair drivers and vehicles; competitors provide business dashboards. | Backend models exist; no hosted fleet portal workflow. | **Inferior** |
| Corporate/team controls | Grab links employees and policies; Lalamove provides tiers and corporate controls. | RBAC architecture exists; no live corporate workspace. | **Inferior** |
| API/business integration | Lalamove and Transportify provide production APIs and sandboxes. | API code exists only in repository/local mode. | **Missing** |
| Reports and expense records | Grab provides reports/e-receipts; business competitors provide statements. | No live downloadable invoice/reconciliation evidence. | **Missing** |
| Support | Established services provide help channels and account support. | Public support contacts/SLA absent. | **Missing** |
| Cancellation/refund/dispute | Competitors publish cancellation/support mechanisms. | Defensive models/tests exist; no live end-to-end user resolution. | **Inferior** |
| Goods/cargo protection | Grab states cover up to ₱20,000; Lalamove advertises tier-dependent protection. | Evidence-gated protected settlement is conceptually broader, but no licensed/live structure. | **Potential differentiator / regulatory concern** |
| Mobile/offline behavior | Competitors operate mature mobile apps. | Responsive web DOM only; no offline driver queue or native app proof. | **Missing** |
| Philippine heavy-haul focus | Main benchmarks emphasize on-demand delivery. | Nationwide trucks, cranes, lowbeds, and engineered jobs are central. | **Potential differentiator** |
| Negotiated/peer pricing | inDrive's global model emphasizes user-provider price agreement. | LiftHaul uses governed quotation/fee logic. | **Not directly comparable** |

## Competitive conclusion

LiftHaul is currently **inferior or missing** on every capability that requires live execution: booking, identity, supply, tracking, communication, support, payment, settlement, invoices, integrations, and mobile operations. Its defensible direction is not another small-parcel app; it is a verified, cargo-first, heavy-haul marketplace with fleet operations and evidence-gated settlement. That differentiation remains hypothetical until the final-submit safety bypass, money ledger, integrations, and operating model are closed.

Do not copy competitor interfaces or language. Adopt the demonstrated product principles: short booking time, explicit capacity, stable quote, live status, POD, team controls, integration, downloadable records, and reachable support—then apply them to heavy-haul constraints.

