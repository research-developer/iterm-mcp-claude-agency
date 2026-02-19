"""
Secret Redaction for Sensitive Data Filtering.

This module implements pattern-based sensitive data filtering in terminal output.
See: research/RESEARCH_SYNTHESIS_ADDENDUM.md and EPIC_PROPOSAL Sub-Issue 7.

SUGGESTED IMPLEMENTATION - Code review comments inline.

Example usage:
    ```python
    redactor = SecretRedactor()
    clean_output = redactor.redact(terminal_output)
    # API keys, passwords, tokens replaced with [REDACTED]
    ```
"""
import re
from typing import List, Optional, Pattern, Tuple
from dataclasses import dataclass, field


# Default patterns for common secrets
# REVIEW: These patterns may need tuning based on false positive rates
DEFAULT_PATTERNS = [
    # API Keys (generic)
    (r'(?i)api[_-]?key["\s:=]+["\']?([a-zA-Z0-9_-]{20,})["\']?', "API_KEY"),

    # Secrets (generic)
    (r'(?i)secret["\s:=]+["\']?([a-zA-Z0-9_-]{20,})["\']?', "SECRET"),

    # Passwords
    (r'(?i)password["\s:=]+["\']?([^\s"\']{8,})["\']?', "PASSWORD"),

    # Tokens (generic)
    (r'(?i)token["\s:=]+["\']?([a-zA-Z0-9_.-]{20,})["\']?', "TOKEN"),

    # Bearer tokens
    (r'(?i)bearer\s+([a-zA-Z0-9_.-]{20,})', "BEARER_TOKEN"),

    # AWS Access Key ID (starts with AKIA)
    (r'(?i)aws_access_key_id["\s:=]+([A-Z0-9]{20})', "AWS_ACCESS_KEY"),
    (r'\b(AKIA[A-Z0-9]{16})\b', "AWS_ACCESS_KEY"),

    # AWS Secret Access Key
    (r'(?i)aws_secret_access_key["\s:=]+([a-zA-Z0-9/+=]{40})', "AWS_SECRET_KEY"),

    # GitHub tokens
    (r'\b(ghp_[a-zA-Z0-9]{36})\b', "GITHUB_PAT"),
    (r'\b(github_pat_[a-zA-Z0-9_]{22,})\b', "GITHUB_PAT"),

    # OpenAI API keys
    (r'\b(sk-[a-zA-Z0-9]{48})\b', "OPENAI_KEY"),

    # Anthropic API keys
    (r'\b(sk-ant-[a-zA-Z0-9-]{80,})\b', "ANTHROPIC_KEY"),

    # Slack tokens
    (r'\b(xox[baprs]-[a-zA-Z0-9-]{10,})\b', "SLACK_TOKEN"),

    # Private keys
    (r'-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----', "PRIVATE_KEY"),

    # Connection strings
    (r'(?i)(mongodb(\+srv)?://[^\s]+)', "CONNECTION_STRING"),
    (r'(?i)(postgres://[^\s]+)', "CONNECTION_STRING"),
    (r'(?i)(mysql://[^\s]+)', "CONNECTION_STRING"),
    (r'(?i)(redis://[^\s]+)', "CONNECTION_STRING"),

    # JWT tokens (basic detection)
    (r'\b(eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,})\b', "JWT"),
]

