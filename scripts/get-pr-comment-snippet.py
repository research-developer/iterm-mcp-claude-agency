#!/usr/bin/env python3
"""Snippet extractor for GitHub PR comments.

This script demonstrates how to extract the code snippet that a PR comment
references. When someone comments on a specific line in a PR, you can retrieve:
1. The commit SHA where the comment was made
2. The file path
3. The line number(s)
4. The actual code snippet

This answers the question: "When a comment is made in GitHub on a PR, 
is it possible to automatically know which commit that code came from?"

Answer: Yes! GitHub PR review comments include the commit SHA, file path, 
and line numbers in the API response.

Usage:
    export GITHUB_TOKEN="your_token"
    python scripts/get-pr-comment-snippet.py owner repo comment_id
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any


def get_review_comment_details(
    owner: str,
    repo: str,
    comment_id: str,
    github_token: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Get full details of a PR review comment.
    
    Args:
        owner: Repository owner
        repo: Repository name
        comment_id: Review comment ID
        github_token: GitHub API token
        
    Returns:
        Dictionary with comment details including:
        - original_commit_id: The commit SHA
        - path: The file path
        - original_line: The line number (or None)
        - original_start_line: Start line for multi-line comments
        - line: Current line number
        - body: Comment text
        - diff_hunk: The diff context
    """
    token = github_token or os.environ.get("GITHUB_TOKEN")
    
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/comments/{comment_id}"
    
    cmd = ["curl", "-s", "-H", "Accept: application/vnd.github.v3+json"]
    if token:
        cmd.extend(["-H", f"Authorization: token {token}"])
    cmd.append(url)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def get_file_content_at_commit(
    owner: str,
    repo: str,
    commit_sha: str,
    file_path: str,
    github_token: Optional[str] = None
) -> Optional[str]:
    """Get the content of a file at a specific commit.
    
    Args:
        owner: Repository owner
        repo: Repository name
        commit_sha: Commit SHA
        file_path: Path to file in repository
        github_token: GitHub API token
        
    Returns:
        File content as string
    """
    token = github_token or os.environ.get("GITHUB_TOKEN")
    
    # GitHub raw content URL
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/{file_path}"
    
    cmd = ["curl", "-s", "-L"]
    if token:
        cmd.extend(["-H", f"Authorization: token {token}"])
    cmd.append(url)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error fetching file: {e}", file=sys.stderr)
        return None


def extract_code_snippet(
    content: str,
    start_line: int,
    end_line: Optional[int] = None,
    context_lines: int = 3
) -> str:
    """Extract a code snippet from file content.
    
    Args:
        content: Full file content
        start_line: Starting line number (1-indexed)
        end_line: Ending line number (1-indexed), or None for single line
        context_lines: Number of context lines to include before/after
        
    Returns:
        Code snippet with line numbers
    """
    lines = content.split('\n')
    
    if end_line is None:
        end_line = start_line
    
    # Calculate range with context
    snippet_start = max(1, start_line - context_lines)
    snippet_end = min(len(lines), end_line + context_lines)
    
    # Build snippet with line numbers
    snippet_lines = []
    for i in range(snippet_start - 1, snippet_end):
        line_num = i + 1
        line_content = lines[i] if i < len(lines) else ""
        
        # Mark the commented lines
        if start_line <= line_num <= end_line:
            marker = ">>> "
        else:
            marker = "    "
        
        snippet_lines.append(f"{marker}{line_num:4d} | {line_content}")
    
    return "\n".join(snippet_lines)


def main():
    parser = argparse.ArgumentParser(
        description="Extract code snippet from GitHub PR comment"
    )
    parser.add_argument("owner", help="Repository owner")
    parser.add_argument("repo", help="Repository name")
    parser.add_argument("comment_id", help="PR review comment ID")
    parser.add_argument("--github-token", help="GitHub API token")
    parser.add_argument("--context", type=int, default=3, help="Lines of context")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    
    # Get comment details
    comment = get_review_comment_details(
        args.owner,
        args.repo,
        args.comment_id,
        args.github_token,
    )
    
    if not comment:
        print("Failed to retrieve comment details", file=sys.stderr)
        return 1
    
    # Extract key information
    commit_sha = comment.get("original_commit_id")
    file_path = comment.get("path")
    line = comment.get("original_line") or comment.get("line")
    start_line = comment.get("original_start_line")
    comment_body = comment.get("body")
    diff_hunk = comment.get("diff_hunk")
    
    if not all([commit_sha, file_path]):
        print("Missing required fields in comment", file=sys.stderr)
        return 1
    
    if args.json:
        # Output as JSON
        output = {
            "commit_sha": commit_sha,
            "file_path": file_path,
            "line": line,
            "start_line": start_line,
            "comment": comment_body,
            "diff_hunk": diff_hunk,
        }
        print(json.dumps(output, indent=2))
        return 0
    
    # Pretty print
    print("=" * 70)
    print("GitHub PR Comment Snippet")
    print("=" * 70)
    print(f"\n📍 Location:")
    print(f"   Commit:  {commit_sha}")
    print(f"   File:    {file_path}")
    if start_line:
        print(f"   Lines:   {start_line}-{line}")
    else:
        print(f"   Line:    {line}")
    
    print(f"\n💬 Comment:")
    print(f"   {comment_body}")
    
    print(f"\n📄 Code Context:")
    print(f"   (Lines marked with >>> are referenced by the comment)")
    print()
    
    # Try to get the actual file content
    content = get_file_content_at_commit(
        args.owner,
        args.repo,
        commit_sha,
        file_path,
        args.github_token,
    )
    
    if content:
        # Extract snippet
        snippet = extract_code_snippet(
            content,
            start_line or line,
            line,
            args.context,
        )
        print(snippet)
    else:
        # Fallback to diff hunk
        print("   Could not fetch file content, showing diff hunk:")
        print()
        for line in (diff_hunk or "").split('\n'):
            print(f"   {line}")
    
    print()
    print("=" * 70)
    
    # Show how to query session info
    print("\n🔍 To find which terminal session created this commit:")
    print(f"   python scripts/query-session.py show {commit_sha[:8]}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
