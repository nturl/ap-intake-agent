"""Load raw cases from data/inbox/.

100% deterministic — no LLM involvement. A "case" is one JSON file
simulating an inbound email (with optional attachments) that may or may not
turn out to be a real invoice.
"""

from __future__ import annotations

import json
from pathlib import Path

from apintake.schemas import RawMessage

DEFAULT_INBOX_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "inbox"


def load_case(path: Path) -> RawMessage:
    """Load a single case file. Raises on malformed JSON — that's an ingest
    bug, not a validation concern, so we don't try to recover from it."""
    data = json.loads(Path(path).read_text())
    return RawMessage.model_validate(data)


def load_all_cases(inbox_dir: Path = DEFAULT_INBOX_DIR) -> list[tuple[Path, RawMessage]]:
    """Load every case in the inbox, sorted by filename for reproducible order."""
    cases = []
    for path in sorted(inbox_dir.glob("*.json")):
        cases.append((path, load_case(path)))
    return cases
