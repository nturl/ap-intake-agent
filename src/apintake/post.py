"""Mock ERP — the system of record an approved invoice gets posted into.

Deterministic, no LLM involvement. A real integration would call
NetSuite/SAP/QuickBooks; this appends to a JSON ledger so the whole demo
runs without any external system.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from apintake.schemas import ExtractedInvoice

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ledger.json"


def post_invoice(
    invoice: ExtractedInvoice, path: Path = DEFAULT_LEDGER_PATH, vendor_name: str | None = None
) -> dict:
    """Append the invoice to the ledger (the "post to accounting system" step).

    `vendor_name` lets the caller pass the canonical/resolved vendor name
    (e.g. after a fuzzy-match judgment) instead of whatever string appeared
    on the raw invoice.
    """
    ledger = json.loads(path.read_text()) if path.exists() else []
    entry = {
        "invoice_number": invoice.invoice_number,
        "vendor_name": vendor_name or invoice.vendor_name,
        "total": invoice.total,
        "currency": invoice.currency,
        "po_number": invoice.po_number,
        "posted_ts": datetime.now(UTC).isoformat(),
    }
    ledger.append(entry)
    path.write_text(json.dumps(ledger, indent=2) + "\n")
    return entry
