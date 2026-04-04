# HERMES-008: Typed Error Classification — Proof

## Consulted Docs
- agent/recovery_recipes.py, planning/MASTER_PLAN.md

## Protected Invariants
1. Alle bestaande classify() calls geven hetzelfde resultaat: ✅
2. String-based fallback werkt als exception types niet beschikbaar: ✅

## Verification
- F1: RateLimitError/APITimeoutError/AuthenticationError -> correct type (no string match) ✅
- F2: HTTP 429/529/403/502 -> correct type ��
- F3: String fallback ("timeout", "rate_limit", "mcp") nog steeds werkend ✅
- S1: Geen harde imports van openai/anthropic/httpx (AST-verified) ✅
- S2: Bestaande tests ongewijzigd groen ✅
- Priority: Exception type overrides string content ✅

## Classification Layers
1. Exception type name (exc_type in known set) — stable API contract
2. HTTP status code from context dict — numeric, stable
3. String pattern fallback — last resort

## Files Changed
- agent/recovery_recipes.py: FailureType.classify() refactored (+57 LOC, -15 LOC)
