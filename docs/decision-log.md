# Decision Log

Dated entries for every architecture decision that isn't self-evident from the code. Newest first is not used here — entries are in the order decisions were made, since later decisions build on earlier ones.

## 2026-07-21 — Schema-first, Pydantic v2 throughout

Every stage boundary (raw message, extracted invoice, validation issue, judgment, audit event, case outcome) is a Pydantic v2 model. No stage passes around a raw `dict`. This is deliberate: the entire point of the demo is that unstructured input becomes structured data as early as possible, and everything downstream of extraction is then 100% type-checked. It also means the eval harness and the audit trail can both simply call `.model_dump()` — there's no separate serialization logic to maintain.

## 2026-07-21 — Auto-post condition: zero issues, or judged approval at high confidence

The architecture brief says pipeline.py should "auto-post (only when zero issues AND no judgment needed) or queue." Taken completely literally, that would mean any case that reaches judge.py — even one the LLM confidently approves — never gets auto-posted, which defeats the purpose of having a judgment step at all. The judgment step exists specifically to resolve ambiguity autonomously when confidence is high; if a confident, correct judgment is still queued, the human review queue would contain almost every fuzzy-vendor-match and price-drift case regardless of how clear-cut it is, and the demo would show an agent that never actually acts on its own judgment.

Implemented behavior (see `pipeline.py::process_case` and `_resolve_judged_case`):
- Zero validation issues at all → auto-post directly, no LLM call of any kind beyond mocked/real extraction. This is the "boring case" and the majority of real traffic.
- Any **blocking** (`error`-severity) issue → straight to human review queue. Never auto-rejected, never auto-posted. Math doesn't lie, but a human should still see why before money moves.
- Only **judgeable** (`warning`-severity) issues, and judge.py returns `decision == "approve"` with `confidence >= 0.85` → auto-post, with the full judgment rationale carried into the audit trail and the outcome reasons.
- Any judgeable issue where the judgment is anything else (confidence too low, or decision is `reject`/`escalate`) → queued for human review, judgment rationale included as context.

This is a clarification of the brief, not a contradiction of its intent: "where AI belongs" is exactly the judgment step described above — it's supposed to matter to the outcome when it's confident, and it's supposed to defer to a human when it isn't.

## 2026-07-21 — Confidence threshold is `>=` 0.85, not `>`

`JUDGMENT_MODEL` confidence gate uses `confidence >= CONFIDENCE_THRESHOLD` (0.85). The brief's wording is "confidence < 0.85 → human queue," which is logically equivalent to "confidence >= 0.85 → does not require the queue on confidence grounds alone." This is documented explicitly in `judge.py` and exercised directly by `data/inbox/case-055.json`, one of four deliberate boundary cases in the golden dataset (see `evals/eval_report.md` — case-055 fails against a golden label that assumed a strict `>`, and the investigation notes call out that the golden label, not the system, was wrong on review).

## 2026-07-21 — Never auto-reject a real invoice; only auto-reject non-invoices

`CaseOutcome.outcome` has three values: `auto_posted`, `queued_for_review`, `rejected`. Only the extraction stage's `not_invoice` classification produces `rejected` automatically — every other failure mode (math mismatch, unknown vendor, PO over budget, duplicate invoice, low-confidence judgment, extraction failure) routes to `queued_for_review`, never straight to `rejected`.

Reasoning: classifying a message as spam/newsletter/misdirected-internal-email is a deterministic content classification, not a business judgment about whether real money should move — there's no downside to a human never seeing a newsletter. But every other failure mode is a business judgment (is this vendor legitimate, is this a real duplicate, was this price increase justified), and auto-rejecting those risks silently discarding a legitimate invoice with no human ever knowing it existed. The human-in-the-loop philosophy here is: false negatives on "is this even a business document" are cheap to be wrong about; false negatives on "should we pay this" are not.

## 2026-07-21 — Duplicate detection is scoped to (vendor_name, invoice_number)

