"""Anthropic prompt caching (system_and_3 strategy) + cache break detection.

Reduces input token costs by ~75% on multi-turn conversations by caching
the conversation prefix. Uses 4 cache_control breakpoints (Anthropic max):
  1. System prompt (stable across all turns)
  2-4. Last 3 non-system messages (rolling window)

Cache break detection (HERMES-001/claw-code inspired):
  Fingerprints each prompt component separately. When cache_read_input_tokens
  drops >50% while fingerprints are stable, logs a WARNING about unexpected
  cache break. Expected breaks (prompt changed) are logged at DEBUG.

Pure functions -- no class state, no AIAgent dependency.
TrackedPromptState is a lightweight dataclass for break detection.
"""

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _apply_cache_marker(msg: dict, cache_marker: dict, native_anthropic: bool = False) -> None:
    """Add cache_control to a single message, handling all format variations."""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: str = "5m",
    native_anthropic: bool = False,
) -> List[Dict[str, Any]]:
    """Apply system_and_3 caching strategy to messages for Anthropic models.

    Places up to 4 cache_control breakpoints: system prompt + last 3 non-system messages.

    Returns:
        Deep copy of messages with cache_control breakpoints injected.
    """
    messages = copy.deepcopy(api_messages)
    if not messages:
        return messages

    marker = {"type": "ephemeral"}
    if cache_ttl == "1h":
        marker["ttl"] = "1h"

    breakpoints_used = 0

    if messages[0].get("role") == "system":
        _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = 4 - breakpoints_used
    non_sys = [i for i in range(len(messages)) if messages[i].get("role") != "system"]
    for idx in non_sys[-remaining:]:
        _apply_cache_marker(messages[idx], marker, native_anthropic=native_anthropic)

    return messages


# ── Prompt Cache Break Detection ──────────────────────────────────────

def _fingerprint(obj: Any) -> str:
    """Stable SHA-256 fingerprint of a JSON-serializable object."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class TrackedPromptState:
    """Lightweight state for detecting prompt cache breaks.

    Updated after each API call with component fingerprints and
    cache token counts from the response.
    """
    system_hash: str = ""
    tools_hash: str = ""
    messages_hash: str = ""
    model: str = ""
    prev_cache_read_tokens: int = 0

    # Stats
    hits: int = 0
    misses: int = 0
    expected_breaks: int = 0
    unexpected_breaks: int = 0

    def update_fingerprints(
        self,
        system_prompt: Any = None,
        tools: Any = None,
        messages: Any = None,
        model: str = "",
    ) -> None:
        """Update component fingerprints before an API call."""
        if system_prompt is not None:
            self.system_hash = _fingerprint(system_prompt)
        if tools is not None:
            self.tools_hash = _fingerprint(tools)
        if messages is not None:
            self.messages_hash = _fingerprint(messages)
        if model:
            self.model = model

    def check_cache_break(
        self,
        cache_read_input_tokens: int,
        cache_creation_input_tokens: int = 0,
        prev_system_hash: str = "",
        prev_tools_hash: str = "",
        prev_model: str = "",
    ) -> Optional[str]:
        """Check for unexpected cache break after an API response.

        Args:
            cache_read_input_tokens: Tokens read from cache (from API response).
            cache_creation_input_tokens: Tokens written to cache.
            prev_*: Previous fingerprints (before update_fingerprints was called).

        Returns:
            None if no break detected, or a diagnostic string describing the break.
        """
        had_cache = self.prev_cache_read_tokens > 100  # had meaningful cache
        lost_cache = cache_read_input_tokens < self.prev_cache_read_tokens * 0.5

        # Update for next comparison
        old_prev = self.prev_cache_read_tokens
        self.prev_cache_read_tokens = cache_read_input_tokens

        if not had_cache or not lost_cache:
            if cache_read_input_tokens > 100:
                self.hits += 1
            else:
                self.misses += 1
            return None

        # Cache dropped significantly. Was it expected?
        fingerprints_stable = (
            (not prev_system_hash or prev_system_hash == self.system_hash)
            and (not prev_tools_hash or prev_tools_hash == self.tools_hash)
            and (not prev_model or prev_model == self.model)
        )

        if fingerprints_stable:
            self.unexpected_breaks += 1
            msg = (
                f"Unexpected cache break: cache_read dropped {old_prev} -> {cache_read_input_tokens} "
                f"({cache_creation_input_tokens} creation tokens) but prompt fingerprints are stable "
                f"(system={self.system_hash[:8]}, tools={self.tools_hash[:8]}, model={self.model})"
            )
            logger.warning(msg)
            return msg
        else:
            self.expected_breaks += 1
            changed = []
            if prev_system_hash and prev_system_hash != self.system_hash:
                changed.append("system")
            if prev_tools_hash and prev_tools_hash != self.tools_hash:
                changed.append("tools")
            if prev_model and prev_model != self.model:
                changed.append("model")
            msg = (
                f"Expected cache break: {', '.join(changed)} changed. "
                f"cache_read {old_prev} -> {cache_read_input_tokens}"
            )
            logger.debug(msg)
            return None

    @property
    def stats(self) -> dict:
        """Cache performance statistics."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "expected_breaks": self.expected_breaks,
            "unexpected_breaks": self.unexpected_breaks,
        }
