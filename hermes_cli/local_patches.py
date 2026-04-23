"""Idempotent local patches that survive ``hermes update`` (git pull + reset --hard).

Each function receives ``cli_path: Path`` and returns:
- True   → freshly applied
- False  → already present (no-op)
- None   → upstream context changed, human review needed
"""

from pathlib import Path


def _apply_discord_skill_compat(cli_path: Path) -> bool | None:
    """Keep Discord /skill registration working even if local drift removes helpers.

    This patch touches sibling files under the Hermes source tree:
    - hermes_cli/commands.py: restore discord_skill_commands_by_category()
    - gateway/platforms/discord.py: fall back to discord_skill_commands()
      if the grouped helper is missing
    """
    project_root = cli_path.parent
    commands_path = project_root / "hermes_cli" / "commands.py"
    discord_path = project_root / "gateway" / "platforms" / "discord.py"

    commands = commands_path.read_text(encoding="utf-8")
    discord = discord_path.read_text(encoding="utf-8")
    changed = False

    helper_block = '''def discord_skill_commands_by_category(
    reserved_names: set[str],
) -> tuple[dict[str, list[tuple[str, str, str]]], list[tuple[str, str, str]], int]:
    """Return skill entries organized by category for Discord ``/skill`` subcommand groups.

    Skills whose directory is nested at least 2 levels under ``SKILLS_DIR``
    (e.g. ``creative/ascii-art/SKILL.md``) are grouped by their top-level
    category. Root-level skills (e.g. ``dogfood/SKILL.md``) are returned as
    uncategorized.
    """
    from pathlib import Path as _P

    _platform_disabled: set[str] = set()
    try:
        from agent.skill_utils import get_disabled_skill_names

        _platform_disabled = get_disabled_skill_names(platform="discord")
    except Exception:
        pass

    categories: dict[str, list[tuple[str, str, str]]] = {}
    uncategorized: list[tuple[str, str, str]] = []
    _names_used: set[str] = set(reserved_names)
    hidden = 0

    try:
        from agent.skill_commands import get_skill_commands
        from tools.skills_tool import SKILLS_DIR

        _skills_dir = SKILLS_DIR.resolve()
        _hub_dir = (SKILLS_DIR / ".hub").resolve()
        skill_cmds = get_skill_commands()

        for cmd_key in sorted(skill_cmds):
            info = skill_cmds[cmd_key]
            skill_path = info.get("skill_md_path", "")
            if not skill_path:
                continue
            sp = _P(skill_path).resolve()
            if not str(sp).startswith(str(_skills_dir)):
                continue
            if str(sp).startswith(str(_hub_dir)):
                continue

            skill_name = info.get("name", "")
            if skill_name in _platform_disabled:
                continue

            raw_name = cmd_key.lstrip("/")
            discord_name = raw_name[:32]
            if discord_name in _names_used:
                continue
            _names_used.add(discord_name)

            desc = info.get("description", "")
            if len(desc) > 100:
                desc = desc[:97] + "..."

            try:
                rel = sp.parent.relative_to(_skills_dir)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) >= 2:
                cat = parts[0]
                categories.setdefault(cat, []).append((discord_name, desc, cmd_key))
            else:
                uncategorized.append((discord_name, desc, cmd_key))
    except Exception:
        pass

    _MAX_GROUPS = 25
    _MAX_PER_GROUP = 25

    trimmed_categories: dict[str, list[tuple[str, str, str]]] = {}
    group_count = 0
    for cat in sorted(categories):
        if group_count >= _MAX_GROUPS:
            hidden += len(categories[cat])
            continue
        entries = categories[cat][:_MAX_PER_GROUP]
        hidden += max(0, len(categories[cat]) - _MAX_PER_GROUP)
        trimmed_categories[cat] = entries
        group_count += 1

    remaining_slots = _MAX_GROUPS - group_count
    if len(uncategorized) > remaining_slots:
        hidden += len(uncategorized) - remaining_slots
        uncategorized = uncategorized[:remaining_slots]

    return trimmed_categories, uncategorized, hidden


'''
    if "def discord_skill_commands_by_category(" not in commands:
        anchor = "\n\ndef slack_subcommand_map() -> dict[str, str]:\n"
        if anchor not in commands or "def discord_skill_commands(" not in commands:
            return None
        commands = commands.replace(anchor, f"\n\n{helper_block}{anchor.lstrip()}", 1)
        changed = True

    fallback_old = '''        try:\n            from hermes_cli.commands import discord_skill_commands_by_category\n\n            existing_names = set()\n            try:\n                existing_names = {cmd.name for cmd in tree.get_commands()}\n            except Exception:\n                pass\n\n            # Reuse the existing collector for consistent filtering\n            # (per-platform disabled, hub-excluded, name clamping), then\n            # flatten — the category grouping was only useful for the\n            # nested layout.\n            categories, uncategorized, hidden = discord_skill_commands_by_category(\n                reserved_names=existing_names,\n            )\n            entries: list[tuple[str, str, str]] = list(uncategorized)\n            for cat_skills in categories.values():\n                entries.extend(cat_skills)\n'''
    fallback_new = '''        try:\n            try:\n                from hermes_cli.commands import (\n                    discord_skill_commands_by_category,\n                    discord_skill_commands,\n                )\n            except ImportError:\n                from hermes_cli.commands import discord_skill_commands\n\n                discord_skill_commands_by_category = None\n\n            existing_names = set()\n            try:\n                existing_names = {cmd.name for cmd in tree.get_commands()}\n            except Exception:\n                pass\n\n            if discord_skill_commands_by_category is None:\n                entries, hidden = discord_skill_commands(\n                    max_slots=10_000,\n                    reserved_names=existing_names,\n                )\n            else:\n                # Reuse the existing collector for consistent filtering\n                # (per-platform disabled, hub-excluded, name clamping), then\n                # flatten — the category grouping was only useful for the\n                # nested layout.\n                categories, uncategorized, hidden = discord_skill_commands_by_category(\n                    reserved_names=existing_names,\n                )\n                entries: list[tuple[str, str, str]] = list(uncategorized)\n                for cat_skills in categories.values():\n                    entries.extend(cat_skills)\n'''
    if "max_slots=10_000" not in discord:
        if fallback_old not in discord:
            return None
        discord = discord.replace(fallback_old, fallback_new, 1)
        changed = True

    if not changed:
        return False

    commands_path.write_text(commands, encoding="utf-8")
    discord_path.write_text(discord, encoding="utf-8")

    import ast

    ast.parse(commands)
    ast.parse(discord)
    return True


