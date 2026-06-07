---
name: code-review-mode
description: >
  Use when Code Review Mode is active. Automatically reviews every PLC block response
  against IEC 61131-3 standards, checking for safety, naming, structure, and documentation.
---

# Code Review Mode — IEC 61131-3 Block Review

## Overview

When Code Review Mode is active, you review **every response that contains block content or code** against IEC 61131-3 best practices and industrial safety standards. You append a structured review section after your normal response.

## Scope

This mode applies to ANY response that includes:
- Block content (from `get_block_content` or `read_block`)
- Code snippets (SCL, STL, LAD, FBD)
- Block interface definitions
- Tag definitions or modifications
- Hardware configuration changes

For responses that are purely conversational (no code/blocks), skip the review.

## Review Categories

### 1. IEC 61131-3 Compliance

| Check | Severity | What to look for |
|-------|----------|------------------|
| Type safety | Critical | Implicit type conversions, mixing INT/DINT/REAL without explicit cast |
| Initialization | Warning | Variables used before assignment, missing initial values in FB/FC interface |
| Boundary checks | Critical | Array access without bounds check, timer/counter limits not validated |
| Return values | Warning | FC return value not used, error codes not checked |
| EN/ENO handling | Info | Enable input not used where available |

### 2. Safety Review

| Check | Severity | What to look for |
|-------|----------|------------------|
| Emergency stop | Critical | No E-STOP handling in motor/actuator control blocks |
| Safe state defaults | Critical | Outputs defaulting to ON (unsafe) instead of OFF on fault |
| Mutual exclusion | Critical | Two outputs that must never be active simultaneously (e.g., forward/reverse) |
| Watchdog | Warning | Long-running operations without timeout or watchdog |
| Redundancy | Info | Safety-critical logic without redundant check |
| Fail-safe design | Critical | System behavior on communication loss, power failure not defined |

### 3. Race Conditions

| Check | Severity | What to look for |
|-------|----------|------------------|
| Shared resources | Critical | Multiple writers to same tag/DB without coordination |
| Interrupt safety | Warning | Variables modified in both cyclic OB and interrupt OB |
| Multi-instance | Warning | Multi-instance FB with shared global data |
| Edge detection | Warning | R_TRIG/F_TRIG used incorrectly or missing where needed |

### 4. Naming Conventions

| Check | Severity | What to look for |
|-------|----------|------------------|
| Block naming | Info | Consistent naming: FC/FB prefix + descriptive name |
| Variable naming | Info | Consistent prefix convention: `i` for inputs, `q` for outputs, `stat` for statics |
| Tag naming | Warning | Inconsistent tag names across blocks or missing tag comments |
| DB member naming | Info | Clear, descriptive member names in data blocks |

### 5. Documentation & Comments

| Check | Severity | What to look for |
|-------|----------|------------------|
| Block header | Warning | Missing or incomplete block header comment (purpose, author, version) |
| Network comments | Warning | Network without a title or comment explaining its purpose |
| Interface docs | Info | FB/FC inputs/outputs without comments |
| Tag comments | Info | Tags in DB or interface without description |
| Magic numbers | Warning | Literal constants instead of named constants (e.g., `500` instead of `#TIMEOUT_MS`) |

### 6. Code Structure

| Check | Severity | What to look for |
|-------|----------|------------------|
| Block size | Info | Block exceeds ~50 networks or 200 lines — consider splitting |
| Nested calls | Warning | Call depth > 3 levels — hard to debug and maintain |
| Dead code | Warning | Unreachable code or unused variables |
| Code duplication | Info | Similar logic repeated across blocks — consider refactoring into reusable FC |

## Output Format

After your normal response, append a review section in this format:

```
---
🔍 **Code Review** (Code Review Mode Active)

| # | Category | Severity | Location | Issue | Suggestion |
|---|----------|----------|----------|-------|------------|
| 1 | Safety | 🔴 Critical | Network 3 | No E-STOP check before motor start | Add `AND #EmergencyStop_OK` condition |
| 2 | Safety | 🔴 Critical | Network 5 | Forward/reverse both possible | Add mutual exclusion interlock |
| 3 | Naming | 🟡 Warning | Interface | Input `IN1` has no comment | Rename to `StartCommand` and add comment |
| 4 | Docs | 🟡 Warning | Block header | No block description | Add: "// Motor control with safety interlock" |
| 5 | Structure | 🔵 Info | Block overall | 45 networks | Consider splitting into sub-FBs |

**Score: 6/10** — Functional but needs safety interlocks and documentation.
```

### Severity Icons
- 🔴 **Critical** — Must fix before deployment (safety or correctness issue)
- 🟡 **Warning** — Should fix (maintainability, readability, best practice)
- 🔵 **Info** — Nice to have (style, convention, optimization)

### Score Calculation
- Start at 10
- -2 per Critical issue
- -1 per Warning
- -0.5 per Info (rounded down)
- Minimum score: 1/10

## Rules

- **Be constructive** — every issue must include a suggestion or fix
- **Be specific** — reference exact network numbers, variable names, tag addresses
- **Be thorough** — check all categories, not just the obvious ones
- **Don't repeat** — if you already mentioned an issue in your main response, just reference it in the table (don't re-explain)
- **Prioritize safety** — Critical safety issues should appear first in the table
- **Use actual values** — don't say "some variable" — say "Motor_Speed (Network 4, line 12)"
- **Don't change your response style** — write your normal response first, then append the review section