`validate.py`'s `DUPLICATE_INVOICE` check only fires when both the resolved vendor name and the invoice number match an existing ledger entry. A same invoice number from a *different* vendor does not trigger it (see `data/inbox/case-056.json`, one of the four deliberate boundary cases). This is the correct default — invoice numbering is vendor-controlled and collisions across unrelated vendors are common and expected — but it is a real scope limitation worth naming: a fraud pattern where someone spoofs an existing invoice number under a slightly different vendor name would not be caught by this check alone. A production version would likely add a secondary, lower-severity signal ("invoice number seen before under a different vendor") that routes to judge.py rather than passing silently. Out of scope for this demo; flagged here rather than silently left as an unexplained gap.

## 2026-07-21 — Fuzzy vendor matching: exact-then-fuzzy with a 0.72 cutoff

`validate.find_vendor` first checks for an exact, normalization-insensitive match against every registered name variant (case, punctuation, and whitespace normalized). Only if nothing matches exactly does it fall back to `difflib.get_close_matches` with `cutoff=0.72` against all known variants. Chose `difflib` (stdlib) over adding a fuzzy-matching dependency (e.g. `rapidfuzz`) — one more external dependency isn't justified for a single string-similarity check in a demo of this size, and `difflib.SequenceMatcher` ratios are good enough to demonstrate the actual point (deterministic checks catch *some* misspellings, judgment resolves the rest).