def _apply_session_duration_counter(cli_path: Path) -> bool | None:
    """Keep footer duration tied to the full session, not the last thinking turn."""
    content = cli_path.read_text(encoding="utf-8")

    session_elapsed = (
        '        # Session timer: total elapsed time since this Hermes session started.\n'
        '        elapsed_seconds = max(0.0, (datetime.now() - self.session_start).total_seconds())\n'
    )
    if session_elapsed in content:
        return False

    task_elapsed = (
        '        # Task timer: live while running, frozen after turn ends\n'
        '        frozen = getattr(self, "_task_elapsed_frozen", None)\n'
        '        if frozen is not None:\n'
        '            elapsed_seconds = frozen\n'
        '        else:\n'
        '            task_start = getattr(self, "_task_start", None)\n'
        '            if task_start is not None:\n'
        '                elapsed_seconds = max(0.0, (datetime.now() - task_start).total_seconds())\n'
        '            else:\n'
        '                elapsed_seconds = 0.0\n'
    )
    if task_elapsed not in content:
        return None

    content = content.replace(task_elapsed, session_elapsed, 1)
    cli_path.write_text(content, encoding="utf-8")

    import ast
    ast.parse(content)
    return True


def _apply_pi_input_surface(cli_path: Path) -> bool | None:
    """Match Hermes' input block to Pi's editor surface."""
    content = cli_path.read_text(encoding="utf-8")

    if (
        "Pi-style editor surface: keep one dark padding row above and below the" in content
        and "'input-surface': 'bg:#181c22'" in content
        and "'input-rule': '#a885ff'" in content
    ):
        return False

    replacements = [
        (
            "        input_rule_bot = Window(\n"
            "            char='─',\n"
            "            height=lambda: cli_ref._tui_input_rule_height(\"bottom\"),\n"
            "            style='class:input-rule',\n"
            "        )\n\n"
            "        # Image attachment indicator — shows badges like [📎 Image #1] above input\n",
            "        input_rule_bot = Window(\n"
            "            char='─',\n"
            "            height=lambda: cli_ref._tui_input_rule_height(\"bottom\"),\n"
            "            style='class:input-rule',\n"
            "        )\n\n"
            "        # Pi-style editor surface: keep one dark padding row above and below the\n"
            "        # actual input line so Hermes matches Pi's editor block.\n"
            "        input_surface_top = Window(\n"
            "            char=' ',\n"
            "            height=1,\n"
            "            style='class:input-surface',\n"
            "        )\n"
            "        input_surface_bottom = Window(\n"
            "            char=' ',\n"
            "            height=1,\n"
            "            style='class:input-surface',\n"
            "        )\n\n"
            "        # Image attachment indicator — shows badges like [📎 Image #1] above input\n",
        ),
        (
            "                    input_rule_top=input_rule_top,\n"
            "                    image_bar=image_bar,\n"
            "                    input_area=input_area,\n"
            "                    input_rule_bot=input_rule_bot,\n"
            "                    voice_status_bar=voice_status_bar,\n",
            "                    input_rule_top=input_rule_top,\n"
            "                    image_bar=image_bar,\n"
            "                    input_surface_top=input_surface_top,\n"
            "                    input_area=input_area,\n"
            "                    input_surface_bottom=input_surface_bottom,\n"
            "                    input_rule_bot=input_rule_bot,\n"
            "                    voice_status_bar=voice_status_bar,\n",
        ),
        (
            "        self._tui_style_base = {\n"
            "            'input-area': '#FFF8DC',\n"
            "            'placeholder': '#555555 italic',\n"
            "            'prompt': '#FFF8DC',\n"
            "            'prompt-working': '#888888 italic',\n"
            "            'hint': '#555555 italic',\n",
            "        self._tui_style_base = {\n"
            "            'input-surface': 'bg:#181c22',\n"
            "            'input-area': 'bg:#181c22 #FFF8DC',\n"
            "            'placeholder': 'bg:#181c22 #6f7782 italic',\n"
            "            'prompt': 'bg:#181c22 #a885ff bold',\n"
            "            'prompt-working': 'bg:#181c22 #a885ff',\n"
            "            'hint': '#555555 italic',\n",
        ),
        (
            "            # Bronze horizontal rules around the input area\n"
            "            'input-rule': '#CD7F32',\n",
            "            # Pi-style purple borders around the editor surface\n"
            "            'input-rule': '#a885ff',\n",
        ),
        (
            "            # Voice mode\n"
            "            'voice-prompt': '#87CEEB',\n"
            "            'voice-recording': '#FF4444 bold',\n",
            "            # Voice mode\n"
            "            'voice-prompt': 'bg:#181c22 #a885ff',\n"
            "            'voice-recording': 'bg:#181c22 #FF4444 bold',\n",
        ),
    ]

    for old, new in replacements:
        if old not in content:
            return None
        content = content.replace(old, new, 1)

    cli_path.write_text(content, encoding="utf-8")

    import ast

    ast.parse(content)
    return True


