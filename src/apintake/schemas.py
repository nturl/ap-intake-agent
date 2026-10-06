"""Pydantic v2 schemas shared across the pipeline.

Kept deliberately flat and close to the wire format each stage actually
produces/consumes — no speculative fields, no inheritance hierarchies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Actor = Literal["deterministic", "llm", "human"]
OutcomeType = Literal["auto_posted", "queued_for_review", "rejected"]
Severity = Literal["info", "warning", "error"]


class Attachment(BaseModel):
    """One attachment on an inbound message.

    `content_text` stands in for whatever OCR/parsing pipeline a real system
    would run (PDF text extraction, spreadsheet-to-CSV, etc.) — for this demo
    the plain-text content is embedded directly in the fixture data.
    """

    filename: str
    type: str
    content_text: str


class RawMessage(BaseModel):
    """A raw inbound message as loaded from data/inbox/*.json."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_: str = Field(alias="from")
    subject: str
    body_text: str
    attachments: list[Attachment] = Field(default_factory=list)
    # Ground-truth extraction/judgment used only in --mock mode. Never sent
    # to a real model — it's the answer key, not input.
    fixture: dict | None = Field(default=None, alias="_fixture")


class LineItem(BaseModel):
    description: str
    qty: float
    unit_price: float
    total: float


class ExtractedInvoice(BaseModel):
    """Structured invoice data pulled from a raw message."""

    vendor_name: str
    invoice_number: str
    invoice_date: str
    po_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float
    tax: float = 0.0
    total: float
    currency: str = "USD"


class ExtractionResult(BaseModel):
    """Outcome of the extract stage — a case is not always a valid invoice."""

    status: Literal["ok", "not_invoice", "failed"]
    invoice: ExtractedInvoice | None = None
    reason: str | None = None
    attempts: int = 1


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str


class JudgmentResult(BaseModel):
    decision: Literal["approve", "reject", "escalate"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class AuditEvent(BaseModel):
    ts: str
    case_id: str
    stage: str
    actor: Actor
    event: str
    payload: dict = Field(default_factory=dict)


class CaseOutcome(BaseModel):
    case_id: str
    outcome: OutcomeType
    reasons: list[str] = Field(default_factory=list)
