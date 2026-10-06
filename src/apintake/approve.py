"""Human-in-the-loop review queue.

Cases the pipeline can't confidently resolve on its own land here — never
silently. `apintake review` walks the queue interactively; every decision
(approve or reject) is a human actor event in the audit trail, and an
approval still goes through the same post.py step a fully-automatic case
would.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from apintake import audit, post
from apintake.schemas import ExtractedInvoice

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_QUEUE_PATH = DATA_DIR / "review_queue.json"


def load_queue(path: Path = DEFAULT_QUEUE_PATH) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_queue(queue: list[dict], path: Path = DEFAULT_QUEUE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, indent=2) + "\n")


def add_to_queue(
    case_id: str,
    reasons: list[str],
    invoice: ExtractedInvoice | None,
    vendor_name: str | None = None,
    path: Path = DEFAULT_QUEUE_PATH,
) -> None:
    queue = load_queue(path)
    queue = [q for q in queue if q["case_id"] != case_id]  # replace if re-queued
    queue.append(
        {
            "case_id": case_id,
            "reasons": reasons,
            "vendor_name": vendor_name,
            "invoice": invoice.model_dump() if invoice else None,
        }
    )
    save_queue(queue, path)


def remove_from_queue(case_id: str, path: Path = DEFAULT_QUEUE_PATH) -> None:
    queue = load_queue(path)
    queue = [q for q in queue if q["case_id"] != case_id]
    save_queue(queue, path)


def run_review(
    queue_path: Path = DEFAULT_QUEUE_PATH,
    ledger_path: Path = post.DEFAULT_LEDGER_PATH,
    audit_path: Path = audit.DEFAULT_AUDIT_PATH,
    input_func: Callable[[str], str] = input,
) -> None:
    """Interactive CLI loop over the review queue."""
    queue = load_queue(queue_path)
    if not queue:
        print("Review queue is empty. Nothing to do.")
        return

    print(f"{len(queue)} case(s) queued for review.\n")

    for entry in list(queue):
        case_id = entry["case_id"]
        print("=" * 70)
        print(f"Case: {case_id}")
        if entry.get("vendor_name"):
            print(f"Vendor (resolved): {entry['vendor_name']}")
        print("Reasons queued for review:")
        for r in entry["reasons"]:
            print(f"  - {r}")
        if entry.get("invoice"):
            inv = entry["invoice"]
            print(
                f"Invoice: #{inv.get('invoice_number')}  "
                f"vendor={inv.get('vendor_name')}  "
                f"total={inv.get('total')} {inv.get('currency', 'USD')}  "
                f"po={inv.get('po_number')}"
            )
        print()

        decision = input_func("[a]pprove / [r]eject / [s]kip? ").strip().lower()

        if decision in ("a", "approve"):
            invoice = ExtractedInvoice(**entry["invoice"]) if entry.get("invoice") else None
            if invoice is None:
                print("  No extracted invoice on this case — cannot approve-and-post. Skipping.")
                continue
            posted = post.post_invoice(invoice, path=ledger_path, vendor_name=entry.get("vendor_name"))
            audit.log(
                case_id,
                "approve",
                "human",
                "human_approved_and_posted",
                {"ledger_entry": posted},
                path=audit_path,
            )
            remove_from_queue(case_id, queue_path)
            print(f"  Approved and posted invoice {invoice.invoice_number}.\n")

        elif decision in ("r", "reject"):
            audit.log(
                case_id,
                "approve",
                "human",
                "human_rejected",
                {"reasons": entry["reasons"]},
                path=audit_path,
            )
            remove_from_queue(case_id, queue_path)
            print(f"  Rejected case {case_id}.\n")

        else:
            print(f"  Skipped case {case_id} (left in queue).\n")