def _apply_input_needed_footer_badge(cli_path: Path) -> bool | None:
    """Restore input-needed bell/sound + footer state + slash command after updates."""
    project_root = cli_path.parent
    commands_path = project_root / "hermes_cli" / "commands.py"

    cli_content = cli_path.read_text(encoding="utf-8")
    commands_content = commands_path.read_text(encoding="utf-8")
    changed = False

    if (
        'self._input_needed = False' in cli_content
        and 'self._input_notified = False' in cli_content
        and 'self._input_needed_enabled = True' in cli_content
        and 'def _notify_input_needed(self) -> None:' in cli_content
        and 'def _clear_input_needed(self) -> None:' in cli_content
        and 'def _handle_input_needed_command(self, command: str):' in cli_content
        and 'elif canonical == "input-needed":' in cli_content
        and '        self._clear_input_needed()\n\n        turn_route = self._resolve_turn_agent_config(message)' in cli_content
        and '                        app.invalidate()  # Refresh status line\n                        self._notify_input_needed()\n' in cli_content
        and '        width = width or self._get_tui_terminal_width()\n        if not getattr(self, "_input_needed_enabled", True):\n            return []\n        if not getattr(self, "_input_needed", False):\n            return []\n' in cli_content
        and 'input_needed_frag = self._get_footer_input_needed_fragments(width=width)' in cli_content
        and 'frags.extend(input_needed_frag)' in cli_content
        and '*input_needed_frag,' in cli_content
        and 'CommandDef("input-needed", "Configure/test input-needed notifications"' in commands_content
    ):
        return False

    init_old = (
        '        # Status bar visibility (toggled via /statusbar)\n'
        '        self._status_bar_visible = True\n\n'
        '        # Background task tracking: {task_id: threading.Thread}\n'
    )
    init_new = (
        '        # Status bar visibility (toggled via /statusbar)\n'
        '        self._status_bar_visible = True\n'
        '        self._input_needed = False\n'
        '        self._input_notified = False\n'
        '        self._input_needed_enabled = True\n\n'
        '        # Background task tracking: {task_id: threading.Thread}\n'
    )
    if 'self._input_notified = False' not in cli_content:
        if init_old not in cli_content:
            return None
        cli_content = cli_content.replace(init_old, init_new, 1)
        changed = True

    methods_anchor = '    def _get_footer_input_needed_fragments(self, width: Optional[int] = None):\n'
    methods_block = '''    def _notify_input_needed(self) -> None:
        """Bell + desktop notify + set awaiting-input state for the status bar."""
        if not getattr(self, "_input_needed_enabled", True):
            return
        if getattr(self, "_input_notified", False):
            return
        self._input_needed = True
        self._input_notified = True

        try:
            if self._app:
                self._app.output.bell()
                self._app.output.flush()
        except Exception:
            pass

        try:
            sys.stderr.write("\\a")
            sys.stderr.flush()
        except Exception:
            pass

        def _desktop_notify_and_sound():
            import shutil
            import subprocess

            if shutil.which("notify-send"):
                try:
                    subprocess.run(
                        ["notify-send", "--urgency=normal", "Hermes", "Input nodig — agent wacht op prompt."],
                        timeout=2,
                        capture_output=True,
                    )
                except Exception:
                    pass

            bell_sound = "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga"
            if os.path.exists(bell_sound) and shutil.which("paplay"):
                try:
                    subprocess.run(["paplay", bell_sound], timeout=2, capture_output=True)
                    return
                except Exception:
                    pass

            fallback_sound = "/usr/share/sounds/freedesktop/stereo/bell.oga"
            if os.path.exists(fallback_sound) and shutil.which("paplay"):
                try:
                    subprocess.run(["paplay", fallback_sound], timeout=2, capture_output=True)
                except Exception:
                    pass

        t = threading.Thread(target=_desktop_notify_and_sound, daemon=False)
        t.start()
        t.join(timeout=5)

        try:
            self._invalidate(min_interval=0)
        except Exception:
            pass

    def _clear_input_needed(self) -> None:
        """Clear awaiting-input state when a new turn starts."""
        self._input_needed = False
        self._input_notified = False
        try:
            self._invalidate(min_interval=0)
        except Exception:
            pass

    def _handle_input_needed_command(self, command: str):
        """Handle /input-needed on|off|toggle|test|status."""
        parts = command.strip().split(maxsplit=1)
        subcommand = parts[1].lower().strip() if len(parts) > 1 else "status"

        if subcommand == "on":
            self._input_needed_enabled = True
            self._input_notified = False
            _cprint("  input-needed: aan")
            self._invalidate(min_interval=0)
            return

        if subcommand == "off":
            self._input_needed_enabled = False
            self._clear_input_needed()
            _cprint("  input-needed: uit")
            return

        if subcommand == "toggle":
            self._input_needed_enabled = not self._input_needed_enabled
            if not self._input_needed_enabled:
                self._clear_input_needed()
            self._invalidate(min_interval=0)
            _cprint(f"  input-needed: {'aan' if self._input_needed_enabled else 'uit'}")
            return

        if subcommand == "test":
            self._notify_input_needed()
            _cprint("  input-needed: test getriggerd (bell/geluid/notificatie)")
            return

        _cprint(
            f"  input-needed status: {'aan' if self._input_needed_enabled else 'uit'}, "
            f"{'waiting' if self._input_needed else 'idle'}"
        )

'''
    if 'def _notify_input_needed(self) -> None:' not in cli_content:
        if methods_anchor not in cli_content:
            return None
        cli_content = cli_content.replace(methods_anchor, methods_block + methods_anchor, 1)
        changed = True

    footer_old = '''    def _get_footer_input_needed_fragments(self, width: Optional[int] = None):
        """Return the footer fragment that marks Hermes as ready for input."""
        width = width or self._get_tui_terminal_width()
        awaiting = (
            not getattr(self, "_agent_running", False)
            and not getattr(self, "_clarify_state", None)
            and not getattr(self, "_sudo_state", None)
            and not getattr(self, "_approval_state", None)
            and not getattr(self, "_voice_recording", False)
            and not getattr(self, "_voice_processing", False)
        )
        if not awaiting:
            return []
        if width < 52:
            return [("class:status-bar-input", "INPUT NODIG")]
        return [("class:status-bar-input", " 🟡 INPUT NODIG")]
'''
    footer_new = '''    def _get_footer_input_needed_fragments(self, width: Optional[int] = None):
        """Return the footer fragment that marks Hermes as waiting for input."""
        width = width or self._get_tui_terminal_width()
        if not getattr(self, "_input_needed_enabled", True):
            return []
        if not getattr(self, "_input_needed", False):
            return []
        if width < 52:
            return [("class:status-bar-input", "INPUT NODIG")]
        return [("class:status-bar-input", " 🟡 INPUT NODIG")]
'''
    footer_marker = '        width = width or self._get_tui_terminal_width()\n        if not getattr(self, "_input_needed_enabled", True):\n            return []\n        if not getattr(self, "_input_needed", False):\n            return []\n'
    if footer_marker not in cli_content:
        if footer_old not in cli_content:
            return None
        cli_content = cli_content.replace(footer_old, footer_new, 1)
        changed = True

    dispatch_old = (
        '        elif canonical == "statusbar":\n'
        '            self._status_bar_visible = not self._status_bar_visible\n'
        '            state = "visible" if self._status_bar_visible else "hidden"\n'
        '            self._console_print(f"  Status bar {state}")\n'
        '        elif canonical == "verbose":\n'
    )
    dispatch_new = (
        '        elif canonical == "statusbar":\n'
        '            self._status_bar_visible = not self._status_bar_visible\n'
        '            state = "visible" if self._status_bar_visible else "hidden"\n'
        '            self._console_print(f"  Status bar {state}")\n'
        '        elif canonical == "input-needed":\n'
        '            self._handle_input_needed_command(cmd_original)\n'
        '        elif canonical == "verbose":\n'
    )
    if 'elif canonical == "input-needed":' not in cli_content:
        if dispatch_old not in cli_content:
            return None
        cli_content = cli_content.replace(dispatch_old, dispatch_new, 1)
        changed = True

    chat_old = (
        '        if not self._ensure_runtime_credentials():\n'
        '            return None\n\n'
        '        turn_route = self._resolve_turn_agent_config(message)\n'
    )
    chat_new = (
        '        if not self._ensure_runtime_credentials():\n'
        '            return None\n\n'
        '        self._clear_input_needed()\n\n'
        '        turn_route = self._resolve_turn_agent_config(message)\n'
    )
    if '        self._clear_input_needed()\n\n        turn_route = self._resolve_turn_agent_config(message)' not in cli_content:
        if chat_old not in cli_content:
            return None
        cli_content = cli_content.replace(chat_old, chat_new, 1)
        changed = True

    notify_old = (
        '                        app.invalidate()  # Refresh status line\n\n'
        '                        # Continuous voice: auto-restart recording after agent responds.\n'
    )
    notify_new = (
        '                        app.invalidate()  # Refresh status line\n'
        '                        self._notify_input_needed()\n\n'
        '                        # Continuous voice: auto-restart recording after agent responds.\n'
    )
    notify_marker = '                        app.invalidate()  # Refresh status line\n                        self._notify_input_needed()\n'
    if notify_marker not in cli_content:
        if notify_old not in cli_content:
            return None
        cli_content = cli_content.replace(notify_old, notify_new, 1)
        changed = True

    status_intro_old = '            width = self._get_tui_terminal_width()\n            duration_label = snapshot["duration"]\n            thinking_frag = self._get_footer_thinking_fragments()\n'
    status_intro_new = '            width = self._get_tui_terminal_width()\n            duration_label = snapshot["duration"]\n            input_needed_frag = self._get_footer_input_needed_fragments(width=width)\n            thinking_frag = self._get_footer_thinking_fragments()\n'
    if 'input_needed_frag = self._get_footer_input_needed_fragments(width=width)' not in cli_content:
        if status_intro_old not in cli_content:
            return None
        cli_content = cli_content.replace(status_intro_old, status_intro_new, 1)
        changed = True

    narrow_old = '''                frags.append(("class:status-bar-dim", duration_label))
                frags.extend(thinking_frag)
                frags.append(("class:status-bar", " "))
'''
    narrow_new = '''                if input_needed_frag:
                    frags.extend(input_needed_frag)
                    frags.extend([
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                    ])
                else:
                    frags.append(("class:status-bar-dim", duration_label))
                frags.extend(thinking_frag)
                frags.append(("class:status-bar", " "))
'''
    if 'frags.extend(input_needed_frag)' not in cli_content:
        if narrow_old not in cli_content:
            return None
        cli_content = cli_content.replace(narrow_old, narrow_new, 1)
        changed = True

    medium_old = '''                    if reasoning_short:
                        frags.extend([
                            ("class:status-bar-dim", reasoning_short),
                            ("class:status-bar-dim", " · "),
                        ])
'''
    medium_new = '''                    if input_needed_frag:
                        frags.extend(input_needed_frag)
                        frags.append(("class:status-bar-dim", " · "))
                    if reasoning_short:
                        frags.extend([
                            ("class:status-bar-dim", reasoning_short),
                            ("class:status-bar-dim", " · "),
                        ])
'''
    if 'if input_needed_frag:\n                        frags.extend(input_needed_frag)' not in cli_content:
        if medium_old not in cli_content:
            return None
        cli_content = cli_content.replace(medium_old, medium_new, 1)
        changed = True

    wide_old = '''                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-strong" if agent_mode != "build" else "class:status-bar-dim", mode_short),
                    ])
                    frags.extend([
'''
    wide_new = '''                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-strong" if agent_mode != "build" else "class:status-bar-dim", mode_short),
                    ])
                    if input_needed_frag:
                        frags.extend([
                            ("class:status-bar-dim", " │ "),
                            *input_needed_frag,
                        ])
                    frags.extend([
'''
    if '*input_needed_frag,' not in cli_content:
        if wide_old not in cli_content:
            return None
        cli_content = cli_content.replace(wide_old, wide_new, 1)
        changed = True

    command_old = '''    CommandDef("voice", "Toggle voice mode", "Configuration",
               args_hint="[on|off|tts|status]", subcommands=("on", "off", "tts", "status")),
'''
    command_new = '''    CommandDef("voice", "Toggle voice mode", "Configuration",
               args_hint="[on|off|tts|status]", subcommands=("on", "off", "tts", "status")),
    CommandDef("input-needed", "Configure/test input-needed notifications", "Configuration",
               cli_only=True, args_hint="[on|off|toggle|test|status]",
               subcommands=("on", "off", "toggle", "test", "status")),
'''
    if 'CommandDef("input-needed", "Configure/test input-needed notifications"' not in commands_content:
        if command_old not in commands_content:
            return None
        commands_content = commands_content.replace(command_old, command_new, 1)
        changed = True

    if not changed:
        return False

    cli_path.write_text(cli_content, encoding="utf-8")
    commands_path.write_text(commands_content, encoding="utf-8")

    import ast

    ast.parse(cli_content)
    ast.parse(commands_content)
    return True


