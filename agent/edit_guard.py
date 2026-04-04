"""Edit Guard — Anti-regression protection for file edits.

Tracks content hashes per file across an agent session. When a write
would revert a file to a previous state (before the most recent edit),
emits a strong warning. Does NOT block writes — the model decides.

Usage (from file_tools.py):
    from agent.edit_guard import edit_guard

    # After reading a file:
    edit_guard.record_read(path, content_hash)

    # Before writing a file:
    warning = edit_guard.check_reversion(path, new_content)
    if warning:
        result_dict["_regression_warning"] = warning

    # After writing:
    edit_guard.record_write(path, new_content)

Thread-safe. Fail-open. No agent runtime imports.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    """Short SHA-256 hash of content for comparison."""
    return hashlib.sha256(content.encode(errors="replace")).hexdigest()[:12]


@dataclass
class FileEditRecord:
    """History of a single file's content states."""
    path: str
    # Ordered list of content hashes: [initial_read, after_edit_1, after_edit_2, ...]
    history: list[str] = field(default_factory=list)
    # Turn number when each hash was recorded
    turns: list[int] = field(default_factory=list)
    # The hash that was most recently written by the agent
    last_written_hash: str = ""
    last_write_turn: int = 0


class EditGuard:
    """Tracks file edit history to detect self-reversions.

    Thread-safe singleton — one instance per agent session.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._files: dict[str, FileEditRecord] = {}
        self._turn: int = 0

    def set_turn(self, turn: int) -> None:
        """Update the current turn counter (call from the conversation loop)."""
        self._turn = turn

    def record_read(self, path: str, content: str) -> None:
        """Record a file's content at read time."""
        h = _content_hash(content)
        with self._lock:
            rec = self._files.get(path)
            if rec is None:
                rec = FileEditRecord(path=path)
                self._files[path] = rec
            # Only add if different from last recorded state
            if not rec.history or rec.history[-1] != h:
                rec.history.append(h)
                rec.turns.append(self._turn)

    def record_write(self, path: str, content: str) -> None:
        """Record a file's content after a successful write."""
        h = _content_hash(content)
        with self._lock:
            rec = self._files.get(path)
            if rec is None:
                rec = FileEditRecord(path=path)
                self._files[path] = rec
            rec.last_written_hash = h
            rec.last_write_turn = self._turn
            if not rec.history or rec.history[-1] != h:
                rec.history.append(h)
                rec.turns.append(self._turn)

    def check_reversion(self, path: str, new_content: str) -> Optional[str]:
        """Check if writing new_content would revert to a previous state.

        Returns a warning string if reversion detected, None otherwise.
        Only warns if the new content matches a state BEFORE the most
        recent write (not the current state).
        """
        h = _content_hash(new_content)
        with self._lock:
            rec = self._files.get(path)
            if rec is None or len(rec.history) < 2:
                return None

            # Don't warn if writing the same content (idempotent write)
            if h == rec.last_written_hash:
                return None

            # Check if new content matches any PREVIOUS state (before last write)
            # This means the agent is reverting its own edit
            for i, old_hash in enumerate(rec.history[:-1]):
                if old_hash == h and rec.last_written_hash and rec.last_written_hash != h:
                    revert_turn = rec.turns[i] if i < len(rec.turns) else "?"
                    last_edit_turn = rec.last_write_turn
                    warning = (
                        f"REGRESSION WARNING: This write reverts {path} to its state "
                        f"from turn {revert_turn}, undoing your edit from turn "
                        f"{last_edit_turn}. Read the file first to see what you "
                        f"already changed, then decide if this revert is intentional."
                    )
                    logger.warning(warning)
                    return warning

        return None

    def get_edited_files_summary(self) -> str:
        """Return a summary of all files edited in this session.

        Useful for injecting into context after compression.
        """
        with self._lock:
            edited = [
                (rec.path, rec.last_write_turn)
                for rec in self._files.values()
                if rec.last_written_hash
            ]
        if not edited:
            return ""
        edited.sort(key=lambda x: x[1])
        lines = ["Files edited in this session (do not revert without re-reading):"]
        for path, turn in edited:
            lines.append(f"  - {path} (last edited turn {turn})")
        return "\n".join(lines)

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "tracked_files": len(self._files),
                "edited_files": sum(1 for r in self._files.values() if r.last_written_hash),
                "current_turn": self._turn,
            }


# Module-level singleton
edit_guard = EditGuard()
