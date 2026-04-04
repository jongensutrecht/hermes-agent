# HERMES-004: Summary Compression Pre-pass — Proof

## Consulted Docs
- planning/MASTER_PLAN.md
- agent/context_compressor.py (bestaande LLM-based compressor)
- claw-code runtime/src/summary_compression.rs (priority tier pattern)

## Protected Invariants
1. **Bestaande context compressor ongewijzigd**: ✅ — geen edits aan context_compressor.py
2. **Pre-pass is optioneel**: ✅ — standalone module, geen verplichte import

## Verification

### Quality Gate
```
$ python -c "from agent.summary_compression import compress_summary; print('OK')"
OK
```

### Acceptance Tests
- F1: 105 lines → 24 lines (default budget 1200 chars/24 lines) ✅
- F2: P0 lines (Scope, Current work, Pending work, Status, Goal) altijd behouden ✅
- F3: "[81 lines omitted]" notice bij truncatie ✅
- S1: Geen LLM imports (AST-verified) ✅
- S2: Pure functie, idempotent ✅
- P1: Budgets getest: small (8 lines/400 chars), default (24/1200), big (80/5000) ✅
- Edge cases: empty input, single line ✅

## Files Changed
- `agent/summary_compression.py` — NEW (186 LOC)

## Compression Algorithm
1. Normalize (collapse blank lines, strip whitespace)
2. Deduplicate (case-insensitive, first occurrence wins)
3. Classify lines: P0 (scope/goal/status) > P1 (headers) > P2 (bullets) > P3 (filler)
4. Greedy select: iterate P0→P3, add lines that fit budget
5. Reconstruct in original order
6. Append "[N lines omitted]" notice
