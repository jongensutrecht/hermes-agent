# HERMES-006: Structured Task Packets — Proof

## Consulted Docs
- planning/MASTER_PLAN.md
- claw-code runtime/src/task_packet.rs (validated packet pattern)

## Protected Invariants
1. **Bestaande delegate_task met vrije tekst blijft werken**: ✅ — TaskPacket is additive, geen bestaande code gewijzigd
2. **TaskPacket is optioneel**: ✅ — standalone module, niet verplicht

## Verification

### Quality Gate
```
$ python -c "from agent.task_packet import TaskPacket, validate_packet; print('OK')"
OK
```

### Acceptance Tests
- F1: validate_packet wijst lege objective/scope af ✅
- F2: Accumulerende validatie (4 errors in 1 call) ✅
- F3: to_prompt() serialiseert naar leesbaar format met alle secties ✅
- S1: TaskPacket is een dataclass ✅
- S2: Geen runtime imports (standalone, AST-verified) ✅
- ValidatedPacket newtype wrapper ✅
- to_delegate_kwargs() converteert naar delegate_task kwargs ✅

## Files Changed
- `agent/task_packet.py` — NEW (152 LOC)
