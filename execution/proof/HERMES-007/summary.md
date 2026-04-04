# HERMES-007: TaskPacket Native Integration — Proof

## Consulted Docs
- agent/task_packet.py, tools/delegate_tool.py, planning/MASTER_PLAN.md

## Protected Invariants
1. String-based delegate_task calls identiek: ✅ — YOUR TASK format ongewijzigd
2. Subagent resultaat formaat ongewijzigd: ✅ — alleen prompt assembly gewijzigd

## Verification
- F1: TaskPacket(objective, scope, ...) -> goal met OBJECTIVE+SCOPE secties ✅
- F2: String goal -> YOUR TASK format (backward compat) ✅
- F3: Ongeldige TaskPacket -> JSON error, geen crash ✅
- S1: Alleen agent.task_packet geïmporteerd ✅

## Files Changed
- tools/delegate_tool.py: packet= parameter + validatie + conversie (+19 LOC)
