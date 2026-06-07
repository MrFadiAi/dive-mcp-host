---
name: batch-operations
description: >
  Use when the user wants to apply a change across multiple blocks, rename tags across PLCs,
  or perform any operation on more than one block. Provides a safe, sequential workflow with
  progress reporting and preview-before-apply semantics.
---

# Batch Operations — Safe Multi-Block Changes

## Overview

This skill provides a structured workflow for applying changes across multiple blocks, tags, or PLCs. It ensures every change is previewed, confirmed, and tracked with progress reporting.

## When to Use

- "Add a safety check to every motor FC"
- "Rename tag Motor_Start to Motor_Run across all PLCs"
- "Add a header comment to all blocks in PLC_1"
- "Update the alarm timeout value in all alarm handler blocks"
- "Apply the same logic change to PLF_01A through PLF_09D"

## Workflow

### Phase 1: Enumerate

1. Call `browse_project_tree` to find all blocks that match the user's criteria
2. Identify the target scope:
   - Single PLC, multiple PLCs, or all PLCs
   - Specific folder or all folders
   - By block type (FC only, FB only, etc.)
   - By name pattern (blocks containing "Motor", "Valve", etc.)
3. **List the matching blocks** for the user to confirm scope:
   ```
   Found 12 matching blocks in 3 PLCs:
   - PLC_1/Program blocks/MotorFC101 (FC101)
   - PLC_1/Program blocks/MotorFC102 (FC102)
   - PLC_2/Program blocks/MotorControl (FC50)
   ...
   ```

### Phase 2: Read

1. Call `get_block_content` for each target block
2. Analyze the current code to determine what needs to change
3. If the change depends on block structure (e.g., adding to a specific network), identify the right location in each block

### Phase 3: Preview

**CRITICAL: Always show the user what will change BEFORE applying anything.**

For each block, show a diff-style preview:
```
📄 PLC_1/Program blocks/MotorFC101 (FC101)
   Network 3: Motor start/stop logic
   ─── Current ───
   // Self-holding circuit
   #Start AND NOT #Stop OR #Running;
   = #Running;

   +++ Proposed +++

   // Self-holding circuit with safety
   #Start AND NOT #Stop AND #SafetyOK OR #Running;
   = #Running;
```

If there are many blocks (>5), show the first 2-3 as detailed examples and summarize the rest:
```
📄 Showing preview for first 3 of 12 blocks.
   The remaining 9 blocks will receive identical changes.
   Confirm to proceed with all 12.
```

### Phase 4: Confirm

Ask the user explicitly:
> "Preview above shows changes to 12 blocks. Apply to all? (Yes / No / Let me review each one)"

Wait for user response before proceeding.

### Phase 5: Apply Sequentially

Apply changes one block at a time:

1. Call the appropriate write tool (`update_block_logic`, `preview_update_block_logic` first, then confirm)
2. After each block, report progress:
   ```
   [1/12] ✅ PLC_1/MotorFC101 — safety check added
   [2/12] ✅ PLC_1/MotorFC102 — safety check added
   [3/12] ⚠️ PLC_2/MotorControl — block structure differs, skipped (see note)
   [4/12] ✅ PLC_3/MotorFC200 — safety check added
   ```

3. If a block **fails**:
   - Log the error with details
   - **STOP and ask the user** whether to continue or abort
   - Do NOT silently skip failures

### Phase 6: Summarize

After all blocks are processed, output a summary table:

```
## Batch Operation Complete

| # | Block | PLC | Status | Note |
|---|-------|-----|--------|------|
| 1 | MotorFC101 | PLC_1 | ✅ Applied | — |
| 2 | MotorFC102 | PLC_1 | ✅ Applied | — |
| 3 | MotorControl | PLC_2 | ⚠️ Skipped | Different structure |
| ... | ... | ... | ... | ... |

**Result**: 11/12 blocks updated successfully, 1 skipped
```

Optionally run `compile_check` on affected PLCs to verify no compilation errors.

## Safety Rules

1. **ALWAYS preview first** — never apply changes without showing the user what will change
2. **ALWAYS ask for confirmation** — do not auto-apply batch operations
3. **Stop on failure** — if a block fails, halt and ask the user before continuing
4. **Use safety tokens** — follow the MCP write tool protocol (preview → get safetyToken → confirm)
5. **Destructive operations** — for delete, overwrite, or rename operations, require explicit per-block confirmation even in batch mode
6. **Preserve existing code** — always read the current block content before modifying; never overwrite blindly
7. **Compile check after batch** — run `compile_check` after all changes to verify correctness

## Common Patterns

### Pattern: Add code to multiple blocks

1. Enumerate target blocks
2. Read each block's current content
3. Determine insertion point (e.g., "after network 3", "before the last network")
4. Preview the addition for each block
5. Apply sequentially

### Pattern: Rename a tag across PLCs

1. Call `read_cross_references` to find all blocks that reference the tag
2. Read each block's content
3. Replace all occurrences of the old tag name with the new name
4. Preview the rename in each block
5. Apply sequentially
6. Verify with `compile_check`

### Pattern: Update a constant/parameter value

1. Find all blocks that reference the constant
2. Read each block's content
3. Replace the old value with the new value
4. Preview each change
5. Apply sequentially

### Pattern: Apply same logic to production line variants

1. Use `browse_project_tree` to find the same block across PLF_01A through PLF_09D
2. Read the "template" block (e.g., from PLF_01A)
3. Read each target block to confirm they have compatible structure
4. Preview the change showing the template vs each target
5. Apply to all matching blocks

## Error Handling

- **Block not found**: Report and skip, ask if user wants to continue
- **Different block structure**: Warn the user, show the difference, ask how to proceed
- **Compile error after change**: Revert the change if possible, report the error
- **Permission denied**: Report the error and stop — do not retry without user confirmation
- **Partial completion**: If some blocks succeed and others fail, report exactly which ones succeeded so the user knows the current state
