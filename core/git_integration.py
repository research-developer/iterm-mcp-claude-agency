"""Git integration utilities for linking commits to terminal sessions.

This module provides utilities for:
- Capturing iTerm session IDs in git commits
- Storing session metadata using git notes
- Retrieving commit information from GitHub PR comments
- Linking commits to terminal sessions for notifications
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("iterm-mcp.git_integration")


# ============================================================================
# GITHUB PR COMMENT UTILITIES
# ============================================================================

def extract_commit_sha_from_pr_comment(comment_url: str) -> Optional[str]:
    """Extract commit SHA from a GitHub PR comment URL.
    
    GitHub PR comments on code include the commit SHA in the URL path:
    https://github.com/owner/repo/pull/123#discussion_r456789012
    
    The comment is associated with a specific commit in the PR, which can
    be retrieved via the GitHub API using the comment's review ID.
    
    Args:
        comment_url: GitHub PR comment URL
        
    Returns:
        Commit SHA if extractable, None otherwise
        
    Example:
        >>> extract_commit_sha_from_pr_comment(
        ...     "https://github.com/owner/repo/pull/123#discussion_r456789012"
        ... )
        # This would require GitHub API call to resolve, see get_commit_from_review_comment
    """
    # Extract review comment ID from URL
    match = re.search(r'discussion_r(\d+)', comment_url)
    if match:
        review_comment_id = match.group(1)
        logger.info(f"Extracted review comment ID: {review_comment_id}")
        return review_comment_id
    return None


def get_commit_from_review_comment(
    owner: str,
    repo: str,
    review_comment_id: str,
    github_token: Optional[str] = None
) -> Optional[str]:
    """Get the commit SHA associated with a PR review comment.
    
    Uses GitHub API to retrieve the commit SHA for a specific review comment.
    This allows tracking which commit a PR comment was made on.
    
    Args:
        owner: Repository owner
        repo: Repository name
        review_comment_id: GitHub review comment ID
        github_token: GitHub API token (optional, uses GITHUB_TOKEN env var if not provided)
        
    Returns:
        Commit SHA if found, None otherwise
        
    Example:
        >>> sha = get_commit_from_review_comment("owner", "repo", "456789012")
        >>> print(f"Comment made on commit: {sha}")
    """
    token = github_token or os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.warning("No GitHub token provided, API calls may be rate limited")
    
    # GitHub API endpoint for review comments
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/comments/{review_comment_id}"
    
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        # Use subprocess to call curl (avoiding external dependencies)
        cmd = ["curl", "-s", "-H", f"Accept: {headers['Accept']}"]
        if token:
            cmd.extend(["-H", f"Authorization: {headers['Authorization']}"])
        cmd.append(url)
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # The 'original_commit_id' field contains the commit SHA
        commit_sha = data.get("original_commit_id")
        if commit_sha:
            logger.info(f"Found commit SHA {commit_sha} for review comment {review_comment_id}")
            return commit_sha
        
        logger.warning(f"No commit SHA found in response: {data}")
        return None
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to get commit from review comment: {e}")
        return None


# ============================================================================
# GIT NOTES FOR SESSION ID STORAGE
# ============================================================================

GIT_NOTES_REF = "refs/notes/iterm-session"


@dataclass
class CommitSessionMetadata:
    """Metadata about terminal session associated with a commit."""
    
    session_id: str
    """iTerm session ID (UUID format)"""
    
    persistent_id: str
    """iTerm persistent session ID for reconnection"""
    
    timestamp: str
    """ISO 8601 timestamp when commit was created"""
    
    hostname: Optional[str] = None
    """Hostname where commit was created"""
    
    username: Optional[str] = None
    """Username who created the commit"""
    
    working_directory: Optional[str] = None
    """Working directory at commit time"""
    
    def to_json(self) -> str:
        """Serialize to JSON for storage in git notes."""
        data = {
            "session_id": self.session_id,
            "persistent_id": self.persistent_id,
            "timestamp": self.timestamp,
        }
        if self.hostname:
            data["hostname"] = self.hostname
        if self.username:
            data["username"] = self.username
        if self.working_directory:
            data["working_directory"] = self.working_directory
        return json.dumps(data, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> "CommitSessionMetadata":
        """Deserialize from JSON stored in git notes."""
        data = json.loads(json_str)
        return cls(
            session_id=data["session_id"],
            persistent_id=data["persistent_id"],
            timestamp=data["timestamp"],
            hostname=data.get("hostname"),
            username=data.get("username"),
            working_directory=data.get("working_directory"),
        )


def store_session_in_commit(
    commit_sha: str,
    session_metadata: CommitSessionMetadata,
    repo_path: Optional[str] = None
) -> bool:
    """Store session metadata in git notes for a commit.
    
    Uses git notes with a custom ref to avoid conflicts with default notes.
    
    Args:
        commit_sha: Git commit SHA
        session_metadata: Session metadata to store
        repo_path: Path to git repository (uses current dir if not provided)
        
    Returns:
        True if successful, False otherwise
        
    Example:
        >>> metadata = CommitSessionMetadata(
        ...     session_id="550e8400-e29b-41d4-a716-446655440000",
        ...     persistent_id="w0t0p0s0",
        ...     timestamp="2024-01-28T12:00:00Z"
        ... )
        >>> store_session_in_commit("abc123", metadata)
        True
    """
    try:
        cmd = ["git", "notes", "--ref", GIT_NOTES_REF, "add", "-f", "-m", 
               session_metadata.to_json(), commit_sha]
        
        if repo_path:
            cmd = ["git", "-C", repo_path] + cmd[1:]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"Stored session metadata for commit {commit_sha}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to store session in git notes: {e.stderr}")
        return False


def get_session_from_commit(
    commit_sha: str,
    repo_path: Optional[str] = None
) -> Optional[CommitSessionMetadata]:
    """Retrieve session metadata from git notes for a commit.
    
    Args:
        commit_sha: Git commit SHA
        repo_path: Path to git repository (uses current dir if not provided)
        
    Returns:
        CommitSessionMetadata if found, None otherwise
        
    Example:
        >>> metadata = get_session_from_commit("abc123")
        >>> if metadata:
        ...     print(f"Session ID: {metadata.session_id}")
    """
    try:
        cmd = ["git", "notes", "--ref", GIT_NOTES_REF, "show", commit_sha]
        
        if repo_path:
            cmd = ["git", "-C", repo_path] + cmd[1:]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata = CommitSessionMetadata.from_json(result.stdout)
        logger.info(f"Retrieved session metadata for commit {commit_sha}")
        return metadata
        
    except subprocess.CalledProcessError:
        logger.debug(f"No session metadata found for commit {commit_sha}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse session metadata: {e}")
        return None


def list_commits_with_session(
    session_id: str,
    repo_path: Optional[str] = None,
    max_commits: int = 100
) -> List[Tuple[str, CommitSessionMetadata]]:
    """Find all commits associated with a specific session ID.
    
    Args:
        session_id: iTerm session ID to search for
        repo_path: Path to git repository (uses current dir if not provided)
        max_commits: Maximum number of commits to check
        
    Returns:
        List of (commit_sha, metadata) tuples
        
    Example:
        >>> commits = list_commits_with_session("550e8400-e29b-41d4-a716-446655440000")
        >>> for sha, metadata in commits:
        ...     print(f"{sha}: {metadata.timestamp}")
    """
    results = []
    
    try:
        # Get list of recent commits
        cmd = ["git", "log", f"--max-count={max_commits}", "--pretty=format:%H"]
        if repo_path:
            cmd = ["git", "-C", repo_path] + cmd[1:]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        commit_shas = result.stdout.strip().split('\n')
        
        # Check each commit for session metadata
        for sha in commit_shas:
            metadata = get_session_from_commit(sha, repo_path)
            if metadata and metadata.session_id == session_id:
                results.append((sha, metadata))
        
        logger.info(f"Found {len(results)} commits for session {session_id}")
        return results
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to list commits: {e.stderr}")
        return []


# ============================================================================
# ITERM SESSION ID RETRIEVAL
# ============================================================================

async def get_current_iterm_session_id() -> Optional[str]:
    """Get the session ID of the current iTerm terminal.
    
    This function uses the iTerm2 Python API to retrieve the session ID
    of the terminal where it's executed. Intended for use in git hooks.
    
    Returns:
        Session ID if in iTerm, None otherwise
        
    Example:
        >>> session_id = await get_current_iterm_session_id()
        >>> print(f"Current session: {session_id}")
    """
    try:
        import iterm2
        
        # Connect to iTerm2
        connection = await iterm2.Connection.async_create()
        app = await iterm2.async_get_app(connection)
        
        # Get the current terminal session
        # The ITERM_SESSION_ID environment variable is set by iTerm2
        iterm_session_id = os.environ.get("ITERM_SESSION_ID")
        if not iterm_session_id:
            logger.warning("Not running in iTerm2 (ITERM_SESSION_ID not set)")
            return None
        
        # Find the session with matching ID
        for window in app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == iterm_session_id:
                        # Return the session ID (UUID format)
                        return session.session_id
        
        logger.warning(f"Could not find session with ID {iterm_session_id}")
        return None
        
    except ImportError:
        logger.warning("iterm2 module not available")
        return None
    except Exception as e:
        logger.error(f"Failed to get iTerm session ID: {e}")
        return None


def get_current_iterm_session_id_sync() -> Optional[str]:
    """Synchronous wrapper for get_current_iterm_session_id().
    
    Returns:
        Session ID if in iTerm, None otherwise
    """
    try:
        # Try to get from environment variable first (simple case)
        session_id = os.environ.get("ITERM_SESSION_ID")
        if session_id:
            logger.info(f"Got iTerm session ID from environment: {session_id}")
            return session_id
        
        # If not available, try async method
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(get_current_iterm_session_id())
            return result
        finally:
            loop.close()
            
    except Exception as e:
        logger.error(f"Failed to get iTerm session ID: {e}")
        return None


# ============================================================================
# GIT REPOSITORY UTILITIES
# ============================================================================

def get_current_commit_sha(repo_path: Optional[str] = None) -> Optional[str]:
    """Get the current commit SHA (HEAD).
    
    Args:
        repo_path: Path to git repository (uses current dir if not provided)
        
    Returns:
        Commit SHA if in a git repo, None otherwise
    """
    try:
        cmd = ["git", "rev-parse", "HEAD"]
        if repo_path:
            cmd = ["git", "-C", repo_path] + cmd[1:]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
        
    except subprocess.CalledProcessError:
        return None


def get_git_remote_info(repo_path: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Get GitHub owner and repo name from git remote.
    
    Args:
        repo_path: Path to git repository (uses current dir if not provided)
        
    Returns:
        Tuple of (owner, repo) if GitHub remote found, None otherwise
    """
    try:
        cmd = ["git", "remote", "get-url", "origin"]
        if repo_path:
            cmd = ["git", "-C", repo_path] + cmd[1:]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        remote_url = result.stdout.strip()
        
        # Parse GitHub URL (both HTTPS and SSH formats)
        # HTTPS: https://github.com/owner/repo.git
        # SSH: git@github.com:owner/repo.git
        match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(\.git)?$', remote_url)
        if match:
            owner = match.group(1)
            repo = match.group(2)
            return (owner, repo)
        
        return None
        
    except subprocess.CalledProcessError:
        return None


def is_git_repository(path: Optional[str] = None) -> bool:
    """Check if a path is within a git repository.
    
    Args:
        path: Path to check (uses current dir if not provided)
        
    Returns:
        True if in a git repository, False otherwise
    """
    try:
        cmd = ["git", "rev-parse", "--git-dir"]
        if path:
            cmd = ["git", "-C", path] + cmd[1:]
        
        subprocess.run(cmd, capture_output=True, check=True)
        return True
        
    except subprocess.CalledProcessError:
        return False
