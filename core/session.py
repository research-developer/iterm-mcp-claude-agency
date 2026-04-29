"""Session management for iTerm2 interaction."""

import asyncio
import base64
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union, Callable, Literal

import iterm2

from utils.logging import ItermSessionLogger
from utils.otel import trace_operation, add_span_attributes, add_span_event


# Logger for session module
_logger = logging.getLogger("iterm-mcp-session")

# Cache time-to-live for CWD in seconds
CWD_CACHE_TTL_SECONDS = 30


@dataclass
class ExpectResult:
    """Result from an expect() operation.

    Attributes:
        matched_pattern: The pattern string, regex, or ExpectTimeout that matched
        match_index: Index of the matching pattern in the patterns list
        output: Full output captured up to and including the match
        matched_text: The specific text that matched the pattern
        before: Text before the match
        match: The regex match object (if pattern was regex)
    """
    matched_pattern: Union[str, re.Pattern, "ExpectTimeout"]
    match_index: int
    output: str
    matched_text: str
    before: str = ""
    match: Optional[re.Match] = None

    def __repr__(self) -> str:
        if isinstance(self.matched_pattern, re.Pattern):
            pattern_str = self.matched_pattern.pattern
        elif hasattr(self.matched_pattern, 'seconds'):
            # ExpectTimeout case
            pattern_str = repr(self.matched_pattern)
        else:
            pattern_str = self.matched_pattern
        return (
            f"ExpectResult(pattern={pattern_str!r}, index={self.match_index}, "
            f"matched_text={self.matched_text!r})"
        )


class ExpectTimeout:
    """Marker class for timeout in expect() pattern lists.

    Use this in the patterns list to specify a timeout that returns
    a specific index instead of raising an exception.

    Example:
        result = await session.expect([
            r'success',
            r'error',
            ExpectTimeout(30)  # Returns match_index=2 on timeout
        ])
    """
    def __init__(self, seconds: int = 30):
        """Initialize an ExpectTimeout marker.

        Args:
            seconds: Timeout duration in seconds
        """
        self.seconds = seconds

    def __repr__(self) -> str:
        return f"ExpectTimeout({self.seconds})"


class ExpectError(Exception):
    """Base exception for expect operations."""
    pass


class ExpectTimeoutError(ExpectError):
    """Raised when expect() times out without matching any pattern.

    This is only raised when no ExpectTimeout marker is in the patterns list.
    """
    def __init__(self, timeout: float, patterns: List[Union[str, re.Pattern, "ExpectTimeout"]], output: str):
        self.timeout = timeout
        self.patterns = patterns
        self.output = output
        pattern_strs = [
            p.pattern if isinstance(p, re.Pattern) else str(p)
            for p in patterns if not isinstance(p, ExpectTimeout)
        ]
        super().__init__(
            f"Timeout after {timeout}s waiting for patterns: {pattern_strs}"
        )

# Characters that can cause shell parsing issues when typed directly
# These require base64 encoding to safely execute
SHELL_UNSAFE_CHARS = set("\"'`$!\\|&;<>(){}[]")

# Simpler pattern for common safe commands (alphanumeric, spaces, basic punctuation)
SIMPLE_COMMAND_PATTERN = re.compile(r'^[a-zA-Z0-9_\-./: =,@#%^*+~]+$')

# Constants for text input delay calculation
BASE_DELAY_SECONDS = 0.05  # 50ms base delay
DELAY_PER_CHAR_SECONDS = 0.0001  # 0.1ms per character (1000 chars = 100ms extra)
MAX_DELAY_SECONDS = 3.0  # Maximum 3 second delay for very large pastes

# Thresholds for tiered delay calculation
LARGE_PASTE_THRESHOLD = 1000  # Characters above which we add extra delay
EXTRA_LARGE_PASTE_THRESHOLD = 5000  # Characters above which we add even more delay


def calculate_text_delay(text: str) -> float:
    """Calculate appropriate delay before sending Enter based on text length.

    For large text pastes, iTerm needs time to process the input buffer before
    the Enter key is sent. This function returns a delay that scales with text
    length to prevent race conditions where Enter is processed before the text.

    Uses a tiered approach:
    - Small pastes (<1000 chars): base + linear scaling
    - Large pastes (1000-5000 chars): additional 200ms buffer
    - Extra large pastes (>5000 chars): additional 500ms buffer

    Args:
        text: The text being sent to the terminal

    Returns:
        Delay in seconds (between BASE_DELAY_SECONDS and MAX_DELAY_SECONDS)
    """
    text_length = len(text)

    # Base linear scaling
    calculated_delay = BASE_DELAY_SECONDS + (text_length * DELAY_PER_CHAR_SECONDS)

    # Add tiered buffers for large pastes
    if text_length > EXTRA_LARGE_PASTE_THRESHOLD:
        calculated_delay += 0.5  # Extra 500ms for very large pastes
    elif text_length > LARGE_PASTE_THRESHOLD:
        calculated_delay += 0.2  # Extra 200ms for large pastes

    return min(calculated_delay, MAX_DELAY_SECONDS)


def needs_base64_encoding(command: str) -> bool:
    """Check if a command contains characters that need base64 encoding.

    Args:
        command: The command string to check

    Returns:
        True if the command should be base64 encoded for safe execution
    """
    # Fast path: simple commands with only safe characters
    if SIMPLE_COMMAND_PATTERN.match(command):
        return False

    # Check for shell-unsafe characters
    if any(c in SHELL_UNSAFE_CHARS for c in command):
        return True

    # Check for control characters or non-ASCII
    for c in command:
        code = ord(c)
        if code < 0x20 and c not in '\t\n\r':  # Control chars except whitespace
            return True
        if code > 0x7e:  # Non-ASCII
            return True

    return False

