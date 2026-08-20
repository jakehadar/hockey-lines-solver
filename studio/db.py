"""Thin sqlite3 repository for studio's persisted rosters/players.

Only the "truth" fields round-trip here: player_key, name, available,
experience, preferred_positions, secondary_positions, unwilling_positions.
optional_position_override/optional_player_link are scenario-only levers
that never reach this layer (studio/app.py strips them before calling in).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, TypedDict

INSTANCE_DIR = Path(__file__).parent / "instance"
DB_PATH = INSTANCE_DIR / "rosters.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class PlayerRecord(TypedDict):
    player_key: str
    name: str
    available: int
    experience: int
    preferred_positions: List[str]
    secondary_positions: List[str]
    unwilling_positions: List[str]


def _join(positions: Iterable[str]) -> str:
    return ";".join(positions)


def _split(positions: str) -> List[str]:
    return [p for p in positions.split(";") if p]


def _resolve(db_path: Path | None) -> Path:
    # NB: default args bind at def-time, so callers that want to point at a
    # different DB_PATH (tests) must pass db_path=None (the default) *after*
    # monkeypatching the module-level DB_PATH, not rely on a bound default.
    return db_path if db_path is not None else DB_PATH


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    resolved = _resolve(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())


def create_workspace(token: str, db_path: Path | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute("INSERT INTO workspaces (token) VALUES (?)", (token,))
        return cur.lastrowid


def get_workspace_by_token(token: str, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT id, token, created_at FROM workspaces WHERE token = ?", (token,)).fetchone()


def list_rosters(workspace_id: int, db_path: Path | None = None) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, created_at FROM rosters WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()


def get_roster(roster_id: int, workspace_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, created_at FROM rosters WHERE id = ? AND workspace_id = ?",
            (roster_id, workspace_id),
        ).fetchone()


def delete_roster(roster_id: int, workspace_id: int, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM rosters WHERE id = ? AND workspace_id = ?", (roster_id, workspace_id))


def create_roster(workspace_id: int, title: str, db_path: Path | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute("INSERT INTO rosters (workspace_id, title) VALUES (?, ?)", (workspace_id, title))
        return cur.lastrowid


def list_players(roster_id: int, db_path: Path | None = None) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT player_key, name, available, experience, preferred_positions, "
            "secondary_positions, unwilling_positions FROM players WHERE roster_id = ? "
            "ORDER BY player_key",
            (roster_id,),
        ).fetchall()


def _insert_players(conn: sqlite3.Connection, roster_id: int, players: List[PlayerRecord]) -> None:
    conn.executemany(
        "INSERT INTO players (roster_id, player_key, name, available, experience, "
        "preferred_positions, secondary_positions, unwilling_positions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                roster_id,
                p["player_key"],
                p["name"],
                p["available"],
                p["experience"],
                _join(p["preferred_positions"]),
                _join(p["secondary_positions"]),
                _join(p["unwilling_positions"]),
            )
            for p in players
        ],
    )


def replace_players(roster_id: int, players: List[PlayerRecord], db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM players WHERE roster_id = ?", (roster_id,))
        _insert_players(conn, roster_id, players)


def save_as_new_roster(
    workspace_id: int, title: str, players: List[PlayerRecord], db_path: Path | None = None
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute("INSERT INTO rosters (workspace_id, title) VALUES (?, ?)", (workspace_id, title))
        roster_id = cur.lastrowid
        _insert_players(conn, roster_id, players)
        return roster_id
