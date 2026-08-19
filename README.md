# Hockey Lines Solver

Simple CP-SAT based solver to assign players to hockey forward lines and defensive pairs.

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
