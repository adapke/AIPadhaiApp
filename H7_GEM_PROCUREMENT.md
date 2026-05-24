# H7 — GeM listing + government procurement readiness

The Government e-Marketplace (GeM, gem.gov.in) is the **only legal
procurement channel** for Indian government purchases above ₹2L.
Indian govt education spend is ~₹4L Cr/year; ~10% going digital
each year. Without GeM listing, even a willing IAS officer can't
buy our product.

## What v2.0 ships (code)

- `padhai/procurement.py` — price-list publishing endpoint + per-SKU
  spec-sheet generator. GeM pulls these from our public URLs during
  the on-boarding review.
- `GOVT_PROCUREMENT_SPEC_SHEET.md` — the spec sheet template each
  SKU needs (filled by sales-eng during onboarding)

## What v2.0 doesn't ship (paperwork — 60-90 day calendar)

This is mostly an admin + legal task, not engineering:

1. **MSME / Udyam registration** — required for GeM seller account.
   Free, online, takes 24 hours. Apply at udyamregistration.gov.in.
2. **GST registration** — required if revenue >₹40L/yr.
3. **GeM seller account** — apply at gem.gov.in/seller. Free; needs
   bank details + GST + Udyam. Review takes 1-2 weeks.
4. **OEM certification** — for software products, declare yourself
   as the OEM. Standard form.
5. **Product catalog upload** — each SKU goes through GeM's
   classification system. Education software falls under category
   "Educational Technology Services (8543.7099.99)".
6. **Bid response capability** — set up alerts for tenders matching
   our category. Most tenders give 7-14 days to respond.

## Recommended SKU lineup for GeM

| SKU | Description | Tier | Price (₹/user/year) |
|---|---|---|---|
| `padhai-school-basic` | School ERP for K-12 | School | 600 |
| `padhai-school-premium` | + photoreal avatar (M3) + analytics | School | 1200 |
| `padhai-coaching-jee` | JEE Main/Adv prep + mock tests | Coaching | 3600 |
| `padhai-coaching-neet` | NEET prep + mock tests | Coaching | 3600 |
| `padhai-coaching-upsc` | UPSC prep + current affairs | Coaching | 4800 |
| `padhai-govt-state` | State govt deployment, white-label | Enterprise | Custom (RFP) |

Govt tenders typically procure for entire districts (~50k students)
or states (~50L students). Pricing must work at those volumes —
expect 60-80% discounts off the public price during bid response.

## Reserved-category considerations

Many GeM tenders are restricted to MSMEs from particular categories:
- Startups (DPIIT-recognized) — apply via startupindia.gov.in
- Women-owned enterprises — gives bid preference
- Make in India compliance — must be Indian-incorporated, with
  >50% local value addition (we trivially qualify)

## Pricing transparency

Each SKU must have its price + terms public + non-discriminatory.
We expose them at `/api/procurement/skus` (this module). The page
must NOT vary pricing by visitor.

## Compliance gotchas

- **Localization**: Govt buyers need SaaS hosted in India (DPDP §16
  + most state govt RFPs). H4 data residency flag handles this.
- **Source-code escrow**: some Public Sector RFPs require source-
  code escrow with a 3rd party (NSDL, etc.) so if we shut down, the
  govt can keep running it. Stallion / TouchEnX-style escrow ~₹50k/yr.
- **Audit trail**: govt buyers can audit *anything* we logged about
  their students. H3 audit log + 7-year retention covers this.
- **DPIIT recognition**: gives access to govt's startup-only
  tenders (high-volume, lower margin but compounding revenue).
