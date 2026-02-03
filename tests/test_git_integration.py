"""Tests for git integration utilities."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.git_integration import (
    CommitSessionMetadata,
    extract_commit_sha_from_pr_comment,
    get_commit_from_review_comment,
    get_current_commit_sha,
    get_git_remote_info,
    get_session_from_commit,
    is_git_repository,
    list_commits_with_session,
    store_session_in_commit,
)


class TestCommitSessionMetadata(unittest.TestCase):
    """Tests for CommitSessionMetadata dataclass."""
    
    def test_to_json(self):
        """Test serialization to JSON."""
        metadata = CommitSessionMetadata(
            session_id="550e8400-e29b-41d4-a716-446655440000",
            persistent_id="w0t0p0s0",
            timestamp="2024-01-28T12:00:00Z",
            hostname="dev-machine",
            username="developer",
            working_directory="/home/developer/project",
        )
        
        json_str = metadata.to_json()
        data = json.loads(json_str)
        
        self.assertEqual(data["session_id"], "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(data["persistent_id"], "w0t0p0s0")
        self.assertEqual(data["timestamp"], "2024-01-28T12:00:00Z")
        self.assertEqual(data["hostname"], "dev-machine")
        self.assertEqual(data["username"], "developer")
        self.assertEqual(data["working_directory"], "/home/developer/project")
    
    def test_from_json(self):
        """Test deserialization from JSON."""
        json_str = json.dumps({
            "session_id": "550e8400-e29b-41d4-a716-446655440000",
            "persistent_id": "w0t0p0s0",
            "timestamp": "2024-01-28T12:00:00Z",
            "hostname": "dev-machine",
            "username": "developer",
            "working_directory": "/home/developer/project",
        })
        
        metadata = CommitSessionMetadata.from_json(json_str)
        
        self.assertEqual(metadata.session_id, "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(metadata.persistent_id, "w0t0p0s0")
        self.assertEqual(metadata.timestamp, "2024-01-28T12:00:00Z")
        self.assertEqual(metadata.hostname, "dev-machine")
        self.assertEqual(metadata.username, "developer")
        self.assertEqual(metadata.working_directory, "/home/developer/project")
    
    def test_roundtrip(self):
        """Test serialization and deserialization roundtrip."""
        original = CommitSessionMetadata(
            session_id="550e8400-e29b-41d4-a716-446655440000",
            persistent_id="w0t0p0s0",
            timestamp="2024-01-28T12:00:00Z",
        )
        
        json_str = original.to_json()
        restored = CommitSessionMetadata.from_json(json_str)
        
        self.assertEqual(original.session_id, restored.session_id)
        self.assertEqual(original.persistent_id, restored.persistent_id)
        self.assertEqual(original.timestamp, restored.timestamp)


class TestGitHubIntegration(unittest.TestCase):
    """Tests for GitHub PR comment integration."""
    
    def test_extract_commit_sha_from_pr_comment(self):
        """Test extracting review comment ID from URL."""
        url = "https://github.com/owner/repo/pull/123#discussion_r456789012"
        comment_id = extract_commit_sha_from_pr_comment(url)
        
        self.assertEqual(comment_id, "456789012")
    
    def test_extract_commit_sha_invalid_url(self):
        """Test with invalid URL."""
        url = "https://github.com/owner/repo/pull/123"
        comment_id = extract_commit_sha_from_pr_comment(url)
        
        self.assertIsNone(comment_id)
    
    @patch('subprocess.run')
    def test_get_commit_from_review_comment(self, mock_run):
        """Test getting commit SHA from review comment via API."""
        # Mock successful API response
        mock_response = {
            "original_commit_id": "abc123def456",
            "path": "src/file.py",
            "body": "Please fix this",
        }
        mock_run.return_value = MagicMock(
            stdout=json.dumps(mock_response),
            returncode=0,
        )
        
        commit_sha = get_commit_from_review_comment(
            owner="testowner",
            repo="testrepo",
            review_comment_id="123456",
            github_token="fake_token",
        )
        
        self.assertEqual(commit_sha, "abc123def456")
        
        # Verify curl was called with correct arguments
        call_args = mock_run.call_args[0][0]
        self.assertIn("curl", call_args)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/pulls/comments/123456", call_args)


class TestGitRepositoryUtils(unittest.TestCase):
    """Tests for git repository utilities."""
    
    def setUp(self):
        """Create a temporary git repository for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        
        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        # Configure git
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
    
    def tearDown(self):
        """Clean up temporary repository."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_is_git_repository(self):
        """Test checking if path is a git repository."""
        self.assertTrue(is_git_repository(str(self.repo_path)))
        
        # Test non-git directory (create outside temp_dir to avoid being within the git repo)
        import tempfile
        non_git_dir = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(is_git_repository(str(non_git_dir)))
        finally:
            import shutil
            shutil.rmtree(non_git_dir, ignore_errors=True)
    
    def test_get_current_commit_sha(self):
        """Test getting current commit SHA."""
        # Create a commit
        test_file = self.repo_path / "test.txt"
        test_file.write_text("test content")
        
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        # Get commit SHA
        commit_sha = get_current_commit_sha(str(self.repo_path))
        self.assertIsNotNone(commit_sha)
        self.assertEqual(len(commit_sha), 40)  # SHA-1 hash length
    
    def test_get_git_remote_info_https(self):
        """Test parsing GitHub remote URL (HTTPS format)."""
        # Add a GitHub remote
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/testowner/testrepo.git"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        owner, repo = get_git_remote_info(str(self.repo_path))
        self.assertEqual(owner, "testowner")
        self.assertEqual(repo, "testrepo")
    
    def test_get_git_remote_info_ssh(self):
        """Test parsing GitHub remote URL (SSH format)."""
        # Add a GitHub remote
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:testowner/testrepo.git"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        owner, repo = get_git_remote_info(str(self.repo_path))
        self.assertEqual(owner, "testowner")
        self.assertEqual(repo, "testrepo")


class TestGitNotesIntegration(unittest.TestCase):
    """Tests for git notes storage and retrieval."""
    
    def setUp(self):
        """Create a temporary git repository for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        
        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        # Configure git
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        # Create a test commit
        test_file = self.repo_path / "test.txt"
        test_file.write_text("test content")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        # Get commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        self.commit_sha = result.stdout.strip()
    
    def tearDown(self):
        """Clean up temporary repository."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_store_and_retrieve_session(self):
        """Test storing and retrieving session metadata."""
        metadata = CommitSessionMetadata(
            session_id="550e8400-e29b-41d4-a716-446655440000",
            persistent_id="w0t0p0s0",
            timestamp="2024-01-28T12:00:00Z",
            hostname="test-machine",
            username="testuser",
        )
        
        # Store metadata
        success = store_session_in_commit(self.commit_sha, metadata, str(self.repo_path))
        self.assertTrue(success)
        
        # Retrieve metadata
        retrieved = get_session_from_commit(self.commit_sha, str(self.repo_path))
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.session_id, metadata.session_id)
        self.assertEqual(retrieved.persistent_id, metadata.persistent_id)
        self.assertEqual(retrieved.timestamp, metadata.timestamp)
        self.assertEqual(retrieved.hostname, metadata.hostname)
        self.assertEqual(retrieved.username, metadata.username)
    
    def test_list_commits_with_session(self):
        """Test finding commits by session ID."""
        session_id = "550e8400-e29b-41d4-a716-446655440000"
        
        # Store metadata for the commit
        metadata = CommitSessionMetadata(
            session_id=session_id,
            persistent_id="w0t0p0s0",
            timestamp="2024-01-28T12:00:00Z",
        )
        store_session_in_commit(self.commit_sha, metadata, str(self.repo_path))
        
        # Create another commit with different session
        test_file = self.repo_path / "test2.txt"
        test_file.write_text("test content 2")
        subprocess.run(
            ["git", "add", "test2.txt"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Second commit"],
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        commit2_sha = result.stdout.strip()
        
        metadata2 = CommitSessionMetadata(
            session_id="different-session-id",
            persistent_id="w0t1p0s0",
            timestamp="2024-01-28T13:00:00Z",
        )
        store_session_in_commit(commit2_sha, metadata2, str(self.repo_path))
        
        # List commits for the first session
        commits = list_commits_with_session(session_id, str(self.repo_path))
        
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0][0], self.commit_sha)
        self.assertEqual(commits[0][1].session_id, session_id)


if __name__ == "__main__":
    unittest.main()
