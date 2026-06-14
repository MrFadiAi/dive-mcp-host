---
name: navigation
description: >
  Use for ANY request to explore, locate, or find things in a TIA Portal project:
  "what PLCs are there", "find the block that does X", "where is this tag/address used",
  "where is the stop logic", "show me the motor control", "which PLC has...",
  "what's in this project". Defines the efficient discovery workflow so you never
  dump the full project tree repeatedly. Load this FIRST for exploratory questions.
---

# Navigation — Efficient TIA Portal Project Discovery

## Why this skill

A TIA project can have 50+ devices and hundreds of blocks. Calling `browse_project_tree`
returns a massive JSON that **truncates** and hides the PLC you want. This skill gives the
**cheap, targeted** tool order so you find things in 1–3 calls, not 8.

## The Golden Workflow — follow this order

### Step 1 — `list_plcs` (ALWAYS first)

Returns every PLC with its **device name**, **PLC-software name**, and block/tag/type counts.
These two names often **differ** (e.g. device `PLF_01A_PLC_CARROUSEL_2` hosts software
`PLF_03A_PLC_CARROUSEL`). Both are accepted by every `plcName` filter — but you must know
them first. Do not skip this.

### Step 2 — `list_blocks` (locate the block)

Cheap index — name, type (FC/FB/OB/DB), number, language, path — **no code**. Pass the
`plcName` from step 1. Far smaller than `browse_project_tree`. Use this to find the block
you need, or to see what exists in a PLC.

### Step 3 — `search_code` / `tag_usage` (locate the LOGIC)

When the user asks **"where is X"**:

| Question | Tool |
|---|---|
| "Where is this tag **read / written**? (exact, authoritative)" | `tag_xref(tag="...")` — compiled XRef, pierces protection, exact Read/Write |
| "Where is this tag used? (fast, no compile)" | `tag_usage(tag="...")` |
| "Where is this address (`%Q1515.0`) written?" | `tag_xref` (authoritative) or `tag_usage`/`search_code(query="%Q1515.0")` |
| "Who calls FB100 / what does FB100 call?" | `call_graph(block="FB100")` — compiled callers + callees |
| "Find the stop/start/motor logic" | `search_code(query="STOP")`, `search_code("MOTOR")` |
| "Where is this constant/keyword?" | `search_code(query="...")` |

> **`tag_xref` vs `tag_usage`:** `tag_xref` reads the **compiled** cross-reference → exact Read/Write
> access (matches TIA's cross-reference editor) and **pierces know-how protection**, but needs the
> project compiled. `tag_usage` greps exported source → fast, no compile, but access is a heuristic.
> For "which blocks read/write this tag", prefer **`tag_xref`**; use `tag_usage` for a quick first pass
> or when the project can't compile. `call_graph` is the authoritative equivalent for call structure.

These scan **all block source in one call** and return block + line + context. They work
**without compiling** the project. This is the single biggest time saver.

> ⚠️ **Know-how-protected blocks & read/write.** `tag_usage` / `search_code` export each
> block via a fallback chain (`ExportAsDocuments` → `Export(FileInfo)`), so they read MOST
> protected blocks — only truly-unreadable ones are skipped (counted in `skippedProtectedCount`).
> `tag_usage` also classifies STL **read vs write** from the instruction token. For the
> *authoritative* read/write access (matching TIA's cross-reference editor), or if
> `skippedProtectedCount > 0` and refs are suspiciously few, escalate to `read_cross_references`
> — it reads TIA's *compiled* cross-reference, which pierces all protection and returns exact
> access. Do **not** call a tag "unused" while `skippedProtectedCount > 0` unless
> `read_cross_references` was tried.

### Step 4 — `get_block_content` (read the actual code)

Once you've located the exact block, read its full source. Use the `Path` from `list_blocks`
(e.g. `PLC/Blocks/FC_MOTOR`). **STL blocks are returned as readable source code** (reconstructed
from Openness XML — e.g. `      =     "AFPAKKER_INSTALLATIE_DRAAIT"`) — quote it directly; you do
NOT need to interpret `<StlToken>`/`<Component>` XML. SCL/FBD/DB blocks may still appear as XML.
If a block is know-how-protected, you'll get the interface only — tell the user it must be unlocked.

### Step 5 — `browse_project_tree` (ONLY when you need full structure)

Use only when you genuinely need the **nested** structure: sub-folders, Software Units,
type groups, tag-table folders. It's large and truncates on big projects — pass `plcName`
to scope it to one PLC's tree.

## Quick reference — don't use these the wrong way

| Tool | Use for | DON'T use for |
|---|---|---|
| `list_plcs` | Learning PLC names + counts | — |
| `list_blocks` | Block index (name/type/path) | Reading code |
| `find_tags` | Search tags by name | Browsing whole tag tables |
| `search_code` | Grep block source for a pattern | Reading one full block |
| `tag_usage` | All references to one tag | Browsing |
| `get_block_content` | Read one block's full source | Discovering what exists |
| `browse_project_tree` | Full nested structure | Quick lookups (too big) |

## Rules

- **Start with `list_plcs`. Always.** Never assume a PLC name.
- **Never call `browse_project_tree` without a `plcName` filter** on a large project — it truncates.
- **"Where is X" → `search_code` or `tag_usage`**, not block-by-block reading.
- **`tag_usage` searches by name AND address.** If it returns 0 references for a tag that clearly exists, the tag is referenced by its absolute address (STL) or inside a protected block — escalate (next rule), don't claim it's "unused".
- **Always report `skippedProtectedCount`.** If it's > 0, the tag/logic may live in protected blocks that source search cannot read.
- **ESCALATE to `read_cross_references` when `skippedProtectedCount > 0` AND `tag_usage`/`search_code` found 0 (or suspiciously few) references, OR when you need authoritative read/write access.** `tag_usage` reads most protected blocks via its export fallback and classifies STL read/write, but `read_cross_references` (TIA's **compiled** cross-reference via `CrossReferenceService`) pierces ALL protection and is the authoritative source for exact read/write access. It auto-compiles if needed. Never conclude a tag is "unused" while `skippedProtectedCount > 0` unless `read_cross_references` was tried and returned nothing for it.
- **To READ know-how-protected source** (not just cross-references), call `knowhow_unlock`. Ask the user ONCE for the project's know-how password (in chat), pass it to `knowhow_unlock(password=...)` — it is cached per-project on disk and **never asked again**. After unlock, formerly-protected blocks are fully readable and future protected blocks auto-unlock silently. This is a permanent change (reversible by re-protecting in TIA Portal).
- **`get_block_content` is for ONE block you've already located** — don't blindly read 10 blocks to find one thing. If a block is know-how-protected, use `knowhow_unlock` to unlock it; after that its full source is readable.
- Pass `plcName` (from step 1) to scope every call — faster, avoids truncation.

## Example

**User**: "Where is the plukschijf stop logic?"

1. `list_plcs` → device `PLF_01A_PLC_CARROUSEL_2`, software `PLF_03A_PLC_CARROUSEL`
2. `search_code(query="PLUKSCHIJF", plcName="PLF_01A_PLC_CARROUSEL_2")` → matches in `FC_MOTOR_PLUKSCHIJF`, `Main_1`
3. `search_code(query="STOP", plcName="PLF_01A_PLC_CARROUSEL_2")` → the stop lines + context
4. `get_block_content` on the located block to show the full logic

Four targeted calls instead of dumping the tree 8 times.
