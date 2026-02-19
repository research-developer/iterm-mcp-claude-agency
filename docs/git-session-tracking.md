# Git Commit Hook for Session ID Tracking and GitHub PR Integration

This feature enables automatic tracking of iTerm terminal sessions in git commits and provides integration with GitHub PR comments for agent notifications.

## Overview

When working with multiple terminal sessions and agents, it's useful to track which terminal/agent created each commit. This integration:

1. **Captures Session IDs**: Automatically records the iTerm session ID in each commit
2. **Links Commits to Sessions**: Stores session metadata using git notes
3. **GitHub PR Comment Integration**: Retrieves commit information from PR comments
4. **Code Snippet Extraction**: Shows exactly which code was commented on
5. **Agent Notifications**: Routes notifications to the specific terminal/agent that created code

## Key Question Answered

**"When a comment is made in GitHub on a PR, is it possible to automatically know which commit that code came from?"**

**Answer: Yes!** GitHub PR review comments include:
- The commit SHA where the comment was made (`original_commit_id`)
- The file path (`path`)
- The line number(s) (`line`, `original_line`, `original_start_line`)
- The diff context (`diff_hunk`)

This information is available via the GitHub API and can be used to:
1. Identify the exact commit
2. Extract the code snippet being discussed
3. Look up which terminal session created that commit
4. Route notifications to the responsible agent/developer

## Components

### 1. Git Hooks

Two git hooks work together to capture and store session information:

- **`prepare-commit-msg`**: Runs before commit, captures session ID from iTerm environment
- **`post-commit`**: Runs after commit, stores session metadata in git notes

### 2. Git Integration Module

The `core/git_integration.py` module provides:

- Functions to store/retrieve session metadata from git notes
- GitHub API integration to get commit SHA from PR comments
- Utilities to list commits by session ID
- Helper functions for git repository operations

### 3. CLI Tools

- **`scripts/query-session.py`**: Query session information from commits
- **`scripts/install-git-hooks.sh`**: Install hooks in any git repository
- **`scripts/get-pr-comment-snippet.py`**: Extract code snippet and commit info from PR comments

## Installation

### Step 1: Install the Hooks

Run the installation script in your repository:

```bash
cd /path/to/your/repo
/path/to/iterm-mcp/scripts/install-git-hooks.sh
```

Or install in the current repository:

```bash
# From the iterm-mcp directory
./scripts/install-git-hooks.sh .
```

The script will:
- Copy hooks to `.git/hooks/`
- Make them executable
- Backup any existing hooks

### Step 2: Verify Installation

Make a test commit to verify the hooks are working:

```bash
echo "test" > test.txt
git add test.txt
git commit -m "Test session tracking"
```

You should see output like:
```
📱 Captured iTerm session: w0t0p0s0
✅ Stored session metadata for commit 1234abcd
   Session ID: w0t0p0s0
```

### Step 3: Query Session Information

Check the session metadata for your test commit:

```bash
python scripts/query-session.py show HEAD
```

## Usage

### Automatic Capture

Once installed, the hooks run automatically on every commit. No user action is needed.

### Viewing Session Metadata

#### For a specific commit:

```bash
# Using the query tool (recommended)
python scripts/query-session.py show <commit-sha>

# Using git notes directly
git notes --ref=refs/notes/iterm-session show <commit-sha>
```

#### List all commits from a session:

```bash
python scripts/query-session.py list-session <session-id>
```

#### Get session info from GitHub PR comment:

```bash
# Export your GitHub token
export GITHUB_TOKEN="your_github_token"

# Query by comment ID
python scripts/query-session.py from-github owner repo comment_id
```

#### Extract code snippet from PR comment:

```bash
# Get the full context of what was commented on
python scripts/get-pr-comment-snippet.py owner repo comment_id

# Example output shows:
# - The commit SHA
# - File path and line numbers
# - The comment text
# - The actual code snippet with context
# - How to find the session that created it
```

### Sharing Session Metadata

Git notes are not pushed by default. To share session metadata with your team:

#### Push notes to remote:

```bash
git push origin refs/notes/iterm-session
```

#### Fetch notes from remote:

```bash
git fetch origin refs/notes/iterm-session:refs/notes/iterm-session
```

#### Configure automatic push/fetch:

```bash
# Push notes automatically with regular push
git config --add remote.origin.push '+refs/notes/iterm-session:refs/notes/iterm-session'

# Fetch notes automatically with regular fetch
git config --add remote.origin.fetch '+refs/notes/iterm-session:refs/notes/iterm-session'
```

## GitHub PR Comment Integration

### How It Works

When a comment is made on a GitHub PR:

1. The comment includes a review comment ID in the URL
2. Use the GitHub API to retrieve the commit SHA associated with that comment
3. Look up the session metadata stored in git notes for that commit
4. Route notifications to the specific terminal session that created the code

### Example Workflow

