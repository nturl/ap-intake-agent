#!/usr/bin/env bash
export NO_COLOR=1
export TERM=dumb
cd "$(dirname "$0")/../.." || exit 1

echo "# apintake demo, mock mode, no API key"
echo

step() {
  # $1 = short form shown to viewers, $2 = command actually run
  echo "\$ $1"
  sleep 1
  eval "$2"
  sleep 2
  echo
}

step "uv run apintake run --mock data/inbox/case-001.json" "env -u ANTHROPIC_API_KEY uv run apintake run --mock data/inbox/case-001.json"
step "uv run apintake trail case-001" "env -u ANTHROPIC_API_KEY uv run apintake trail case-001"
step "uv run apintake run --mock data/inbox/case-030.json" "env -u ANTHROPIC_API_KEY uv run apintake run --mock data/inbox/case-030.json"
step "uv run apintake trail case-030" "env -u ANTHROPIC_API_KEY uv run apintake trail case-030"
step "uv run apintake eval --mock" "env -u ANTHROPIC_API_KEY uv run apintake eval --mock"
step 'grep -n "Overall pass rate" evals/eval_report.md' 'grep -n "Overall pass rate" evals/eval_report.md'
