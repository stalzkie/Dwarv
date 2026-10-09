#!/usr/bin/env bash
# Step 10: the required demo sequence. Assumes `dwarv setup-offline` has
# already run (the 3 models + llama-server are cached) and `dwarv` is on
# PATH. The demo bug/repo is chosen in advance -- see demo_repo_template/ --
# not improvised at demo time.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEMO_DIR="$(mktemp -d -t dwarv-demo-XXXXXX)"
trap 'rm -rf "$DEMO_DIR"' EXIT

banner() { printf '\n=== %s ===\n' "$1"; }

banner "1/5: hardware detection"
dwarv doctor

banner "setting up the demo repo (chosen in advance, see scripts/demo_repo_template/)"
cp -r "$SCRIPT_DIR/demo_repo_template/." "$DEMO_DIR/"
(
  cd "$DEMO_DIR"
  git init -q
  git config user.email "demo@example.com"
  git config user.name "Dwarv Demo"
  git add -A
  git commit -q -m "init: apply_discount has a bug"
)
echo "demo repo: $DEMO_DIR"
echo "--- app.py (buggy) ---"
cat "$DEMO_DIR/app.py"

banner "2/5: fix a real failing test, trigger a memory squeeze mid-conversation"
(
  cd "$DEMO_DIR"
  printf '%s\n' \
    "The test in tests/test_app.py is failing. Look at app.py and fix the bug so the test passes." \
    "/squeeze 2200" \
    "what does apply_discount do now, in one sentence?" \
    "/status" \
    "/exit" \
  | dwarv chat
)
echo "--- app.py (after the session) ---"
cat "$DEMO_DIR/app.py"

banner "3/5: offline proof"
dwarv check-offline

banner "4/5: internal eval evidence (developer-facing validation, not a user-facing claim)"
LATEST_RUN="$(ls -t "$REPO_ROOT/eval_results" 2>/dev/null | grep -v '\.gitkeep' | head -n1 || true)"
if [ -n "$LATEST_RUN" ] && [ -f "$REPO_ROOT/eval_results/$LATEST_RUN/summary.md" ]; then
  echo "(most recent internal eval run: $LATEST_RUN -- see docs/EXPERIMENT.md for the full protocol and honest caveats)"
  cat "$REPO_ROOT/eval_results/$LATEST_RUN/summary.md"
else
  echo "no eval_results/ run found -- run 'dwarv eval --run-id <id>' first"
fi

banner "5/5: honesty check -- a real task Dwarv did NOT solve"
echo "On HumanEval/103 (rounded_avg: average, round, format as a '0b'-prefixed"
echo "binary string), the small model got it wrong twice, retried with the real"
echo "failure feedback both times, and then stopped honestly once its attempt"
echo "budget ran out -- no unverified code was left applied. Real data, not"
echo "staged for this demo -- see docs/EXPERIMENT.md's 'A real case where Dwarv"
echo "did not help' section and eval_results/dryrun2/results.jsonl."

banner "demo complete"
