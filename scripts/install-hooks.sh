#!/usr/bin/env bash
# scripts/install-hooks.sh
#
# Configure this repo to use .githooks/ as its git hooks directory.
# Idempotent: safe to re-run.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.githooks"

if [[ ! -d "$HOOKS_DIR" ]]; then
  echo "❌ .githooks/ directory not found at $HOOKS_DIR"
  exit 1
fi

# Make all hook scripts executable
find "$HOOKS_DIR" -type f -exec chmod +x {} \;

# Set core.hooksPath
CURRENT=$(git config --local core.hooksPath 2>/dev/null || echo "")
if [[ "$CURRENT" == ".githooks" ]]; then
  echo "✓ core.hooksPath already set to .githooks"
else
  git config --local core.hooksPath .githooks
  echo "✓ Set core.hooksPath = .githooks (was: ${CURRENT:-unset})"
fi

# Verify the chain works
echo ""
echo "Active pre-commit hook:"
git config --local core.hooksPath
ls -la "$HOOKS_DIR/pre-commit" 2>&1
