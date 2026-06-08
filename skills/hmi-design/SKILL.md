---
name: hmi-design
description: >
  Use when the user needs help with HMI screen design, faceplates, alarm configuration,
  trend views, or operator interface design for TIA Portal WinCC (Unified or Comfort).
  Triggers on: "HMI screen", "faceplate", "alarm list", "trend view", "operator panel",
  "WinCC", "display design", "button layout", "navigation", "screen hierarchy",
  "popup window", "tag logging", "recipe", "user management", or any HMI/UI design request.
---

# HMI Design — TIA Portal WinCC Screen & Faceplate Design

## Overview

Guides design and implementation of HMI screens, faceplates, alarm systems, and navigation for TIA Portal WinCC (Unified and Comfort panels). Focuses on operator usability, consistent design patterns, and industrial HMI best practices (ISA-101).

## When to Use

- User asks about HMI screen layout, faceplates, or navigation
- User needs alarm configuration or alarm list design
- User wants trend views, tag logging, or historical data display
- User is building operator interfaces for S7-1200/S7-1500 systems

## Design Principles (ISA-101 Aligned)

### 1. Hierarchy Levels

```
Level 0: Overview
  └── Level 1: Process Area (e.g., Conveyor, Mixing, Packaging)
        └── Level 2: Unit/Equipment (e.g., Motor 1, Pump 2, Valve 3)
              └── Level 3: Detail (e.g., Parameter tuning, Diagnostics)
```

| Level | Content | Navigation |
|-------|---------|------------|
| Overview | Plant status at a glance, key KPIs | Click area → Level 1 |
| Process Area | Equipment status, batch state, key values | Click unit → Level 2 |
| Unit/Equipment | Detailed I/O, mode selection, setpoints | Popup for params → Level 3 |
| Detail | Tuning, diagnostics, raw I/O | Back to parent |

### 2. Color Standards

| Color | Meaning | Usage |
|-------|---------|-------|
| Green | Running / Normal / Good | Equipment running, valve open |
| Red | Alarm / Fault / Stopped | Equipment fault, alarm active |
| Yellow | Warning / Attention | Maintenance needed, limit approaching |
| Gray | Off / Inactive / Disabled | Equipment not in current mode |
| Blue | Manual / Override | Manual mode active |
| White | Neutral / Status | Setpoints, values, labels |

**Never use color alone** — always combine with shape, text, or pattern for accessibility.

### 3. Navigation Pattern

```
[Plant Overview]
    ├── [Conveyor Area]
    │     ├── [Motor 1] → Faceplate
    │     ├── [Motor 2] → Faceplate
    │     └── [Conveyor Sync]
    ├── [Mixing Area]
    │     ├── [Pump A] → Faceplate
    │     ├── [Pump B] → Faceplate
    │     └── [Temperature Control]
    └── [Alarms] → Alarm List
    └── [Trends] → Trend View
```

## Faceplate Design

### Standard Motor/Pump Faceplate

```
┌─────────────────────────────────────┐
│  Motor 1 — Conveyor Belt Drive     │
│─────────────────────────────────────│
│  Mode: [AUTO] [MAN] [OFF]         │
│                                     │
│       ┌───────────┐                │
│       │   MOTOR   │  1450 RPM     │
│       │  [▶ RUN]  │  12.4 A       │
│       └───────────┘  OK            │
│                                     │
│  Setpoint: [____] RPM              │
│  Runtime:  1,234 h                 │
│                                     │
│  Interlocks:                       │
│  ✓ E-STOP OK   ✓ Thermal OK       │
│  ✓ Guard OK    ✗ Overload         │
│                                     │
│  [Start]  [Stop]  [Reset]  [← Back]│
└─────────────────────────────────────┘
```

### Faceplate Implementation Checklist

