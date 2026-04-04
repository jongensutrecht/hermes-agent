# Hermes Agent Improvements — claw-code Patterns

## Doel

Implementeer bewezen patterns uit claw-code (open Claude Code reverse-engineering) in Hermes agent.
Prioriteit: features die direct dagelijks gebruik verbeteren (minder token-waste, betere foutafhandeling, veiliger autonomie).

## Bron

Analyse van `/home/mrbiggles/dev/github.com/claw-code/` (292 commits, ~48K Rust LOC, april 2026).
Vergelijking met bestaande Hermes source in `~/.hermes/hermes-agent/`.

## Wat Hermes al heeft

- Context compaction (agent/context_compressor.py, 676 LOC) — LLM-gebaseerde samenvatting
- Trajectory compression (trajectory_compressor.py) — post-processing voor training
- Prompt caching (agent/prompt_caching.py) — Anthropic cache breakpoints (system + 3 msgs)
- Prompt builder (agent/prompt_builder.py) — system prompt assembly met skills, memory, context files
- Toolsets (toolsets.py) — toolset grouping en distributie
- Usage pricing (agent/usage_pricing.py) — kostenberekening per model

## Wat ontbreekt (prioriteit hoog → laag)

### P0 — Direct merkbaar in dagelijks gebruik

1. **Recovery Recipes** — gestructureerde auto-recovery bij bekende faalscenario's
   - Provider timeouts, MCP-fouten, tool crashes, permission denied
   - "Try once, then escalate" — voorkomt infinite retry loops
   - PartialRecovery als derde uitkomst naast succes/fail
   - Bron: claw-code `runtime/src/recovery_recipes.rs` (631 LOC)

2. **Deferred Tool Loading** — niet alle tool-definities in elke prompt
   - Hermes stuurt nu ~20 tools mee in elke system prompt (~8K tokens)
   - Bij simpele vragen (chat, uitleg) is dat 40% verspilling
   - ToolSearch: agent kan tools dynamisch ontdekken
   - Bron: claw-code `tools/src/lib.rs` ToolSearch pattern

3. **Prompt Cache Break Detection** — detecteer onverwacht cache-verlies
   - Fingerprint per prompt-component (system, tools, messages)
   - Alert bij onverwachte cache-break (zelfde prompt, maar tokens niet gecached)
   - Hermes heeft caching maar geen break-detectie
   - Bron: claw-code `api/src/prompt_cache.rs` (fingerprinting + break alerting)

### P1 — Architectureel waardevol

4. **Summary Compression met Priority Tiers** — budget-aware compressie
   - Complement op bestaande LLM-gebaseerde context compressor
   - Goedkope pre-pass: priority-based line selection (scope > headers > bullets > filler)
   - Nuttig voor subagent-context en cron-job prompts
   - Bron: claw-code `runtime/src/summary_compression.rs`

5. **MCP Graceful Degradation** — doorwerken als 1 MCP-server faalt
   - Track working/failed servers en missing_tools
   - Recoverable vs fatal errors
   - Bron: claw-code `runtime/src/mcp_lifecycle_hardened.rs`

6. **Structured Task Packets voor Delegation** — protocol voor subagent-aansturing
   - objective, scope, acceptance_tests, escalation_policy, reporting_contract
   - Validated packet met accumulerende validatie
   - Bron: claw-code `runtime/src/task_packet.rs`

### P2 — Nice-to-have

7. **Permission Tiers** — ReadOnly < WorkspaceWrite < FullAccess
   - Workspace boundary enforcement
   - Bash command heuristic (whitelist veilige commands)
   - Bron: claw-code `runtime/src/permission_enforcer.rs`

8. **Green Contract** — verificatieniveaus (targeted < package < workspace < merge-ready)
   - Formaliseer proof-check niveaus
   - Bron: claw-code `runtime/src/green_contract.rs`

9. **Policy Engine** — composable And/Or conditions met priority-ordered regels
   - Bron: claw-code `runtime/src/policy_engine.rs`

## Stories (executievolgorde)

| ID | Titel | Prio | Risico | Depends | Status |
|----|-------|------|--------|---------|--------|
| HERMES-001 | Recovery recipes framework | P0 | low | - | done |
| HERMES-002 | Tiered tool loading | P0 | medium | - | done |
| HERMES-003 | Prompt cache break detection | P0 | low | - | done |
| HERMES-004 | Summary compression pre-pass | P1 | low | - | done |
| HERMES-005 | MCP graceful degradation | P1 | medium | - | done |
| HERMES-006 | Structured task packets | P1 | low | - | done |
| HERMES-007 | TaskPacket native integratie in delegate_task | P0 | low | HERMES-006 | done |
| HERMES-008 | Error classificatie: typed exceptions i.p.v. regex | P0 | low | HERMES-001 | done |
| HERMES-009 | Permission tiers | P2 | medium | - | backlog |
| HERMES-010 | Green contract formalisatie | P2 | low | - | backlog |

## Review-feedback (2026-04-05)

Terechte kritiek op ons werk:
1. TaskPacket (HERMES-006) is utility naast het systeem, niet erin — delegate_task accepteert nog steeds alleen strings
2. FailureType.classify() in recovery_recipes.py matcht op substrings — breekt bij gewijzigde error messages

Pre-existing structurele schuld (nu gepland als HERMES-011 t/m 016):
- run_agent.py monoliet (9045 LOC, 116 methods, 17 methods >100 LOC)
- Global state in model_tools._last_resolved_tool_names
- Impliciete state machine (10+ vlaggen i.p.v. expliciete state enum)
- MCP health tracking is write-only (niet gebruikt in agent beslissingen)
- Recovery recipes alleen actief in concurrent tool path, niet in sequential of provider fallback

## Fase 3 — Structurele decompositie (HERMES-011 t/m 016)

Doel: run_agent.py opsplitsen in testbare modules, expliciete state, global state elimineren.
Volgorde: extract modules eerst (011-014), dan state/integratie (015-016).

| ID | Titel | Prio | Risico | Depends | Status |
|----|-------|------|--------|---------|--------|
| HERMES-011 | Extract ProviderFallback module | P1 | medium | - | ready |
| HERMES-012 | Extract ToolExecutor module | P1 | medium | - | ready |
| HERMES-013 | Extract ContextOverseer module | P1 | medium | - | ready |
| HERMES-014 | Explicit AgentState enum | P1 | medium | 011,012,013 | ready |
| HERMES-015 | Eliminate global state in delegate_tool | P0 | low | HERMES-007 | ready |
| HERMES-016 | Activate MCP health in agent loop | P1 | low | HERMES-005 | ready |
