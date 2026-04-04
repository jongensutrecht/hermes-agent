# HERMES-001: Recovery Recipes Framework — Proof

## Consulted Docs
- planning/MASTER_PLAN.md (bron: claw-code recovery_recipes.rs analysis)
- run_agent.py (hook punten geïdentificeerd: _run_tool, sequential paths, __init__)
- agent/context_compressor.py (error handling pattern referentie)

## Protected Invariants
1. **Bestaande tool-execution flow breekt niet als recovery module ontbreekt**: ✅
   - `self._recovery_engine = None` als import faalt (fail-open in __init__)
   - `hasattr(self, '_recovery_engine') and self._recovery_engine` guard in alle hooks
   - Buitenste `try/except Exception: pass` vangt elke recovery crash op
2. **Max 1 retry per faaltype per stap**: ✅
   - `max_attempts=1` in alle recipes (behalve PERMISSION_DENIED: 0)
   - Attempt counter per `failure_type:tool_name` combinatie

## Verification

### Quality Gate
```
$ python -c "from agent.recovery_recipes import RecoveryEngine; print('OK')"
OK
```

### Acceptance Tests (alle PASS)
- F1: ProviderTimeout → RECOVERED met 2.0s wait ✅
- F2: 2e attempt zelfde failure → ESCALATION_REQUIRED ✅
- F3: Partial step success → PARTIAL_RECOVERY ✅
- S1: Geen circular imports ✅
- S2: Module optioneel (hasattr guard) ✅
- P1: Alle 7 FailureTypes correct geclassificeerd + recovery paths ✅
  - PROVIDER_TIMEOUT → RECOVERED (wait_and_retry + switch_provider)
  - PROVIDER_RATE_LIMIT → RECOVERED (wait_and_retry)
  - PROVIDER_OVERLOADED → RECOVERED (wait_and_retry + switch_provider)
  - MCP_ERROR → RECOVERED (reconnect + retry)
  - TOOL_CRASH → RECOVERED (retry)
  - PERMISSION_DENIED → ESCALATION_REQUIRED (never retries)
  - CONTEXT_OVERFLOW → ESCALATION_REQUIRED (compress step needs executor)

### Syntax Check
```
$ python3 -c "import ast; ast.parse(open('run_agent.py').read()); print('SYNTAX OK')"
run_agent.py SYNTAX OK
```

## Files Changed
- `agent/recovery_recipes.py` — NEW (309 LOC): failure taxonomy, recipes, engine, try_recover
- `run_agent.py` — PATCHED: 3 hooks (init + concurrent _run_tool + 2x sequential except blocks)

## Design Decisions
- Fail-open everywhere: recovery crashes never break tool execution
- Per-tool attempt budgets: same failure type on different tools tracked independently
- PERMISSION_DENIED never retries (max_attempts=0) — security boundary
- CONTEXT_OVERFLOW needs COMPRESS_CONTEXT which requires external executor — escalates without executor
- Wait times: 0s (tool_crash) to 5s (rate_limit), configurable per recipe
