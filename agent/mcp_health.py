"""MCP Graceful Degradation — per-server health tracking.

Tracks health status per MCP server so that a single server failure
doesn't take down all MCP tools. Provides a missing_tools report so
the agent knows which capabilities are lost.

Inspired by claw-code mcp_lifecycle_hardened.rs — adapted for Python/Hermes.

Usage:
    tracker = McpHealthTracker()
    tracker.register_server("firecrawl", tools=["web_search", "web_extract"])
    tracker.register_server("browserbase", tools=["browser_navigate", "browser_click"])

    # On server failure:
    tracker.mark_failed("firecrawl", error="Connection refused", recoverable=True)

    # Check what's available:
    print(tracker.healthy_servers)    # ["browserbase"]
    print(tracker.missing_tools)     # ["web_search", "web_extract"]
    print(tracker.is_degraded)       # True

Thread-safe. Fail-open. No agent runtime imports.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ServerStatus(Enum):
    """Health status of an MCP server."""
    HEALTHY = auto()
    DEGRADED = auto()   # intermittent errors, still partially working
    DEAD = auto()       # failed to start or consecutive failures


class ErrorClass(Enum):
    """Classification of MCP errors."""
    STARTUP = auto()        # server failed to start
    HANDSHAKE = auto()      # protocol handshake failed
    CONFIG = auto()         # configuration error
    TIMEOUT = auto()        # server timed out
    CONNECTION = auto()     # connection refused/reset
    PROTOCOL = auto()       # protocol-level error during tool call
    UNKNOWN = auto()        # unclassified

    @property
    def recoverable(self) -> bool:
        """Whether this error class is typically recoverable."""
        return self in (
            ErrorClass.TIMEOUT,
            ErrorClass.CONNECTION,
            ErrorClass.PROTOCOL,
        )

    @classmethod
    def classify(cls, error: str, recoverable_hint: bool | None = None) -> "ErrorClass":
        """Best-effort classification from error string."""
        msg = error.lower() if error else ""
        if "startup" in msg or "spawn" in msg or "failed to start" in msg:
            result = cls.STARTUP
        elif "handshake" in msg or "initialize" in msg:
            result = cls.HANDSHAKE
        elif "config" in msg or "configuration" in msg or "invalid" in msg:
            result = cls.CONFIG
        elif "timeout" in msg or "timed out" in msg:
            result = cls.TIMEOUT
        elif "connection" in msg or "refused" in msg or "reset" in msg:
            result = cls.CONNECTION
        elif "protocol" in msg or "json-rpc" in msg:
            result = cls.PROTOCOL
        else:
            result = cls.UNKNOWN

        # Override recoverability if explicitly specified
        # (only matters for the ServerEntry tracking, not the enum property)
        return result


@dataclass
class ServerEntry:
    """State of a single MCP server."""
    name: str
    tools: list[str] = field(default_factory=list)
    status: ServerStatus = ServerStatus.HEALTHY
    last_error: str = ""
    error_class: ErrorClass | None = None
    recoverable: bool = True
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0

    # After this many consecutive failures, mark as DEAD
    DEAD_THRESHOLD: int = 3


class McpHealthTracker:
    """Thread-safe per-server health tracker for MCP.

    Designed to be instantiated once per agent session and updated
    as servers are registered, succeed, or fail.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._servers: dict[str, ServerEntry] = {}

    def register_server(self, name: str, tools: list[str] | None = None) -> None:
        """Register an MCP server with its provided tools."""
        with self._lock:
            self._servers[name] = ServerEntry(
                name=name,
                tools=list(tools or []),
                status=ServerStatus.HEALTHY,
                last_success_time=time.time(),
            )
            logger.debug("MCP server registered: %s (tools: %s)", name, tools)

    def mark_failed(
        self,
        name: str,
        error: str = "",
        recoverable: bool | None = None,
    ) -> None:
        """Mark a server as failed. Tracks consecutive failures."""
        with self._lock:
            entry = self._servers.get(name)
            if entry is None:
                # Unknown server — register it as dead
                entry = ServerEntry(name=name, status=ServerStatus.DEAD)
                self._servers[name] = entry

            error_class = ErrorClass.classify(error)
            is_recoverable = recoverable if recoverable is not None else error_class.recoverable

            entry.last_error = error
            entry.error_class = error_class
            entry.recoverable = is_recoverable
            entry.consecutive_failures += 1
            entry.last_failure_time = time.time()

            if not is_recoverable or entry.consecutive_failures >= entry.DEAD_THRESHOLD:
                entry.status = ServerStatus.DEAD
                logger.warning(
                    "MCP server %s marked DEAD: %s (class=%s, consecutive=%d)",
                    name, error, error_class.name, entry.consecutive_failures,
                )
            else:
                entry.status = ServerStatus.DEGRADED
                logger.info(
                    "MCP server %s DEGRADED: %s (class=%s, consecutive=%d)",
                    name, error, error_class.name, entry.consecutive_failures,
                )

    def mark_healthy(self, name: str) -> None:
        """Mark a server as healthy (e.g., after a successful tool call)."""
        with self._lock:
            entry = self._servers.get(name)
            if entry is None:
                return
            entry.status = ServerStatus.HEALTHY
            entry.consecutive_failures = 0
            entry.last_error = ""
            entry.error_class = None
            entry.last_success_time = time.time()

    def is_server_available(self, name: str) -> bool:
        """Check if a specific server is healthy or degraded (still usable)."""
        with self._lock:
            entry = self._servers.get(name)
            return entry is not None and entry.status != ServerStatus.DEAD

    @property
    def healthy_servers(self) -> list[str]:
        """List of fully healthy server names."""
        with self._lock:
            return [
                name for name, entry in self._servers.items()
                if entry.status == ServerStatus.HEALTHY
            ]

    @property
    def degraded_servers(self) -> list[str]:
        """List of degraded (but still usable) server names."""
        with self._lock:
            return [
                name for name, entry in self._servers.items()
                if entry.status == ServerStatus.DEGRADED
            ]

    @property
    def dead_servers(self) -> list[str]:
        """List of dead (unusable) server names."""
        with self._lock:
            return [
                name for name, entry in self._servers.items()
                if entry.status == ServerStatus.DEAD
            ]

    @property
    def available_tools(self) -> list[str]:
        """Tools from healthy + degraded servers."""
        with self._lock:
            tools: list[str] = []
            for entry in self._servers.values():
                if entry.status != ServerStatus.DEAD:
                    tools.extend(entry.tools)
            return tools

    @property
    def missing_tools(self) -> list[str]:
        """Tools that are unavailable due to dead servers."""
        with self._lock:
            tools: list[str] = []
            for entry in self._servers.values():
                if entry.status == ServerStatus.DEAD:
                    tools.extend(entry.tools)
            return tools

    @property
    def is_degraded(self) -> bool:
        """True if any server is not healthy."""
        with self._lock:
            return any(
                e.status != ServerStatus.HEALTHY
                for e in self._servers.values()
            )

    def get_degraded_report(self) -> dict:
        """Structured degradation report."""
        with self._lock:
            return {
                "healthy": [n for n, e in self._servers.items() if e.status == ServerStatus.HEALTHY],
                "degraded": [n for n, e in self._servers.items() if e.status == ServerStatus.DEGRADED],
                "dead": [n for n, e in self._servers.items() if e.status == ServerStatus.DEAD],
                "available_tools": self.available_tools,
                "missing_tools": self.missing_tools,
                "total_servers": len(self._servers),
            }