```python
from core.git_integration import (
    get_commit_from_review_comment,
    get_session_from_commit,
)

# 1. Get commit SHA from PR comment
commit_sha = get_commit_from_review_comment(
    owner="your-org",
    repo="your-repo",
    review_comment_id="1234567890",
    github_token=os.environ.get("GITHUB_TOKEN")
)

# 2. Get session metadata for that commit
metadata = get_session_from_commit(commit_sha)

# 3. Use session ID to route notifications
if metadata:
    print(f"Notify session: {metadata.session_id}")
    print(f"Created by: {metadata.username}@{metadata.hostname}")
    print(f"Working dir: {metadata.working_directory}")
```

### GitHub API Authentication

The integration uses the GitHub API which requires authentication for higher rate limits:

```bash
# Set your GitHub token
export GITHUB_TOKEN="ghp_your_personal_access_token"

# Or pass it directly
python scripts/query-session.py from-github owner repo comment_id --github-token "ghp_..."
```

## Session Metadata Structure

Session metadata is stored as JSON in git notes:

```json
{
  "session_id": "w0t0p0s0",
  "persistent_id": "w0t0p0s0",
  "timestamp": "2024-01-28T12:00:00+00:00",
  "hostname": "dev-machine",
  "username": "developer",
  "working_directory": "/home/developer/project"
}
```

### Fields

- **session_id**: iTerm session ID (from `ITERM_SESSION_ID` environment variable)
- **persistent_id**: Persistent session ID for reconnection (same as session_id in simple case)
- **timestamp**: ISO 8601 timestamp when commit was created
- **hostname**: Machine where commit was created
- **username**: User who created the commit
- **working_directory**: Working directory at commit time

## Webhook Integration (Future)

For real-time notifications when PR comments are made:

### Webhook Setup (Planned)

1. Configure a webhook in your GitHub repository settings
2. Point it to your webhook endpoint (to be implemented)
3. When a comment is posted, the webhook receives the event
4. Extract commit SHA and look up session metadata
5. Send notification to the terminal session

### Air Integration (Planned)

The webhook handler will integrate with the "Air" repository mentioned in the requirements for:
- Message routing to specific agents/terminals
- Email/notification delivery
- WebSocket connections for real-time updates

## Advanced Usage

### Custom Metadata

You can extend the session metadata by modifying `scripts/prepare-commit-msg`:

```python
def get_session_metadata() -> dict:
    metadata = {
        # ... existing fields ...
        "custom_field": "custom_value",
        "project_name": os.environ.get("PROJECT_NAME"),
    }
    return metadata
```

### Filtering Commits

Find commits matching specific criteria:

```bash
# Find commits from a specific user
git log --format="%H" | while read sha; do
    metadata=$(git notes --ref=refs/notes/iterm-session show $sha 2>/dev/null)
    if echo "$metadata" | grep -q "\"username\": \"alice\""; then
        echo $sha
    fi
done
```

### Integration with Other Tools

The `core/git_integration.py` module can be imported into other Python tools:

```python
from core.git_integration import (
    get_session_from_commit,
    list_commits_with_session,
    store_session_in_commit,
    CommitSessionMetadata,
)

# Custom usage in your tools
metadata = get_session_from_commit("abc123")
if metadata:
    # Do something with the session info
    notify_agent(metadata.session_id, "PR comment received!")
```

## Troubleshooting

### Hook Not Running

Check if the hook is installed and executable:

```bash
ls -la .git/hooks/prepare-commit-msg
ls -la .git/hooks/post-commit
```

Both should have execute permissions (`-rwxr-xr-x`).

### No Session ID Captured

The session ID comes from the `ITERM_SESSION_ID` environment variable. Verify it's set:

```bash
echo $ITERM_SESSION_ID
```

If empty, you're not running in an iTerm2 terminal, or the Python API is not enabled.

### Git Notes Not Showing

Make sure you're using the correct ref:

```bash
git notes --ref=refs/notes/iterm-session list
```

If empty, the post-commit hook may not have run successfully.

### GitHub API Rate Limiting

Without authentication, GitHub API limits requests to 60 per hour. Set `GITHUB_TOKEN` to increase this to 5000 per hour:

```bash
export GITHUB_TOKEN="your_github_token"
```

## Security Considerations

- **Session IDs**: Session IDs are not sensitive but can identify which terminal created code
- **Git Notes**: Git notes can be pushed to remotes, be aware of what metadata you're sharing
- **GitHub Tokens**: Never commit GitHub tokens to the repository, use environment variables
- **Hooks**: Git hooks run local code, only install hooks from trusted sources

## Future Enhancements

- [ ] Real-time webhook handler for PR comments
- [ ] Integration with Air repository for notifications
- [ ] Email/messaging adapters for agent notifications
- [ ] Dashboard showing commit-to-session mapping
- [ ] Support for other terminal emulators (tmux, etc.)
- [ ] Cloud storage for session metadata (beyond git notes)

## References

- [Git Hooks Documentation](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks)
- [Git Notes Documentation](https://git-scm.com/docs/git-notes)
- [GitHub REST API - Pull Request Review Comments](https://docs.github.com/en/rest/pulls/comments)
- [iTerm2 Python API](https://iterm2.com/python-api/)