def _apply_footer_thinking_badge(cli_path: Path) -> bool | None:
    """Restore the footer thinking badge with pulse dot and hide the input badge there."""
    content = cli_path.read_text(encoding="utf-8")
    changed = False

    if (
        'self._footer_thinking_badge = CLI_CONFIG["display"].get("footer_thinking_badge", True)' in content
        and 'def _get_footer_thinking_fragments(self):' in content
        and "'status-bar-thinking-label': 'bg:#1a1a2e #87CEEB bold'" in content
        and 'thinking_frag = self._get_footer_thinking_fragments()' in content
        and 'frags.extend(thinking_frag)' in content
        and content.count('*thinking_frag,') >= 2
    ):
        return False

    if 'self._footer_thinking_badge = CLI_CONFIG["display"].get("footer_thinking_badge", True)' not in content:
        old = (
            '        self._spinner_text: str = ""  # thinking spinner text for TUI\n'
            '        self._tool_start_time: float = 0.0  # monotonic timestamp when current tool started (for live elapsed)\n'
        )
        new = (
            '        self._spinner_text: str = ""  # thinking spinner text for TUI\n'
            '        self._footer_thinking_badge = CLI_CONFIG["display"].get("footer_thinking_badge", True)\n'
            '        self._tool_start_time: float = 0.0  # monotonic timestamp when current tool started (for live elapsed)\n'
        )
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    thinking_method = '''    def _get_footer_thinking_fragments(self):
        """Return the footer badge that shows Hermes is thinking."""
        if not getattr(self, "_footer_thinking_badge", True):
            return []
        if not (
            getattr(self, "_agent_running", False)
            or getattr(self, "_command_running", False)
            or getattr(self, "_spinner_text", "")
        ):
            return []
        pulse_on = int(time.monotonic() * 2) % 2 == 0
        pulse_style = "class:status-bar-thinking-pulse" if pulse_on else "class:status-bar-thinking-pulse-dim"
        return [
            ("class:status-bar-dim", " · "),
            (pulse_style, "●"),
            ("class:status-bar-thinking-label", " thinking"),
        ]

'''
    anchor = '    def _get_footer_input_needed_fragments(self, width: Optional[int] = None):\n'
    if 'def _get_footer_thinking_fragments(self):' not in content:
        if anchor not in content:
            return None
        content = content.replace(anchor, thinking_method + anchor, 1)
        changed = True

    if "'status-bar-thinking-label': 'bg:#1a1a2e #87CEEB bold'" not in content:
        old = "            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',\n            'status-bar-input': 'bg:#1a1a2e #FFA500 bold',\n"
        new = "            'status-bar-critical': 'bg:#1a1a2e #FF6B6B bold',\n            'status-bar-think': 'bg:#1a1a2e #87CEEB bold',\n            'status-bar-thinking-label': 'bg:#1a1a2e #87CEEB bold',\n            'status-bar-thinking-pulse': 'bg:#1a1a2e #FFD700 bold',\n            'status-bar-thinking-pulse-dim': 'bg:#1a1a2e #8B8682',\n            'status-bar-input': 'bg:#1a1a2e #FFA500 bold',\n"
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    if 'thinking_frag = self._get_footer_thinking_fragments()' not in content:
        old = '            duration_label = snapshot["duration"]\n            input_needed_frag = self._get_footer_input_needed_fragments(width=width)\n'
        new = '            duration_label = snapshot["duration"]\n            thinking_frag = self._get_footer_thinking_fragments()\n'
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    old = '''                if input_needed_frag:
                    frags.extend(input_needed_frag)
                    frags.extend([
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                    ])
                else:
                    frags.append(("class:status-bar-dim", duration_label))
                frags.append(("class:status-bar", " "))
'''
    new = '''                frags.append(("class:status-bar-dim", duration_label))
                frags.extend(thinking_frag)
                frags.append(("class:status-bar", " "))
'''
    if 'frags.extend(thinking_frag)' not in content:
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    old = '''                    if input_needed_frag:
                        frags.extend(input_needed_frag)
                        frags.append(("class:status-bar-dim", " · "))
                    if reasoning_short:
'''
    new = '''                    if reasoning_short:
'''
    if 'input_needed_frag' in content:
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    old = '''                    frags.extend([
                        ("class:status-bar-strong" if agent_mode != "build" else "class:status-bar-dim", mode_short),
                        ("class:status-bar-dim", " · "),
                        (self._status_bar_context_style(percent), percent_label),
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                        ("class:status-bar", " "),
                    ])
'''
    new = '''                    frags.extend([
                        ("class:status-bar-strong" if agent_mode != "build" else "class:status-bar-dim", mode_short),
                        ("class:status-bar-dim", " · "),
                        (self._status_bar_context_style(percent), percent_label),
                        ("class:status-bar-dim", " · "),
                        ("class:status-bar-dim", duration_label),
                        *thinking_frag,
                        ("class:status-bar", " "),
                    ])
'''
    if '*thinking_frag,' not in content:
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    old = '''                    if input_needed_frag:
                        frags.extend([
                            ("class:status-bar-dim", " │ "),
                            *input_needed_frag,
                        ])
                    frags.extend([
'''
    new = '''                    frags.extend([
'''
    if '*input_needed_frag,' in content:
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    old = '''                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", context_label),
                        ("class:status-bar-dim", " │ "),
                        (bar_style, self._build_context_bar(percent)),
                        ("class:status-bar-dim", " "),
                        (bar_style, percent_label),
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                    ])
'''
    new = '''                    frags.extend([
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", context_label),
                        ("class:status-bar-dim", " │ "),
                        (bar_style, self._build_context_bar(percent)),
                        ("class:status-bar-dim", " "),
                        (bar_style, percent_label),
                        ("class:status-bar-dim", " │ "),
                        ("class:status-bar-dim", duration_label),
                        *thinking_frag,
                    ])
'''
    if content.count('*thinking_frag,') < 2:
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        changed = True

    if not changed:
        return False

    cli_path.write_text(content, encoding="utf-8")

    import ast

    ast.parse(content)
    return True


