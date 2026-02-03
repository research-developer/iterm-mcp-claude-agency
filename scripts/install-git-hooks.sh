#!/bin/bash
# Install git hooks for iTerm session ID tracking
#
# This script installs the prepare-commit-msg and post-commit hooks
# into the current git repository's .git/hooks directory.
#
# Usage:
#   ./scripts/install-git-hooks.sh [repo-path]
#
# If repo-path is not provided, uses the current directory.

set -e

# Determine the script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Target repository (default to current directory)
TARGET_REPO="${1:-.}"

# Resolve to absolute path
TARGET_REPO="$(cd "$TARGET_REPO" && pwd)"

echo "📦 Installing iTerm session tracking hooks..."
echo "   Target repository: $TARGET_REPO"

# Check if target is a git repository
if [ ! -d "$TARGET_REPO/.git" ]; then
    echo "❌ Error: $TARGET_REPO is not a git repository"
    exit 1
fi

# Create hooks directory if it doesn't exist
HOOKS_DIR="$TARGET_REPO/.git/hooks"
mkdir -p "$HOOKS_DIR"

# Install prepare-commit-msg hook
PREPARE_HOOK="$HOOKS_DIR/prepare-commit-msg"
if [ -f "$PREPARE_HOOK" ]; then
    echo "⚠️  Warning: prepare-commit-msg hook already exists"
    echo "   Creating backup at $PREPARE_HOOK.backup"
    cp "$PREPARE_HOOK" "$PREPARE_HOOK.backup"
fi

echo "📝 Installing prepare-commit-msg hook..."
cp "$SCRIPT_DIR/prepare-commit-msg" "$PREPARE_HOOK"
chmod +x "$PREPARE_HOOK"

# Install post-commit hook
POST_HOOK="$HOOKS_DIR/post-commit"
if [ -f "$POST_HOOK" ]; then
    echo "⚠️  Warning: post-commit hook already exists"
    echo "   Creating backup at $POST_HOOK.backup"
    cp "$POST_HOOK" "$POST_HOOK.backup"
fi

echo "📝 Installing post-commit hook..."
cp "$SCRIPT_DIR/post-commit" "$POST_HOOK"
chmod +x "$POST_HOOK"

echo ""
echo "✅ Hooks installed successfully!"
echo ""
echo "📖 Usage:"
echo "   The hooks will run automatically on each commit."
echo "   Session metadata is stored in git notes under refs/notes/iterm-session"
echo ""
echo "🔍 Query session data:"
echo "   git notes --ref=refs/notes/iterm-session show <commit-sha>"
echo ""
echo "🔄 Push notes to remote:"
echo "   git push origin refs/notes/iterm-session"
echo ""
echo "📥 Fetch notes from remote:"
echo "   git fetch origin refs/notes/iterm-session:refs/notes/iterm-session"
echo ""
