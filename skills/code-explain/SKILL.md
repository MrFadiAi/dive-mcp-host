---
name: code-explain
description: >
  Use when the user asks to understand, interpret, or review PLC code they didn't write.
  Triggers on: "explain this block", "what does this SCL do", "help me understand",
  "walk me through this code", "decode this STL", "what is FB200 doing",
  "someone else wrote this", inherited project, code walkthrough, block analysis,
  or any request to make sense of existing PLC logic.
---

# Code Explain — PLC Code Walkthrough & Interpretation

## Overview

Decodes and explains PLC code (SCL, STL, LAD, FBD) in plain language. Designed for engineers inheriting code from others, reviewing vendor blocks, or understanding complex interlocks. Translates cryptic logic into clear intent.

## When to Use

- User says "explain this", "what does X do", "walk me through", "help me understand"
- User pastes or references a block they didn't write
- User is new to a project and needs orientation
- User encounters unfamiliar programming style or pattern

## Workflow

### 1. Read the Block

Use read-only tools to get the full picture:

1. `browse_project_tree` — locate the block in the project hierarchy
2. `get_block_content` or `read_block` — read the full block with all networks
3. `list_tags` — find the tag table for any referenced tags
4. If the block calls other blocks, read those too (at least the interface)

### 2. Analyze Structure

Before diving into line-by-line, explain the **big picture**:

```
**Block: FC102_MotorControl**
- Type: FC (function, no memory)
- Called by: OB1 (main cycle), Network 4
- Calls: FC50_AlarmHandler
- Purpose: Self-holding motor start/stop with safety interlock and alarm forwarding
- Networks: 6 total
  - N1: Input conditioning (debounce)
  - N2: Safety interlock check (E-STOP, thermal)
  - N3: Self-holding circuit (start/stop/latch)
  - N4: Output assignment
  - N5: Runtime counter
  - N6: Alarm forwarding
```

### 3. Network-by-Network Explanation

For each network, explain in this format:

```
**Network 3: Self-holding circuit**

Logic:
```
#start_cmd AND NOT #stop_cmd OR #motor_running → #motor_running
```

Plain language:
When the START button is pressed AND the STOP button is NOT pressed,
the motor runs. Once running, it stays on even after releasing START
(because `#motor_running` feeds back into the OR). Pressing STOP breaks
the latch.

Key insight: This is a classic SET/RESET pattern implemented with
boolean logic instead of S/R coils. Works the same way but uses only
one output variable.
```

### 4. SCL-Specific Translation Table

When reading SCL code, translate common patterns:

| SCL Pattern | Plain English |
|------------|---------------|
| `#temp := #a AND #b;` | "Store the AND of a and b in temp" |
| `IF #condition THEN ... END_IF;` | "When condition is true, do ..." |
| `#out := SEL(#sel, #val0, #val1);` | "If sel is FALSE use val0, if TRUE use val1" |
| `#out := LIMIT(#mn, #in, #mx);` | "Clamp in between min and max" |
| `#out := N_TO_X(#val);` | "Convert/normalize val to X type" |
| `#timer(IN:=, PT:=);` | "Start a timer with preset time PT" |
| `#counter(CU:=, R:=, PV:=);` | "Count up, reset at R, preset value PV" |
| `#arr[#i]` | "Array element at index i" |
| `#fb_inst()` | "Call FB instance (stores state in instance DB)" |
| `CASE #x OF 1: ... 2: ... END_CASE;` | "Switch on x: if 1 do ..., if 2 do ..." |

### 5. STL-Specific Translation Table

When reading STL code, translate stack operations:

| STL Instruction | Plain English |
|----------------|---------------|
| `LD #a` | "Load a onto the stack (RLO)" |
| `AND #b` | "AND b with current RLO" |
| `OR #c` | "OR c with current RLO" |
| `AND NOT #d` | "AND (NOT d) with current RLO" |
| `= #out` | "Assign RLO to output" |
| `S #bit` | "Set bit to 1 (latch)" |
| `R #bit` | "Reset bit to 0 (unlatch)" |
| `FP #edge` | "Detect rising edge (0→1)" |
| `FN #edge` | "Detect falling edge (1→0)" |
| `TAK` | "Swap accumulator 1 and 2" |
| `+ #val` | "Add val to accumulator" |
| `L #addr` | "Load address/value into accumulator" |
| `T #addr` | "Transfer accumulator to address" |
| `CALL #fb` | "Call function block" |
| `UC #fc` | "Unconditional call to FC" |
| `JMP #lbl` | "Jump to label" |
| `JCN #lbl` | "Jump if RLO = 1" |

### 6. Generate Summary

End with a concise summary:

```
## Summary

**FC102_MotorControl** is a motor control block with:
- Safety interlocks (E-STOP, thermal overload)
- Self-holding start/stop circuit
- Runtime tracking
- Alarm forwarding to a central handler

**Signal flow:**
START_BTN → debounce → safety check → self-hold → MOTOR_OUT
                                      ↘ runtime counter
                                      ↘ alarm handler

**Notable patterns:**
- Uses boolean self-hold instead of S/R coils (line 45)
- Runtime counter resets daily at midnight (requires external trigger)
- Alarm priority set to 3 (medium) — configurable via DB parameter
```

## Rules

- **Always read the actual block first** — don't explain from the user's description alone
- **Explain WHY, not just WHAT** — "this checks the E-STOP" is obvious; explain *why it's checked here* (safety interlock must be before the self-hold)
- **Use the user's language** — if they say "I don't know STL", translate everything to plain logic or SCL
- **Show signal flow** — trace inputs through networks to outputs
- **Note dependencies** — if the block reads from a DB or calls another block, mention it
- **Highlight non-obvious tricks** — experienced programmers do clever things (edge detection, state machines, timer cascades). Call these out explicitly
- **Don't judge the code** — explain what it does, not whether it's good or bad (use code-review-mode for that)
- **Keep it concise** — a 200-network block doesn't need 200 paragraphs. Group related networks and explain the pattern
