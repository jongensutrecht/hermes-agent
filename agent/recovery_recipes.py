"""Recovery Recipes Framework for Hermes agent.

Structured auto-recovery for known failure scenarios. Follows the
"try once, then escalate" pattern: each failure type gets exactly 1
automatic recovery attempt before escalating to the user.

Inspired by claw-code recovery_recipes.rs — adapted for Python/Hermes.

Usage:
    engine = RecoveryEngine()
    result = engine.attempt_recovery(FailureType.PROVIDER_TIMEOUT, context={"model": "claude-sonnet-4-20250514"})
    if result.outcome == RecoveryOutcome.RECOVERED:
        # retry the failed operation
    elif result.outcome == RecoveryOutcome.ESCALATION_REQUIRED:
        # surface error to user

Design constraints:
    - No circular imports with other agent modules
    - Fail-open: if this module crashes, tool execution continues unchanged
    - Max 1 automatic retry per failure type per step
    - No UI/display dependencies
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Failure Taxonomy ──────────────────────────────────────────────────

class FailureType(Enum):
    """Known failure categories with distinct recovery strategies."""
    PROVIDER_TIMEOUT = auto()    # API call timed out
    PROVIDER_RATE_LIMIT = auto() # 429 / rate limited
    PROVIDER_OVERLOADED = auto() # 529 / server overloaded
    MCP_ERROR = auto()           # MCP server connection or call failed
    TOOL_CRASH = auto()          # Tool raised an unhandled exception
    PERMISSION_DENIED = auto()   # Tool blocked by permission / sandbox
    CONTEXT_OVERFLOW = auto()    # Context window exceeded

    @classmethod
    def classify(cls, error: Exception | str, context: dict | None = None) -> "FailureType":
        """Classify an error into a FailureType.

        Classification priority (HERMES-008):
          1. Exception type (stable API contracts, no string matching)
          2. HTTP status code from context dict
          3. String pattern fallback (last resort)

        Uses getattr for exception type checks to avoid hard imports of
        openai/anthropic/httpx — works even if SDKs aren't installed.
        """
        ctx = context or {}

        # ── Layer 1: Exception type classification ────────────────────
        # Stable API contracts — exception class names don't change per release.
        if isinstance(error, Exception):
            exc_type = type(error).__name__
            exc_module = type(error).__module__ or ""

            # Timeout exceptions (httpx, openai, stdlib)
            if exc_type in ("ConnectTimeout", "ReadTimeout", "WriteTimeout",
                            "PoolTimeout", "TimeoutError", "APITimeoutError",
                            "Timeout", "ConnectionTimeout"):
                return cls.PROVIDER_TIMEOUT

            # Rate limit exceptions
            if exc_type in ("RateLimitError",):
                return cls.PROVIDER_RATE_LIMIT

            # Overloaded / service unavailable
            if exc_type in ("APIStatusError", "InternalServerError"):
                # Check HTTP status on the exception object
                exc_status = getattr(error, "status_code", None)
                if exc_status == 429:
                    return cls.PROVIDER_RATE_LIMIT
                if exc_status == 529:
                    return cls.PROVIDER_OVERLOADED
                if isinstance(exc_status, int) and exc_status >= 500:
                    return cls.PROVIDER_TIMEOUT

            # Permission / auth errors
            if exc_type in ("AuthenticationError", "PermissionDeniedError",
                            "PermissionError"):
                return cls.PERMISSION_DENIED

            # Context length / bad request with token overflow
            if exc_type in ("BadRequestError",):
                exc_msg = str(error).lower()
                if any(p in exc_msg for p in ("context length", "token", "too long")):
                    return cls.CONTEXT_OVERFLOW

            # MCP-specific errors (mcp SDK exceptions)
            if "mcp" in exc_module.lower() or exc_type.startswith("Mcp"):
                return cls.MCP_ERROR

            # Connection errors (httpx, urllib3, stdlib)
            if exc_type in ("ConnectError", "ConnectionError", "ConnectionRefusedError",
                            "ConnectionResetError", "RemoteProtocolError"):
                return cls.PROVIDER_TIMEOUT

        # ── Layer 2: HTTP status code from context ────────────────────
        status = ctx.get("status_code")
        if isinstance(status, int):
            if status == 429:
                return cls.PROVIDER_RATE_LIMIT
            if status == 529:
                return cls.PROVIDER_OVERLOADED
            if status == 403:
                return cls.PERMISSION_DENIED
            if status >= 500:
                return cls.PROVIDER_TIMEOUT

        # ── Layer 3: String pattern fallback ──────────────────────────
        # Last resort — only used when layers 1+2 don't match.
        msg = str(error).lower() if error else ""

        if any(p in msg for p in ("timeout", "timed out", "deadline exceeded")):
            return cls.PROVIDER_TIMEOUT
        if any(p in msg for p in ("rate limit", "rate_limit", "too many requests")):
            return cls.PROVIDER_RATE_LIMIT
        if any(p in msg for p in ("overloaded", "capacity")):
            return cls.PROVIDER_OVERLOADED
        if any(p in msg for p in ("mcp", "model context protocol")):
            return cls.MCP_ERROR
        if any(p in msg for p in ("permission", "denied", "forbidden", "not allowed")):
            return cls.PERMISSION_DENIED
        if any(p in msg for p in ("context length", "context_length", "token limit", "max_tokens")):
            return cls.CONTEXT_OVERFLOW

        return cls.TOOL_CRASH


# ── Recovery Steps ────────────────────────────────────────────────────

class RecoveryStep(Enum):
    """Atomic recovery actions."""
    WAIT_AND_RETRY = auto()       # Simple backoff + retry
    RETRY_IMMEDIATELY = auto()    # Retry without delay
    RECONNECT_MCP = auto()        # Reconnect to MCP server
    COMPRESS_CONTEXT = auto()     # Trigger context compression
    SWITCH_PROVIDER = auto()      # Try fallback provider
    SKIP_TOOL = auto()            # Skip this tool call, continue
    LOG_AND_CONTINUE = auto()     # Log the failure, don't retry
    ESCALATE = auto()             # Surface to user


# ── Escalation Policy ────────────────────────────────────────────────

class EscalationPolicy(Enum):
    """What to do when recovery fails."""
    ABORT = auto()          # Stop the agent loop
    LOG_AND_CONTINUE = auto()  # Log warning, continue with error result
    SURFACE_TO_USER = auto()   # Show error to user but keep going


# ── Recovery Outcome ──────────────────────────────────────────────────

class RecoveryOutcome(Enum):
    """Result of a recovery attempt."""
    RECOVERED = auto()            # Recovery succeeded, retry safe
    PARTIAL_RECOVERY = auto()     # Some steps succeeded, situation improved
    ESCALATION_REQUIRED = auto()  # Recovery failed, needs human intervention
    NOT_APPLICABLE = auto()       # No recipe for this failure type


@dataclass(frozen=True)
class RecoveryEvent:
    """Structured log of a recovery attempt."""
    timestamp: float
    failure_type: FailureType
    steps_attempted: tuple[RecoveryStep, ...]
    steps_succeeded: tuple[RecoveryStep, ...]
    outcome: RecoveryOutcome
    detail: str = ""
    context: dict = field(default_factory=dict)


@dataclass
class RecoveryResult:
    """Outcome of attempting recovery for a failure."""
    outcome: RecoveryOutcome
    event: RecoveryEvent
    wait_seconds: float = 0.0  # how long to wait before retrying
    action: RecoveryStep | None = None  # recommended next action


# ── Recipe Definitions ────────────────────────────────────────────────

@dataclass(frozen=True)
class Recipe:
    """Recovery recipe for a failure type."""
    failure_type: FailureType
    steps: tuple[RecoveryStep, ...]
    max_attempts: int = 1
    escalation: EscalationPolicy = EscalationPolicy.SURFACE_TO_USER
    wait_seconds: float = 0.0  # delay before retry


# Default recipes — one per failure type
_RECIPES: dict[FailureType, Recipe] = {
    FailureType.PROVIDER_TIMEOUT: Recipe(
        failure_type=FailureType.PROVIDER_TIMEOUT,
        steps=(RecoveryStep.WAIT_AND_RETRY, RecoveryStep.SWITCH_PROVIDER),
        max_attempts=1,
        escalation=EscalationPolicy.SURFACE_TO_USER,
        wait_seconds=2.0,
    ),
    FailureType.PROVIDER_RATE_LIMIT: Recipe(
        failure_type=FailureType.PROVIDER_RATE_LIMIT,
        steps=(RecoveryStep.WAIT_AND_RETRY,),
        max_attempts=1,
        escalation=EscalationPolicy.LOG_AND_CONTINUE,
        wait_seconds=5.0,
    ),
    FailureType.PROVIDER_OVERLOADED: Recipe(
        failure_type=FailureType.PROVIDER_OVERLOADED,
        steps=(RecoveryStep.WAIT_AND_RETRY, RecoveryStep.SWITCH_PROVIDER),
        max_attempts=1,
        escalation=EscalationPolicy.SURFACE_TO_USER,
        wait_seconds=3.0,
    ),
    FailureType.MCP_ERROR: Recipe(
        failure_type=FailureType.MCP_ERROR,
        steps=(RecoveryStep.RECONNECT_MCP, RecoveryStep.RETRY_IMMEDIATELY),
        max_attempts=1,
        escalation=EscalationPolicy.LOG_AND_CONTINUE,
        wait_seconds=1.0,
    ),
    FailureType.TOOL_CRASH: Recipe(
        failure_type=FailureType.TOOL_CRASH,
        steps=(RecoveryStep.RETRY_IMMEDIATELY,),
        max_attempts=1,
        escalation=EscalationPolicy.LOG_AND_CONTINUE,
        wait_seconds=0.0,
    ),
    FailureType.PERMISSION_DENIED: Recipe(
        failure_type=FailureType.PERMISSION_DENIED,
        steps=(RecoveryStep.LOG_AND_CONTINUE,),
        max_attempts=0,  # never auto-retry permission issues
        escalation=EscalationPolicy.SURFACE_TO_USER,
        wait_seconds=0.0,
    ),
    FailureType.CONTEXT_OVERFLOW: Recipe(
        failure_type=FailureType.CONTEXT_OVERFLOW,
        steps=(RecoveryStep.COMPRESS_CONTEXT,),
        max_attempts=1,
        escalation=EscalationPolicy.ABORT,
        wait_seconds=0.0,
    ),
}


# ── Recovery Engine ───────────────────────────────────────────────────

class RecoveryEngine:
    """Stateful recovery engine tracking attempts per failure type.

    Thread-safe for concurrent tool execution: each tool call should
    create its own context dict but share the engine instance.
    """

    def __init__(self, recipes: dict[FailureType, Recipe] | None = None):
        self._recipes = recipes or dict(_RECIPES)
        self._attempts: dict[str, int] = {}  # "failure_type:context_key" -> count
        self._events: list[RecoveryEvent] = []

    def _attempt_key(self, failure_type: FailureType, context: dict | None = None) -> str:
        """Build a unique key for tracking attempts.

        Uses failure_type + optional tool_name to allow per-tool retry budgets.
        """
        ctx = context or {}
        tool_name = ctx.get("tool_name", "")
        return f"{failure_type.name}:{tool_name}"

    def attempt_recovery(
        self,
        failure_type: FailureType,
        context: dict | None = None,
        step_executor: Callable[[RecoveryStep, dict], bool] | None = None,
    ) -> RecoveryResult:
        """Attempt recovery for a classified failure.

        Args:
            failure_type: The classified failure type.
            context: Optional context dict (tool_name, error message, etc.)
            step_executor: Optional callback to execute recovery steps.
                Receives (step, context) and returns True if step succeeded.
                If None, steps are simulated (logged but not executed).

        Returns:
            RecoveryResult with outcome and recommended action.
        """
        ctx = context or {}
        recipe = self._recipes.get(failure_type)

        if recipe is None:
            event = RecoveryEvent(
                timestamp=time.time(),
                failure_type=failure_type,
                steps_attempted=(),
                steps_succeeded=(),
                outcome=RecoveryOutcome.NOT_APPLICABLE,
                detail=f"No recipe for {failure_type.name}",
                context=ctx,
            )
            self._events.append(event)
            return RecoveryResult(outcome=RecoveryOutcome.NOT_APPLICABLE, event=event)

        # Check attempt budget
        key = self._attempt_key(failure_type, ctx)
        current_attempts = self._attempts.get(key, 0)

        if current_attempts >= recipe.max_attempts:
            event = RecoveryEvent(
                timestamp=time.time(),
                failure_type=failure_type,
                steps_attempted=(),
                steps_succeeded=(),
                outcome=RecoveryOutcome.ESCALATION_REQUIRED,
                detail=f"Attempt budget exhausted ({current_attempts}/{recipe.max_attempts})",
                context=ctx,
            )
            self._events.append(event)
            logger.warning(
                "Recovery budget exhausted for %s (tool=%s): %d/%d attempts used",
                failure_type.name,
                ctx.get("tool_name", "?"),
                current_attempts,
                recipe.max_attempts,
            )
            return RecoveryResult(
                outcome=RecoveryOutcome.ESCALATION_REQUIRED,
                event=event,
                action=RecoveryStep.ESCALATE,
            )

        # Increment attempt counter
        self._attempts[key] = current_attempts + 1

        # Execute recovery steps
        steps_attempted: list[RecoveryStep] = []
        steps_succeeded: list[RecoveryStep] = []

        for step in recipe.steps:
            steps_attempted.append(step)
            try:
                if step_executor:
                    success = step_executor(step, ctx)
                else:
                    # No executor → simulate success for steps that don't
                    # need external state. SWITCH_PROVIDER and RECONNECT_MCP
                    # are handled by the agent's own fallback/retry logic,
                    # so they pass-through here.
                    success = step in (
                        RecoveryStep.WAIT_AND_RETRY,
                        RecoveryStep.RETRY_IMMEDIATELY,
                        RecoveryStep.LOG_AND_CONTINUE,
                        RecoveryStep.SKIP_TOOL,
                        RecoveryStep.SWITCH_PROVIDER,
                        RecoveryStep.RECONNECT_MCP,
                    )
                if success:
                    steps_succeeded.append(step)
                    logger.info(
                        "Recovery step %s succeeded for %s (tool=%s)",
                        step.name, failure_type.name, ctx.get("tool_name", "?"),
                    )
                else:
                    logger.warning(
                        "Recovery step %s failed for %s (tool=%s)",
                        step.name, failure_type.name, ctx.get("tool_name", "?"),
                    )
            except Exception as e:
                logger.error(
                    "Recovery step %s raised for %s: %s",
                    step.name, failure_type.name, e,
                )

        # Determine outcome
        if len(steps_succeeded) == len(steps_attempted) and steps_attempted:
            outcome = RecoveryOutcome.RECOVERED
        elif steps_succeeded:
            outcome = RecoveryOutcome.PARTIAL_RECOVERY
        else:
            outcome = RecoveryOutcome.ESCALATION_REQUIRED

        # Determine recommended action
        if outcome == RecoveryOutcome.RECOVERED:
            action = RecoveryStep.RETRY_IMMEDIATELY if recipe.wait_seconds == 0 else RecoveryStep.WAIT_AND_RETRY
        elif outcome == RecoveryOutcome.PARTIAL_RECOVERY:
            # Find the first step that didn't succeed as the recommended action
            failed_steps = [s for s in steps_attempted if s not in steps_succeeded]
            action = failed_steps[0] if failed_steps else RecoveryStep.ESCALATE
        else:
            action = RecoveryStep.ESCALATE

        event = RecoveryEvent(
            timestamp=time.time(),
            failure_type=failure_type,
            steps_attempted=tuple(steps_attempted),
            steps_succeeded=tuple(steps_succeeded),
            outcome=outcome,
            detail=f"{'→'.join(s.name for s in steps_attempted)}: {len(steps_succeeded)}/{len(steps_attempted)} succeeded",
            context=ctx,
        )
        self._events.append(event)

        logger.info(
            "Recovery for %s: %s (%s)",
            failure_type.name, outcome.name, event.detail,
        )

        return RecoveryResult(
            outcome=outcome,
            event=event,
            wait_seconds=recipe.wait_seconds if outcome == RecoveryOutcome.RECOVERED else 0.0,
            action=action,
        )

    def reset(self, failure_type: FailureType | None = None, context: dict | None = None) -> None:
        """Reset attempt counters.

        Args:
            failure_type: Reset only this type. If None, reset all.
            context: If provided, reset only the specific type+context key.
        """
        if failure_type is None:
            self._attempts.clear()
        else:
            key = self._attempt_key(failure_type, context)
            self._attempts.pop(key, None)

    @property
    def events(self) -> list[RecoveryEvent]:
        """All recovery events logged by this engine."""
        return list(self._events)

    @property
    def stats(self) -> dict[str, int]:
        """Summary statistics."""
        outcomes = {}
        for event in self._events:
            name = event.outcome.name.lower()
            outcomes[name] = outcomes.get(name, 0) + 1
        return {
            "total_attempts": len(self._events),
            **outcomes,
        }


# ── Convenience: classify + attempt in one call ──────────────────────

def try_recover(
    engine: RecoveryEngine,
    error: Exception | str,
    context: dict | None = None,
    step_executor: Callable[[RecoveryStep, dict], bool] | None = None,
) -> RecoveryResult:
    """Classify an error and attempt recovery in one call.

    This is the primary entry point for tool-execution hooks.
    """
    failure_type = FailureType.classify(error, context)
    return engine.attempt_recovery(failure_type, context, step_executor)
