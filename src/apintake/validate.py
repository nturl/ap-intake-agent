"""Validation stage — 100% deterministic. No LLM calls here, ever.

Everything checkable with arithmetic and lookups happens here, not in an LLM
call: PO existence/status, line-item math, duplicate detection, budget
checks, and price-drift detection. The only things validate.py *flags* but
does not *decide* are the genuinely ambiguous ones (fuzzy vendor match,
missing PO, price drift) — those get a "warning" severity and get routed to
judge.py. Everything else ("error" severity) is a hard stop that goes
straight to the human review queue.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from apintake.schemas import ExtractedInvoice, ValidationIssue

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_VENDORS_PATH = DATA_DIR / "vendors.json"
DEFAULT_POS_PATH = DATA_DIR / "pos.json"
DEFAULT_LEDGER_PATH = DATA_DIR / "ledger.json"

PRICE_DRIFT_THRESHOLD = 0.10
FUZZY_MATCH_CUTOFF = 0.72
MONEY_EPSILON = 0.01

# Issue codes that represent genuine ambiguity — resolvable by LLM judgment,
# not a hard stop. Every other code is a deterministic blocking error.
JUDGEABLE_CODES = {"VENDOR_FUZZY_MATCH", "MISSING_PO", "PRICE_DRIFT"}


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def find_vendor(name: str, vendors: list[dict]) -> tuple[dict | None, bool]:
    """Return (vendor_record, is_exact_match). vendor_record is None if
    nothing matches even fuzzily."""
    normalized = _normalize(name)

    for v in vendors:
        for variant in v["name_variants"]:
            if _normalize(variant) == normalized:
                return v, True

    all_variants: list[tuple[str, dict]] = [
        (variant, v) for v in vendors for variant in v["name_variants"]
    ]
    normalized_variants = [_normalize(variant) for variant, _ in all_variants]
    matches = difflib.get_close_matches(
        normalized, normalized_variants, n=1, cutoff=FUZZY_MATCH_CUTOFF
    )
    if matches:
        idx = normalized_variants.index(matches[0])
        return all_variants[idx][1], False

    return None, False


def validate_invoice(
    invoice: ExtractedInvoice,
    vendors: list[dict] | None = None,
    pos: list[dict] | None = None,
    ledger: list[dict] | None = None,
) -> list[ValidationIssue]:
    """Run every deterministic check against one extracted invoice."""
    if vendors is None:
        vendors = load_json(DEFAULT_VENDORS_PATH)
    if pos is None:
        pos = load_json(DEFAULT_POS_PATH)
    if ledger is None:
        ledger = load_json(DEFAULT_LEDGER_PATH)

    issues: list[ValidationIssue] = []

    vendor_match, is_exact = find_vendor(invoice.vendor_name, vendors)
    if vendor_match is None:
        issues.append(
            ValidationIssue(
                code="UNKNOWN_VENDOR",
                severity="error",
                message=(
                    f"Vendor '{invoice.vendor_name}' has no exact or fuzzy match in the "
                    f"vendor master."
                ),
            )
        )
    elif not is_exact:
        issues.append(
            ValidationIssue(
                code="VENDOR_FUZZY_MATCH",
                severity="warning",
                message=(
                    f"Vendor name '{invoice.vendor_name}' is not a registered variant of "
                    f"'{vendor_match['canonical_name']}' but fuzzy-matches it."
                ),
            )
        )

    if invoice.line_items:
        line_sum = round(sum(li.total for li in invoice.line_items), 2)
        if abs(line_sum - invoice.subtotal) > MONEY_EPSILON:
            issues.append(
                ValidationIssue(
                    code="MATH_MISMATCH",
                    severity="error",
                    message=(
                        f"Line items sum to {line_sum:.2f} but subtotal states "
                        f"{invoice.subtotal:.2f}."
                    ),
                )
            )
    expected_total = round(invoice.subtotal + invoice.tax, 2)
    if abs(expected_total - invoice.total) > MONEY_EPSILON:
        issues.append(
            ValidationIssue(
                code="MATH_MISMATCH",
                severity="error",
                message=(
                    f"Subtotal ({invoice.subtotal:.2f}) + tax ({invoice.tax:.2f}) = "
                    f"{expected_total:.2f}, but total states {invoice.total:.2f}."
                ),
            )
        )

    canonical_vendor_name = vendor_match["canonical_name"] if vendor_match else invoice.vendor_name
    for entry in ledger:
        if (
            entry["invoice_number"] == invoice.invoice_number
            and entry["vendor_name"] == canonical_vendor_name
        ):
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_INVOICE",
                    severity="error",
                    message=(
                        f"Invoice {invoice.invoice_number} for vendor {canonical_vendor_name} "
                        f"was already posted on {entry.get('posted_ts', 'an earlier date')}."
                    ),
                )
            )
            break

    if invoice.po_number is None:
        issues.append(
            ValidationIssue(
                code="MISSING_PO",
                severity="warning",
                message="No PO number on the invoice — judgment needed on whether to infer one.",
            )
        )
    else:
        po = next((p for p in pos if p["po_number"] == invoice.po_number), None)
        if po is None:
            issues.append(
                ValidationIssue(
                    code="PO_NOT_FOUND",
                    severity="error",
                    message=f"PO {invoice.po_number} does not exist in PO records.",
                )
            )
        else:
            if po["status"] != "open":
                issues.append(
                    ValidationIssue(
                        code="PO_NOT_OPEN",
                        severity="error",
                        message=f"PO {invoice.po_number} has status '{po['status']}', not open.",
                    )
                )
            elif invoice.total > po["remaining_balance"] + MONEY_EPSILON:
                issues.append(
                    ValidationIssue(
                        code="PO_OVER_BUDGET",
                        severity="error",
                        message=(
                            f"Invoice total {invoice.total:.2f} exceeds PO {invoice.po_number} "
                            f"remaining balance {po['remaining_balance']:.2f}."
                        ),
                    )
                )

    if vendor_match:
        price_history = vendor_match.get("price_history", {})
        for li in invoice.line_items:
            historical = price_history.get(li.description)
            if historical and historical > 0:
                drift = abs(li.unit_price - historical) / historical
                if drift > PRICE_DRIFT_THRESHOLD:
                    issues.append(
                        ValidationIssue(
                            code="PRICE_DRIFT",
                            severity="warning",
                            message=(
                                f"Unit price for '{li.description}' is {li.unit_price:.2f} vs. "
                                f"historical {historical:.2f} ({drift * 100:.0f}% drift)."
                            ),
                        )
                    )

    return issues
