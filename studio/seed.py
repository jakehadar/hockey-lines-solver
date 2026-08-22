#!/usr/bin/env python3
"""Import an existing roster CSV into studio's database as a named roster.

Usage (from studio/):
    python seed.py --csv ../rosters/sample_roster.csv --title "Sample"
    python seed.py --csv ../rosters/sample_roster.csv --title "Sample" --token <existing-workspace-token>
"""

from __future__ import annotations

import argparse
import csv
import secrets

import db


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a studio roster from a CSV file")
    ap.add_argument("--csv", required=True, help="Path to a roster CSV")
    ap.add_argument("--title", required=True, help="Title for the new roster")
    ap.add_argument("--token", help="Workspace token to seed into (default: create a new workspace)")
    args = ap.parse_args()

    db.apply_schema()

    if args.token:
        workspace = db.get_workspace_by_token(args.token)
        if workspace is None:
            raise SystemExit(f"No workspace found for token {args.token!r}.")
        token, workspace_id = args.token, workspace["id"]
    else:
        token = secrets.token_urlsafe(16)
        workspace_id = db.create_workspace(token)

    with open(args.csv, newline="", encoding="utf-8") as f:
        records = db.player_records_from_csv_rows(csv.DictReader(f))
    roster_id = db.save_as_new_roster(workspace_id, args.title, records)

    print(f"Created roster {roster_id} ({args.title}) with {len(records)} players.")
    if not args.token:
        print(f"New workspace token: {token} (visit /w/{token}/rosters)")


if __name__ == "__main__":
    main()
