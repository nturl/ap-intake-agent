"""Orchestrates one case through: ingest -> extract -> validate -> judge (only
if needed) -> auto-post or queue for human review.

This is the "where AI belongs" decision made concrete in code:

  ingest    - deterministic (file I/O)
  extract   - LLM (reading unstructured text), but its output is verified
              deterministically (strict schema, retry-with-feedback, then
              route to human on repeated failure)
  validate  - 100% deterministic (arithmetic, lookups, duplicate detection)
  judge     - LLM, and ONLY invoked when validate.py found a genuinely
              ambiguous issue with no hard blocking errors present
  post      - deterministic (mock ERP write)
  approve   - human (interactive review queue), for anything the pipeline
              couldn't confidently resolve itself

See docs/decision-log.md for the reasoning behind the exact auto-post
conditions below.
"""

from __future__ import annotations

from pathlib import Path

from apintake import approve, audit, extract, ingest, judge, post, validate
from apintake.schemas import CaseOutcome, ExtractedInvoice, JudgmentResult, ValidationIssue

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class PipelinePaths:
    """Bundles the data-file paths a pipeline run reads/writes.

    Exists so the eval harness can point the whole pipeline at isolated
    scratch copies (see evals/run_eval.py) without touching the real
    data/ledger.json, data/review_queue.json, or data/audit_trail.jsonl that
    `apintake run` uses.
    """

    def __init__(
        self,
        vendors: Path = DATA_DIR / "vendors.json",
        pos: Path = DATA_DIR / "pos.json",
        ledger: Path = DATA_DIR / "ledger.json",
        queue: Path = DATA_DIR / "review_queue.json",
        audit_trail: Path = DATA_DIR / "audit_trail.jsonl",
    ):
        self.vendors = vendors
        self.pos = pos
        self.ledger = ledger
        self.queue = queue
        self.audit_trail = audit_trail


DEFAULT_PATHS = PipelinePaths()


def process_case(case_path: Path, mock: bool = True, paths: PipelinePaths = DEFAULT_PATHS) -> CaseOutcome:
    raw = ingest.load_case(case_path)
    case_id = raw.id

    audit.log(
        case_id,
        "ingest",
        "deterministic",
        "case_loaded",
        {"from": raw.from_, "subject": raw.subject, "attachments": len(raw.attachments)},
        path=paths.audit_trail,
    )

    extraction = extract.extract_invoice(raw, mock=mock)
    audit.log(
        case_id,
        "extract",
        "llm",
        f"extraction_{extraction.status}",
        {"attempts": extraction.attempts, "reason": extraction.reason},
        path=paths.audit_trail,
    )

    if extraction.status == "not_invoice":
        outcome = CaseOutcome(
            case_id=case_id,
            outcome="rejected",
            reasons=[f"Classified as non-invoice correspondence: {extraction.reason}"],
        )
        audit.log(
            case_id, "pipeline", "deterministic", "outcome_rejected",
            outcome.model_dump(), path=paths.audit_trail,
        )
        return outcome

    if extraction.status == "failed":
        outcome = CaseOutcome(
            case_id=case_id,
            outcome="queued_for_review",
            reasons=[f"Extraction failed after retry: {extraction.reason}"],
        )
        approve.add_to_queue(case_id, outcome.reasons, invoice=None, path=paths.queue)
        audit.log(
            case_id, "pipeline", "deterministic", "outcome_queued_extraction_failure",
            outcome.model_dump(), path=paths.audit_trail,
        )
        return outcome

    invoice = extraction.invoice
    assert invoice is not None  # extraction.status == "ok" guarantees this

    vendors = validate.load_json(paths.vendors)
    pos = validate.load_json(paths.pos)
    ledger = validate.load_json(paths.ledger) if paths.ledger.exists() else []

    issues = validate.validate_invoice(invoice, vendors=vendors, pos=pos, ledger=ledger)
    audit.log(
        case_id,
        "validate",
        "deterministic",
        "validation_complete",
        {"issues": [i.model_dump() for i in issues]},
        path=paths.audit_trail,
    )

    blocking = [i for i in issues if i.code not in validate.JUDGEABLE_CODES]
    judgeable = [i for i in issues if i.code in validate.JUDGEABLE_CODES]

    vendor_match, _ = validate.find_vendor(invoice.vendor_name, vendors)
    resolved_vendor_name = vendor_match["canonical_name"] if vendor_match else invoice.vendor_name

    if blocking:
        reasons = [i.message for i in blocking]
        outcome = CaseOutcome(case_id=case_id, outcome="queued_for_review", reasons=reasons)
        approve.add_to_queue(case_id, reasons, invoice, vendor_name=resolved_vendor_name, path=paths.queue)
        audit.log(
            case_id, "pipeline", "deterministic", "outcome_queued_blocking_errors",
            outcome.model_dump(), path=paths.audit_trail,
        )
        return outcome

    if judgeable:
        judgment = judge.judge_case(raw, invoice, judgeable, mock=mock)
        audit.log(
            case_id, "judge", "llm", "judgment_rendered", judgment.model_dump(), path=paths.audit_trail,
        )
        return _resolve_judged_case(case_id, invoice, judgeable, judgment, resolved_vendor_name, paths)

    # Zero issues at all, no judgment needed: the fully clean path.
    posted = post.post_invoice(invoice, path=paths.ledger, vendor_name=resolved_vendor_name)
    outcome = CaseOutcome(case_id=case_id, outcome="auto_posted", reasons=["No validation issues; auto-posted."])
    audit.log(
        case_id, "post", "deterministic", "auto_posted", {"ledger_entry": posted}, path=paths.audit_trail,
    )
    audit.log(case_id, "pipeline", "deterministic", "outcome_auto_posted", outcome.model_dump(), path=paths.audit_trail)
    return outcome


def _resolve_judged_case(
    case_id: str,
    invoice: ExtractedInvoice,
    judgeable: list[ValidationIssue],
    judgment: JudgmentResult,
    resolved_vendor_name: str,
    paths: PipelinePaths,
) -> CaseOutcome:
    issue_reasons = [i.message for i in judgeable]

    if judgment.decision == "approve" and judgment.confidence >= judge.CONFIDENCE_THRESHOLD:
        posted = post.post_invoice(invoice, path=paths.ledger, vendor_name=resolved_vendor_name)
        outcome = CaseOutcome(
            case_id=case_id,
            outcome="auto_posted",
            reasons=issue_reasons + [f"LLM judgment approved (confidence={judgment.confidence:.2f}): {judgment.rationale}"],
        )
        audit.log(case_id, "post", "deterministic", "auto_posted_after_judgment", {"ledger_entry": posted}, path=paths.audit_trail)
        audit.log(case_id, "pipeline", "deterministic", "outcome_auto_posted", outcome.model_dump(), path=paths.audit_trail)
        return outcome

    reasons = issue_reasons + [
        f"LLM judgment={judgment.decision} confidence={judgment.confidence:.2f}: {judgment.rationale}"
    ]
    outcome = CaseOutcome(case_id=case_id, outcome="queued_for_review", reasons=reasons)
    approve.add_to_queue(case_id, reasons, invoice, vendor_name=resolved_vendor_name, path=paths.queue)
    audit.log(case_id, "pipeline", "deterministic", "outcome_queued_low_confidence_or_no_approve", outcome.model_dump(), path=paths.audit_trail)
    return outcome
