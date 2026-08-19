---
name: roster-import
description: Convert a roster screenshot or pasted table (player #, name, position code, experience rank 1-5) into this project's roster CSV format under rosters/. Use when the user shares a roster image/table and asks to create, import, or update a roster CSV.
---

Convert a source roster (screenshot or pasted table) into the CSV format this project's solver expects.

## Output format

```
id,name,available,experience,preferred_positions,secondary_positions
P01,Ali,1,5,C;LW;RW;LD;RD,
P02,Batz,1,1,LW,RW
```

Columns:
- `id` — arbitrary but unique, `P01`, `P02`, ... in source order.
- `name` — the player's **first (given) name only**, even if the source lists a full name or a suffix like `(C)` for captain.
- `available` — `1` for everyone unless the user says otherwise.
- `experience` — the 1-5 rank value from the source.
- `preferred_positions` — see mapping below.
- `secondary_positions` — see mapping below.

## Position mapping

The canonical position set, in this fixed order, is: `C, LW, RW, LD, RD`.

Source rosters list a position code per player (e.g. `D`, `U`, `C`, `W`, `W/D`, `C/W`). Map each token in the code to a set of canonical positions, then union them (preserving canonical order) for combined codes like `W/D`:

- `D` → `LD;RD`
- `W` → `LW;RW`
- `C` → `C`
- `U` (utility) → `LW;RW;LD;RD;C` if experience ≥ 2, else `LW;RW` if experience == 1
- Combined codes (`W/D`, `C/W`, etc.) → union of each part's mapping, e.g. `W/D` → `LW;RW;LD;RD`, `C/W` → `C;LW;RW`

`preferred_positions` is that mapped/unioned set, in canonical order.

`secondary_positions` is the **set difference**: canonical set minus `preferred_positions`, in canonical order — **except** it is always empty (blank) when `experience == 1`.

## Workflow

1. Read the source data (screenshot or pasted table) — player #, full name, position code, experience rank. Ignore any "value" or computed-score column; it's not part of the CSV.
2. Derive each row per the mapping above, in source order, assigning sequential `PXX` ids.
3. Confirm the destination filename with the user before writing — check `rosters/` for naming conventions already in use (e.g. `roster_<team>_season<N>_<label>.csv`) and don't silently overwrite an existing file without confirming that's intended.
4. Write the CSV to `rosters/`.