- [ ] **Header**: Equipment name and description
- [ ] **Mode selector**: Auto / Manual / Off (with current state highlighted)
- [ ] **Status display**: Running/Stopped with animated graphic
- [ ] **Key values**: Speed, current, temperature (with units)
- [ ] **Setpoint entry**: Numeric input with limits
- [ ] **Interlock status**: Green check / Red X for each interlock
- [ ] **Controls**: Start, Stop, Reset (mode-dependent visibility)
- [ ] **Alarm indicator**: Flashing if active alarm on this equipment
- [ ] **Back button**: Return to parent screen

## Alarm Configuration

### Alarm Categories

| Priority | Color | Behavior | Example |
|----------|-------|----------|---------|
| Critical | Red, flashing | Acknowledge required, always visible | E-STOP tripped, motor overload |
| High | Red, steady | Acknowledge required | Temperature high-high |
| Medium | Yellow | Acknowledge optional | Temperature high, filter dirty |
| Low | Yellow, dim | Information only | Maintenance reminder |
| Info | Gray | Logged only | Mode change, auto-start |

### Alarm List Design

```
┌─────────────────────────────────────────────────┐
│  Active Alarms (3)          [Ack All] [Filter ▼]│
│─────────────────────────────────────────────────│
│  🔴 14:23:01 Motor 1 overload        [ACK]      │
│  🔴 14:22:45 E-STOP Zone 3           [ACK]      │
│  🟡 14:20:12 Tank temp high          [ACK]      │
│─────────────────────────────────────────────────│
│  Alarm History                    [Export CSV]   │
│  14:18:00 Motor 2 started — cleared             │
│  14:15:30 Pump A maintenance due — cleared      │
└─────────────────────────────────────────────────┘
```

### Alarm Implementation Notes

- Use PLC alarm bits mapped to HMI alarm tags
- Include **alarm text** (plain language), **help text** (action to take), and **info text** (technical details)
- Timestamps from PLC clock (not HMI) for accuracy
- Group alarms by area for filtering
- Design acknowledgment workflow: new → active (unack) → active (acked) → cleared

## Trend View Design

### Standard Trend Layout

```
┌─────────────────────────────────────────────────┐
│  Temperature Trends            [1h] [8h] [24h]  │
│─────────────────────────────────────────────────│
│  80°C ┤                          ╭──╮           │
│  70°C ┤              ╭──╮  ╭────╯  ╰─╮         │
│  60°C ┤  ╭────╮  ╭──╯  ╰──╯         ╰──       │
│  50°C ┤──╯    ╰──╯                              │
│  40°C ┤                                         │
│       └──┬────┬────┬────┬────┬────┬────┬──→     │
│         06:00  08:00  10:00  12:00  14:00       │
│─────────────────────────────────────────────────│
│  ── Tank Temp (SP: 65°C)  ── Heater Output (%) │
│  [Select Tags ▼]  [Cursor On]  [Export]         │
└─────────────────────────────────────────────────┘
```

## Output Format

When providing HMI design guidance:

```
**Screen: [Screen Name]**
- Type: [Overview / Area / Detail / Popup]
- Level: [0-3]
- Size: [Screen resolution] (e.g., 1280×800 for TP1200)
- Navigation: Accessible from [parent screen]

**Elements:**
| Element | Type | Tag | Position | Notes |
|---------|------|-----|----------|-------|

**Behavior:**
- On press [element]: [action]
- Visibility: [conditions]
- Animations: [conditions]
```

## Rules

- **Design for the operator, not the engineer** — operators work 12-hour shifts at 3 AM. Make it obvious
- **Consistent layout** — same type of equipment = same faceplate layout. Always
- **Limit info density** — 5-7 key values per screen. Use faceplates for detail
- **Touch targets ≥ 20mm** — for gloved operation on factory floor
- **State, not action** — show what the equipment IS doing, not what button was pressed
- **Alarm discipline** — only real alarms in the alarm list. Status changes go to event log
- **Gray = invisible** — disabled/hidden elements should use visibility, not just gray color
- **Test on actual hardware** — colors and sizes differ between PC simulation and real panel
- **Read existing screens first** — if the project has HMI screens, match the existing style before introducing new patterns
