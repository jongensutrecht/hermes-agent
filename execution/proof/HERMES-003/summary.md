# HERMES-003: Prompt Cache Break Detection — Proof

## Consulted Docs
- planning/MASTER_PLAN.md
- agent/prompt_caching.py (bestaande caching strategie)
- claw-code api/src/prompt_cache.rs (fingerprinting + break detection pattern)

## Protected Invariants
1. **Bestaande cache_control breakpoint plaatsing ongewijzigd**: ✅
   - apply_anthropic_cache_control() ongewijzigd
   - TrackedPromptState is additive (nieuw, geen bestaande code gewijzigd)
2. **Fail-open**: ✅
   - TrackedPromptState is een standalone dataclass
   - Als detectie crasht, werkt caching gewoon door
   - Geen imports van agent runtime modules

## Verification

### Quality Gate
```
$ python -c "from agent.prompt_caching import TrackedPromptState; print('OK')"
OK
```

### Acceptance Tests (alle PASS)
- F1: Stabiele fingerprints + dalende cache_read → WARNING "Unexpected cache break" ✅
- F2: Gewijzigde system prompt + dalende cache_read → DEBUG (expected) ✅
- F3: Stats telt hits/misses/breaks correct ✅
- S1: TrackedPromptState is pure dataclass ✅
- S2: Detectie is fail-open (caching werkt ongewijzigd) ✅
- P1: Unit test met mock responses voor expected en unexpected breaks ✅
- Fingerprint order-independent (sort_keys=True) ✅

## Files Changed
- `agent/prompt_caching.py` — EXTENDED: +122 LOC (TrackedPromptState, _fingerprint, check_cache_break)

## Integration Note
TrackedPromptState is ready for integration in run_agent.py's API call loop.
Not yet hooked in — requires reading cache_read_input_tokens from API response
usage dict and calling state.check_cache_break() after each call. This is a
lightweight addition to the existing usage tracking code.