def _apply_paste_handler_crash_guard(cli_path: Path) -> bool | None:
    """Wrap clipboard-image checks in the paste handlers so TUI paste never crashes."""
    content = cli_path.read_text(encoding="utf-8")

    if "clipboard image check must never crash" in content:
        return False

    replacements = [
        (
            "            if _should_auto_attach_clipboard_image_on_paste(pasted_text) and self._try_attach_clipboard_image():\n"
            "                event.app.invalidate()\n",
            "            try:\n"
            "                if _should_auto_attach_clipboard_image_on_paste(pasted_text) and self._try_attach_clipboard_image():\n"
            "                    event.app.invalidate()\n"
            "            except Exception:\n"
            "                pass  # clipboard image check must never crash the paste handler\n",
        ),
        (
            "            if self._try_attach_clipboard_image():\n"
            "                event.app.invalidate()\n\n"
            "        @kb.add('escape', 'v')\n",
            "            try:\n"
            "                if self._try_attach_clipboard_image():\n"
            "                    event.app.invalidate()\n"
            "            except Exception:\n"
            "                pass  # clipboard image check must never crash the app\n\n"
            "        @kb.add('escape', 'v')\n",
        ),
        (
            "            if self._try_attach_clipboard_image():\n"
            "                event.app.invalidate()\n"
            "            else:\n"
            "                # No image found — show a hint\n"
            "                pass  # silent when no image (avoid noise on accidental press)\n",
            "            try:\n"
            "                if self._try_attach_clipboard_image():\n"
            "                    event.app.invalidate()\n"
            "                else:\n"
            "                    # No image found — show a hint\n"
            "                    pass  # silent when no image (avoid noise on accidental press)\n"
            "            except Exception:\n"
            "                pass  # clipboard image check must never crash the app\n",
        ),
    ]

    for old, new in replacements:
        if old not in content:
            return None
        content = content.replace(old, new, 1)

    cli_path.write_text(content, encoding="utf-8")
    return True



