#!/usr/bin/env python3
"""Import an existing roster CSV into studio's SQLite DB as a named roster.

Usage:
    python -m studio.seed --csv rosters/sample_roster.csv --title "Sample"
    python -m studio.seed --csv rosters/sample_roster.csv --title "Sample" --token <existing-workspace-token>
"""

from __future__ import annotations

import argparse
import secrets

import solver
from studio import db


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a studio roster from a CSV file")
    ap.add_argument("--csv", required=True, help="Path to a roster CSV")
    ap.add_argument("--title", required=True, help="Title for the new roster")
    ap.add_argument("--token", help="Workspace token to seed into (default: create a new workspace)")
    args = ap.parse_args()

    db.init_db()

    if args.token:
        workspace = db.get_workspace_by_token(args.token)
        if workspace is None:
            raise SystemExit(f"No workspace found for token {args.token!r}.")
        token, workspace_id = args.token, workspace["id"]
    else:
        token = secrets.token_urlsafe(16)
        workspace_id = db.create_workspace(token)

    players = solver.read_roster(args.csv)
    roster_id = db.save_as_new_roster(workspace_id, args.title, db.player_records_from_players(players))

    print(f"Created roster {roster_id} ({args.title}) with {len(players)} players.")
    if not args.token:
        print(f"New workspace token: {token} (visit /w/{token}/rosters)")


if __name__ == "__main__":
    main()
