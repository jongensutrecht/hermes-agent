# HERMES-005: MCP Graceful Degradation — Proof

## Consulted Docs
- planning/MASTER_PLAN.md
- claw-code runtime/src/mcp_lifecycle_hardened.rs

## Protected Invariants
1. **Werkende servers onbeïnvloed door gefaalde server**: ✅ — per-server tracking, geen global state
2. **Tool calls identiek**: ✅ — tracker is observational only, geen invloed op MCP protocol

## Verification

### Acceptance Tests
- F1: 1 failed server van 3 → 2 healthy, missing_tools correct ✅
- F2: available_tools bevat alleen tools van healthy+degraded servers ✅
- F3: Recoverable→DEGRADED, Fatal→DEAD, 3x consecutive→DEAD ✅
- S1: Thread-safe (threading.Lock) ✅
- S2: Fail-open (None guard) ✅
- ErrorClass: 6 classes + recoverability classification ✅
- mark_healthy resets status ✅
- get_degraded_report() structured output ✅

## Files Changed
- `agent/mcp_health.py` — NEW (230 LOC)

## Integration Note
McpHealthTracker is standalone, ready for integration in the MCP server
startup/connection code. register_server() at startup, mark_failed() on
errors, mark_healthy() on successful tool calls.
