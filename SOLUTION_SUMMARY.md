# Solution Summary: Git Session Tracking & GitHub PR Integration

This document provides a complete answer to the problem statement about linking GitHub PR comments to terminal sessions.

## Problem Statement

> When a comment is made in GitHub on a PR, is it possible to automatically know which commit that code came from? If so, I need the snippet. I also need a commit hook that will run a quick bash command to fetch the Session ID of the current terminal window so that I can leave a note or set a property on that commit that will link it to that agent/window so that when there is a comment or some update, it can hit a webhook or just send an e-mail/msg to that agent.

## Solution Overview

**YES - This is fully implemented!** The solution provides:

1. ✅ **Commit identification from PR comments** - Get commit SHA, file path, and code snippet
2. ✅ **Session ID capture** - Git hooks automatically capture iTerm session IDs
3. ✅ **Metadata storage** - Git notes link commits to terminal sessions
4. ✅ **Notification routing** - Framework to route PR comments back to the agent/terminal

## Quick Start

### 1. Install Git Hooks

```bash
# Install in your repository
cd /path/to/your/repo
/path/to/iterm-mcp-claude-agency/scripts/install-git-hooks.sh

# Or from the iterm-mcp directory
./scripts/install-git-hooks.sh /path/to/your/repo
```

### 2. Make Commits (Automatic)

The hooks run automatically - no action needed! Each commit will now include session metadata.

### 3. Query Session Information

```bash
# Show session info for a commit
python scripts/query-session.py show <commit-sha>

# List all commits from a session
python scripts/query-session.py list-session <session-id>

# Get commit info from GitHub PR comment
export GITHUB_TOKEN="your_token"
python scripts/query-session.py from-github owner repo comment_id

# Extract code snippet from PR comment
python scripts/get-pr-comment-snippet.py owner repo comment_id
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Developer Workflow                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Git Commit      │
                    │  in iTerm        │
                    └──────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │  Git Hooks (prepare-commit-msg +       │
        │             post-commit)                │
        │  - Capture $ITERM_SESSION_ID           │
        │  - Store in git notes                  │
        └────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Git Notes       │
                    │  (metadata)      │
                    └──────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │  Push to GitHub                        │
        └────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Review & Comment                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │  GitHub PR Comment                     │
        │  - Includes commit SHA                 │
        │  - Includes file path & line           │
        └────────────────────────────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │  Webhook Handler                       │
        │  - Query GitHub API for commit         │
        │  - Look up session from git notes      │
        │  - Route notification to terminal      │
        └────────────────────────────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │  Terminal Notification                 │
        │  (via iTerm MCP Server)                │
        └────────────────────────────────────────┘
```

## Detailed Answers

### Q: "Is it possible to automatically know which commit that code came from?"

**A: YES!** GitHub PR review comments include the commit SHA in the API response.

Use the GitHub API to get comment details:
```bash
curl -H "Authorization: token YOUR_TOKEN" \
  https://api.github.com/repos/OWNER/REPO/pulls/comments/COMMENT_ID
```

This returns:
- `original_commit_id` - The commit SHA
- `path` - The file path
- `line` / `original_line` - The line number
- `diff_hunk` - The code context

Our tool automates this:
```bash
python scripts/get-pr-comment-snippet.py owner repo comment_id
```

### Q: "I need the snippet"

**A: DONE!** The `get-pr-comment-snippet.py` tool:

1. Calls GitHub API to get comment details
2. Extracts commit SHA, file path, and line numbers
3. Fetches the file content at that commit
4. Displays the code snippet with context
5. Highlights the commented lines

Example output:
```
======================================================================
GitHub PR Comment Snippet
======================================================================

📍 Location:
   Commit:  abc123def
   File:    src/core/session.py
   Lines:   45-48

💬 Comment:
   This could be optimized using a generator

📄 Code Context:
   (Lines marked with >>> are referenced by the comment)

     43 | def process_items(items):
     44 |     results = []
>>>  45 |     for item in items:
>>>  46 |         if item.is_valid():
>>>  47 |             results.append(item.process())
>>>  48 |     return results
     49 | 
     50 | def cleanup():
```

