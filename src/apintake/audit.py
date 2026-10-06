"""Append-only audit trail.

Every stage transition in the pipeline gets logged here — this is the "if the
client can't see what the agent did, they'll never trust it" artifact. One
JSON object per line, never rewritten, never deleted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from apintake.schemas import Actor, AuditEvent

DEFAULT_AUDIT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "audit_trail.jsonl"


def log(
    case_id: str,
    stage: str,
    actor: Actor,
    event: str,
    payload: dict | None = None,
    path: Path = DEFAULT_AUDIT_PATH,
) -> AuditEvent:
    """Append one audit event. Returns the event that was written."""
    entry = AuditEvent(
        ts=datetime.now(UTC).isoformat(),
        case_id=case_id,
        stage=stage,
        actor=actor,
        event=event,
        payload=payload or {},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(entry.model_dump_json() + "\n")
    return entry


def read_trail(case_id: str | None = None, path: Path = DEFAULT_AUDIT_PATH) -> list[AuditEvent]:
    """Read all audit events, optionally filtered to one case."""
    if not path.exists():
        return []
    events = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(AuditEvent.model_validate_json(line))
    if case_id is not None:
        events = [e for e in events if e.case_id == case_id]
    return events


def format_trail(events: list[AuditEvent]) -> str:
    """Pretty-print a list of audit events as a human-readable trail."""
    if not events:
        return "(no audit events found)"
    lines = []
    for e in events:
        payload_str = json.dumps(e.payload, default=str) if e.payload else ""
        lines.append(f"[{e.ts}] {e.stage:<10} actor={e.actor:<13} {e.event}")
        if payload_str and payload_str != "{}":
            lines.append(f"    {payload_str}")
    return "\n".join(lines)
