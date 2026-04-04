"""Structured Task Packets for delegate_task.

Defines a validated TaskPacket format for delegating work to subagents.
Each packet specifies objective, scope, acceptance criteria, escalation
policy, and reporting contract — giving subagents clear structure instead
of free-form text.

Inspired by claw-code task_packet.rs — adapted for Python/Hermes.

Usage:
    from agent.task_packet import TaskPacket, validate_packet

    packet = TaskPacket(
        objective="Fix the login bug",
        scope="src/auth/*.py",
        acceptance_tests=["python -m pytest tests/test_auth.py"],
        escalation_policy="Stop if destructive ambiguity found",
    )
    errors = validate_packet(packet)
    if not errors:
        prompt = packet.to_prompt()
        # pass prompt to delegate_task

No imports from agent runtime. Standalone module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TaskPacket:
    """Structured work delegation packet.

    All fields are plain strings/lists for easy serialization.
    Use validate_packet() to check completeness before use.
    """
    objective: str = ""
    scope: str = ""
    repo: str = ""
    branch_policy: str = ""
    acceptance_tests: list[str] = field(default_factory=list)
    commit_policy: str = ""
    reporting_contract: str = ""
    escalation_policy: str = ""
    context: str = ""  # background info for the subagent
    toolsets: list[str] = field(default_factory=list)
    max_iterations: int | None = None

    def to_prompt(self) -> str:
        """Serialize packet to a readable subagent instruction string.

        Returns a structured prompt that can be passed as the `goal`
        parameter to delegate_task.
        """
        parts = []

        if self.objective:
            parts.append(f"OBJECTIVE: {self.objective}")

        if self.scope:
            parts.append(f"SCOPE: {self.scope}")

        if self.context:
            parts.append(f"CONTEXT: {self.context}")

        if self.repo:
            parts.append(f"REPO: {self.repo}")

        if self.branch_policy:
            parts.append(f"BRANCH POLICY: {self.branch_policy}")

        if self.acceptance_tests:
            tests_str = "\n".join(f"  - {t}" for t in self.acceptance_tests)
            parts.append(f"ACCEPTANCE TESTS:\n{tests_str}")

        if self.commit_policy:
            parts.append(f"COMMIT POLICY: {self.commit_policy}")

        if self.escalation_policy:
            parts.append(f"ESCALATION POLICY: {self.escalation_policy}")

        if self.reporting_contract:
            parts.append(f"REPORTING CONTRACT: {self.reporting_contract}")

        return "\n\n".join(parts)

    def to_delegate_kwargs(self) -> dict:
        """Convert packet to kwargs for delegate_task tool call.

        Returns a dict suitable for passing to the delegate_task function.
        """
        kwargs: dict = {"goal": self.to_prompt()}

        if self.context:
            kwargs["context"] = self.context

        if self.toolsets:
            kwargs["toolsets"] = self.toolsets

        if self.max_iterations is not None:
            kwargs["max_iterations"] = self.max_iterations

        return kwargs


def validate_packet(packet: TaskPacket) -> list[str]:
    """Validate a TaskPacket. Returns list of error strings (empty = valid).

    Accumulates ALL errors before returning, not just the first.
    """
    errors: list[str] = []

    if not packet.objective or not packet.objective.strip():
        errors.append("objective is required and must not be empty")

    if not packet.scope or not packet.scope.strip():
        errors.append("scope is required and must not be empty")

    # acceptance_tests: if provided, none should be empty
    for i, test in enumerate(packet.acceptance_tests):
        if not test or not test.strip():
            errors.append(f"acceptance_tests[{i}] is empty")

    return errors


class ValidatedPacket:
    """Newtype wrapper: can only be created via validate_and_wrap().

    Guarantees that the packet passed validation at construction time.
    """

    def __init__(self, packet: TaskPacket):
        """Private-ish constructor. Use validate_and_wrap() instead."""
        self._packet = packet

    @property
    def packet(self) -> TaskPacket:
        return self._packet

    def to_prompt(self) -> str:
        return self._packet.to_prompt()

    def to_delegate_kwargs(self) -> dict:
        return self._packet.to_delegate_kwargs()


def validate_and_wrap(packet: TaskPacket) -> tuple[ValidatedPacket | None, list[str]]:
    """Validate and wrap a packet. Returns (ValidatedPacket, []) on success,
    or (None, errors) on failure."""
    errors = validate_packet(packet)
    if errors:
        return None, errors
    return ValidatedPacket(packet), []
