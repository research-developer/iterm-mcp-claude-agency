"""Terminal management for iTerm2 integration."""

import asyncio
import os
from typing import Dict, List, Optional, Tuple, Union, Any, Literal

import iterm2

from .session import ItermSession
from utils.logging import ItermLogManager, ItermSessionLogger

class ItermTerminal:
    """Manages an iTerm2 terminal with multiple sessions (panes)."""
    
    def __init__(
        self,
        connection: iterm2.Connection,
        log_dir: Optional[str] = None,
        enable_logging: bool = True,
        default_max_lines: int = 50,
        max_snapshot_lines: int = 1000
    ):
        """Initialize the terminal manager.
        
        Args:
            connection: The iTerm2 connection object
            log_dir: Optional directory for log files
            enable_logging: Whether to enable session logging
            default_max_lines: Default number of lines to show per session
            max_snapshot_lines: Maximum number of lines to keep in snapshots
        """
        self.connection = connection
        self.app = None
        self.sessions: Dict[str, ItermSession] = {}
        self.default_max_lines = default_max_lines
        
        # Initialize logging if enabled
        self.enable_logging = enable_logging
        if enable_logging:
            self.log_manager = ItermLogManager(
                log_dir=log_dir,
                default_max_lines=default_max_lines,
                max_snapshot_lines=max_snapshot_lines
            )
        
    async def initialize(self) -> None:
        """Initialize the connection to iTerm2."""
        self.app = await iterm2.async_get_app(self.connection)
        await self._refresh_sessions()
    
    async def _refresh_sessions(self) -> None:
        """Refresh the list of available sessions."""
        if not self.app:
            raise RuntimeError("Terminal not initialized")
        
        # Clear existing sessions
        self.sessions = {}
        
        # Get all windows
        windows = self.app.windows
        
        # Loop through all windows and tabs to find all sessions
        for window in windows:
            tabs = window.tabs
            for tab in tabs:
                tab_sessions = tab.sessions
                for session in tab_sessions:
                    # Check if we have a persistent ID for this session
                    persistent_id = None
                    if self.enable_logging and hasattr(self, "log_manager"):
                        # Search persistent sessions for this session ID
                        for p_id, details in self.log_manager.persistent_sessions.items():
                            if details.get("session_id") == session.session_id:
                                persistent_id = p_id
                                break
                    
                    # Create a new ItermSession with logger and add to the dictionary
                    iterm_session = ItermSession(
                        session=session,
                        persistent_id=persistent_id,
                        max_lines=self.default_max_lines
                    )
                    
                    # Add logger if logging is enabled
                    if self.enable_logging and hasattr(self, "log_manager"):
                        session_logger = self.log_manager.get_session_logger(
                            session_id=iterm_session.id,
                            session_name=iterm_session.name,
                            persistent_id=iterm_session.persistent_id
                        )
                        iterm_session.set_logger(session_logger)
                    
                    self.sessions[iterm_session.id] = iterm_session
    
    async def get_sessions(self) -> List[ItermSession]:
        """Get all available sessions.
        
        Returns:
            List of session objects
        """
        await self._refresh_sessions()
        return list(self.sessions.values())
    
    async def get_session_by_id(self, session_id: str) -> Optional[ItermSession]:
        """Get a session by its ID.
        
        Args:
            session_id: The unique ID of the session
            
        Returns:
            The session if found, None otherwise
        """
        await self._refresh_sessions()
        return self.sessions.get(session_id)
    
    async def get_session_by_name(self, name: str) -> Optional[ItermSession]:
        """Get a session by its name.
        
        Args:
            name: The name of the session
            
        Returns:
            The first session with the given name if found, None otherwise
        """
        await self._refresh_sessions()
        for session in self.sessions.values():
            if session.name == name:
                return session
        return None
        
    async def get_session_by_persistent_id(self, persistent_id: str) -> Optional[ItermSession]:
        """Get a session by its persistent ID.
        
        Args:
            persistent_id: The persistent ID of the session
            
        Returns:
            The session with the given persistent ID if found, None otherwise
        """
        # Find the session ID from persistent ID
        if self.enable_logging and hasattr(self, "log_manager"):
            session_info = self.log_manager.get_persistent_session(persistent_id)
            if session_info:
                session_id = session_info.get("session_id")
                if session_id:
                    # Refresh to ensure we have the latest sessions
                    await self._refresh_sessions()
                    return self.sessions.get(session_id)
        
        # If not found in persistent mapping, try direct search
        await self._refresh_sessions()
        for session in self.sessions.values():
            if session.persistent_id == persistent_id:
                return session
        
        return None

    async def get_focused_session(self) -> Optional[ItermSession]:
        """Get the currently focused session.

        Returns:
            The currently focused session, or None if no session is focused
        """
        if not self.app:
            return None

        # Get the current window
        window = self.app.current_terminal_window
        if not window:
            return None

        # Get the current tab
        tab = window.current_tab
        if not tab:
            return None

        # Get the current session
        iterm_session = tab.current_session
        if not iterm_session:
            return None

        # Refresh sessions to ensure we have the latest
        await self._refresh_sessions()

        # Return the matching ItermSession wrapper
        return self.sessions.get(iterm_session.session_id)

    async def create_window(self, profile: Optional[str] = None) -> ItermSession:
        """Create a new iTerm2 window.

        Args:
            profile: Optional profile name to use. If None, uses the "MCP Agent"
                     profile if it exists, otherwise the default profile.

        Returns:
            The session for the new window
        """
        if not self.app:
            raise RuntimeError("Terminal not initialized")

        # Use MCP Agent profile by default if available
        profile_to_use = profile or "MCP Agent"

        # Create a new window with the specified profile
        try:
            window = await iterm2.Window.async_create(
                connection=self.connection,
                profile=profile_to_use
            )
        except Exception:
            # Fall back to default profile if the specified profile doesn't exist
            window = await iterm2.Window.async_create(connection=self.connection)
        
        # Get the first session from the window
        tabs = window.tabs
        if not tabs:
            raise RuntimeError("Failed to create window with tabs")
            
        sessions = tabs[0].sessions
        if not sessions:
            raise RuntimeError("Failed to create window with sessions")
        
        # Create a new ItermSession with logger and add to the dictionary
        session = ItermSession(
            session=sessions[0],
            max_lines=self.default_max_lines
        )
        
        # Add logger if logging is enabled
        if self.enable_logging and hasattr(self, "log_manager"):
            session_logger = self.log_manager.get_session_logger(
                session_id=session.id,
                session_name=session.name,
                persistent_id=session.persistent_id
            )
            session.set_logger(session_logger)
            
            # Log window creation event
            self.log_manager.log_app_event(
                "WINDOW_CREATED", 
                f"Created new window with session: {session.name} ({session.id}) - Persistent ID: {session.persistent_id}"
            )
        
        self.sessions[session.id] = session
        
        return session
    
    async def create_tab(self, window_id: Optional[str] = None) -> ItermSession:
        """Create a new tab in the specified window or current window.
        
        Args:
            window_id: Optional ID of the window to create the tab in
            
        Returns:
            The session for the new tab
        """
        if not self.app:
            raise RuntimeError("Terminal not initialized")
            
        # Get the window to create the tab in
        window = None
        if window_id:
            windows = self.app.windows
            for w in windows:
                if w.window_id == window_id:
                    window = w
                    break
            if not window:
                raise ValueError(f"Window with ID {window_id} not found")
        else:
            window = self.app.current_window
            if not window:
                # Create a new window if none exists
                return await self.create_window()
        
        # Create a new tab with default profile
        tab = await window.async_create_tab()
        
        # Get the session from the tab
        sessions = tab.sessions
        if not sessions:
            raise RuntimeError("Failed to create tab with sessions")
        
        # Create a new ItermSession with logger and add to the dictionary
        session = ItermSession(
            session=sessions[0],
            max_lines=self.default_max_lines
        )
        
        # Add logger if logging is enabled
        if self.enable_logging and hasattr(self, "log_manager"):
            session_logger = self.log_manager.get_session_logger(
                session_id=session.id,
                session_name=session.name,
                persistent_id=session.persistent_id
            )
            session.set_logger(session_logger)
            
            # Log tab creation event
            self.log_manager.log_app_event(
                "TAB_CREATED", 
                f"Created new tab with session: {session.name} ({session.id}) - Persistent ID: {session.persistent_id}"
            )
        
        self.sessions[session.id] = session
        
        return session
    
    async def create_split_pane(
        self,
        session_id: str,
        vertical: bool = False,
        name: Optional[str] = None,
        profile: Optional[str] = None
    ) -> ItermSession:
        """Create a new split pane from an existing session.

        Args:
            session_id: The ID of the session to split
            vertical: Whether to split vertically (True) or horizontally (False)
            name: Optional name for the new session
            profile: Optional profile name to use. If None, uses the "MCP Agent"
                     profile if it exists.

        Returns:
            The session for the new pane
        """
        # Get the source session
        source_session = await self.get_session_by_id(session_id)
        if not source_session:
            raise ValueError(f"Session with ID {session_id} not found")

        # Use MCP Agent profile by default if available
        profile_to_use = profile or "MCP Agent"

        # Create a new split pane with the specified profile
        try:
            new_session = await source_session.session.async_split_pane(
                vertical=vertical,
                profile=profile_to_use
            )
        except Exception:
            # Fall back to using profile customizations if profile doesn't exist
            profile_customizations = iterm2.LocalWriteOnlyProfile()
            new_session = await source_session.session.async_split_pane(
                vertical=vertical,
                profile_customizations=profile_customizations
            )
        
        # Create a new ItermSession with logger and add to the dictionary
        iterm_session = ItermSession(
            session=new_session,
            name=name,
            max_lines=self.default_max_lines
        )

        # set_name retries internally until iTerm2 agrees (see ItermSession.set_name).
        if name:
            await iterm_session.set_name(name)

        # Add logger if logging is enabled
        if self.enable_logging and hasattr(self, "log_manager"):
            session_logger = self.log_manager.get_session_logger(
                session_id=iterm_session.id,
                session_name=iterm_session.name,
                persistent_id=iterm_session.persistent_id
            )
            iterm_session.set_logger(session_logger)

            # Log split pane creation event
            split_type = "Vertical" if vertical else "Horizontal"
            self.log_manager.log_app_event(
                "PANE_SPLIT", 
                f"Created new {split_type.lower()} split pane: {iterm_session.name} ({iterm_session.id}) - Persistent ID: {iterm_session.persistent_id}"
            )
            
        self.sessions[iterm_session.id] = iterm_session

        return iterm_session

    async def split_session_directional(
        self,
        session_id: str,
        direction: Literal["above", "below", "left", "right"],
        name: Optional[str] = None,
        profile: Optional[str] = None
    ) -> ItermSession:
        """Split an existing session in a specific direction.

        Creates a new pane by splitting an existing session. The direction
        determines where the new pane appears relative to the target session.

        Args:
            session_id: The ID of the session to split
            direction: Direction to split:
                - "above": New pane appears above the target
                - "below": New pane appears below the target
                - "left": New pane appears to the left of the target
                - "right": New pane appears to the right of the target
            name: Optional name for the new session
            profile: Optional iTerm2 profile to use. If None, uses the "MCP Agent"
                     profile if it exists.

        Returns:
            The session for the new pane

        Raises:
            ValueError: If the session is not found or direction is invalid
        """
        # Get the source session
        source_session = await self.get_session_by_id(session_id)
        if not source_session:
            raise ValueError(f"Session with ID {session_id} not found")

        # Map direction to iTerm2 API parameters
        direction_map = {
            "above": {"vertical": False, "before": True},
            "below": {"vertical": False, "before": False},
            "left": {"vertical": True, "before": True},
            "right": {"vertical": True, "before": False},
        }

        if direction not in direction_map:
            raise ValueError(f"Invalid direction: {direction}. Use one of: above, below, left, right")

        params = direction_map[direction]
        vertical = params["vertical"]
        before = params["before"]

        # Use MCP Agent profile by default if available
        profile_to_use = profile or "MCP Agent"

        # Create a new split pane with the specified profile and direction
        try:
            new_session = await source_session.session.async_split_pane(
                vertical=vertical,
                before=before,
                profile=profile_to_use
            )
        except Exception:
            # Fall back to using profile customizations if profile doesn't exist
            profile_customizations = iterm2.LocalWriteOnlyProfile()
            new_session = await source_session.session.async_split_pane(
                vertical=vertical,
                before=before,
                profile_customizations=profile_customizations
            )

        # Create a new ItermSession with logger and add to the dictionary
        iterm_session = ItermSession(
            session=new_session,
            name=name,
            max_lines=self.default_max_lines
        )

        # set_name retries internally until iTerm2 agrees (see ItermSession.set_name).
        if name:
            await iterm_session.set_name(name)

        # Add logger if logging is enabled
        if self.enable_logging and hasattr(self, "log_manager"):
            session_logger = self.log_manager.get_session_logger(
                session_id=iterm_session.id,
                session_name=iterm_session.name,
                persistent_id=iterm_session.persistent_id
            )
            iterm_session.set_logger(session_logger)

            # Log directional split pane creation event
            self.log_manager.log_app_event(
                "PANE_SPLIT_DIRECTIONAL",
                f"Created new split pane ({direction}): {iterm_session.name} ({iterm_session.id}) - Persistent ID: {iterm_session.persistent_id}"
            )

        self.sessions[iterm_session.id] = iterm_session

        return iterm_session

    async def focus_session(self, session_id: str) -> None:
        """Focus on a specific session.
        
        Args:
            session_id: The ID of the session to focus
        """
        # Get the session
        session = await self.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session with ID {session_id} not found")
        
        # Focus the session
        await session.session.async_activate()
        
    async def close_session(self, session_id: str) -> None:
        """Close a specific session.
        
        Args:
            session_id: The ID of the session to close
        """
        # Get the session
        session = await self.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session with ID {session_id} not found")
        
        # Log session closure if logging is enabled
        if self.enable_logging and hasattr(self, "log_manager"):
            # Log in the session logger
            if session.logger:
                session.logger.log_session_closed()
            
            # Log in the app logger
            self.log_manager.log_app_event(
                "SESSION_CLOSED", 
                f"Closed session: {session.name} ({session.id})"
            )
            
            # Remove the session logger
            self.log_manager.remove_session_logger(session_id)
        
        # Close the session
        await session.session.async_close()

        # Remove from our sessions dictionary
        if session_id in self.sessions:
            del self.sessions[session_id]

    async def execute_command(
        self,
        session_id: str,
        command: str,
        use_encoding: Union[bool, Literal["auto"]] = False
    ) -> None:
        """Execute a command in a session.

        Args:
            session_id: The ID of the session to execute the command in
            command: The command to execute (raw, unencoded)
            use_encoding: Encoding mode:
                - False (default): Send command directly (recommended)
                - "auto": Only encode if command contains unusual characters
                - True: Always use base64 encoding (rarely needed)

        Raises:
            ValueError: If the session is not found
        """
        # Get the session
        session = await self.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session with ID {session_id} not found")

        # Execute the command using the session's smart execute method
        await session.execute_command(command, use_encoding=use_encoding)

    async def create_multiple_sessions(self, configs: List[Dict[str, Any]]) -> Dict[str, str]:
        """Create multiple sessions with different initial commands.
        
        Args:
            configs: List of session configs with parameters:
                - name: Name for the session
                - command: (Optional) Command to run
                - layout: (Optional) Layout type if splitting from previous session
                - vertical: (Optional) Whether to split vertically (True) or horizontally (False)
                - monitor: (Optional) Whether to start monitoring the session
                
        Returns:
            Dictionary mapping session names to their IDs
        """
        results = {}
        prev_session = None
        
        for config in configs:
            session_name = config.get("name")
            command = config.get("command")
            monitor = config.get("monitor", False)
            
            if not session_name:
                continue
                
            # If no previous session or not using layout, create a new window
            if not prev_session or "layout" not in config:
                session = await self.create_window()
            else:
                # Split from previous session
                vertical = config.get("vertical", False)
                session = await self.create_split_pane(
                    session_id=prev_session.id,
                    vertical=vertical,
                    name=session_name
                )
                
            # Set session name
            await session.set_name(session_name)
            
            # Configure max_lines if specified
            max_lines = config.get("max_lines", self.default_max_lines)
            if max_lines != self.default_max_lines:
                session.set_max_lines(max_lines)
            
            # Start monitoring if requested
            if monitor and session.logger:
                try:
                    await session.start_monitoring(update_interval=0.2)
                    # Wait to ensure monitoring is fully started
                    await asyncio.sleep(1)
                    if not session.is_monitoring:
                        import logging
                        logger = logging.getLogger("iterm-terminal")
                        logger.warning(f"Failed to start monitoring for {session_name}")
                except Exception as e:
                    import logging
                    logger = logging.getLogger("iterm-terminal")
                    logger.error(f"Error starting monitoring for {session_name}: {str(e)}")
                
            # Execute command if provided
            if command:
                await session.send_text(f"{command}\n")
                
            # Save the session
            results[session_name] = session.id
            prev_session = session
            
            # Small delay to avoid race conditions
            await asyncio.sleep(0.2)
            
        # Log the batch creation
        if self.enable_logging and hasattr(self, "log_manager"):
            self.log_manager.log_app_event(
                "BATCH_CREATED",
                f"Created {len(results)} sessions in batch: {', '.join(results.keys())}"
            )
            
        return results