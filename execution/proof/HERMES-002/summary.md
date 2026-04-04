# HERMES-002: Deferred Tool Loading — Proof

## Design Change
Original plan: ToolSearch meta-tool for on-demand tool discovery.
Problem: API vereist tools in schema → 2-turn overhead.
Final design: Simpele core/extended split met `tool_loading_mode` parameter.
Tiered mode stuurt alleen 16 core tool schemas naar API (~36% reductie).

## Consulted Docs
- planning/MASTER_PLAN.md
- toolsets.py (bestaande toolset systeem)
- model_tools.py (get_tool_definitions)
- claw-code tools/src/lib.rs (ToolSearch pattern → aangepast)

## Token Measurement
- Full: 31 tools = ~8,545 tokens
- Core (tiered): 16 tools = ~5,452 tokens
- Extended: 18 tools = ~3,093 tokens (36% savings)
- Note: ha_*/rl_* zijn al conditioneel; typische besparing ~1.5K-3K tok/turn

## Protected Invariants
1. **tool_loading_mode=full identiek aan huidig**: ✅ — default is "full", geen gedragswijziging
2. **Alle tools bereikbaar**: ✅ — handlers bestaan ongewijzigd, alleen schema niet in API
3. **MCP tools niet geraakt**: ✅ — CORE_TOOLS filtert alleen registry tools

## Verification
- CORE_TOOLS: 16 tools, importeerbaar uit toolsets ✅
- get_tool_definitions accepteert tool_loading_mode parameter ✅
- Tiered mode filtert extended tools uit tools_to_include set ✅
- Default "full" geeft identiek gedrag ✅

## Files Changed
- `toolsets.py` — EXTENDED: +16 LOC (CORE_TOOLS frozenset)
- `model_tools.py` — EXTENDED: +12 LOC (tiered filter in get_tool_definitions)

## Integration Note
Caller (run_agent.py __init__) kan `tool_loading_mode="tiered"` meegeven.
Config optie `agent.tool_loading_mode` kan dit sturen vanuit config.yaml.
Niet gehooked in config — bewust, zodat de feature expliciet geactiveerd wordt.
