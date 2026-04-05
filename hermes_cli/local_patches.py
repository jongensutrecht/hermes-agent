"""Local patches applied after `hermes update`.

These patches survive code updates by being re-applied automatically at the end
of ``cmd_update()``.  Each patch function returns:
    ``True``  – patch was applied (file was modified)
    ``False`` – patch was already present (no-op)
    ``None``  – patch could not be applied (upstream changed; review needed)

Patches are idempotent: calling them on already-patched code returns False.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PATCH: status-bar-thinking-indicator
# ---------------------------------------------------------------------------
# Adds [_spinner_text] to the footer status bar so the user can see live
# what the agent is doing (thinking / running tool / etc.)

_STATUS_BAR_THINKING_PATCH_STYLE = "            'status-bar-think': 'bg:#1a1a2e #87CEEB bold',\n"


def _apply_thinking_indicator(cli_path: Path) -> bool | None:
    """Add spinner text (_spinner_text) to the status bar footer."""
    if not cli_path.exists():
        return None

    content = cli_path.read_text(encoding="utf-8")

    # --- Check if already applied ---
    if "status-bar-think" in content and "spin_frag" in content:
        return False  # Already applied

    # --- Style dict (TUI styling) ---
    if "'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold'," in content:
        if _STATUS_BAR_THINKING_PATCH_STYLE not in content:
            content = content.replace(
                "            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',\n",
                "            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',\n"
                + _STATUS_BAR_THINKING_PATCH_STYLE,
            )

    # --- Fragment injection for all three width modes ---
    # Narrow mode (< 52): add spin_frag after duration_label
    narrow_old = '''                    ("class:status-bar-dim", duration_label),
                    ("class:status-bar", " "),
                ]
            else:'''
    narrow_new = '''                    ("class:status-bar-dim", duration_label),
                ] + spin_frag + [
                    ("class:status-bar", " "),
                ]
            else:'''

    if narrow_old not in content:
        # May already be updated or upstream changed; don't guess
        logger.warning("Cannot find narrow mode insert point for thinking indicator")
        return None

    # Only replace if not already done
    if narrow_new not in content:
        content = content.replace(narrow_old, narrow_new)

    # Medium mode (52-76)
    medium_old = '''                        ("class:status-bar-dim", duration_label),
                        ("class:status-bar", " "),
                    ]
                else:
                    if snapshot["context_length"]:'''
    medium_new = '''                        ("class:status-bar-dim", duration_label),
                    ] + spin_frag + [
                        ("class:status-bar", " "),
                    ]
                else:
                    if snapshot["context_length"]:'''

    if medium_old in content and medium_new not in content:
        content = content.replace(medium_old, medium_new)

    # Wide mode (>= 76)
    wide_marker = '''                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                        ("class:status-bar", " "),
                    ]

            total_width = sum(self._status_bar_display_width'''
    wide_new = '''                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                    ] + spin_frag + [
                        ("class:status-bar", " "),
                    ]

            total_width = sum(self._status_bar_display_width'''

    if wide_marker in content and wide_new not in content:
        content = content.replace(wide_marker, wide_new)

    # --- Add spin_frag extraction at the top of the else block ---
    spinner_extract = '''            spinner = getattr(self, "_spinner_text", "") or ""
            spin_frag = [("class:status-bar-think", f" [{spinner}]")] if spinner else []

            if width < 52:'''

    if spinner_extract not in content and "if width < 52:" in content:
        # Find the first "if width < 52:" inside _get_status_bar_fragments
        # and add the spinner extraction before it
        content = content.replace("            if width < 52:", spinner_extract, 1)

    cli_path.write_text(content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# PATCH: input-needed-notifications
# ---------------------------------------------------------------------------
# Replaces bare terminal bell (\a) with full input-needed notification:
# bell + desktop sound (paplay/canberra) + notify-send + terminal title.
# Ported from Pi CLI input-needed.ts extension.

_INPUT_NEEDED_METHOD = '''
    # ── Input-needed notifications (bell + sound + desktop + title) ────
    # Survives hermes update via local_patches.py.
    # Ported from Pi CLI input-needed.ts extension.

    def _notify_input_needed(self) -> None:
        """Play bell, desktop sound, notify-send, and set terminal title."""
        if not self.bell_on_complete:
            return

        # 1. Terminal bell
        try:
            sys.stdout.write("\\a")
            sys.stdout.flush()
        except Exception:
            pass

        # 2. Set terminal title to [INPUT NODIG]
        try:
            if self._input_needed_title_base is None:
                self._input_needed_title_base = "hermes"
            sys.stdout.write(f"\\033]0;{self._input_needed_title_base}  [INPUT NODIG]\\007")
            sys.stdout.flush()
        except Exception:
            pass

        # 3. Desktop sound (best-effort, non-blocking)
        def _play_sound():
            import shutil
            if shutil.which("canberra-gtk-play"):
                try:
                    subprocess.run(
                        ["canberra-gtk-play", "-i", "message-new-instant"],
                        timeout=3, capture_output=True,
                    )
                    return
                except Exception:
                    pass
            if shutil.which("paplay"):
                for sf in [
                    "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
                    "/usr/share/sounds/freedesktop/stereo/complete.oga",
                    "/usr/share/sounds/freedesktop/stereo/bell.oga",
                ]:
                    if os.path.exists(sf):
                        try:
                            subprocess.run(["paplay", sf], timeout=4, capture_output=True)
                            return
                        except Exception:
                            pass

        threading.Thread(target=_play_sound, daemon=True).start()

        # 4. Desktop notification (best-effort, non-blocking)
        def _desktop_notify():
            import shutil
            title, body = "Hermes", "Input nodig — agent wacht op prompt."
            if shutil.which("notify-send"):
                try:
                    subprocess.run(
                        ["notify-send", title, body],
                        timeout=2, capture_output=True,
                    )
                    return
                except Exception:
                    pass
            # Fallback: OSC 777 (Ghostty, iTerm2, WezTerm)
            try:
                sys.stdout.write(f"\\033]777;notify;{title};{body}\\007")
                sys.stdout.flush()
            except Exception:
                pass

        threading.Thread(target=_desktop_notify, daemon=True).start()

    def _clear_input_needed_title(self) -> None:
        """Restore terminal title when user starts typing."""
        if self._input_needed_title_base:
            try:
                sys.stdout.write(f"\\033]0;{self._input_needed_title_base}\\007")
                sys.stdout.flush()
            except Exception:
                pass

    # ── End input-needed notifications ────────────────────────────────
'''


def _apply_input_needed(cli_path: Path) -> bool | None:
    """Replace bare bell with full input-needed notifications."""
    if not cli_path.exists():
        return None

    content = cli_path.read_text(encoding="utf-8")

    # Already applied?
    if "_notify_input_needed" in content:
        return False

    modified = False

    # 1. Add title-base attr after bell_on_complete init
    bell_init = (
        '        self.bell_on_complete = CLI_CONFIG["display"]'
        '.get("bell_on_complete", False)'
    )
    if bell_init in content and "_input_needed_title_base" not in content:
        content = content.replace(
            bell_init,
            bell_init + "\n"
            "        self._input_needed_title_base = None",
        )
        modified = True

    # 2. Inject method block before _invalidate
    invalidate_anchor = (
        "    def _invalidate(self, min_interval: float = 0.25) -> None:\n"
        '        """Throttled UI repaint'
    )
    if invalidate_anchor in content:
        content = content.replace(
            invalidate_anchor,
            _INPUT_NEEDED_METHOD + "\n" + invalidate_anchor,
        )
        modified = True
    else:
        logger.warning("Cannot find _invalidate anchor for input-needed method")
        return None

    # 3. Replace all 3 bare-bell call-sites
    bare_bell_patterns = [
        # Main agent completion
        (
            '            # Play terminal bell when agent finishes (if enabled).\n'
            '            # Works over SSH — the bell propagates to the user\'s terminal.\n'
            '            if self.bell_on_complete:\n'
            '                sys.stdout.write("\\a")\n'
            '                sys.stdout.flush()',
            '            # Notify user that input is needed (bell + sound + desktop + title).\n'
            '            self._notify_input_needed()',
        ),
        # Background task + /btw (same pattern)
        (
            '                # Play bell if enabled\n'
            '                if self.bell_on_complete:\n'
            '                    sys.stdout.write("\\a")\n'
            '                    sys.stdout.flush()',
            '                self._notify_input_needed()',
        ),
        # /btw standalone bell
        (
            '                if self.bell_on_complete:\n'
            '                    sys.stdout.write("\\a")\n'
            '                    sys.stdout.flush()',
            '                self._notify_input_needed()',
        ),
    ]
    for old, new in bare_bell_patterns:
        if old in content:
            content = content.replace(old, new)
            modified = True

    # 4. Add title-clear on agent start
    agent_start_anchor = (
        "            def run_agent():\n"
        "                nonlocal result\n"
        "                agent_message"
    )
    agent_start_new = (
        "            def run_agent():\n"
        "                nonlocal result\n"
        "                self._clear_input_needed_title()\n"
        "                agent_message"
    )
    if agent_start_anchor in content and "_clear_input_needed_title" not in content:
        content = content.replace(agent_start_anchor, agent_start_new)
        modified = True

    if not modified:
        return None

    cli_path.write_text(content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
PATCHES = [
    ("status-bar-thinking-indicator", _apply_thinking_indicator),
    ("input-needed-notifications", _apply_input_needed),
]
