# ROI Memo — AP Invoice Intake Agent

**Scenario:** SMB processing 120 vendor invoices/week (6,240/year) through accounts payable.
All inputs below are labeled **[A]** (assumption — replace with the client's real numbers
during an audit) or **[M]** (measured — pulled directly from this system's eval run,
`evals/eval_report.md`). Nothing here is presented as a guarantee; it's the shape of the
math a real engagement would fill in with the client's actual invoice volume, average
value, and staff cost.

## Straight-through rate (measured)

Running the full 56-case golden dataset through the pipeline in mock mode gives the
outcome mix that grounds every estimate below **[M]**:

| Outcome | Share | Weekly volume @ 120/wk |
|---|---|---|
| `auto_posted` (fully automated) | 38/56 = 67.9% | 81.5 |
| `queued_for_review` (human, pre-extracted + reasoned) | 15/56 = 26.8% | 32.1 |
| `rejected` (classified as non-invoice, e.g. spam/newsletter) | 3/56 = 5.4% | 6.4 |

## 1. Cost savings — labor hours

| Input | Value | Source |
|---|---|---|
| Fully manual processing time/invoice (entry + 3-way match + filing, blended across clean and exception cases) | 10 min | **[A]** — mid-range of commonly cited AP-benchmark figures (~5–15 min); confirm against the client's actual timesheets |
| Post-agent time for an auto-posted invoice (periodic QA sampling, not per-invoice review) | 1 min | **[A]** |
| Post-agent time for a queued invoice (human reviews pre-extracted data + system-stated reasons, per `apintake review`) | 4 min | **[A]** — down from 10 min because the extraction, math check, and PO/vendor lookup are already done |
| Post-agent time for a rejected (filtered) message | 0.5 min | **[A]** |
| Fully-loaded AP staff cost | $35/hr | **[A]** |

**Baseline:** 120/wk × 10 min = 20 hrs/wk → 1,040 hrs/yr
**With agent:** (81.5×1 + 32.1×4 + 6.4×0.5) / 60 = 3.6 hrs/wk → **186 hrs/yr**
**Hours saved: ≈854 hrs/yr → ≈$29,900/yr** at $35/hr.

## 2. Risk mitigation — duplicate / overpayment catches

The `duplicate_invoice` category in the golden dataset is 2/56 = 3.6% of volume **[M]** —
invoices whose (vendor, invoice number) pair exactly matches something already posted. This
is a floor, not a ceiling: it's scoped to exact vendor+number matches by design (see
`docs/decision-log.md` — cross-vendor number collisions are a known, documented blind spot).

| Input | Value | Source |
|---|---|---|
| Avg invoice value | $1,500 | **[A]** |
| Baseline duplicate-payment error rate in manual AP (industry-cited range ~0.1–1%) | 0.3% | **[A]** — conservative end of the range |
| Invoices/yr | 6,240 | computed |

**Duplicate payments previously slipping through:** 6,240 × 0.3% ≈ 19/yr
**Avoided overpayment: ≈19 × $1,500 ≈ $28,500/yr**

This is the category the deterministic `DUPLICATE_INVOICE` check is built to catch every
time, with zero LLM involvement — no judgment call, just a lookup. It's also the cheapest
kind of value to defend in front of a CFO: it isn't a productivity estimate, it's "we would
have paid this bill twice and didn't."

## 3. Revenue/cost uplift — early-payment discount capture

| Input | Value | Source |
|---|---|---|
| Share of invoices with an early-payment term (e.g. 2/10 net 30) | 15% | **[A]** |
| Discount rate | 2% | **[A]** — standard 2/10 net 30 |
| Baseline capture rate (manual queue routinely exceeds the 10-day window) | ~0% | **[A]** |
| Post-agent capture rate (68% of volume is same-day auto-posted; most of the rest clears within days via the review queue) | 50% | **[A]** — conservative; not claiming universal capture |

**Discount-eligible volume:** 6,240 × 15% = 936 invoices/yr × $1,500 avg = $1,404,000/yr eligible
**Discount pool:** $1,404,000 × 2% = $28,080/yr potential
**Newly captured (50% − 0%):** **≈$14,040/yr**

## Total estimated annual value

| Bucket | Annual estimate |
|---|---|
| Cost savings (labor) | ≈$29,900 |
| Risk mitigation (duplicate/overpayment) | ≈$28,500 |
| Revenue/cost uplift (early-payment discounts) | ≈$14,040 |
| **Total** | **≈$72,440/yr** |

## What would change this number in a real engagement

- The 67.9%/26.8%/5.4% split is specific to *this* synthetic dataset's issue rates. A real
  client's vendor cleanliness, PO discipline, and invoice volume will shift it — this is
  exactly what the audit stage (per `PLAN.md`) is for: sit with the actual AP team, pull a
  sample of real historical invoices, and re-run the eval harness against reality before
  quoting a number to a VP.
- Every **[A]** row above is a placeholder for a number the client can usually produce from
  their own AP system (average invoice value, current processing time, vendor payment
  terms) — replacing them is a half-day exercise, not a re-architecture.
- This memo intentionally does not include the cost of building/maintaining the system,
  API spend, or the cost of the review-queue staffing itself — those belong on the other
  side of the ledger in a real proposal.