# Patterns that are often false positives - used more carefully
# REVIEW: Consider making these opt-in rather than default
OPTIONAL_PATTERNS = [
    # Email addresses (may be intentional in logs)
    (r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b', "EMAIL"),

    # IP addresses (may be intentional)
    (r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', "IP_ADDRESS"),

    # Phone numbers (various formats)
    (r'\b(\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b', "PHONE"),
]


@dataclass
class RedactionResult:
    """Result of a redaction operation."""
    original_length: int
    redacted_length: int
    redactions_made: int
    redaction_types: List[str] = field(default_factory=list)


class SecretRedactor:
    """
    Pattern-based secret redactor for terminal output.

    # REVIEW: Consider adding support for custom replacement templates
    # REVIEW: Should we log redaction events for audit purposes?
    # SUGGESTION: Add whitelist patterns for known-safe content
    """

    def __init__(
        self,
        patterns: Optional[List[Tuple[str, str]]] = None,
        include_optional: bool = False,
        custom_patterns: Optional[List[Tuple[str, str]]] = None,
        replacement_template: str = "[{type}_REDACTED]"
    ):
        """
        Initialize the SecretRedactor.

        Args:
            patterns: Override default patterns (list of (regex, type_name) tuples)
            include_optional: Include optional patterns (email, IP, phone)
            custom_patterns: Additional patterns to include
            replacement_template: Template for replacement text, {type} is replaced
        """
        self.replacement_template = replacement_template
        self.patterns: List[Tuple[Pattern, str]] = []

        # Add patterns
        pattern_list = patterns or DEFAULT_PATTERNS
        if include_optional:
            pattern_list = pattern_list + OPTIONAL_PATTERNS
        if custom_patterns:
            pattern_list = pattern_list + custom_patterns

        for pattern_str, type_name in pattern_list:
            try:
                self.patterns.append((re.compile(pattern_str), type_name))
            except re.error as e:
                # REVIEW: Should we raise or just log and skip?
                print(f"Warning: Invalid pattern '{pattern_str}': {e}")

    def redact(
        self,
        text: str,
        replacement: Optional[str] = None
    ) -> str:
        """
        Redact sensitive data from text.

        Args:
            text: Text to redact
            replacement: Override replacement text (None uses template)

        Returns:
            Text with sensitive data replaced
        """
        result = text
        for pattern, type_name in self.patterns:
            if replacement:
                result = pattern.sub(replacement, result)
            else:
                repl = self.replacement_template.format(type=type_name)
                result = pattern.sub(repl, result)
        return result

    def redact_with_stats(
        self,
        text: str,
        replacement: Optional[str] = None
    ) -> Tuple[str, RedactionResult]:
        """
        Redact sensitive data and return statistics.

        # REVIEW: This is useful for auditing but adds overhead
        # SUGGESTION: Make stats collection optional

        Args:
            text: Text to redact
            replacement: Override replacement text

        Returns:
            Tuple of (redacted_text, RedactionResult)
        """
        result = text
        redaction_types = []
        total_redactions = 0

        for pattern, type_name in self.patterns:
            matches = pattern.findall(text)
            if matches:
                redaction_types.append(type_name)
                total_redactions += len(matches)

            if replacement:
                result = pattern.sub(replacement, result)
            else:
                repl = self.replacement_template.format(type=type_name)
                result = pattern.sub(repl, result)

        stats = RedactionResult(
            original_length=len(text),
            redacted_length=len(result),
            redactions_made=total_redactions,
            redaction_types=redaction_types
        )
        return result, stats

    def add_pattern(self, pattern: str, type_name: str):
        """
        Add a custom pattern at runtime.

        Args:
            pattern: Regex pattern string
            type_name: Name for the secret type (used in replacement)
        """
        self.patterns.append((re.compile(pattern), type_name))

    def has_secrets(self, text: str) -> bool:
        """
        Check if text contains any secrets without redacting.

        # REVIEW: More efficient than redact() for validation-only use cases

        Args:
            text: Text to check

        Returns:
            True if any secret patterns match
        """
        for pattern, _ in self.patterns:
            if pattern.search(text):
                return True
        return False

    def find_secrets(self, text: str) -> List[Tuple[str, str, int, int]]:
        """
        Find all secrets in text with positions.

        # REVIEW: Useful for highlighting in UI or detailed audit logs

        Args:
            text: Text to scan

        Returns:
            List of (matched_text, type_name, start_pos, end_pos)
        """
        secrets = []
        for pattern, type_name in self.patterns:
            for match in pattern.finditer(text):
                secrets.append((
                    match.group(0),
                    type_name,
                    match.start(),
                    match.end()
                ))
        return sorted(secrets, key=lambda x: x[2])


# Global redactor instance (can be configured at startup)
# REVIEW: Consider making this configurable via environment variables
_global_redactor: Optional[SecretRedactor] = None


def get_redactor() -> SecretRedactor:
    """Get or create the global redactor instance."""
    global _global_redactor
    if _global_redactor is None:
        _global_redactor = SecretRedactor()
    return _global_redactor


def configure_redactor(**kwargs):
    """Configure the global redactor instance."""
    global _global_redactor
    _global_redactor = SecretRedactor(**kwargs)


# =============================================================================
# Integration with Session Output
# =============================================================================
#
# To integrate with core/session.py, modify get_screen_contents():
#
# async def get_screen_contents(self, ..., redact_secrets: bool = False) -> str:
#     output = await self._get_raw_screen_contents(...)
#     if redact_secrets:
#         from core.redaction import get_redactor
#         output = get_redactor().redact(output)
#     return output


# =============================================================================
# MCP Tool Integration (to be added to fastmcp_server.py)
# =============================================================================
#
# @mcp.tool()
# async def configure_redaction(
#     enabled: bool = True,
#     additional_patterns: Optional[List[Tuple[str, str]]] = None,
#     include_optional: bool = False,
#     replacement_template: str = "[{type}_REDACTED]"
# ) -> dict:
#     """
#     Configure secret redaction settings.
#
#     Args:
#         enabled: Whether to enable automatic redaction
#         additional_patterns: Custom patterns as (regex, type_name) tuples
#         include_optional: Include email, IP, phone patterns
#         replacement_template: Template for replacement text
#
#     Returns:
#         {"success": True, "pattern_count": N}
#     """
#     pass
#
# @mcp.tool()
# async def redact_text(text: str) -> dict:
#     """
#     Manually redact sensitive data from text.
#
#     Args:
#         text: Text to redact
#
#     Returns:
#         {
#             "redacted": "...",
#             "redactions_made": N,
#             "types_found": ["API_KEY", "PASSWORD", ...]
#         }
#     """
#     pass
#
# @mcp.tool()
# async def check_for_secrets(text: str) -> dict:
#     """
#     Check if text contains secrets without redacting.
#
#     Args:
#         text: Text to check
#
#     Returns:
#         {
#             "has_secrets": True/False,
#             "secrets_found": [
#                 {"type": "API_KEY", "position": [10, 50]},
#                 ...
#             ]
#         }
#     """
#     pass
