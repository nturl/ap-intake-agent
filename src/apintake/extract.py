"""Extraction stage — LLM judgment point #1.

Turns a messy raw message into a strict ExtractedInvoice. This is a
judgment point (reading unstructured, inconsistently-formatted text) but the
*validation* of what comes back is 100% deterministic: strict Pydantic
schema, one retry with the validation error fed back, then route to a human.

Mock mode (APINTAKE_MOCK=1 or --mock): reads ground-truth extraction from the
case's "_fixture" key instead of calling the API. This is what the eval
harness runs against, so it's hermetic and free — but it exercises the exact
same retry/failure/not-invoice code paths a real model would hit, using
fixture data written to simulate them (see data/inbox/*.json).

Real mode: cheap/fast model (claude-haiku-4-5) — extraction is pattern
matching against a schema, not judgment, so it doesn't need a frontier model.
"""

from __future__ import annotations

import json
import os

from pydantic import ValidationError

from apintake.schemas import ExtractedInvoice, ExtractionResult, RawMessage

EXTRACTION_MODEL = "claude-haiku-4-5"

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "is_invoice": {
            "type": "boolean",
            "description": "False if this message is not an invoice (spam, newsletter, unrelated correspondence).",
        },
        "not_invoice_reason": {"type": "string"},
        "vendor_name": {"type": "string"},
        "invoice_number": {"type": "string"},
        "invoice_date": {"type": "string"},
        "po_number": {"type": ["string", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "qty": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "total": {"type": "number"},
                },
                "required": ["description", "qty", "unit_price", "total"],
                "additionalProperties": False,
            },
        },
        "subtotal": {"type": "number"},
        "tax": {"type": "number"},
        "total": {"type": "number"},
        "currency": {"type": "string"},
    },
    "required": ["is_invoice"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """You extract structured invoice data from raw business correspondence (emails, \
forwarded threads, attachment text). The message may be a clean invoice, a messy forwarded \
thread with an invoice buried in it, an attachment-only message, or not an invoice at all \
(spam, a newsletter, unrelated correspondence).

If it is not an invoice, set is_invoice=false and give a short not_invoice_reason. Otherwise \
set is_invoice=true and fill in every other field as precisely as you can from the text. Use \
the exact numbers present in the text — do not compute or "correct" totals, even if the math \
looks wrong. If a field is genuinely absent from the source, use an empty string (for \
required string fields) rather than guessing."""


def extract_invoice(raw: RawMessage, mock: bool = True) -> ExtractionResult:
    """Extract an ExtractedInvoice (or classify as not-an-invoice) from a raw case."""
    if mock:
        return _extract_mock(raw)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            f"[extract] ANTHROPIC_API_KEY not set — cannot run real extraction for "
            f"case {raw.id}. Use --mock, or export ANTHROPIC_API_KEY. Falling back to mock."
        )
        return _extract_mock(raw)

    return _extract_llm(raw)


# ---------------------------------------------------------------------------
# Mock path — fixture-driven, deterministic, free.
# ---------------------------------------------------------------------------


def _extract_mock(raw: RawMessage) -> ExtractionResult:
    fixture = raw.fixture or {}

    if "extraction" in fixture:
        data = fixture["extraction"]
        if isinstance(data, dict) and data.get("status") == "not_invoice":
            return ExtractionResult(
                status="not_invoice",
                reason=data.get("reason", "classified as non-invoice correspondence"),
            )
        return _validate_attempt(data)

    attempts = fixture.get("extraction_attempts")
    if not attempts:
        return ExtractionResult(
            status="failed", reason="no _fixture data present for mock extraction", attempts=0
        )

    last_error = "unknown"
    for i, attempt_data in enumerate(attempts[:2], start=1):
        result = _validate_attempt(attempt_data)
        result.attempts = i
        if result.status == "ok":
            return result
        last_error = result.reason or "validation error"

    return ExtractionResult(
        status="failed",
        reason=f"extraction failed after {min(len(attempts), 2)} attempt(s): {last_error}",
        attempts=min(len(attempts), 2),
    )


def _validate_attempt(data: dict) -> ExtractionResult:
    try:
        invoice = ExtractedInvoice(**data)
        return ExtractionResult(status="ok", invoice=invoice)
    except ValidationError as e:
        return ExtractionResult(status="failed", reason=_short_error(e))


def _short_error(e: ValidationError) -> str:
    first = e.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "(root)"
    return f"{loc}: {first['msg']}"


# ---------------------------------------------------------------------------
# Real path — Anthropic API, strict JSON output, one retry with feedback.
# ---------------------------------------------------------------------------


def _extract_llm(raw: RawMessage) -> ExtractionResult:
    import anthropic

    client = anthropic.Anthropic()
    source_text = _render_source(raw)

    error_feedback = ""
    for attempt in (1, 2):
        prompt = source_text
        if error_feedback:
            prompt += (
                f"\n\nYour previous extraction did not match the required schema: "
                f"{error_feedback}\nFix it and return the full extraction again."
            )
        try:
            response = client.messages.create(
                model=EXTRACTION_MODEL,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                output_config={"format": {"type": "json_schema", "schema": _JSON_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:  # network/API error — not a schema problem, don't retry
            return ExtractionResult(status="failed", reason=f"API error: {e}", attempts=attempt)

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            error_feedback = f"response was not valid JSON: {e}"
            continue

        if not data.get("is_invoice", True):
            return ExtractionResult(
                status="not_invoice",
                reason=data.get("not_invoice_reason", "classified as non-invoice correspondence"),
                attempts=attempt,
            )

        result = _validate_attempt(data)
        result.attempts = attempt
        if result.status == "ok":
            return result
        error_feedback = result.reason or "validation error"

    return ExtractionResult(
        status="failed",
        reason=f"extraction failed after 2 attempts: {error_feedback}",
        attempts=2,
    )


def _render_source(raw: RawMessage) -> str:
    parts = [f"From: {raw.from_}", f"Subject: {raw.subject}", "", raw.body_text]
    for att in raw.attachments:
        parts.append(f"\n--- Attachment: {att.filename} ({att.type}) ---\n{att.content_text}")
    return "\n".join(parts)
