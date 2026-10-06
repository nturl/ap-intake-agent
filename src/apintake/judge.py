"""Judgment stage — LLM judgment point #2, and the only other one.

This is called ONLY when validate.py has flagged a genuinely ambiguous
issue (fuzzy vendor name, missing PO, or price drift) and there are no hard
blocking errors. Everything checkable by arithmetic already happened in
validate.py — this stage exists because "is 'Acme Corp.' close enough to
'Acme Corporation' to trust" and "is a 12% price increase plausible or a
mistake" are judgment calls, not lookups.

confidence < 0.85 always routes to the human queue regardless of decision —
the model is allowed to be uncertain, but uncertainty is never silently
auto-approved.

Real mode uses a stronger model (claude-sonnet-5) than extraction, because
this is where the AI's opinion actually matters to the outcome.
"""

from __future__ import annotations

import json
import os

from apintake.schemas import ExtractedInvoice, JudgmentResult, RawMessage, ValidationIssue

JUDGMENT_MODEL = "claude-sonnet-5"
CONFIDENCE_THRESHOLD = 0.85

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject", "escalate"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["decision", "confidence", "rationale"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """You are the judgment step in an accounts-payable intake pipeline. Every \
deterministic check (math, PO lookup, duplicate detection) has already run — you are only \
called for issues that require real judgment:

- VENDOR_FUZZY_MATCH: the invoice's vendor name isn't a registered spelling, but a known \
  vendor's name is a close textual match. Decide whether it's plausibly the same vendor.
- MISSING_PO: no PO number was on the invoice. Decide whether this is safe to approve anyway \
  (e.g. a small recurring vendor charge) or must be escalated to a human to track down the PO.
- PRICE_DRIFT: a line-item unit price has moved more than 10% from this vendor's price history. \
  Decide whether that drift is plausible (inflation, bulk discount reversal) or suspicious.

Be conservative. If you are not confident, say so honestly in your confidence score rather than \
picking a decision and inflating your confidence — the pipeline routes anything under 0.85 \
confidence to a human regardless of your decision, so calibrated uncertainty costs nothing and \
false confidence is actively harmful."""


def judge_case(
    raw: RawMessage,
    invoice: ExtractedInvoice,
    issues: list[ValidationIssue],
    mock: bool = True,
) -> JudgmentResult:
    if mock:
        return _judge_mock(raw)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            f"[judge] ANTHROPIC_API_KEY not set — cannot run real judgment for case "
            f"{raw.id}. Use --mock, or export ANTHROPIC_API_KEY. Falling back to mock."
        )
        return _judge_mock(raw)

    return _judge_llm(invoice, issues)


def _judge_mock(raw: RawMessage) -> JudgmentResult:
    fixture = raw.fixture or {}
    judgment = fixture.get("judgment")
    if judgment is None:
        # Defensive fallback — a well-formed fixture always has this when the
        # case is designed to reach judgment. Missing data escalates rather
        # than guesses.
        return JudgmentResult(
            decision="escalate",
            confidence=0.0,
            rationale="No _fixture.judgment provided for this case; escalating rather than guessing.",
        )
    return JudgmentResult(**judgment)


def _judge_llm(invoice: ExtractedInvoice, issues: list[ValidationIssue]) -> JudgmentResult:
    import anthropic

    client = anthropic.Anthropic()

    issue_text = "\n".join(f"- {i.code}: {i.message}" for i in issues)
    prompt = (
        f"Invoice under review:\n{invoice.model_dump_json(indent=2)}\n\n"
        f"Flagged issues requiring judgment:\n{issue_text}\n\n"
        f"Return your decision, confidence (0-1), and rationale."
    )

    try:
        response = client.messages.create(
            model=JUDGMENT_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": _JSON_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        return JudgmentResult(**data)
    except Exception as e:
        return JudgmentResult(
            decision="escalate",
            confidence=0.0,
            rationale=f"Judgment call failed ({e}); escalating to a human rather than guessing.",
        )
