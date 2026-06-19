#!/usr/bin/env bash
# test_example_placeholder_discipline.sh
#
# CI test: assert that no real production credentials exist in
# examples/** files in the working tree. The check targets the
# specific known leaks from commits 3aac8fe8 (jleechanclaw) and
# 45836c8 (browserclaw, this repo).
#
# Patterns flagged (real values must NOT appear in working tree):
# - Firebase API key format: AIza[A-Za-z0-9_-]{35}
# - The 6 known production campaign IDs from 45836c8
# - The known test user UID: 0wf6sCREyLcgynidU5LjyZEfm7D2
# - The known test email: jleechantest@gmail.com
# - GCP project IDs: worldarchitecture-ai, worldai-prod-c4977
#
# Placeholder scheme (what SHOULD be there):
# - <your-firebase-api-key>
# - <your-campaign-id> or <your-campaign-id-N>
# - <your-firebase-uid>
# - <your-test-user>@example.com
# - <your-firebase-project-id>
#
# Usage:
#   bash tests/test_example_placeholder_discipline.sh
# Returns: 0 if all examples/ files are clean, 1 if any real PII found.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Patterns to flag (real values, NOT placeholders)
REAL_PATTERNS=(
  'AIza[A-Za-z0-9_-]{35}'                  # Firebase API key
  'cntvDfj7cGUhUFkxcmV3'                   # campaign ID 1
  'L5iB5eWq8TyzQW3qFDDv'                   # campaign ID 2
  'z7eDk3NzY1mB6BTm23yu'                   # campaign ID 3
  'Z2sEA1hQW3YJbyQHvvt6'                   # campaign ID 4
  'XHWCpllzfKNgwf6o1Jvc'                   # campaign ID 5
  'zheWLda5wsDVQTdXrRFm'                   # campaign ID 6
  '0wf6sCREyLcgynidU5LjyZEfm7D2'           # test user UID
  'jleechantest@gmail\.com'                # test user email
  'worldarchitecture-ai'                   # GCP project
  'worldai-prod-c4977'                     # GCP project variant
)

PASS=1
TOTAL_FILES=0
TOTAL_HITS=0

echo "=== test_example_placeholder_discipline ==="
echo ""

# Walk examples/** files (or all files if no examples/ dir)
if [[ -d "$REPO_DIR/examples" ]]; then
  EXAMPLE_DIRS=("$REPO_DIR/examples")
else
  echo "  WARN: no examples/ directory in $REPO_DIR — test is a no-op"
  echo "  Consider creating examples/ or updating this test to target a real dir"
  echo ""
  echo "  PASS (no examples/ to scan)"
  exit 0
fi

# Build a single regex with all patterns
COMBINED_REGEX=$(printf '%s\n' "${REAL_PATTERNS[@]}" | paste -sd'|' -)

# Search recursively
for EXAMPLE_DIR in "${EXAMPLE_DIRS[@]}"; do
  while IFS= read -r -d '' FILE; do
    TOTAL_FILES=$((TOTAL_FILES + 1))
    # grep -E for the combined regex; -n for line numbers; -H for filename
    HITS=$(grep -E -n "$COMBINED_REGEX" "$FILE" 2>/dev/null || true)
    if [[ -n "$HITS" ]]; then
      echo "FAIL: $FILE contains real production value(s):"
      echo "$HITS" | sed 's/^/  /'
      PASS=0
      HITS_COUNT=$(printf '%s\n' "$HITS" | wc -l | tr -d ' ')
      TOTAL_HITS=$((TOTAL_HITS + HITS_COUNT))
    fi
  done < <(find "$EXAMPLE_DIR" -type f -print0)
done

echo ""
echo "Scanned $TOTAL_FILES file(s) under examples/"
if [[ "$TOTAL_HITS" -eq 0 ]]; then
  echo "  0 real production values found — PASS"
else
  echo "  $TOTAL_HITS real production value(s) found — FAIL"
  echo ""
  echo "Remediation:"
  echo "  bash scripts/redact-examples.sh        # auto-replace with placeholders"
  echo "  Or manually: replace real values with <your-...> placeholders"
  echo "  See ~/.claude/CLAUDE.md 'Example / seed / test fixture credential discipline'"
fi
echo ""

if [[ "$PASS" -eq 1 ]]; then
  echo "✓ All examples/ files are clean"
  exit 0
else
  echo "✗ One or more examples/ files contain real production values"
  exit 1
fi