class ItermSession:
    """Manages an iTerm2 session (terminal pane)."""
    
    def __init__(
        self,
        session: iterm2.Session,
        name: Optional[str] = None,
        logger: Optional[ItermSessionLogger] = None,
        persistent_id: Optional[str] = None,
        max_lines: int = 50
    ):
        """Initialize a session wrapper.
        
        Args:
            session: The iTerm2 session object
            name: Optional name for the session
            logger: Optional logger for the session
            persistent_id: Optional persistent ID for reconnection
            max_lines: Maximum number of lines to retrieve by default
        """
        self.session = session
        self._name = name or session.name
        self.logger = logger
        
        # Generate or use persistent ID
        self._persistent_id = persistent_id or str(uuid.uuid4())
        
        # Default number of lines to retrieve
        self._max_lines = max_lines
        
        # For screen monitoring
        self._monitoring = False
        self._monitor_task = None
        self._monitor_callbacks = []
        self._last_screen_update = time.time()

        # Suspension state
        self._suspended = False
        self._suspended_at: Optional[datetime] = None
        self._suspended_by: Optional[str] = None

        # CWD tracking
        self._cached_cwd: Optional[str] = None
        self._cwd_updated_at: float = 0
    
    @property
    def id(self) -> str:
        """Get the unique identifier for this session."""
        return self.session.session_id
        
    @property
    def persistent_id(self) -> str:
        """Get the persistent identifier for this session."""
        return self._persistent_id
    
    @property
    def name(self) -> str:
        """Get the name of the session."""
        return self._name
        
    @property
    def max_lines(self) -> int:
        """Get the maximum number of lines to retrieve."""
        return self._max_lines

    def set_max_lines(self, max_lines: int) -> None:
        """Set the maximum number of lines to retrieve.
        
        Args:
            max_lines: Maximum number of lines
        """
        self._max_lines = max_lines
    
    @property
    def is_processing(self) -> bool:
        """Check if the session is currently processing a command."""
        try:
            # Try to access the is_processing attribute of the iTerm2 session
            if hasattr(self.session, 'is_processing'):
                return self.session.is_processing
            else:
                # If it doesn't exist, log a warning and return a default value
                _logger.warning(
                    f"Session {self.id} ({self._name}) does not have is_processing attribute"
                )
                return False
        except Exception as e:
            # Handle any exceptions that might occur
            _logger.error(
                f"Error checking is_processing for session {self.id} ({self._name}): {str(e)}"
            )
            return False

    @property
    def is_suspended(self) -> bool:
        """Check if the session has a suspended process (via Ctrl+Z)."""
        return self._suspended

    @property
    def suspended_at(self) -> Optional[datetime]:
        """Get the timestamp when the session was suspended."""
        return self._suspended_at

    @property
    def suspended_by(self) -> Optional[str]:
        """Get the agent that suspended this session."""
        return self._suspended_by

    @trace_operation("session.suspend")
    async def suspend(self, agent: Optional[str] = None) -> None:
        """Suspend the current foreground process by sending Ctrl+Z.

        This sends a SIGTSTP signal to the foreground process, pausing it.
        The process can be resumed later with resume().

        Args:
            agent: Optional name of the agent suspending this session

        Raises:
            RuntimeError: If the session is already suspended
        """
        if self._suspended:
            raise RuntimeError(
                f"Session {self.id} ({self._name}) is already suspended"
            )

        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            suspended_by=agent or "unknown",
        )

        # Send Ctrl+Z to suspend
        await self.send_control_character("z")

        self._suspended = True
        self._suspended_at = datetime.now(timezone.utc)
        self._suspended_by = agent

        if self.logger:
            self.logger.log_control_character("Z (suspend)")

        add_span_event("session_suspended", {
            "agent": agent or "unknown",
            "timestamp": self._suspended_at.isoformat(),
        })

    @trace_operation("session.resume")
    async def resume(self) -> None:
        """Resume the most recently suspended process by sending 'fg'.

        This brings the most recently suspended job back to the foreground.

        Raises:
            RuntimeError: If the session is not suspended
        """
        if not self._suspended:
            raise RuntimeError(
                f"Session {self.id} ({self._name}) is not suspended"
            )

        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            was_suspended_by=self._suspended_by or "unknown",
        )

        suspended_duration = None
        if self._suspended_at:
            suspended_duration = (datetime.now(timezone.utc) - self._suspended_at).total_seconds()

        # Send 'fg' to resume
        await self.session.async_send_text("fg\n")

        self._suspended = False
        self._suspended_at = None
        self._suspended_by = None

        if self.logger:
            self.logger.log_text_sent("fg", executed=True)

        add_span_event("session_resumed", {
            "suspended_duration_seconds": suspended_duration,
        })

    def set_logger(self, logger: ItermSessionLogger) -> None:
        """Set the logger for this session.
        
        Args:
            logger: The logger to use
        """
        self.logger = logger
    
    async def set_name(self, name: str, max_attempts: int = 3, retry_delay: float = 0.2) -> None:
        """Set the name of the session, retrying until iTerm2 agrees.

        iterm2's `async_set_name` propagates asynchronously inside iTerm; on
        a freshly-created session the first call can land before iTerm has
        finished applying the profile default, and the rename gets clobbered.
        We therefore set, sleep briefly, then re-read `session.name` to confirm.
        See feedback fb-20260424-157473f7 item #2 for the user-visible bug
        ("session created with name='x' came back named ' '").

        Args:
            name: The new name for the session.
            max_attempts: How many times to call `async_set_name` before
                giving up. Best-effort; we do not raise on final failure.
            retry_delay: Seconds to wait between attempts.
        """
        self._name = name

        # Fast path: iTerm already agrees, no need to call set_name at all.
        if self.session.name == name:
            if self.logger:
                self.logger.log_session_renamed(name)
            return

        for attempt in range(max_attempts):
            await self.session.async_set_name(name)
            # Always sleep and verify after every attempt, including the last.
            # This ensures iTerm has had a chance to propagate the rename before
            # we evaluate whether it succeeded (fixes false "did not apply"
            # warnings caused by reading session.name before iTerm async-applies
            # the final attempt).
            await asyncio.sleep(retry_delay)
            if self.session.name == name:
                break

        if self.session.name != name:
            # Best-effort warning via stdlib logging; ItermSessionLogger does not
            # expose log_app_event (that method is on ItermLogManager), so we use
            # the module-level stdlib logger to ensure the warning is always
            # emitted regardless of which logger object is attached to the session.
            _logger.warning(
                "iterm2 did not apply name %r after %d attempt(s)", name, max_attempts
            )

        if self.logger:
            self.logger.log_session_renamed(name)
    
    @trace_operation("session.send_text")
    async def send_text(self, text: str, execute: bool = True) -> None:
        """Send text to the session.

        Args:
            text: The text to send
            execute: Whether to execute the text as a command by sending Enter
        """
        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            text_length=len(text),
            execute=execute,
        )

        # Strip any trailing newlines/carriage returns to avoid double execution
        clean_text = text.rstrip("\r\n")

        # Send the text first
        await self.session.async_send_text(clean_text)

        # Send Enter/Return key to execute if requested
        if execute:
            # Wait for iTerm to process the text before sending Enter
            # Delay scales with text length to handle large pastes
            delay = calculate_text_delay(clean_text)
            await asyncio.sleep(delay)
            await self.session.async_send_text("\r")

        # Log the command
        if self.logger:
            self.logger.log_command(clean_text)

        add_span_event("text_sent", {"text_length": len(clean_text), "executed": execute})

    @trace_operation("session.execute_command")
    async def execute_command(
        self,
        command: str,
        use_encoding: Union[bool, Literal["auto"]] = False
    ) -> None:
        """Execute a command in the session.

        By default, sends the command directly without any encoding. The base64
        encoding option exists for edge cases where direct typing fails, but
        should rarely be needed since most shells handle quoted/escaped input.

        Args:
            command: The command to execute (raw, unencoded)
            use_encoding: Encoding mode:
                - False (default): Send command directly (recommended)
                - "auto": Only encode if command contains unusual characters
                - True: Always use base64 encoding (rarely needed)

        Note:
            The base64 encoding wraps commands in 'eval "$(echo ... | base64 -d)"'
            which can trigger security policy violations in some environments.
            Only use encoding when absolutely necessary (e.g., binary data or
            control characters in the command).
        """
        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            command_length=len(command),
            use_encoding=str(use_encoding),
        )

        # Strip any trailing newlines/carriage returns from input
        clean_command = command.rstrip("\r\n")

        # Determine if we should encode
        should_encode = (
            use_encoding is True or
            (use_encoding == "auto" and needs_base64_encoding(clean_command))
        )

        if should_encode:
            # Encode the command to avoid quote/special character issues
            # The command goes in as plain text, gets encoded, sent, decoded, and executed
            encoded = base64.b64encode(clean_command.encode('utf-8')).decode('ascii')

            # Wrap in a one-liner that decodes and executes
            # Using 'eval "$(echo ... | base64 -d)"' ensures proper shell parsing
            # Note: base64 output is safe (only contains A-Z, a-z, 0-9, +, /, =)
            # so no shell escaping of the encoded string is needed
            wrapper = f'eval "$(echo {encoded} | base64 -d)"'

            # Send the wrapper command
            await self.session.async_send_text(wrapper)
            text_sent = wrapper
        else:
            # Direct sending - command is sent as-is (default behavior)
            await self.session.async_send_text(clean_command)
            text_sent = clean_command

        # Wait for iTerm to process the text before sending Enter
        # Delay scales with text length to handle large pastes
        delay = calculate_text_delay(text_sent)
        await asyncio.sleep(delay)
        await self.session.async_send_text("\r")

        # Log the original command (not the encoded wrapper)
        if self.logger:
            self.logger.log_command(clean_command)

        add_span_event("command_executed", {
            "command_length": len(clean_command),
            "encoded": should_encode,
        })

    @trace_operation("session.get_screen_contents")
    async def get_screen_contents(
        self,
        max_lines: Optional[int] = None,
        *,
        from_end: bool = True,
    ) -> str:
        """Get the contents of the session's screen.

        Args:
            max_lines: Maximum number of lines to retrieve (defaults to session's max_lines).
            from_end: If True (default), return the last `max_lines` lines (tail);
                if False, return the first `max_lines` (legacy top-of-buffer slice).
                Tail is almost always what callers want when monitoring a long-running
                session — see fb-20260424-157473f7 item #3.

        Returns:
            The text contents of the screen
        """
        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            requested_max_lines=max_lines if max_lines is not None else self._max_lines,
            from_end=from_end,
        )

        contents = await self.session.async_get_screen_contents()
        lines = []

        # Use instance default if not specified
        if max_lines is None:
            max_lines = self._max_lines

        total = contents.number_of_lines
        max_lines = min(max_lines, total)
        start = total - max_lines if from_end else 0
        end = total if from_end else max_lines

        for i in range(start, end):
            line = contents.line(i)
            line_text = line.string
            if line_text:
                lines.append(line_text)

        output = "\n".join(lines)

        # Log the output
        if self.logger:
            self.logger.log_output(output)

        add_span_event("screen_contents_retrieved", {
            "lines_retrieved": len(lines),
            "total_lines_available": contents.number_of_lines,
            "output_length": len(output),
        })

        return output
    
    @trace_operation("session.send_control_character")
    async def send_control_character(self, character: str) -> None:
        """Send a control character to the session.

        Args:
            character: The character (e.g., "c" for Ctrl+C)
        """
        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            control_character=character.upper() if character.isalpha() else character,
        )

        if len(character) != 1 or not character.isalpha():
            raise ValueError("Control character must be a single letter")

        # Convert to uppercase and then to control code
        character = character.upper()
        code = ord(character) - 64
        control_sequence = chr(code)

        await self.session.async_send_text(control_sequence)

        # Log the control character
        if self.logger:
            self.logger.log_control_character(character)

        add_span_event("control_character_sent", {"character": character})
    
    @trace_operation("session.send_special_key")
    async def send_special_key(self, key: str) -> None:
        """Send a special key to the session.

        Args:
            key: The special key name ('enter', 'return', 'tab', 'escape', etc.)
        """
        add_span_attributes(
            session_id=self.id,
            session_name=self._name,
            special_key=key.lower(),
        )

        key = key.lower()

        # Map special key names to their character sequences
        key_map = {
            'enter': '\r',
            'return': '\r',
            'tab': '\t',
            'escape': '\x1b',
            'esc': '\x1b',
            'space': ' ',
            'backspace': '\x7f',
            'delete': '\x1b[3~',
            'up': '\x1b[A',
            'down': '\x1b[B',
            'right': '\x1b[C',
            'left': '\x1b[D',
            'home': '\x1b[H',
            'end': '\x1b[F'
        }

        if key not in key_map:
            raise ValueError(f"Unknown special key: {key}. Supported keys: {', '.join(key_map.keys())}")

        sequence = key_map[key]
        await self.session.async_send_text(sequence)

        # Log the special key
        if self.logger:
            self.logger.log_custom_event("SPECIAL_KEY", f"Sent special key: {key}")

        add_span_event("special_key_sent", {"key": key})
    
    async def clear_screen(self) -> None:
        """Clear the screen."""
        await self.session.async_send_text("\u001b[2J\u001b[H")  # ANSI clear screen
        
        # Log the clear action
        if self.logger:
            self.logger.log_custom_event("CLEAR_SCREEN", "Screen cleared")
            
    async def start_monitoring(self, update_interval: float = 0.5) -> None:
        """Start monitoring the screen for changes.
        
        This allows real-time capture of terminal output without requiring explicit
        calls to get_screen_contents(). Uses polling-based approach as a fallback
        since subscription-based approach may have WebSocket issues.
        
        Args:
            update_interval: How often to check for updates (in seconds)
        """
        if self._monitoring:
            return

        # Initialize monitoring state, but only set to True once we confirm task is running
        _logger.info(f"Setting up monitoring for session {self.id} ({self._name})")
        
        async def monitor_screen_polling():
            """Polling-based screen monitoring as a fallback approach."""
            try:
                _logger.info(f"Starting polling-based screen monitoring for session {self.id}")
                
                if self.logger:
                    self.logger.log_custom_event("MONITORING_STARTED", "Polling-based screen monitoring started")
                
                # Use a ready event to signal when monitoring is fully initialized
                monitoring_initialized.set()
                
                last_content = await self.get_screen_contents()
                
                while self._monitoring:
                    try:
                        # Get current content
                        current_content = await self.get_screen_contents()
                        
                        # Check if content has changed
                        if current_content != last_content:
                            # Process the content through any registered callbacks
                            callback_tasks = []
                            for callback in self._monitor_callbacks:
                                # Run each callback in a separate task to prevent blocking
                                try:
                                    # Create and track all callback tasks
                                    task = asyncio.create_task(callback(current_content))
                                    callback_tasks.append(task)
                                except Exception as callback_error:
                                    _logger.error(f"Error in callback: {str(callback_error)}")
                            
                            # Wait for all callbacks to complete
                            if callback_tasks:
                                await asyncio.gather(*callback_tasks, return_exceptions=True)
                            
                            # Update last content and timestamp
                            last_content = current_content
                            self._last_screen_update = time.time()
                        
                        # Sleep to prevent excessive CPU usage
                        await asyncio.sleep(update_interval)
                    except Exception as poll_error:
                        # Special handling for SESSION_NOT_FOUND - expected when session is closed
                        if "SESSION_NOT_FOUND" in str(poll_error):
                            if self._monitoring:
                                # Only log at debug level since this is expected during cleanup
                                _logger.debug(f"Session no longer available during monitoring (likely closed): {self.id}")
                                # Signal to exit the monitoring loop
                                self._monitoring = False
                                return
                        else:
                            # Log any other errors as actual errors
                            _logger.error(f"Error in polling loop: {str(poll_error)}")
                        await asyncio.sleep(update_interval)
            except asyncio.CancelledError:
                _logger.info(f"Polling monitor task cancelled for session {self.id}")
            except Exception as e:
                _logger.error(f"Fatal error in polling monitor: {str(e)}")
                if self.logger:
                    self.logger.log_custom_event("MONITORING_ERROR", f"Error in screen monitoring: {str(e)}")
            finally:
                self._monitoring = False
                if self.logger:
                    self.logger.log_custom_event("MONITORING_STOPPED", "Screen monitoring stopped")
        
        # Create an event to signal when monitoring is fully initialized
        monitoring_initialized = asyncio.Event()
        
        # Start monitoring flag
        self._monitoring = True
        
        # Use polling-based approach instead of subscription-based approach
        # to avoid WebSocket frame errors
        self._monitor_task = asyncio.create_task(monitor_screen_polling())
        
        # Wait for the monitoring to be properly initialized before returning
        try:
            await asyncio.wait_for(monitoring_initialized.wait(), timeout=3.0)
            _logger.info(f"Monitoring successfully started for session {self.id}")
        except asyncio.TimeoutError:
            _logger.error(f"Timeout waiting for monitoring to initialize for session {self.id}")
            # If initialization times out, clean up
            self._monitoring = False
            if not self._monitor_task.done():
                self._monitor_task.cancel()
            self._monitor_task = None
            raise RuntimeError("Timeout waiting for monitoring to initialize")
        
    async def stop_monitoring(self) -> None:
        """Stop monitoring the screen for changes and ensure callbacks are completed."""
        if not self._monitoring or not self._monitor_task:
            _logger.info(f"Monitoring already stopped for session {self.id}")
            return

        _logger.info(f"Stopping monitoring for session {self.id}")
        # Set monitoring flag to False first to signal the loop to exit
        self._monitoring = False
        
        # Cancel the task if it's still running
        if not self._monitor_task.done():
            try:
                # Wait a short time for graceful shutdown
                await asyncio.sleep(0.2)
                # Cancel if still running after grace period
                if not self._monitor_task.done():
                    _logger.info(f"Cancelling monitor task for session {self.id}")
                    self._monitor_task.cancel()
                    # Wait for cancellation to complete
                    try:
                        await asyncio.wait_for(self._monitor_task, timeout=1.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
            except Exception as e:
                _logger.error(f"Error stopping monitoring for session {self.id}: {str(e)}")

        # Cleanup
        self._monitor_task = None
        _logger.info(f"Monitoring stopped for session {self.id}")
        
    def add_monitor_callback(self, callback: Callable[[str], None]) -> None:
        """Add a callback to be called when the screen changes.
        
        Args:
            callback: A function that takes the screen content as a string
        """
        if callback not in self._monitor_callbacks:
            self._monitor_callbacks.append(callback)
            
    def remove_monitor_callback(self, callback: Callable[[str], None]) -> None:
        """Remove a previously registered callback.
        
        Args:
            callback: The callback to remove
        """
        if callback in self._monitor_callbacks:
            self._monitor_callbacks.remove(callback)
            
    @property
    def is_monitoring(self) -> bool:
        """Check if the session is being monitored.
        
        Returns True if monitoring is active (flag is set AND task is running)
        """
        # Check both the flag and that the task exists and is not done
        monitoring_active = (
            self._monitoring and 
            self._monitor_task is not None and 
            not self._monitor_task.done()
        )
        return monitoring_active
        
    @property
    def last_update_time(self) -> float:
        """Get the timestamp of the last screen update."""
        return self._last_screen_update

    @property
    def cached_cwd(self) -> Optional[str]:
        """Get the cached current working directory."""
        return self._cached_cwd

    def update_cwd_cache(self, cwd: str) -> None:
        """Update the cached CWD (called when cd commands are detected)."""
        self._cached_cwd = cwd
        self._cwd_updated_at = time.time()

    def parse_prompt_cwd(self, screen_content: str) -> Optional[str]:
        """Parse CWD from terminal prompt.

        Tries multiple common prompt patterns to extract the current directory.

        Args:
            screen_content: Recent terminal output

        Returns:
            Extracted CWD path or None if not found
        """
        # Patterns to try (most specific first)
        patterns = [
            # hostname :: ~/path 123 » or (env) hostname :: ~/path 123 »
            # (common pattern for oh-my-zsh and similar prompts)
            r"(?:\([^)]+\)\s+)?\w+\s+::\s+([~/][^\s]+)\s+\d+\s*»",
            # Starship git prompt: ~/path on branch ⇣⇡ *? ── (with status line)
            r"^([~/][^\s]+)\s+on\s+[^\s]+\s*(?:⇣|⇡|\*|\?|!|\d)*\s*─",
            # Git prompt: ~/path on branch (simple)
            r"^([~/][^\s]+)\s+on\s+\S+",
            # Claude Code header: ▘▘ ▝▝  ~/path
            r"▝▝\s+([~/][^\s]+)",
            # Standard PS1: user@host:~/path$
            r"@[^:]+:([~/][^\s$]+)\$",
            # Simple: ~/path followed by prompt char
            r"^([~/][^\s]+)\s*(?:»|>|❯|\$)\s*$",
            # Path in brackets [/path/to/dir]
            r"\[(/[^\]]+)\]",
            # Absolute path at start (fallback)
            r"^(/Users/[^\s]+?)(?:\s|$)",
        ]

        # Get last few lines of content
        lines = screen_content.strip().split('\n')
        recent_lines = lines[-10:] if len(lines) > 10 else lines

        for line in reversed(recent_lines):
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    cwd = match.group(1)
                    # Expand ~ to full path
                    if cwd.startswith('~'):
                        cwd = os.path.expanduser(cwd)
                    return cwd

        return None

    async def get_cwd(self, force_refresh: bool = False) -> Optional[str]:
        """Get the current working directory of the session.

        Uses iTerm2's native async_get_variable("path") API when available,
        falls back to prompt parsing if that fails.

        Args:
            force_refresh: If True, always try to fetch fresh value

        Returns:
            Current working directory path or None
        """
        # If we have a recent cached value and not forcing refresh, use it
        if not force_refresh and self._cached_cwd:
            if time.time() - self._cwd_updated_at < CWD_CACHE_TTL_SECONDS:
                return self._cached_cwd

        # Try iTerm2's native API first (requires shell integration)
        try:
            cwd = await self.session.async_get_variable("path")
            if cwd:
                self.update_cwd_cache(cwd)
                return cwd
        except Exception as e:
            _logger.debug(f"async_get_variable('path') failed: {e}")

        # Fallback: parse from terminal prompt
        try:
            screen_content = await self.get_screen_contents(max_lines=20)
            parsed_cwd = self.parse_prompt_cwd(screen_content)
            if parsed_cwd:
                self.update_cwd_cache(parsed_cwd)
                return parsed_cwd
        except Exception as e:
            _logger.debug(f"Error parsing CWD from screen: {e}")

        # Return cached value even if stale
        return self._cached_cwd

    async def set_background_color(self, red: int, green: int, blue: int, alpha: int = 255) -> None:
        """Set the background color of the session.

        Args:
            red: Red component (0-255)
            green: Green component (0-255)
            blue: Blue component (0-255)
            alpha: Alpha component (0-255), default fully opaque
        """
        color = iterm2.Color(red, green, blue, alpha)
        change = iterm2.LocalWriteOnlyProfile()
        change.set_background_color(color)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("SET_BACKGROUND", f"Background color set to RGB({red},{green},{blue})")

    async def set_tab_color(self, red: int, green: int, blue: int, enabled: bool = True) -> None:
        """Set the tab color of the session.

        Args:
            red: Red component (0-255)
            green: Green component (0-255)
            blue: Blue component (0-255)
            enabled: Whether to enable tab coloring
        """
        color = iterm2.Color(red, green, blue)
        change = iterm2.LocalWriteOnlyProfile()
        change.set_tab_color(color)
        change.set_use_tab_color(enabled)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("SET_TAB_COLOR", f"Tab color set to RGB({red},{green},{blue})")

    async def set_tab_color_enabled(self, enabled: bool) -> None:
        """Toggle tab color on or off without changing the configured color.

        Args:
            enabled: Whether to enable tab coloring
        """
        change = iterm2.LocalWriteOnlyProfile()
        change.set_use_tab_color(enabled)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("SET_TAB_COLOR_ENABLED", f"Tab color enabled={enabled}")

    async def set_badge(self, text: str) -> None:
        """Set the badge text for the session.

        Args:
            text: The badge text to display (supports escape sequences)
        """
        change = iterm2.LocalWriteOnlyProfile()
        change.set_badge_text(text)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("SET_BADGE", f"Badge set to: {text}")

    async def set_cursor_color(self, red: int, green: int, blue: int) -> None:
        """Set the cursor color of the session.

        Args:
            red: Red component (0-255)
            green: Green component (0-255)
            blue: Blue component (0-255)
        """
        color = iterm2.Color(red, green, blue)
        change = iterm2.LocalWriteOnlyProfile()
        change.set_cursor_color(color)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("SET_CURSOR_COLOR", f"Cursor color set to RGB({red},{green},{blue})")

    async def reset_colors(self) -> None:
        """Reset all color customizations to profile defaults."""
        change = iterm2.LocalWriteOnlyProfile()
        change.set_use_tab_color(False)
        await self.session.async_set_profile_properties(change)

        if self.logger:
            self.logger.log_custom_event("RESET_COLORS", "Colors reset to profile defaults")

    # ==================== State Persistence ====================

    async def save_state(self) -> Dict[str, Any]:
        """Serialize session state to a JSON-compatible dict.

        Returns a snapshot of the session's current state that can be
        used for checkpointing, crash recovery, or debugging.

        Returns:
            Dict containing serializable session state
        """
        # Capture current screen content if available
        last_output = None
        try:
            last_output = await self.get_screen_contents(max_lines=100)
        except Exception:
            pass  # Screen content may not be available

        # Get last command from logger if available (currently not exposed)
        last_command = None

        state = {
            "session_id": self.id,
            "persistent_id": self._persistent_id,
            "name": self._name,
            "max_lines": self._max_lines,
            "is_monitoring": self._monitoring,
            "last_screen_update": self._last_screen_update,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_command": last_command,
            "last_output": last_output,
            "metadata": {}
        }

        # Add logger telemetry if available
        if self.logger:
            state["metadata"]["command_count"] = getattr(self.logger, 'command_count', 0)
            state["metadata"]["output_line_count"] = getattr(self.logger, 'output_line_count', 0)

        return state

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore session configuration from a saved state dict.

        Note: This restores configuration state only. The underlying
        iTerm2 session object and live monitoring state cannot be
        restored from a checkpoint - those require reconnection.

        Args:
            state: Previously saved state dict from save_state()
        """
        # Restore configurable properties
        if "name" in state:
            self._name = state["name"]

        if "max_lines" in state:
            self._max_lines = state["max_lines"]

        if "last_screen_update" in state:
            self._last_screen_update = state["last_screen_update"]

        # Note: We don't restore is_monitoring because monitoring
        # requires active tasks that can't be serialized

        # Note: persistent_id and session_id are set during session
        # creation and shouldn't be overwritten from checkpoint

    def get_state_summary(self) -> Dict[str, Any]:
        """Get a brief summary of session state for logging/debugging.

        Returns:
            Dict with key session state info
        """
        return {
            "session_id": self.id,
            "persistent_id": self._persistent_id,
            "name": self._name,
            "max_lines": self._max_lines,
            "is_monitoring": self.is_monitoring,
            "last_update": self._last_screen_update
        }

    # =========================================================================
    # Expect-Style Pattern Matching
    # =========================================================================

    async def expect(
        self,
        patterns: List[Union[str, re.Pattern, ExpectTimeout]],
        timeout: int = 30,
        poll_interval: float = 0.1,
        search_window_lines: Optional[int] = None
    ) -> ExpectResult:
        """Wait for one of the patterns to appear in terminal output.

        This method polls the terminal screen and checks for pattern matches.
        It's inspired by pexpect's expect() function but adapted for iTerm2's
        async screen access.

        Args:
            patterns: List of patterns to match. Each can be:
                - A string (treated as a regex pattern)
                - A compiled re.Pattern object
                - An ExpectTimeout marker (specifies timeout behavior)
            timeout: Maximum wait time in seconds (default 30).
                     Overridden by ExpectTimeout in patterns list.
            poll_interval: How often to check for new output (default 0.1s)
            search_window_lines: Number of lines to search (default: session max_lines)

        Returns:
            ExpectResult with match details including:
                - matched_pattern: The pattern that matched
                - match_index: Index of the matching pattern
                - output: Full output up to the match
                - matched_text: The specific text that matched
                - before: Text before the match
                - match: The regex match object

        Raises:
            ExpectTimeoutError: If timeout expires and no ExpectTimeout marker
                               is in the patterns list
            ValueError: If patterns list is empty or contains only ExpectTimeout

        Example:
            # Wait for shell prompt or error
            result = await session.expect([
                r'\$\s*$',             # Shell prompt (bash)
                r'>\s*$',              # Shell prompt (zsh)
                r'error:',             # Error detected
                ExpectTimeout(30)      # Timeout (returns index 3)
            ])

            if result.match_index == 0 or result.match_index == 1:
                print("Command completed successfully")
            elif result.match_index == 2:
                print(f"Error detected: {result.matched_text}")
            elif result.match_index == 3:
                print("Timeout waiting for response")
        """
        # Validate and process patterns
        if not patterns:
            raise ValueError("patterns list cannot be empty")

        # Separate patterns from timeout marker
        regex_patterns: List[Tuple[int, re.Pattern]] = []
        timeout_marker: Optional[Tuple[int, ExpectTimeout]] = None

        for i, pattern in enumerate(patterns):
            if isinstance(pattern, ExpectTimeout):
                if timeout_marker is not None:
                    _logger.warning("Multiple ExpectTimeout markers; using first one")
                else:
                    timeout_marker = (i, pattern)
                    timeout = pattern.seconds  # Override timeout
            elif isinstance(pattern, re.Pattern):
                regex_patterns.append((i, pattern))
            elif isinstance(pattern, str):
                try:
                    compiled = re.compile(pattern)
                    regex_patterns.append((i, compiled))
                except re.error as e:
                    raise ValueError(f"Invalid regex pattern at index {i}: {e}")
            else:
                raise ValueError(
                    f"Invalid pattern type at index {i}: {type(pattern).__name__}. "
                    f"Expected str, re.Pattern, or ExpectTimeout"
                )

        if not regex_patterns:
            raise ValueError("patterns list must contain at least one regex pattern")

        # Determine search window
        if search_window_lines is None:
            search_window_lines = self._max_lines

        # Track start time and accumulated output
        start_time = time.time()
        last_output = ""
        accumulated_output = ""

        _logger.debug(
            f"expect() started: {len(regex_patterns)} patterns, "
            f"timeout={timeout}s, poll={poll_interval}s"
        )

        if self.logger:
            pattern_strs = [
                p.pattern if isinstance(p, re.Pattern) else str(p)
                for p in patterns if not isinstance(p, ExpectTimeout)
            ]
            self.logger.log_custom_event(
                "EXPECT_START",
                f"Waiting for patterns: {pattern_strs}"
            )

        try:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    if timeout_marker is not None:
                        # Return timeout result with marker index
                        idx, marker = timeout_marker
                        _logger.debug(f"expect() timeout, returning marker at index {idx}")
                        if self.logger:
                            self.logger.log_custom_event(
                                "EXPECT_TIMEOUT",
                                f"Timeout after {elapsed:.1f}s"
                            )
                        return ExpectResult(
                            matched_pattern=marker,
                            match_index=idx,
                            output=accumulated_output,
                            matched_text="",
                            before=accumulated_output,
                            match=None
                        )
                    else:
                        # Raise timeout error
                        if self.logger:
                            self.logger.log_custom_event(
                                "EXPECT_TIMEOUT_ERROR",
                                f"Timeout after {elapsed:.1f}s"
                            )
                        raise ExpectTimeoutError(
                            timeout=timeout,
                            patterns=patterns,
                            output=accumulated_output
                        )

                # Get current screen contents
                try:
                    current_output = await self.get_screen_contents(
                        max_lines=search_window_lines
                    )
                except Exception as e:
                    if "SESSION_NOT_FOUND" in str(e):
                        _logger.error("Session closed during expect()")
                        raise ExpectError(f"Session closed during expect(): {e}")
                    raise

                # Check for new content
                if current_output != last_output:
                    accumulated_output = current_output
                    last_output = current_output

                    # Check each pattern against the current output
                    for idx, pattern in regex_patterns:
                        match = pattern.search(current_output)
                        if match:
                            matched_text = match.group(0)
                            before_text = current_output[:match.start()]

                            _logger.debug(
                                f"expect() matched pattern {idx}: {pattern.pattern!r}"
                            )
                            if self.logger:
                                self.logger.log_custom_event(
                                    "EXPECT_MATCH",
                                    f"Pattern matched: {pattern.pattern!r}"
                                )

                            return ExpectResult(
                                matched_pattern=pattern,
                                match_index=idx,
                                output=current_output,
                                matched_text=matched_text,
                                before=before_text,
                                match=match
                            )

                # Wait before next poll (don't overshoot timeout)
                remaining = timeout - (time.time() - start_time)
                await asyncio.sleep(min(poll_interval, max(0.01, remaining)))

        except asyncio.CancelledError:
            _logger.debug("expect() cancelled")
            if self.logger:
                self.logger.log_custom_event("EXPECT_CANCELLED", "Operation cancelled")
            raise

    async def wait_for_prompt(
        self,
        timeout: int = 30,
        custom_prompts: Optional[List[str]] = None
    ) -> bool:
        """Wait for a shell prompt to appear, indicating command completion.

        This is a convenience wrapper around expect() for the common case
        of waiting for a command to complete.

        Args:
            timeout: Maximum wait time in seconds (default 30)
            custom_prompts: Additional prompt patterns to match. These are
                           added to the default set of common shell prompts.

        Returns:
            True if a prompt was detected, False on timeout

        Example:
            # Execute command and wait for completion
            await session.send_text("ls -la")
            if await session.wait_for_prompt(timeout=10):
                output = await session.get_screen_contents()
                print("Command completed:", output)
            else:
                print("Command timed out")
        """
        # Common shell prompt patterns
        default_prompts = [
            r'\$\s*$',           # Bash prompt ending with $
            r'>\s*$',            # Zsh/fish prompt ending with >
            r'#\s*$',            # Root prompt ending with #
            r'%\s*$',            # Zsh default ending with %
            r'\]\s*$',           # Prompt ending with ]
            r'❯\s*$',            # Starship/fancy prompt
            r'➜\s*$',            # Oh-my-zsh arrow
            r'\)\s*$',           # Prompt ending with )
        ]

        patterns = default_prompts.copy()
        if custom_prompts:
            patterns.extend(custom_prompts)

        # Add timeout marker at the end
        patterns.append(ExpectTimeout(timeout))

        result = await self.expect(patterns, timeout=timeout)

        # If we matched the timeout marker, return False
        return result.match_index < len(patterns) - 1

    async def wait_for_patterns(
        self,
        success_patterns: List[str],
        error_patterns: Optional[List[str]] = None,
        timeout: int = 30
    ) -> Tuple[bool, ExpectResult]:
        """Wait for success or error patterns in output.

        A convenience method for the common case of waiting for a command
        to succeed or fail.

        Args:
            success_patterns: Patterns indicating success
            error_patterns: Patterns indicating failure (optional)
            timeout: Maximum wait time in seconds

        Returns:
            Tuple of (is_success, ExpectResult):
                - is_success: True if a success pattern matched, False otherwise
                - result: The full ExpectResult for detailed inspection

        Example:
            # Wait for git command to succeed or fail
            await session.send_text("git push origin main")
            is_success, result = await session.wait_for_patterns(
                success_patterns=[r'Everything up-to-date', r'->\s+main'],
                error_patterns=[r'error:', r'fatal:', r'rejected'],
                timeout=60
            )

            if is_success:
                print("Push succeeded!")
            else:
                print(f"Push failed: {result.matched_text}")
        """
        # Build combined pattern list
        patterns: List[Union[str, ExpectTimeout]] = []
        success_count = len(success_patterns)

        # Add success patterns first
        patterns.extend(success_patterns)

        # Add error patterns
        if error_patterns:
            patterns.extend(error_patterns)

        # Add timeout marker
        patterns.append(ExpectTimeout(timeout))

        result = await self.expect(patterns, timeout=timeout)

        # Determine if it was a success pattern
        is_success = result.match_index < success_count

        return (is_success, result)

    async def send_and_expect(
        self,
        text: str,
        patterns: List[Union[str, re.Pattern, ExpectTimeout]],
        timeout: int = 30,
        execute: bool = True
    ) -> ExpectResult:
        """Send text and wait for expected output patterns.

        Combines send_text() and expect() for convenience.

        Args:
            text: Text to send to the terminal
            patterns: Patterns to wait for (see expect() for format)
            timeout: Maximum wait time for patterns (default 30s)
            execute: Whether to press Enter after sending (default True)

        Returns:
            ExpectResult from the expect() call

        Example:
            # Send command and wait for prompt
            result = await session.send_and_expect(
                "echo 'Hello World'",
                [r'Hello World', r'error:', ExpectTimeout(10)]
            )

            if result.match_index == 0:
                print("Command succeeded!")
        """
        await self.send_text(text, execute=execute)
        return await self.expect(patterns, timeout=timeout)

    async def interact_until(
        self,
        prompt_pattern: str,
        responses: Dict[str, str],
        timeout: int = 30,
        max_iterations: int = 100
    ) -> List[ExpectResult]:
        """Handle interactive prompts automatically.

        Useful for scripts that ask multiple questions, like installation
        wizards or configuration tools.

        Args:
            prompt_pattern: Pattern indicating the interaction is complete
            responses: Dict mapping prompt patterns to responses
            timeout: Timeout per prompt (default 30s)
            max_iterations: Maximum number of prompts to handle (default 100)

        Returns:
            List of ExpectResult from each interaction

        Example:
            # Handle npm init interactively
            results = await session.interact_until(
                prompt_pattern=r'Is this OK\?',
                responses={
                    r'package name:': 'my-package',
                    r'version:': '1.0.0',
                    r'description:': 'My awesome package',
                    r'entry point:': 'index.js',
                    r'test command:': 'npm test',
                    r'git repository:': '',
                    r'keywords:': '',
                    r'author:': 'Me',
                    r'license:': 'MIT',
                }
            )
        """
        results: List[ExpectResult] = []

        # Build pattern list: prompt_pattern + all response patterns + timeout
        # Track remaining response patterns (remove after answering to prevent re-matching)
        remaining_responses = dict(responses)  # Copy to avoid mutating input

        for iteration in range(max_iterations):
            # Build current pattern list with remaining response patterns
            current_patterns: List[Union[str, ExpectTimeout]] = [prompt_pattern]
            response_pattern_list = list(remaining_responses.keys())
            current_patterns.extend(response_pattern_list)
            current_patterns.append(ExpectTimeout(timeout))

            result = await self.expect(current_patterns, timeout=timeout)
            results.append(result)

            # Check if we hit the final prompt
            if result.match_index == 0:
                _logger.debug(f"interact_until completed after {iteration + 1} iterations")
                break

            # Check if we timed out
            if isinstance(result.matched_pattern, ExpectTimeout):
                _logger.warning(f"interact_until timed out at iteration {iteration + 1}")
                break

            # Find the matching response pattern and send response
            if result.match_index > 0 and result.match_index <= len(response_pattern_list):
                pattern = response_pattern_list[result.match_index - 1]
                response = remaining_responses[pattern]
                await self.send_text(response, execute=True)
                # Remove answered pattern to prevent re-matching
                del remaining_responses[pattern]
        else:
            _logger.warning(f"interact_until hit max iterations ({max_iterations})")

        return results