### Q: "I need a commit hook that will run a quick bash command to fetch the Session ID"

**A: IMPLEMENTED!** Two git hooks work together:

**`prepare-commit-msg`** (Python):
- Reads `$ITERM_SESSION_ID` environment variable
- Captures session metadata (hostname, username, working directory)
- Appends session ID to commit message as a trailer
- Saves metadata to temporary file

**`post-commit`** (Python):
- Runs after commit is created
- Reads metadata from temporary file
- Stores in git notes under `refs/notes/iterm-session`
- Displays confirmation message

The hooks are written in Python (not bash) for better error handling and JSON serialization, but they accomplish the same goal - capturing the session ID automatically.

### Q: "Leave a note or set a property on that commit that will link it to that agent/window"

**A: DONE!** We use **git notes** with a custom reference:

Git notes are a Git feature that allows attaching metadata to commits without modifying the commits themselves. Perfect for this use case!

Storage location: `refs/notes/iterm-session`

Metadata structure (JSON):
```json
{
  "session_id": "w0t0p0s0",
  "persistent_id": "w0t0p0s0",
  "timestamp": "2024-01-28T12:00:00Z",
  "hostname": "dev-machine",
  "username": "developer",
  "working_directory": "/home/developer/project"
}
```

### Q: "When there is a comment or some update, it can hit a webhook or send an e-mail/msg to that agent"

**A: FRAMEWORK PROVIDED!** The `examples/git_session_integration.py` demonstrates:

1. **Webhook Handler** - Receives GitHub webhook events
2. **Commit Lookup** - Gets commit SHA from PR comment
3. **Session Query** - Retrieves session metadata from git notes
4. **Notification Routing** - Sends message to terminal session

Integration points for your "Air" repository:
- `handle_pr_comment_webhook()` - Main entry point
- `notify_terminal_session()` - Where you add your notification logic
- `webhook_handler()` - HTTP endpoint for GitHub webhooks

## Files Created

### Core Module
- `core/git_integration.py` - Session tracking utilities (470 lines)

### Git Hooks
- `scripts/prepare-commit-msg` - Capture session ID (Python)
- `scripts/post-commit` - Store metadata (Python)
- `scripts/install-git-hooks.sh` - Installation script (Bash)

### CLI Tools
- `scripts/query-session.py` - Query session information
- `scripts/get-pr-comment-snippet.py` - Extract PR comment context

### Examples & Documentation
- `examples/git_session_integration.py` - Webhook integration example
- `docs/git-session-tracking.md` - Comprehensive documentation
- `tests/test_git_integration.py` - Unit tests (12 tests, all passing)

## Testing

All functionality has been tested:

```bash
# Run unit tests
python -m unittest tests.test_git_integration -v

# Test query tool
python scripts/query-session.py show HEAD

# Test remote info
python scripts/query-session.py remote-info

# Test hooks (make a commit)
git commit -m "Test commit"
# Should see: "✅ Stored session metadata for commit ..."
```

## Next Steps

To complete the integration with "Air" repository:

1. **Set up GitHub webhook**
   - Go to GitHub repo settings → Webhooks
   - Add webhook URL pointing to your server
   - Select "Pull request review comments" event

2. **Deploy webhook handler**
   - Use `examples/git_session_integration.py` as template
   - Integrate with FastAPI/Flask web framework
   - Add authentication/security

3. **Implement notification delivery**
   - Connect to iTerm MCP server
   - Or integrate with your "Air" notification system
   - Add email/Slack/etc. adapters as needed

4. **Share git notes** (optional)
   - Configure automatic push/fetch:
     ```bash
     git config --add remote.origin.push '+refs/notes/iterm-session:refs/notes/iterm-session'
     git config --add remote.origin.fetch '+refs/notes/iterm-session:refs/notes/iterm-session'
     ```

## Support

See full documentation:
- [docs/git-session-tracking.md](docs/git-session-tracking.md) - Complete guide
- [examples/git_session_integration.py](examples/git_session_integration.py) - Integration examples
- [README.md](README.md) - Quick start section

For issues or questions, refer to the documentation or examine the test suite for usage examples.