def _apply_hotkey_layout_restore(cli_path: Path) -> bool | None:
    """Restore the preferred Hermes hotkey layout in ``cli.py``."""
    content = cli_path.read_text(encoding="utf-8")

    marker = "# Hermes local hotkey layout: F1 mode cycle, Ctrl+Q model switch, Shift+Tab reasoning"
    if marker in content:
        return False

    old = """        @kb.add('f2', filter=_normal_input)
        @kb.add('escape', 'm', filter=_normal_input)
        def open_model_picker_shortcut(event):
            try:
                self.process_command('/model')
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f3', filter=_normal_input)
        @kb.add('escape', 'r', filter=_normal_input)
        def cycle_reasoning_shortcut(event):
            try:
                cli_ref._cycle_reasoning_effort(announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f5', filter=_normal_input)
        def mode_ask_shortcut(event):
            try:
                cli_ref._apply_agent_mode('ask', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f6', filter=_normal_input)
        def mode_analyze_shortcut(event):
            try:
                cli_ref._apply_agent_mode('analyze', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f7', filter=_normal_input)
        def mode_plan_shortcut(event):
            try:
                cli_ref._apply_agent_mode('plan', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f8', filter=_normal_input)
        def mode_build_shortcut(event):
            try:
                cli_ref._apply_agent_mode('build', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()
"""
    new = """        # Hermes local hotkey layout: F1 mode cycle, Ctrl+Q model switch, Shift+Tab reasoning
        @kb.add('f1', filter=_normal_input)
        def cycle_mode_shortcut(event):
            try:
                current = getattr(cli_ref, '_agent_mode', 'build') or 'build'
                sequence = ('ask', 'analyze', 'plan', 'build')
                try:
                    idx = sequence.index(current)
                except ValueError:
                    idx = len(sequence) - 1
                cli_ref._apply_agent_mode(sequence[(idx + 1) % len(sequence)], announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('c-q', filter=_normal_input)
        @kb.add('f2', filter=_normal_input)
        @kb.add('escape', 'm', filter=_normal_input)
        def open_model_picker_shortcut(event):
            try:
                self.process_command('/model')
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('s-tab', filter=_normal_input)
        @kb.add('f3', filter=_normal_input)
        @kb.add('escape', 'r', filter=_normal_input)
        def cycle_reasoning_shortcut(event):
            try:
                cli_ref._cycle_reasoning_effort(announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f5', filter=_normal_input)
        def mode_ask_shortcut(event):
            try:
                cli_ref._apply_agent_mode('ask', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f6', filter=_normal_input)
        def mode_analyze_shortcut(event):
            try:
                cli_ref._apply_agent_mode('analyze', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f7', filter=_normal_input)
        def mode_plan_shortcut(event):
            try:
                cli_ref._apply_agent_mode('plan', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()

        @kb.add('f8', filter=_normal_input)
        def mode_build_shortcut(event):
            try:
                cli_ref._apply_agent_mode('build', announce=False)
            except Exception:
                pass
            finally:
                event.app.invalidate()
"""

    if old not in content:
        return None

    content = content.replace(old, new, 1)
    cli_path.write_text(content, encoding="utf-8")

    import ast

    ast.parse(content)
    return True



def _apply_cli_terminal_markdown_defaults(cli_path: Path) -> bool | None:
    """Keep CLI markdown-friendly output defaults after `hermes update`."""
    project_root = cli_path.parent
    prompt_builder_path = project_root / "agent" / "prompt_builder.py"
    config_path = project_root / "hermes_cli" / "config.py"

    prompt_builder = prompt_builder_path.read_text(encoding="utf-8")
    config_content = config_path.read_text(encoding="utf-8")
    changed = False

    prompt_old = '''    "cli": (
        "You are a CLI AI Agent. Try not to use markdown but simple text "
        "renderable inside a terminal."
    ),'''
    prompt_new = '''    "cli": (
        "You are a CLI AI Agent. Use compact terminal-friendly markdown when it "
        "improves readability. Bullets, checklists, small tables, strikethrough, "
        "and short code fences are fine. Keep output scannable and avoid bloated prose."
    ),'''
    if "terminal-friendly markdown" not in prompt_builder:
        if prompt_old not in prompt_builder:
            return None
        prompt_builder = prompt_builder.replace(prompt_old, prompt_new, 1)
        changed = True

    config_old = '        "final_response_markdown": "strip",  # render | strip | raw'
    config_new = '        "final_response_markdown": "render",  # render | strip | raw'
    if config_new not in config_content:
        if config_old not in config_content:
            return None
        config_content = config_content.replace(config_old, config_new, 1)
        changed = True

    if not changed:
        return False

    prompt_builder_path.write_text(prompt_builder, encoding="utf-8")
    config_path.write_text(config_content, encoding="utf-8")

    import ast

    ast.parse(prompt_builder)
    ast.parse(config_content)
    return True


def _apply_plain_final_response_output(cli_path: Path) -> bool | None:
    """Remove the framed Hermes response panel and print final content plainly."""
    content = cli_path.read_text(encoding="utf-8")
    changed = False

    helper_old = """def _cprint(text: str):\n"""
    helper_new = """def _print_final_assistant_response(console, text: str, mode: str = \"render\") -> None:\n    \"\"\"Print the final assistant response without a framed panel.\"\"\"\n    console.print(_render_final_assistant_content(text, mode=mode))\n\n\ndef _cprint(text: str):\n"""
    if "def _print_final_assistant_response(" not in content:
        if helper_old not in content:
            return None
        anchor = """def _cprint(text: str):\n"""
        insert_after = """    plain = _rich_text_from_ansi(text or \"\").plain\n    return Markdown(plain)\n\n\n"""
        if insert_after not in content:
            return None
        content = content.replace(insert_after, insert_after + helper_new.replace(anchor, ""), 1)
        changed = True

    old_bg = """                if response:\n                    try:\n                        from hermes_cli.skin_engine import get_active_skin\n                        _skin = get_active_skin()\n                        label = _skin.get_branding(\"response_label\", \"⚕ Hermes\")\n                        _resp_color = _skin.get_color(\"response_border\", \"#CD7F32\")\n                        _resp_text = _skin.get_color(\"banner_text\", \"#FFF8DC\")\n                    except Exception:\n                        label = \"⚕ Hermes\"\n                        _resp_color = \"#CD7F32\"\n                        _resp_text = \"#FFF8DC\"\n\n                    _chat_console = ChatConsole()\n                    _chat_console.print(Panel(\n                        _render_final_assistant_content(response, mode=self.final_response_markdown),\n                        title=f\"[{_resp_color} bold]{label} (background #{task_num})[/]\",\n                        title_align=\"left\",\n                        border_style=_resp_color,\n                        style=_resp_text,\n                        box=rich_box.HORIZONTALS,\n                        padding=(1, 4),\n                    ))\n                else:\n"""
    new_bg = """                if response:\n                    _chat_console = ChatConsole()\n                    _print_final_assistant_response(\n                        _chat_console,\n                        response,\n                        mode=self.final_response_markdown,\n                    )\n                else:\n"""
    if new_bg not in content:
        if old_bg not in content:
            return None
        content = content.replace(old_bg, new_bg, 1)
        changed = True

    old_btw = """                if response:\n                    try:\n                        from hermes_cli.skin_engine import get_active_skin\n                        _skin = get_active_skin()\n                        _resp_color = _skin.get_color(\"response_border\", \"#4F6D4A\")\n                    except Exception:\n                        _resp_color = \"#4F6D4A\"\n\n                    ChatConsole().print(Panel(\n                        _render_final_assistant_content(response, mode=self.final_response_markdown),\n                        title=f\"[{_resp_color} bold]⚕ /btw[/]\",\n                        title_align=\"left\",\n                        border_style=_resp_color,\n                        box=rich_box.HORIZONTALS,\n                        padding=(1, 4),\n                    ))\n                else:\n"""
    new_btw = """                if response:\n                    _print_final_assistant_response(\n                        ChatConsole(),\n                        response,\n                        mode=self.final_response_markdown,\n                    )\n                else:\n"""
    if new_btw not in content:
        if old_btw not in content:
            return None
        content = content.replace(old_btw, new_btw, 1)
        changed = True

    old_main = """            if response and not response_previewed:\n                # Use skin engine for label/color with fallback\n                try:\n                    from hermes_cli.skin_engine import get_active_skin\n                    _skin = get_active_skin()\n                    label = _skin.get_branding(\"response_label\", \"⚕ Hermes\")\n                    _resp_color = _skin.get_color(\"response_border\", \"#CD7F32\")\n                    _resp_text = _skin.get_color(\"banner_text\", \"#FFF8DC\")\n                except Exception:\n                    label = \"⚕ Hermes\"\n                    _resp_color = \"#CD7F32\"\n                    _resp_text = \"#FFF8DC\"\n\n                is_error_response = result and (result.get(\"failed\") or result.get(\"partial\"))\n                already_streamed = self._stream_started and self._stream_box_opened and not is_error_response\n                if use_streaming_tts and _streaming_box_opened and not is_error_response:\n                    # Text was already printed sentence-by-sentence; just close the box\n                    w = shutil.get_terminal_size().columns\n                    _cprint(f\"\\n{_ACCENT}╰{'─' * (w - 2)}╯{_RST}\")\n                elif already_streamed:\n                    # Response was already streamed token-by-token with box framing;\n                    # _flush_stream() already closed the box. Skip Rich Panel.\n                    pass\n                else:\n                    _chat_console = ChatConsole()\n                    _chat_console.print(Panel(\n                        _render_final_assistant_content(response, mode=self.final_response_markdown),\n                        title=f\"[{_resp_color} bold]{label}[/]\",\n                        title_align=\"left\",\n                        border_style=_resp_color,\n                        style=_resp_text,\n                        box=rich_box.HORIZONTALS,\n                        padding=(1, 4),\n                    ))\n"""
    new_main = """            if response and not response_previewed:\n                is_error_response = result and (result.get(\"failed\") or result.get(\"partial\"))\n                already_streamed = self._stream_started and self._stream_box_opened and not is_error_response\n                if use_streaming_tts and _streaming_box_opened and not is_error_response:\n                    # Text was already printed sentence-by-sentence; just close the box\n                    w = shutil.get_terminal_size().columns\n                    _cprint(f\"\\n{_ACCENT}╰{'─' * (w - 2)}╯{_RST}\")\n                elif already_streamed:\n                    # Response was already streamed token-by-token with box framing;\n                    # _flush_stream() already closed the box. Skip duplicate final print.\n                    pass\n                else:\n                    _chat_console = ChatConsole()\n                    _print_final_assistant_response(\n                        _chat_console,\n                        response,\n                        mode=self.final_response_markdown,\n                    )\n"""
    if new_main not in content:
        if old_main not in content:
            return None
        content = content.replace(old_main, new_main, 1)
        changed = True

    if not changed:
        return False

    cli_path.write_text(content, encoding="utf-8")

    import ast

    ast.parse(content)
    return True


