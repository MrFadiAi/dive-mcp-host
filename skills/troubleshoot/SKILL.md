---
name: troubleshoot
description: >
  Use when the user reports a PLC fault, alarm, unexpected behavior, or anything broken in TIA Portal.
  Triggers on: fault diagnosis, alarm investigation, "why does X not work", "PLC stopped",
  "LED flashing red", "communication error", "motor won't start", "HMI shows error",
  block returns wrong value, watchdog fault, OB86/OB122 trip, or any "it doesn't work" request.
---

# Troubleshoot — Systematic TIA Portal Fault Diagnosis

## Overview

Structured troubleshooting workflow for TIA Portal / S7-1500 / S7-1200 PLC systems. Follows a **symptom → gather → isolate → fix** loop adapted for industrial automation. Works with both Guide Mode (read-only) and full-access sessions.

## Workflow

### Phase 1: Symptom Intake

Ask the user for (or read from the project if available):

- **What happened?** — fault LED, alarm text, HMI popup, unexpected output
- **When does it happen?** — always, intermittent, after change, at startup
- **What changed recently?** — new hardware, software update, wiring, setpoint change
- **Error codes** — OB number, diagnostic buffer entries, LED pattern

**If the user is vague**, use read-only tools to investigate:

1. `list_plcs` — learn the exact PLC names first (device name + PLC-software name)
2. `list_blocks` — find diagnostic blocks (OB80–OB87, OB121–OB122) without dumping the whole tree
3. `search_code` — when chasing *where* a signal/fault is handled, grep all block source in one call (e.g. `search_code("OB86")`, `search_code("FAULT")`)
4. `tag_usage` — trace a specific tag/signal across all blocks (who reads/writes it)
5. `get_block_content` — read the actual logic once you've located the block
6. `read_hardware_config` — check configured modules vs actual

### Phase 2: Information Gathering

Use read-only tools to collect evidence before proposing fixes:

| Tool | What it reveals |
|------|----------------|
| `list_plcs` | PLC inventory — exact names + block/tag counts (cheapest entry point) |
| `list_blocks` | Block index for one PLC — find the right block fast, no code |
| `search_code` | Where a signal/address/keyword appears across ALL block code |
| `tag_usage` | Every read/write of a specific tag across the PLC |
| `get_block_content` | Actual logic — trace signal flow through networks |
| `read_hardware_config` | Module configuration, IP addresses, PROFINET names |
| `list_tag_tables` | Tag definitions, data types, addresses |

**Prefer `list_plcs` → `list_blocks` → `search_code`/`tag_usage` to *locate* logic.** Reserve `browse_project_tree` for when you need the full nested structure (folders, types, tag-table groups) — it's large and often truncates.

**Always read before suggesting.** If you haven't read the block, you don't know what's in it.

### Phase 3: Root Cause Isolation

Apply this diagnostic decision tree:

```
Symptom reported
├── Communication fault?
│   ├── Check PROFINET device names match (case-sensitive!)
│   ├── Check IP configuration (subnet, gateway)
│   ├── Check hardware config vs actual modules
│   └── Check OB86 / diagnostic buffer for lost station
├── Logic fault (wrong output)?
│   ├── Read the block, trace the signal from output → input
│   ├── Check interlocks — is something holding it off?
│   ├── Check data types — implicit conversion losing precision?
│   ├── Check timing — is the signal arriving in the right scan?
│   └── Check multi-instance — shared FB writing to same instance DB?
├── Fault / Alarm trip?
│   ├── Read the fault handler block
│   ├── Check alarm limits in DB or HMI tag
│   ├── Check sensor range — analog out of range?
│   └── Check watchdog / timeout settings
├── Intermittent?
│   ├── Check for race conditions (shared tags, interrupt OBs)
│   ├── Check cycle time — is OB1 exceeding watchdog?
│   ├── Check network load / PROFINET update time
│   └── Check for uninitialized variables
└── Startup fault?
    ├── Check OB100 / startup logic
    ├── Check initial values in DBs
    ├── Check retentive vs non-retentive data
    └── Check hardware module firmware compatibility
```

### Phase 4: Fix Proposal

Present the fix in this format:

```
**Root Cause:** [one-line explanation]

**Fix: [Action Title]**
- 📍 Location: [PLC] > [Folder] > [Block] (Network N)
- 📝 What to do: [specific instruction]
- 📄 Code:
  ```
  [exact code if applicable]
  ```

**Verification:** [how to confirm the fix works]
```

If in Guide Mode, use the Guide Mode output format with step numbers.

## Common TIA Portal Pitfalls

| Symptom | Common Cause | Check |
|---------|-------------|-------|
| PROFINET device won't connect | Device name mismatch (case-sensitive) | Compare `read_hardware_config` name with actual device |
| Motor won't start | Missing interlock or E-STOP wired NC | Trace forward path in motor block |
| Analog value jumps | Wrong wire (2-wire vs 4-wire) or wrong range | Check hardware config analog channel type |
| DB data resets on restart | DB marked non-retentive | Check DB properties |
| Cycle watchdog fault | Large block in main OB, tight watchdog | Move to background OB or increase watchdog |
| OB86 trip | PROFINET station dropped | Check cable, duplicate IP, device name |
| SCL compile error | Type mismatch INT↔DINT | Check all arithmetic operands |
| HMI shows "####" | Tag quality bad or address wrong | Check HMI tag connection and PLC address |

## Rules

- **Read first, suggest second** — never propose a fix without having read the relevant block or config
- **One root cause at a time** — don't shotgun 5 fixes; isolate the most likely cause first
- **Ask for diagnostic buffer** — if the user mentions a fault, ask for the PLC diagnostic buffer contents
- **Show the signal trace** — when debugging logic, show the path from input to output through each network
- **Don't guess hardware** — if it could be a wiring issue, say so. Software can't fix bad wiring
- **Prioritize safety** — if a fault involves safety functions (E-STOP, safety PLC), flag it explicitly
- **Use actual block/tag names** — never say "the motor block" — say "FC102_MotorControl (Network 3)"
