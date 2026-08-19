#!/usr/bin/env python3
"""Import an existing roster CSV into studio's SQLite DB as a named roster.

Usage:
    python -m studio.seed --csv rosters/sample_roster.csv --title "Sample"
"""

from __future__ import annotations

import argparse

import solver
from studio import db


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a studio roster from a CSV file")
    ap.add_argument("--csv", required=True, help="Path to a roster CSV")
    ap.add_argument("--title", required=True, help="Title for the new roster")
    args = ap.parse_args()

    db.init_db()
    players = solver.read_roster(args.csv)
    records: list[db.PlayerRecord] = [
        {
            "player_key": p.id,
            "name": p.name,
            "available": p.available,
            "experience": p.experience,
            "preferred_positions": p.prefs,
            "secondary_positions": p.secondary,
            "unwilling_positions": p.unwilling,
        }
        for p in players
    ]
    roster_id = db.save_as_new_roster(args.title, records)
    print(f"Created roster {roster_id} ({args.title}) with {len(records)} players.")


if __name__ == "__main__":
    main()
