"""Example: Integrating Git Session Tracking with Notification Routing

This example demonstrates how to use the git session tracking feature
to route GitHub PR comments back to the specific terminal session that
created the code.

Workflow:
1. Developer makes commits in terminal session A
2. Commits include session ID in git notes
3. PR is created and code review begins
4. Reviewer comments on specific lines of code
5. Webhook receives comment event
6. System looks up session ID from commit
7. Notification is routed to terminal session A

This enables the original author/agent to receive feedback directly
in their working environment.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.git_integration import (
    get_commit_from_review_comment,
    get_session_from_commit,
    CommitSessionMetadata,
)


# ============================================================================
# NOTIFICATION ROUTING
# ============================================================================

async def notify_terminal_session(
    session_id: str,
    message: str,
    metadata: Optional[CommitSessionMetadata] = None
) -> bool:
    """Send a notification to a specific terminal session.
    
    This would integrate with the iTerm MCP server to send messages
    to the terminal session.
    
    Args:
        session_id: The iTerm session ID to notify
        message: The notification message
        metadata: Optional session metadata for context
        
    Returns:
        True if notification sent successfully
    """
    # In a real implementation, this would use the iTerm MCP server
    # to send the message to the terminal session
    
    print(f"\n🔔 Notification to session {session_id}")
    print(f"   Message: {message}")
    if metadata:
        print(f"   Context: {metadata.username}@{metadata.hostname}")
        print(f"   Working Dir: {metadata.working_directory}")
    
    # TODO: Integrate with iTerm MCP server
    # from core.terminal import ItermTerminal
    # terminal = ItermTerminal()
    # session = terminal.get_session_by_id(session_id)
    # await session.send_text(f"\n\n🔔 PR Comment: {message}\n")
    
    return True


async def handle_pr_comment_webhook(
    owner: str,
    repo: str,
    review_comment_id: str,
    comment_body: str,
    repo_path: Optional[str] = None,
    github_token: Optional[str] = None,
) -> bool:
    """Handle a GitHub PR review comment webhook event.
    
    This is the main entry point for the webhook handler. It:
    1. Retrieves the commit SHA from the review comment
    2. Looks up the session metadata for that commit
    3. Routes the notification to the appropriate terminal session
    
    Args:
        owner: GitHub repository owner
        repo: GitHub repository name
        review_comment_id: The review comment ID
        comment_body: The text of the comment
        repo_path: Local path to the git repository
        github_token: GitHub API token for authentication
        
    Returns:
        True if notification was successfully routed
    """
    print(f"\n📬 Processing PR comment webhook")
    print(f"   Repository: {owner}/{repo}")
    print(f"   Comment ID: {review_comment_id}")
    
    # Step 1: Get commit SHA from GitHub
    commit_sha = get_commit_from_review_comment(
        owner=owner,
        repo=repo,
        review_comment_id=review_comment_id,
        github_token=github_token,
    )
    
    if not commit_sha:
        print("❌ Could not retrieve commit SHA from GitHub")
        return False
    
    print(f"✅ Found commit: {commit_sha[:8]}")
    
    # Step 2: Get session metadata from git notes
    metadata = get_session_from_commit(commit_sha, repo_path)
    
    if not metadata:
        print(f"⚠️  No session metadata found for commit {commit_sha}")
        print("   Comment will not be routed to terminal")
        return False
    
    print(f"✅ Found session: {metadata.session_id}")
    
    # Step 3: Route notification to terminal session
    notification_msg = f"PR Comment on commit {commit_sha[:8]}: {comment_body}"
    success = await notify_terminal_session(
        session_id=metadata.session_id,
        message=notification_msg,
        metadata=metadata,
    )
    
    if success:
        print("✅ Notification routed successfully")
    else:
        print("❌ Failed to route notification")
    
    return success


# ============================================================================
# EXAMPLE WEBHOOK SERVER (Simplified)
# ============================================================================

async def webhook_handler(request_data: dict) -> dict:
    """Simplified webhook handler for GitHub events.
    
    In a production system, this would be integrated with a web framework
    like FastAPI or Flask to receive webhook POST requests from GitHub.
    
    Args:
        request_data: The webhook payload from GitHub
        
    Returns:
        Response dictionary
    """
    # Extract relevant fields from GitHub webhook payload
    action = request_data.get("action")
    comment = request_data.get("comment", {})
    
    # Only handle created comments
    if action != "created":
        return {"status": "ignored", "reason": f"action={action}"}
    
    # Extract comment details
    review_comment_id = str(comment.get("id"))
    comment_body = comment.get("body")
    
    # Extract repository details
    repository = request_data.get("repository", {})
    owner = repository.get("owner", {}).get("login")
    repo = repository.get("name")
    
    if not all([owner, repo, review_comment_id, comment_body]):
        return {"status": "error", "reason": "missing required fields"}
    
    # Get GitHub token from environment
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_path = os.environ.get("REPO_PATH")
    
    # Handle the comment
    success = await handle_pr_comment_webhook(
        owner=owner,
        repo=repo,
        review_comment_id=review_comment_id,
        comment_body=comment_body,
        repo_path=repo_path,
        github_token=github_token,
    )
    
    return {
        "status": "success" if success else "failed",
        "comment_id": review_comment_id,
    }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def example_manual_notification():
    """Example: Manually route a notification to a session."""
    print("\n" + "="*60)
    print("Example 1: Manual Notification Routing")
    print("="*60)
    
    # Simulate having a commit SHA from a PR comment
    commit_sha = "abc123def456"  # Replace with actual commit
    
    # Look up session metadata
    metadata = get_session_from_commit(commit_sha)
    
    if metadata:
        # Route notification
        await notify_terminal_session(
            session_id=metadata.session_id,
            message="PR approved! Ready to merge.",
            metadata=metadata,
        )
    else:
        print(f"No session metadata found for commit {commit_sha}")


async def example_webhook_simulation():
    """Example: Simulate receiving a GitHub webhook."""
    print("\n" + "="*60)
    print("Example 2: Webhook Simulation")
    print("="*60)
    
    # Simulate a GitHub webhook payload
    webhook_payload = {
        "action": "created",
        "comment": {
            "id": 123456789,
            "body": "Great work! Just one small suggestion...",
        },
        "repository": {
            "owner": {"login": "research-developer"},
            "name": "iterm-mcp-claude-agency",
        },
    }
    
    # Process the webhook
    response = await webhook_handler(webhook_payload)
    print(f"\nWebhook response: {response}")


async def example_list_session_commits():
    """Example: List all commits from a specific session."""
    print("\n" + "="*60)
    print("Example 3: List Commits by Session")
    print("="*60)
    
    from core.git_integration import list_commits_with_session
    
    # Example session ID (replace with actual)
    session_id = "550e8400-e29b-41d4-a716-446655440000"
    
    # Find all commits from this session
    commits = list_commits_with_session(session_id)
    
    if commits:
        print(f"Found {len(commits)} commits from session {session_id}:")
        for sha, metadata in commits:
            print(f"  - {sha[:8]} at {metadata.timestamp}")
    else:
        print(f"No commits found for session {session_id}")


async def main():
    """Run all examples."""
    print("\n" + "="*60)
    print("Git Session Tracking Integration Examples")
    print("="*60)
    
    # Example 1: Manual notification
    await example_manual_notification()
    
    # Example 2: Webhook simulation
    await example_webhook_simulation()
    
    # Example 3: List commits by session
    await example_list_session_commits()
    
    print("\n" + "="*60)
    print("Examples complete!")
    print("="*60)
    print("\nNext steps:")
    print("1. Set up a GitHub webhook pointing to your server")
    print("2. Integrate with iTerm MCP server for actual notifications")
    print("3. Configure authentication and security")
    print("4. Deploy the webhook handler")


if __name__ == "__main__":
    asyncio.run(main())
