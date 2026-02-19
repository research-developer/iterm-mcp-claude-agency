#!/usr/bin/env python3
"""CLI tool to query iTerm session information from git commits.

This tool provides commands to:
- Show session metadata for a commit
- List commits made in a specific session
- Find the session ID for a commit from a GitHub PR comment

Usage:
    # Show session info for a commit
    python scripts/query-session.py show <commit-sha>
    
    # List commits from a specific session
    python scripts/query-session.py list-session <session-id>
    
    # Get commit SHA from GitHub PR comment
    python scripts/query-session.py from-github <owner> <repo> <comment-id>
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.git_integration import (
    get_session_from_commit,
    list_commits_with_session,
    get_commit_from_review_comment,
    get_git_remote_info,
)


def cmd_show(args):
    """Show session metadata for a commit."""
    metadata = get_session_from_commit(args.commit_sha, args.repo)
    
    if not metadata:
        print(f"❌ No session metadata found for commit {args.commit_sha}")
        return 1
    
    print(f"📱 Session Information for commit {args.commit_sha[:8]}")
    print(f"   Session ID: {metadata.session_id}")
    print(f"   Persistent ID: {metadata.persistent_id}")
    print(f"   Timestamp: {metadata.timestamp}")
    if metadata.hostname:
        print(f"   Hostname: {metadata.hostname}")
    if metadata.username:
        print(f"   Username: {metadata.username}")
    if metadata.working_directory:
        print(f"   Working Dir: {metadata.working_directory}")
    
    if args.json:
        print("\nJSON:")
        print(metadata.to_json())
    
    return 0


def cmd_list_session(args):
    """List all commits from a specific session."""
    commits = list_commits_with_session(args.session_id, args.repo, args.max_commits)
    
    if not commits:
        print(f"❌ No commits found for session {args.session_id}")
        return 1
    
    print(f"📚 Commits from session {args.session_id}:")
    print(f"   Found {len(commits)} commits\n")
    
    for sha, metadata in commits:
        print(f"   {sha[:8]} - {metadata.timestamp}")
        if args.verbose:
            print(f"      Working Dir: {metadata.working_directory}")
    
    return 0


def cmd_from_github(args):
    """Get commit SHA and session info from GitHub PR review comment."""
    # Get commit SHA from GitHub API
    commit_sha = get_commit_from_review_comment(
        args.owner, args.repo, args.comment_id, args.github_token
    )
    
    if not commit_sha:
        print(f"❌ Could not find commit for review comment {args.comment_id}")
        return 1
    
    print(f"✅ Found commit {commit_sha[:8]} for review comment {args.comment_id}")
    
    # Get session metadata for that commit
    metadata = get_session_from_commit(commit_sha, args.repo_path)
    
    if not metadata:
        print(f"⚠️  No session metadata found for commit {commit_sha}")
        return 0
    
    print(f"\n📱 Session Information:")
    print(f"   Session ID: {metadata.session_id}")
    print(f"   Persistent ID: {metadata.persistent_id}")
    print(f"   Timestamp: {metadata.timestamp}")
    if metadata.hostname:
        print(f"   Hostname: {metadata.hostname}")
    if metadata.username:
        print(f"   Username: {metadata.username}")
    
    return 0


def cmd_remote_info(args):
    """Show GitHub remote information for the repository."""
    remote_info = get_git_remote_info(args.repo)
    
    if not remote_info:
        print("❌ No GitHub remote found for this repository")
        return 1
    
    owner, repo = remote_info
    print(f"🔗 GitHub Repository Information:")
    print(f"   Owner: {owner}")
    print(f"   Repo: {repo}")
    print(f"   URL: https://github.com/{owner}/{repo}")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Query iTerm session information from git commits"
    )
    parser.add_argument(
        "--repo", "-r",
        help="Path to git repository (default: current directory)",
        default=None,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Show command
    show_parser = subparsers.add_parser("show", help="Show session info for a commit")
    show_parser.add_argument("commit_sha", help="Commit SHA to query")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # List session command
    list_parser = subparsers.add_parser("list-session", help="List commits from a session")
    list_parser.add_argument("session_id", help="Session ID to search for")
    list_parser.add_argument("--max-commits", type=int, default=100, help="Max commits to check")
    list_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    # GitHub command
    github_parser = subparsers.add_parser(
        "from-github",
        help="Get commit and session info from GitHub PR comment"
    )
    github_parser.add_argument("owner", help="GitHub repository owner")
    github_parser.add_argument("repo", help="GitHub repository name")
    github_parser.add_argument("comment_id", help="GitHub review comment ID")
    github_parser.add_argument("--github-token", help="GitHub API token")
    github_parser.add_argument("--repo-path", help="Path to local git repository")
    
    # Remote info command
    remote_parser = subparsers.add_parser("remote-info", help="Show GitHub remote info")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Execute the appropriate command
    if args.command == "show":
        return cmd_show(args)
    elif args.command == "list-session":
        return cmd_list_session(args)
    elif args.command == "from-github":
        return cmd_from_github(args)
    elif args.command == "remote-info":
        return cmd_remote_info(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
