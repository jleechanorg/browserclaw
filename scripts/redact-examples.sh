#!/usr/bin/env bash
# scripts/redact-examples.sh
#
# Auto-redact real production values in examples/** files with
# <your-...> placeholders. Idempotent: safe to re-run.
#
# Patterns handled:
# - Firebase API key (AIzaSy...) → <your-firebase-api-key>
# - 6 known production campaign IDs → <your-campaign-id-N>
# - Test user UID → <your-firebase-uid>
# - Test user email → <your-test-user>@example.com
# - GCP project IDs → <your-firebase-project-id>
#
# Usage:
#   bash scripts/redact-examples.sh         # dry-run (prints what would change)
#   bash scripts/redact-examples.sh --apply  # actually rewrite files
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
fi

# Pairs of (regex_to_find, replacement)
PAIRS=(
  's|AIza[A-Za-z0-9_-]\{35\}|<your-firebase-api-key>|g'
  's|cntvDfj7cGUhUFkxcmV3|<your-campaign-id-1>|g'
  's|L5iB5eWq8TyzQW3qFDDv|<your-campaign-id-2>|g'
  's|z7eDk3NzY1mB6BTm23yu|<your-campaign-id-3>|g'
  's|Z2sEA1hQW3YJbyQHvvt6|<your-campaign-id-4>|g'
  's|XHWCpllzfKNgwf6o1Jvc|<your-campaign-id-5>|g'
  's|zheWLda5wsDVQTdXrRFm|<your-campaign-id-6>|g'
  's|0wf6sCREyLcgynidU5LjyZEfm7D2|<your-firebase-uid>|g'
  's|jleechantest@gmail\.com|<your-test-user>@example.com|g'
  's|worldarchitecture-ai|<your-firebase-project-id>|g'
  's|worldai-prod-c4977|<your-firebase-project-id>|g'
)

EXAMPLE_DIR="$REPO_ROOT/examples"
if [[ ! -d "$EXAMPLE_DIR" ]]; then
  echo "❌ No examples/ directory at $EXAMPLE_DIR"
  exit 1
fi

CHANGED=0
TOTAL=0
while IFS= read -r -d '' FILE; do
  TOTAL=$((TOTAL + 1))
  BEFORE=$(cat "$FILE")
  AFTER="$BEFORE"
  for PAIR in "${PAIRS[@]}"; do
    AFTER=$(echo "$AFTER" | sed "$PAIR")
  done
  if [[ "$BEFORE" != "$AFTER" ]]; then
    if [[ $APPLY -eq 1 ]]; then
      echo "$AFTER" > "$FILE"
      echo "✓ redacted: $FILE"
    else
      echo "would redact: $FILE"
    fi
    CHANGED=$((CHANGED + 1))
  fi
done < <(find "$EXAMPLE_DIR" -type f -print0)

echo ""
echo "Scanned $TOTAL file(s) under examples/"
if [[ $APPLY -eq 1 ]]; then
  echo "Redacted $CHANGED file(s)"
  if [[ $CHANGED -eq 0 ]]; then
    echo "✓ Nothing to redact — examples/ is clean"
  fi
else
  echo "Would redact $CHANGED file(s) (dry-run)"
  echo "Re-run with --apply to actually rewrite:"
  echo "  bash scripts/redact-examples.sh --apply"
fi
