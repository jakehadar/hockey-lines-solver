# Hockey Lines Solver

A constraint solver for building hockey forward lines and defense pairs — built around the puzzle that actually comes up: *if I try player A and B on positions X and Y, given that C and D just dropped and alt E is filling in, where does everyone else need to move so the lines stay balanced and nobody ends up on a position they can't play?*

That's the real use case: players dropping last second, someone wanting to try a new position, a captain wanting to test two players on the same line, a night where only two people can play center, half the defense being out and needing to know which forwards can slide back. Instead of re-deriving the whole lineup by hand every time something changes, you feed in the change and get a feasible starting point to build from.

It's not meant to replace a captain's judgment, discussion, or feel for the team — it's here to assist and sanity-check, not decide. It gets you to that starting point around an unexpected event or an idea you want to validate, and you take it from there.

Because it's reasoning over constraints rather than judging talent, it also scales past a single team: if every captain in a division uses it, they can simulate trades against their own roster and see how it would affect balance and how much flexibility they'd have left, before making one for real.

Files added:
- `solver.py` - the main solver script (uses OR-Tools CP-SAT)
- `rosters/sample_roster.csv` - example roster CSV
- `requirements.txt` - Python dependency list

CSV format (header): `id,name,available,experience,preferred_positions,secondary_positions`
	- `available`: 1 or 0
	- `experience`: integer 1..5 (1 = beginner, 5 = advanced)
	- `preferred_positions`: semicolon-separated positions like `LW;C` (positions: `LW`,`C`,`RW`,`LD`,`RD`)
	- `secondary_positions`: semicolon-separated positions a player will play if needed (lower priority)
- `available`: 1 or 0
- `experience`: integer 1..5
- `preferred_positions`: semicolon-separated positions like `LW;C` (positions: `LW`,`C`,`RW`,`LD`,`RD`)

Optional columns (omit entirely for a roster CSV that predates these — fully backwards compatible):
- `unwilling_positions`: semicolon-separated positions this player must never be assigned to (hard constraint).
- `optional_position_override`: a single position; if set, it's the *only* position this player may be assigned to, overriding preferred/secondary/unwilling. Meant for quick what-if tweaks without editing a player's actual preferences.
- `optional_player_link`: another player's id; forces both players onto the same forward line or defense pair together (or both benched together). Works for forwards and defense.

These optional levers do no validation — an unsatisfiable combination (e.g. two players both overridden to the same position with only one such slot, or conflicting links) will simply make the solve infeasible (`NO_SOLUTION`) rather than raising an error.

Quick start (macOS, using provided venv at `./venv`):

```bash
source ./venv/bin/activate
pip install -r requirements.txt
python solver.py --forwards 3 --defense 3 --roster rosters/sample_roster.csv
```

Options:
- `--forwards` number of forward lines (default 3)
- `--defense` number of defensive pairs (default 3)
- `--allow-oop` allow assignments outside player's preferred positions (default off)
- `--time-limit` solver time limit in seconds (default 20)

Notes:
- The objective maximizes number of assigned players first, then preference satisfaction, then minimizes forward-line experience imbalance (L1 norm).
- Output now annotates assignments as `primary` (player's primary position), `secondary` (player's secondary position), or `OOP` (out-of-position when `--allow-oop` is used).

## REST API

`api.py` wraps the solver in a FastAPI service, so a separate frontend/client
can call it over HTTP instead of shelling out to the CLI. `solver.py`'s core
logic (`solve_lines`) is unchanged by this — the API and the CLI both call
the same function.

Run the dev server:

```bash
source ./venv/bin/activate
pip install -r requirements.txt
uvicorn api:app --reload
```

Interactive, auto-generated docs (try requests right in the browser):
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

### Endpoints

- `GET /health` — liveness check.
- `POST /solve` — JSON roster in, JSON (or CSV) result out.
- `POST /solve/csv` — CSV file upload in, JSON (or CSV) result out.

Both `/solve` endpoints accept `?format=json` (default) or `?format=csv` to
choose the response shape. `format=csv` returns a flat table of every
player-slot assignment (`section,line_number,slot,position,player_id,
player_name,experience,status`); `format=json` returns the full structured
result (forward lines, defense pairs, and summary counts) — see the
`SolveResponse` schema in `/docs` for exact field descriptions.

### Examples

JSON roster in, JSON out:

```bash
curl -X POST "http://127.0.0.1:8000/solve" \
  -H "Content-Type: application/json" \
  -d '{
    "players": [
      {"id": "p1", "name": "Alice", "available": 1, "experience": 3, "preferred_positions": ["LW"]},
      {"id": "p2", "name": "Bob", "available": 1, "experience": 2, "preferred_positions": ["C"]},
      {"id": "p3", "name": "Cy", "available": 1, "experience": 1, "preferred_positions": ["RW"]}
    ],
    "forwards": 1,
    "defense": 0
  }'
```

JSON roster in, CSV out:

```bash
curl -X POST "http://127.0.0.1:8000/solve?format=csv" \
  -H "Content-Type: application/json" \
  -d '{"players": [...], "forwards": 3, "defense": 3}'
```

CSV file upload in, JSON out:

```bash
curl -X POST "http://127.0.0.1:8000/solve/csv" \
  -F "file=@rosters/sample_roster.csv" \
  -F "forwards=3" \
  -F "defense=3"
```

CSV file upload in, CSV out:

