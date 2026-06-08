---
name: documentation
description: >
  Use when the user needs to document PLC code, create functional descriptions, generate block headers,
  write tag comments, or produce project documentation. Triggers on: "document this block", "add comments",
  "write a functional description", "generate docs", "block header", "we need documentation",
  "explain the project", "create a manual", or any request to produce written documentation
  for TIA Portal PLC code or project structure.
---

# Documentation — TIA Portal Code & Project Documentation

## Overview

Generates structured documentation for TIA Portal PLC projects. Covers block headers, network comments, tag descriptions, functional descriptions, and project overview documents. Adapts output to the audience (maintainer, operator, auditor).

## When to Use

- User asks to "document this", "add comments", "write a description"
- User needs a functional description (FDS) or design document
- User inherits a project with zero documentation
- User needs to produce handover / commissioning docs
- User wants block headers and network comments added

## Workflow

### 1. Read the Project Context

Gather the information needed for documentation:

1. `browse_project_tree` — full project structure
2. `get_block_content` on blocks to document — read all networks
3. `list_tags` — tag tables for address documentation
4. `read_hardware_config` — if documenting hardware setup

### 2. Choose Documentation Type

| Type | Audience | Content |
|------|----------|---------|
| Block header | Maintainer | Purpose, I/O, author, version, changelog |
| Network comments | Maintainer | Per-network explanation of logic intent |
| Tag descriptions | Maintainer / Operator | Address, data type, unit, range, purpose |
| Functional description (FDS) | Engineer / Auditor | Full block behavior, sequences, safety |
| Project overview | New team member | Architecture, signal flow, hardware map |
| Commissioning guide | Commissioning engineer | Step-by-step setup and test procedures |

### 3. Block Header Template

Generate this comment block for the top of SCL/STL code:

```
// =============================================================================
// Block:      FC102_MotorControl
// Type:       FC (Function)
// Author:     [from user or "Original author unknown"]
// Date:       2026-06-08
// Version:    1.0
// Purpose:    Self-holding motor start/stop with safety interlock,
//             thermal monitoring, and alarm forwarding.
// Called by:  OB1 (Main), Network 4
// Calls:      FC50_AlarmHandler
// Modified:   [date] - [description of change]
// =============================================================================
//
// Interface:
//   Inputs:
//     start_cmd     (Bool)  - Start pushbutton command
//     stop_cmd      (Bool)  - Stop pushbutton command (NC contact)
//     estop_ok      (Bool)  - E-STOP healthy (TRUE = safe)
//     thermal_ok    (Bool)  - Thermal overload relay healthy
//   Outputs:
//     motor_running (Bool)  - Motor contactor output
//     alarm_active  (Bool)  - Alarm indication
//   InOut:
//     run_seconds   (DInt)  - Accumulated runtime in seconds
//
// Dependencies:
//   - DB10_MotorParams: parameter DB with alarm timeout, debounce time
//   - FC50_AlarmHandler: centralized alarm management
//
// Safety notes:
//   - E-STOP interlock is hardwired (this is software backup only)
//   - Thermal monitoring has 5-second debounce to avoid nuisance trips
// =============================================================================
```

### 4. Network Comment Template

For each network, add a comment explaining the **intent** (not just restating the code):

```
// --- Network 3: Self-holding circuit ---
// Implements a classic start/stop latch:
// - Pressing START engages the motor
// - Motor stays ON via self-feedback (holding contact)
// - Pressing STOP or loss of safety interlock breaks the latch
// - Note: Uses boolean logic instead of S/R coils for scan-cycle safety
```

### 5. Functional Description Template

For formal FDS documents:

```markdown
# Functional Description: [Block Name]

## 1. Overview
- Block identifier and type
- Functional purpose (one paragraph)
- Safety classification (SIL level if applicable)

## 2. Inputs and Outputs
| Name | Direction | Type | Unit | Range | Description |
|------|-----------|------|------|-------|-------------|

## 3. Functional Requirements
### FR-01: [Function name]
- **Trigger:** [what initiates this function]
- **Behavior:** [what happens]
- **Conditions:** [prerequisites and interlocks]
- **Result:** [expected outcome]

### FR-02: ...

## 4. Sequence Diagram
Step-by-step description of the main operating sequence.

## 5. Alarm and Fault Handling
| Alarm | Condition | Priority | Response |
|-------|-----------|----------|----------|

## 6. Dependencies
- Hardware: [modules, I/O addresses]
- Software: [called blocks, shared DBs]
- Communication: [PROFINET, OPC UA, etc.]

## 7. Test Cases
| Test | Precondition | Action | Expected Result |
|------|-------------|--------|-----------------|
```

### 6. Project Overview Template

```markdown
# Project Overview: [Project Name]

## Architecture
- PLC(s): [model and firmware]
- HMI(s): [model and number of screens]
- Communication: [PROFINET, PROFIBUS, OPC UA]
- Safety: [F-CPU, safety I/O, SIL level]

## Hardware Map
| Module | Slot | Type | Address Range | Purpose |
|--------|------|------|---------------|---------|

## Software Structure
### Main Cycle (OB1)
| Network | Block Call | Purpose |
|---------|-----------|---------|

### Interrupts
| OB | Trigger | Purpose |
|----|---------|---------|

## Signal Flow
[ASCII diagram or description of main signal paths]

## Tag Tables
| Table | Purpose | Approx. Tags |
|-------|---------|-------------|

## Data Blocks
| DB | Type | Purpose | Size |
|----|------|---------|------|
```

## Rules

- **Read before documenting** — never write docs without reading the actual block content
- **Document intent, not syntax** — "Starts the motor" not "Sets Motor_Running to TRUE"
- **Include the why** — "5-second debounce to filter contact bounce on thermal relay"
- **Use actual names** — tag names, block names, DB names from the project
- **Note safety functions** — always flag safety-related logic explicitly
- **Keep it maintainable** — documentation should be easy to update when code changes
- **Don't over-document** — a simple AND gate doesn't need 3 paragraphs. One line: "E-STOP interlock — motor only runs when E-STOP is healthy"
- **Include units and ranges** — for analog values, always specify engineering unit and expected range
- **Version and date** — always include modification date in block headers