def _apply_browser_start_live_chrome(cli_path: Path) -> bool | None:
    """Add /browser start for a dedicated live Chrome debug instance.

    This makes Hermes behave more like Claude Code for login-heavy browser work:
    one stable Chrome profile, one stable CDP endpoint, optional target URL, and
    no forced headless hop during manual login.
    """
    project_root = cli_path.parent
    commands_path = project_root / "hermes_cli" / "commands.py"

    cli_content = cli_path.read_text(encoding="utf-8")
    commands_content = commands_path.read_text(encoding="utf-8")
    changed = False

    commands_old = """    CommandDef(\"browser\", \"Connect browser tools to your live Chrome via CDP\", \"Tools & Skills\",\n               cli_only=True, args_hint=\"[connect|disconnect|status]\",\n               subcommands=(\"connect\", \"disconnect\", \"status\")),\n"""
    commands_new = """    CommandDef(\"browser\", \"Start or connect browser tools to your live Chrome via CDP\", \"Tools & Skills\",\n               cli_only=True, args_hint=\"[start|connect|disconnect|status] [url]\",\n               subcommands=(\"start\", \"connect\", \"disconnect\", \"status\")),\n"""
    if commands_new not in commands_content:
        if commands_old not in commands_content:
            return None
        commands_content = commands_content.replace(commands_old, commands_new, 1)
        changed = True

    sig_old = """    @staticmethod\n    def _try_launch_chrome_debug(port: int, system: str) -> bool:\n        \"\"\"Try to launch Chrome/Chromium with remote debugging enabled.\n\n        Uses a dedicated user-data-dir so the debug instance doesn't conflict\n        with an already-running Chrome using the default profile.\n\n        Returns True if a launch command was executed (doesn't guarantee success).\n        \"\"\"\n        import subprocess as _sp\n\n        candidates = _get_chrome_debug_candidates(system)\n\n        if not candidates:\n            return False\n\n        # Dedicated profile dir so debug Chrome won't collide with normal Chrome\n        data_dir = str(_hermes_home / \"chrome-debug\")\n        os.makedirs(data_dir, exist_ok=True)\n\n        chrome = candidates[0]\n        try:\n            _sp.Popen(\n                [\n                    chrome,\n                    f\"--remote-debugging-port={port}\",\n                    f\"--user-data-dir={data_dir}\",\n                    \"--no-first-run\",\n                    \"--no-default-browser-check\",\n                ],\n                stdout=_sp.DEVNULL,\n                stderr=_sp.DEVNULL,\n                start_new_session=True,  # detach from terminal\n            )\n            return True\n        except Exception:\n            return False\n"""
    sig_new = """    @staticmethod\n    def _try_launch_chrome_debug(port: int, system: str, start_target: str = \"\") -> bool:\n        \"\"\"Try to launch Chrome/Chromium with remote debugging enabled.\n\n        Uses a dedicated user-data-dir so the debug instance doesn't conflict\n        with an already-running Chrome using the default profile.\n\n        When ``start_target`` is provided, Chrome opens that URL/path in the same\n        dedicated debug profile. This is useful for extension pages or login URLs.\n\n        Returns True if a launch command was executed (doesn't guarantee success).\n        \"\"\"\n        import subprocess as _sp\n\n        candidates = _get_chrome_debug_candidates(system)\n\n        if not candidates:\n            return False\n\n        # Dedicated profile dir so debug Chrome won't collide with normal Chrome\n        data_dir = str(_hermes_home / \"chrome-debug\")\n        os.makedirs(data_dir, exist_ok=True)\n\n        chrome = candidates[0]\n        argv = [\n            chrome,\n            f\"--remote-debugging-port={port}\",\n            f\"--user-data-dir={data_dir}\",\n            \"--no-first-run\",\n            \"--no-default-browser-check\",\n        ]\n        if start_target:\n            argv.append(start_target)\n\n        try:\n            _sp.Popen(\n                argv,\n                stdout=_sp.DEVNULL,\n                stderr=_sp.DEVNULL,\n                start_new_session=True,  # detach from terminal\n            )\n            return True\n        except Exception:\n            return False\n"""
    if sig_new not in cli_content:
        if sig_old not in cli_content:
            return None
        cli_content = cli_content.replace(sig_old, sig_new, 1)
        changed = True

    branch_old = """        if sub.startswith(\"connect\"):\n            # Optionally accept a custom CDP URL: /browser connect ws://host:port\n            connect_parts = cmd.strip().split(None, 2)  # [\"/browser\", \"connect\", \"ws://...\"]\n            cdp_url = connect_parts[2].strip() if len(connect_parts) > 2 else _DEFAULT_CDP\n"""
    branch_new = """        if sub.startswith(\"connect\") or sub.startswith(\"start\"):\n            is_start = sub.startswith(\"start\")\n            connect_parts = cmd.strip().split(None, 2)\n            cdp_url = _DEFAULT_CDP\n            start_target = \"\"\n            if is_start:\n                start_target = connect_parts[2].strip() if len(connect_parts) > 2 else \"\"\n            else:\n                # Optionally accept a custom CDP URL: /browser connect ws://host:port\n                cdp_url = connect_parts[2].strip() if len(connect_parts) > 2 else _DEFAULT_CDP\n"""
    if branch_new not in cli_content:
        if branch_old not in cli_content:
            return None
        cli_content = cli_content.replace(branch_old, branch_new, 1)
        changed = True

    open_old = """            if _already_open:\n                print(f\"   ✓ Chrome is already listening on port {_port}\")\n            elif cdp_url == _DEFAULT_CDP:\n                # Try to auto-launch Chrome with remote debugging\n                print(\"   Chrome isn't running with remote debugging — attempting to launch...\")\n                _launched = self._try_launch_chrome_debug(_port, _plat.system())\n"""
    open_new = """            if _already_open:\n                print(f\"   ✓ Chrome is already listening on port {_port}\")\n                if is_start and start_target:\n                    print(f\"   Opening target in the dedicated Chrome profile: {start_target}\")\n                    self._try_launch_chrome_debug(_port, _plat.system(), start_target)\n            elif cdp_url == _DEFAULT_CDP:\n                # Try to auto-launch Chrome with remote debugging\n                print(\"   Chrome isn't running with remote debugging — attempting to launch...\")\n                _launched = self._try_launch_chrome_debug(_port, _plat.system(), start_target)\n"""
    if open_new not in cli_content:
        if open_old not in cli_content:
            return None
        cli_content = cli_content.replace(open_old, open_new, 1)
        changed = True

    connect_old = """            os.environ[\"BROWSER_CDP_URL\"] = cdp_url\n            print()\n            print(\"🌐 Browser connected to live Chrome via CDP\")\n            print(f\"   Endpoint: {cdp_url}\")\n            print()\n"""
    connect_new = """            os.environ[\"BROWSER_CDP_URL\"] = cdp_url\n            if is_start:\n                save_config_value(\"browser.cdp_url\", cdp_url)\n            print()\n            print(\"🌐 Browser connected to live Chrome via CDP\")\n            print(f\"   Endpoint: {cdp_url}\")\n            if is_start:\n                print(f\"   Profile: {_hermes_home / 'chrome-debug'}\")\n                if start_target:\n                    print(f\"   Target:  {start_target}\")\n                print(\"   Dit is nu de vaste Chrome-instance voor Hermes.\")\n                print(\"   Log hier handmatig in of open je extensie; Hermes doet niets tot je volgende opdracht.\")\n            print()\n"""
    if connect_new not in cli_content:
        if connect_old not in cli_content:
            return None
        cli_content = cli_content.replace(connect_old, connect_new, 1)
        changed = True

    disconnect_old = """                os.environ.pop(\"BROWSER_CDP_URL\", None)\n                try:\n"""
    disconnect_new = """                os.environ.pop(\"BROWSER_CDP_URL\", None)\n                save_config_value(\"browser.cdp_url\", \"\")\n                try:\n"""
    if disconnect_new not in cli_content:
        if disconnect_old not in cli_content:
            return None
        cli_content = cli_content.replace(disconnect_old, disconnect_new, 1)
        changed = True

    status_old = """            print(\"   /browser connect      — connect to your live Chrome\")\n            print(\"   /browser disconnect   — revert to default\")\n"""
    status_new = """            print(\"   /browser start [url]  — start vaste Chrome-instance en koppel Hermes\")\n            print(\"   /browser connect      — koppel alleen deze sessie aan live Chrome\")\n            print(\"   /browser disconnect   — revert to default\")\n"""
    if status_new not in cli_content:
        if status_old not in cli_content:
            return None
        cli_content = cli_content.replace(status_old, status_new, 1)
        changed = True

    usage_old = """            print(\"Usage: /browser connect|disconnect|status\")\n            print()\n            print(\"   connect      Connect browser tools to your live Chrome session\")\n            print(\"   disconnect   Revert to default browser backend\")\n            print(\"   status       Show current browser mode\")\n"""
    usage_new = """            print(\"Usage: /browser start [url] | /browser connect [cdp_url] | /browser disconnect | /browser status\")\n            print()\n            print(\"   start        Start vaste Chrome-instance op poort 9222 en koppel Hermes\")\n            print(\"   connect      Connect browser tools to your live Chrome session\")\n            print(\"   disconnect   Revert to default browser backend\")\n            print(\"   status       Show current browser mode\")\n"""
    if usage_new not in cli_content:
        if usage_old not in cli_content:
            return None
        cli_content = cli_content.replace(usage_old, usage_new, 1)
        changed = True

    if not changed:
        return False

    cli_path.write_text(cli_content, encoding="utf-8")
    commands_path.write_text(commands_content, encoding="utf-8")

    import ast
    ast.parse(cli_content)
    ast.parse(commands_content)
    return True


# ---------------------------------------------------------------------------
# Register all patches below
# ---------------------------------------------------------------------------

PATCHES = [
    ("browser-start-live-chrome", _apply_browser_start_live_chrome),
    ("plain-final-response-output", _apply_plain_final_response_output),
    ("cli-terminal-markdown-defaults", _apply_cli_terminal_markdown_defaults),
    ("discord-skill-compat", _apply_discord_skill_compat),
    ("footer-thinking-badge", _apply_footer_thinking_badge),
    ("input-needed-footer-badge", _apply_input_needed_footer_badge),
    ("pi-input-surface", _apply_pi_input_surface),
    ("session-duration-counter", _apply_session_duration_counter),
    ("paste-handler-crash-guard", _apply_paste_handler_crash_guard),
    ("hotkey-layout-restore", _apply_hotkey_layout_restore),
]
