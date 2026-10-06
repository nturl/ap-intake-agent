# AP Intake Agent — Eval Report

Mode: mock (fixture-driven, hermetic)
Cases evaluated: 56
**Overall pass rate: 52/56 (92.9%)**

## Per-category breakdown

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| happy_path | 25 | 25 | 100% |
| malformed_missing_fields | 3 | 3 | 100% |
| math_mismatch | 3 | 3 | 100% |
| unknown_vendor | 2 | 2 | 100% |
| vendor_fuzzy_match | 3 | 3 | 100% |
| missing_po | 2 | 2 | 100% |
| po_over_budget | 2 | 2 | 100% |
| duplicate_invoice | 2 | 2 | 100% |
| price_drift | 3 | 3 | 100% |
| forwarded_thread_noise | 2 | 2 | 100% |
| attachment_only | 2 | 2 | 100% |
| non_invoice | 3 | 3 | 100% |
| boundary_threshold_cases | 0 | 4 | 0% |

## Investigation — 4 failing case(s)

### case-053 (boundary_threshold_cases)
- **Mismatch**: outcome: expected 'auto_posted', got 'queued_for_review'
- **Root cause**: BOUNDARY CASE: naive expectation is 'Cascade Supplies' should fuzzy-match 'Cascade Office Supply' and judge-approve; actual normalized similarity (0.703) falls just under FUZZY_MATCH_CUTOFF=0.72, so find_vendor() returns no match at all and it hits UNKNOWN_VENDOR (blocking error) instead of ever reaching judge. See eval_report.md investigation.

### case-054 (boundary_threshold_cases)
- **Mismatch**: outcome: expected 'queued_for_review', got 'auto_posted'
- **Root cause**: BOUNDARY CASE: unit price moved exactly 10.0% (22.00 -> 24.20); naive golden label assumed a round-number 10% jump would still get a second look, but validate.py's PRICE_DRIFT check is strictly '> 0.10', so exactly 10.0% does not trigger it and the case auto-posts with zero issues. See eval_report.md investigation.

### case-055 (boundary_threshold_cases)
- **Mismatch**: outcome: expected 'queued_for_review', got 'auto_posted'
- **Root cause**: BOUNDARY CASE: naive golden label assumed confidence must be STRICTLY greater than 0.85 to auto-approve; judge.py's documented threshold is confidence >= 0.85, so this case actually auto-posts. On review this is a golden-label authoring error, not a system bug — see eval_report.md investigation.

### case-056 (boundary_threshold_cases)
- **Mismatch**: outcome: expected 'queued_for_review', got 'auto_posted'
- **Root cause**: BOUNDARY CASE: invoice number 'INV-0500' coincidentally matches a ledger entry already posted for a DIFFERENT vendor (Acme Corp). Naive golden label assumed any invoice-number collision should be flagged; actual duplicate check is scoped to (vendor_name, invoice_number) pairs, per the DUPLICATE_INVOICE check in validate.py, so a different vendor with the same number does not trigger it and the case auto-posts. This is a real, documented scope limitation (see decision-log.md) rather than a bug — cross-vendor number collisions are common in practice and vendor-scoping is the correct default, but it's worth knowing the check won't catch this class.

## How to read this report

Every failing case above is a genuine, traced root cause — not noise. Four of them (the `boundary_threshold_cases` category) are *deliberately* included: each one sits exactly on a documented threshold (fuzzy-match cutoff, price-drift percentage, judgment confidence, duplicate-detection scope) where a naive human expectation differs from the system's actual, documented behavior. See docs/decision-log.md for the full reasoning on each threshold. A production rollout would review these four specifically: two are threshold-tuning candidates, one is a golden-label authoring bug (not a system bug), and one is a scoping decision worth a product conversation.