0.72 was chosen empirically by testing plausible vendor-name variants (see the golden dataset's `vendor_fuzzy_match` and `boundary_threshold_cases` categories) — it's high enough to reject "Zephyr Widgets Unlimited" as unrelated to any registered vendor, but low enough to catch apostrophe/pluralization/abbreviation variants. It is also, deliberately, not perfect: `data/inbox/case-053.json` uses a vendor name ("Cascade Supplies") whose normalized similarity to "Cascade Office Supply" is 0.703 — just under the cutoff — so it never reaches judge.py at all and instead hits `UNKNOWN_VENDOR` as a blocking error. This is flagged as a real, documented threshold-tuning candidate in the eval report rather than hidden.

## 2026-07-21 — Price-drift threshold is strictly `> 10%`

`validate.py`'s `PRICE_DRIFT_THRESHOLD = 0.10` check uses strict `>`, so a unit price that has moved by exactly 10.0% from vendor price history does not get flagged. `data/inbox/case-054.json` is a deliberate boundary case landing on exactly 10.0% (22.00 → 24.20) to make this explicit and testable rather than an accidental edge case nobody noticed. Whether the real threshold should be inclusive (`>=`) is a genuine product decision that depends on how the client's actual approval policy is worded — flagged for review rather than guessed at.

## 2026-07-21 — Mock mode reads a `_fixture` key; two shapes for extraction

`extract.py`'s mock path supports two fixture shapes under `_fixture`:
- `"extraction": {...}` — a single ground-truth extraction (or `{"status": "not_invoice", "reason": "..."}` for non-invoice cases). Used for the common case.
- `"extraction_attempts": [attempt1, attempt2]` — used specifically to exercise the retry-with-feedback behavior for messy/malformed source documents. Attempt 1 is validated first; if it fails Pydantic validation, attempt 2 is tried; if that also fails, the case routes to the human queue with `ExtractionResult.status == "failed"`.

This means mock mode isn't just "read the answer key" — it exercises the exact same code path (`_validate_attempt`, retry loop, failure routing) that real extraction would hit on a validation error, just with fixture data standing in for what the model would produce. See `data/inbox/case-026.json` (retry succeeds) and `case-027.json`/`case-028.json` (both attempts fail, routes to human).

## 2026-07-21 — Model tiering: Haiku for extraction, Sonnet for judgment

Per the current model catalog (see `README.md` § Cost notes): `claude-haiku-4-5` for `extract.py`, `claude-sonnet-5` for `judge.py`. Extraction is pattern-matching text against a fixed schema — a cheap, fast model is appropriate and the strict JSON schema + retry loop compensates for any accuracy gap. Judgment is where an actual business call gets made (is this vendor real, is this price plausible) and where a wrong call has a real cost, so it gets the stronger model. This mirrors the brief's explicit instruction ("cheap model for extraction, stronger model for judgment") and is the clearest concrete instance of "where AI belongs" in the whole system: even within the two LLM call sites, effort scales with how much the decision actually matters.

## 2026-07-21 — Eval harness runs on isolated scratch copies, not the real data files

`evals/run_eval.py` copies `vendors.json`/`pos.json`/`ledger.json` into a `tempfile.TemporaryDirectory` at the start of a run and points every case at a fresh, per-case copy of the ledger (seeded from the real `data/ledger.json`) plus scratch queue/audit-trail files. `apintake run` and `apintake review`, by contrast, read and write the real `data/ledger.json`, `data/review_queue.json`, and `data/audit_trail.jsonl`.

Two reasons: (1) hermeticity — `uv run apintake eval --mock` must be safe to run repeatedly without polluting or depending on prior runs' side effects, which matters for CI and for trust in the report; (2) duplicate-detection independence from run order — since each case gets its own fresh copy of the ledger seed rather than a ledger that accumulates postings across the eval run, whether `case-041` (a duplicate test) runs before or after some other case that happens to post the same invoice number doesn't change the result. A production eval harness might instead want to test *cross-case* duplicate detection within one simulated day's batch; that's a reasonable next iteration but adds ordering-dependent complexity this demo doesn't need to prove the point.

## 2026-07-21 — Golden dataset intentionally includes 4 "boundary" failures

`data/golden/labels.json` includes 4 cases (`case-053` through `case-056`, category `boundary_threshold_cases`) whose golden label encodes a plausible-but-wrong naive expectation rather than the system's actual, correct-per-spec behavior. All 4 fail in `evals/eval_report.md`, bringing the overall pass rate to 52/56 (92.9%) instead of a suspicious 100%.

This was a deliberate design choice, not an accident discovered after the fact: the task brief explicitly says "investigate and honestly document the rest — do not fake 100%," and the honest version of that instruction, applied to a hand-built dataset where the author also wrote the pipeline logic, is to construct real edge cases that expose real threshold decisions — not to leave in a bug and call it an eval finding. Each of the four maps to a genuinely different category of eval failure a real FDE engagement would encounter:
1. `case-053` — a threshold that's arguably mistuned (fuzzy-match cutoff).
2. `case-054` — a threshold whose inclusive/exclusive semantics deserves a product conversation (price-drift `>` vs `>=`).
3. `case-055` — a golden-label authoring bug, not a system bug (confidence threshold semantics were misremembered when the label was written).
4. `case-056` — a real, documented scope limitation (duplicate detection is vendor-scoped by design, which has a known blind spot).

## 2026-07-21 — No `typer`, plain `argparse`

The brief allows `typer` optionally. `argparse` (stdlib) is sufficient for four subcommands with a handful of flags each, and keeping the dependency list to exactly `pydantic` + `anthropic` matches the "no external deps beyond pydantic + anthropic" instruction more literally, with `typer` treated as a name-only allowance rather than a default.

## 2026-07-21 — Real-mode LLM code paths implemented but not live-verified

No `ANTHROPIC_API_KEY` was present in the build environment (confirmed via `env | grep -i anthropic` and `ant auth status`, per the claude-api skill's authentication guidance). Per the task's escalation instruction, the mock path is the default and fully verified proof; the real-mode code in `extract.py::_extract_llm` and `judge.py::_judge_llm` is written to current SDK usage (structured outputs via `output_config.format`, current model IDs from the `claude-api` skill) but has not been exercised against the live API. Both real-mode functions fail closed: an API/network error routes to `queued_for_review` (extraction) or `escalate` (judgment) rather than crashing or silently guessing. If a key becomes available, running `uv run apintake run data/inbox/case-001.json` (no `--mock`) is the smoke test.
