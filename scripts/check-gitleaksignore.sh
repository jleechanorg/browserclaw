#!/usr/bin/env bash
# scripts/check-gitleaksignore.sh
#
# Treats .gitleaksignore as a TODO queue. Flags entries older than
# 30 days as overdue — the proper fix is to remove the suppression
# after rotating the leaked credential.
#
# Why: .gitleaksignore is a placeholder suppression, NOT a fix.
# Every entry is "this commit is still in history with a real
# credential — the fix is rotation + history rewrite." If the entry
# has been there for >30 days, it's a TODO that nobody owns.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
GITLEAKS_IGNORE="$REPO_ROOT/.gitleaksignore"

if [[ ! -f "$GITLEAKS_IGNORE" ]]; then
  echo "✓ No .gitleaksignore file — nothing to check"
  exit 0
fi

NOW_EPOCH=$(date +%s)
THIRTY_DAYS_AGO=$((NOW_EPOCH - 30 * 24 * 60 * 60))
OVERDUE=0
WARN=0
TOTAL=0

echo "=== check-gitleaksignore ==="
echo ""

# Parse entries of the form: <sha>:<file>:<rule>:<line>
while IFS= read -r LINE; do
  # Skip comments and blank lines
  [[ -z "$LINE" || "$LINE" =~ ^# ]] && continue
  TOTAL=$((TOTAL + 1))
  SHA=$(echo "$LINE" | cut -d: -f1)
  if [[ ${#SHA} -lt 7 ]]; then
    echo "WARN: malformed entry: $LINE"
    WARN=$((WARN + 1))
    continue
  fi
  # Look up the commit date
  COMMIT_DATE=$(git log -1 --format='%ct' "$SHA" 2>/dev/null || echo "0")
  if [[ "$COMMIT_DATE" == "0" ]]; then
    echo "WARN: SHA not found in history: $SHA"
    WARN=$((WARN + 1))
    continue
  fi
  if [[ "$COMMIT_DATE" -lt "$THIRTY_DAYS_AGO" ]]; then
    DAYS_OLD=$(( (NOW_EPOCH - COMMIT_DATE) / 86400 ))
    echo "❌ OVERDUE ($DAYS_OLD days old): $LINE"
    echo "   Action: rotate the leaked credential in the upstream system,"
    echo "           then remove this line from .gitleaksignore."
    OVERDUE=$((OVERDUE + 1))
  else
    DAYS_OLD=$(( (NOW_EPOCH - COMMIT_DATE) / 86400 ))
    echo "⚠️  recent ($DAYS_OLD days old): $LINE"
  fi
done < "$GITLEAKS_IGNORE"

echo ""
echo "Total entries: $TOTAL"
echo "Overdue (>30d): $OVERDUE"
echo "Warnings: $WARN"

if [[ $OVERDUE -gt 0 ]]; then
  echo ""
  echo "❌ .gitleaksignore has $OVERDUE overdue entries — these are leaks that have been public for >30 days."
  echo "   Each entry needs: (1) credential rotation in the upstream system, (2) remove from .gitleaksignore."
  exit 1
fi

exit 0
