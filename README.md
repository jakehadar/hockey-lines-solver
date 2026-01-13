# Hockey Lines Solver

Simple CP-SAT based solver to assign players to hockey forward lines and defensive pairs.

Files added:
- `solver.py` - the main solver script (uses OR-Tools CP-SAT)
- `roster_sample.csv` - example roster CSV
- `requirements.txt` - Python dependency list

CSV format (header): `id,name,available,experience,preferred_positions,secondary_positions`
	- `available`: 1 or 0
	- `experience`: integer 1..5 (1 = beginner, 5 = advanced)
	- `preferred_positions`: semicolon-separated positions like `LW;C` (positions: `LW`,`C`,`RW`,`LD`,`RD`)
	- `secondary_positions`: semicolon-separated positions a player will play if needed (lower priority)
- `available`: 1 or 0
- `experience`: integer 1..3
- `preferred_positions`: semicolon-separated positions like `LW;C` (positions: `LW`,`C`,`RW`,`LD`,`RD`)

Quick start (macOS, using provided venv at `./venv`):

```bash
source ./venv/bin/activate
pip install -r requirements.txt
python solver.py --roster roster_sample.csv --forwards 3 --defense 3
```

Options:
- `--forwards` number of forward lines (default 3)
- `--defense` number of defensive pairs (default 3)
- `--allow-oop` allow assignments outside player's preferred positions (default off)
- `--time-limit` solver time limit in seconds (default 20)

Notes:
- The objective maximizes number of assigned players first, then preference satisfaction, then minimizes forward-line experience imbalance (L1 norm).
- Output now annotates assignments as `primary` (player's primary position), `secondary` (player's secondary position), or `OOP` (out-of-position when `--allow-oop` is used).