```bash
curl -X POST "http://127.0.0.1:8000/solve/csv?format=csv" \
  -F "file=@rosters/sample_roster.csv" \
  -F "forwards=3" \
  -F "defense=3"
```

Note: JSON roster payloads take `preferred_positions`/`secondary_positions`
as actual arrays (e.g. `["LW", "C"]`), unlike the CSV format's
semicolon-separated strings.

### Tests

```bash
source ./venv/bin/activate
pytest tests/ -v
```

`tests/test_api.py` exercises all four request/response combinations above
via FastAPI's `TestClient` (no server process needed), and cross-checks the
CSV-upload path against calling `solver.solve_lines()` directly.

## Deploy to FastAPI Cloud

The project ships a `pyproject.toml` (dependencies + `[tool.fastapi] entrypoint
= "api:app"`) and a `.fastapicloudignore` (excludes `tests/` and local scratch
roster CSVs from the uploaded archive), so it deploys as-is from the repo root:

```bash
pip install fastapi-cloud-cli
fastapi cloud login
fastapi cloud deploy
```

The first `deploy` will prompt you to create/link an app; subsequent runs
from the same directory reuse that link (stored in `.fastapicloud/cloud.json`,
which is git-ignored automatically).

Note: `api.py` currently sets `allow_origins=["*"]` for CORS, which is a
dev-friendly default — restrict it to your actual frontend origin(s) before
relying on this in production.

## Studio: interactive scenario planning

`studio/` is a Flask app for building and persisting rosters in SQLite and
running interactive what-if scenarios against `solver.py` — a UI on top of
the same solver core the CLI and `api.py` use (imported in-process, not over
HTTP). It's an experimental first iteration; team/season/game-day concepts
are intentionally out of scope.

Run it:

```bash
source ./venv/bin/activate
pip install -r requirements.txt
python -m studio.app          # http://127.0.0.1:5000, SQLite at studio/instance/studio.db
```

No signup: visiting `/` mints an anonymous **workspace** — a token in a
long-lived cookie and in every URL under `/w/<token>/...` — and every roster
and scenario is scoped to it, so one workspace can never see or touch
another's. The token in the URL *is* the credential; anyone with the link
has full read/write access to that workspace, same as a "anyone with the
link can edit" share. The workspace-badge dropdown (top right) explains
this and offers "Copy workspace link" to carry it to another device or hand
it to someone else. New workspaces are pre-seeded with
`rosters/sample_roster.csv` so there's something to explore immediately.

Optionally seed an additional roster from a CSV yourself:

```bash
python -m studio.seed --csv rosters/sample_roster.csv --title "Sample Roster"
# --token <existing-workspace-token> to seed into a workspace you already have,
# instead of minting a new one
```

What it does:
- **Rosters list** (`/w/<token>/rosters`) — create a blank roster (title only), or drag-and-drop/browse a CSV to import one directly (only `name` is required; every other column is optional and can be filled in by hand afterward — see the in-page template link for the exact columns).
- **Studio page** (`/w/<token>/studio/<roster_id>`) has two modes, toggled top-right, and remembers which one you were last in per roster (new rosters always start in Roster mode):
  - **Roster mode** — build the roster itself: add/remove players and alts, edit name/experience/preferred/secondary/**unwilling** positions. No solver here (nothing auto-runs while you're just entering data), and no scenario-only levers (availability, position override, player link) — a roster has no concept of those.
  - **Scenario mode** — the what-if workspace. The solver runs here (auto, debounced, after every edit; or manually via "Solve now"), and the scenario-only levers reappear. Players that came from the roster keep their name locked and can't be deleted — toggling **available** off is how you exclude one, with the same effect as deleting them; every scenario starts with everyone available unless you deselect some, and a branch inherits its parent's deselections. Players added while already in Scenario mode are fully yours: editable, deletable, and they travel with the scenario if you save it, never touching the roster itself.
  - Switching modes with unsaved changes pending prompts you to save, discard, or stay — never silently.
- **Save** (the split button) is context-sensitive:
  - In Roster mode: **Save roster** (overwrites the saved roster; confirms if there are unsaved changes) and **Save roster as…** (forks into a new, independent roster).
  - In Scenario mode: **Save scenario** — creates a new scenario (prompting for a title, sequentially suggested as "Scenario 1", "Scenario 2", …, plus an optional description) if nothing's loaded yet, or silently overwrites the currently loaded scenario in place if one is. **Branch scenario…** always creates a new scenario, linked via a parent reference to whichever scenario was loaded (or none, if you started fresh) — so a scenario tree can grow over repeated what-ifs.
- **Scenarios** (`/w/<token>/studio/<roster_id>/scenarios`) — every scenario saved for a roster, its branch parent if any. Select several and **Compare** to see their cached results side by side (`/compare`) — no re-solving, since each scenario already carries the result it produced when saved. **Load…** (from the list or the compare view) reopens the editor in Scenario mode with that scenario's players/settings restored, so you can branch from a past what-if instead of rebuilding it from memory; Reset unloads it and returns to the roster's own saved state.
- **Alts** — "+ Add alt" creates a player flagged as not part of the core roster (`A##` id vs. the regular `P##`), pre-filled with all positions as preferred and experience 3 as a starting point. Alt-vs-full-time is fixed at creation; to change it, delete the row and add the other kind.
- Deleting a roster deletes its scenarios too (warns first if there are any).

Tests: `pytest tests/test_studio.py -v` (uses Flask's test client with an isolated temp SQLite DB per test).
