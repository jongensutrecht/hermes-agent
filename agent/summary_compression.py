"""Budget-aware summary compression without LLM calls.

Compresses text using priority-based line selection within a character/line
budget. Designed as a cheap pre-pass before LLM summarization, and as a
standalone compressor for subagent context and cron-job prompts.

Inspired by claw-code summary_compression.rs — adapted for Python/Hermes.

Priority tiers (lower = more important):
  P0: Scope, current work, goal, pending work, key decisions
  P1: Section headers (lines starting with # or ##)
  P2: Bullet points (lines starting with - or *)
  P3: Everything else (filler, narrative, timestamps)

Usage:
    from agent.summary_compression import compress_summary

    result = compress_summary(long_text)
    print(result.text)           # compressed output
    print(result.lines_omitted)  # how many lines were cut

No LLM calls. No state. No imports from agent runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CompressionBudget:
    """Budget constraints for compression."""
    max_chars: int = 1200
    max_lines: int = 24
    max_line_chars: int = 160


@dataclass(frozen=True)
class CompressionResult:
    """Output of compress_summary."""
    text: str
    original_lines: int
    kept_lines: int
    lines_omitted: int
    original_chars: int
    compressed_chars: int


# Default budget
DEFAULT_BUDGET = CompressionBudget()

# ── Priority classification ───────────────────────────────────────────

# P0 patterns: scope, current work, goal, key fields
_P0_PATTERNS = re.compile(
    r"^(?:"
    r"(?:summary|conversation summary|goal|objective|scope|current work|"
    r"current task|pending work|next steps|decisions|key decision|"
    r"progress|status|result|outcome|conclusion|files changed|"
    r"- scope:|- current work:|- pending work:|- goal:|- status:|"
    r"- progress:|- next steps:|- decisions:|- files:|"
    r"- files changed:|- result:|- outcome:)"
    r")",
    re.IGNORECASE,
)

# P1 patterns: section headers
_P1_PATTERNS = re.compile(r"^#{1,4}\s")

# P2 patterns: bullet points
_P2_PATTERNS = re.compile(r"^\s*[-*•]\s")


def _classify_line(line: str) -> int:
    """Return priority tier (0-3) for a line. Lower = more important."""
    stripped = line.strip()
    if not stripped:
        return 3  # empty lines are filler

    if _P0_PATTERNS.match(stripped):
        return 0
    if _P1_PATTERNS.match(stripped):
        return 1
    if _P2_PATTERNS.match(stripped):
        return 2
    return 3


# ── Pre-processing ────────────────────────────────────────────────────

def _normalize(text: str) -> list[str]:
    """Normalize text: collapse multiple blank lines, strip trailing whitespace."""
    lines = text.splitlines()
    result: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.rstrip()
        is_blank = not stripped
        if is_blank and prev_blank:
            continue  # collapse consecutive blanks
        result.append(stripped)
        prev_blank = is_blank
    return result


def _deduplicate(lines: list[str]) -> list[str]:
    """Remove duplicate lines (case-insensitive), keeping first occurrence."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if not key:  # keep blank lines (they're structural)
            result.append(line)
            continue
        if key not in seen:
            seen.add(key)
            result.append(line)
    return result


def _truncate_line(line: str, max_chars: int) -> str:
    """Truncate a single line if it exceeds max_chars."""
    if len(line) <= max_chars:
        return line
    return line[:max_chars - 12] + " [truncated]"


# ── Main compression ─────────────────────────────────────────────────

def compress_summary(
    text: str,
    budget: CompressionBudget | None = None,
) -> CompressionResult:
    """Compress summary text within budget using priority-based line selection.

    Algorithm:
      1. Normalize (collapse blanks, strip whitespace)
      2. Deduplicate lines
      3. Classify each line by priority tier (0-3)
      4. Greedy selection: iterate tiers low→high, add lines that fit budget
      5. Reconstruct text in original order
      6. Add "[N lines omitted]" notice if lines were cut

    Args:
        text: Input text to compress.
        budget: Compression budget. Defaults to 1200 chars / 24 lines / 160 chars per line.

    Returns:
        CompressionResult with compressed text and stats.
    """
    if not text or not text.strip():
        return CompressionResult(
            text="", original_lines=0, kept_lines=0,
            lines_omitted=0, original_chars=0, compressed_chars=0,
        )

    b = budget or DEFAULT_BUDGET

    # Step 1-2: normalize and deduplicate
    lines = _normalize(text)
    original_lines = len(lines)
    original_chars = len(text)

    lines = _deduplicate(lines)

    # Step 3: classify
    classified: list[tuple[int, int, str]] = []  # (priority, original_index, line)
    for i, line in enumerate(lines):
        truncated = _truncate_line(line, b.max_line_chars)
        priority = _classify_line(truncated)
        classified.append((priority, i, truncated))

    # Step 4: greedy selection by priority
    selected_indices: set[int] = set()
    chars_used = 0
    lines_used = 0

    for tier in range(4):  # P0, P1, P2, P3
        tier_lines = [(idx, line) for (prio, idx, line) in classified if prio == tier]
        for idx, line in tier_lines:
            line_chars = len(line) + 1  # +1 for newline
            if lines_used >= b.max_lines:
                break
            if chars_used + line_chars > b.max_chars:
                break
            selected_indices.add(idx)
            chars_used += line_chars
            lines_used += 1

    # Step 5: reconstruct in original order
    kept_lines_list = [
        classified[i][2]  # the (possibly truncated) line
        for i in sorted(selected_indices)
    ]

    # Step 6: add omission notice
    omitted = original_lines - len(kept_lines_list)
    if omitted > 0:
        kept_lines_list.append(f"\n[{omitted} lines omitted]")

    compressed_text = "\n".join(kept_lines_list)

    return CompressionResult(
        text=compressed_text,
        original_lines=original_lines,
        kept_lines=len(kept_lines_list) - (1 if omitted > 0 else 0),
        lines_omitted=omitted,
        original_chars=original_chars,
        compressed_chars=len(compressed_text),
    )
