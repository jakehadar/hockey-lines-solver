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


def create_workspace(token: str, client_ip: str | None = None, db_path: Path | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute("INSERT INTO workspaces (token, client_ip) VALUES (?, ?)", (token, client_ip))
        return cur.lastrowid


def get_workspace_by_token(token: str, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT id, token, created_at FROM workspaces WHERE token = ?", (token,)).fetchone()


def count_recent_workspaces_from_ip(client_ip: str, window_seconds: int, db_path: Path | None = None) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM workspaces WHERE client_ip = ? AND created_at >= datetime('now', ?)",
            (client_ip, f"-{window_seconds} seconds"),
        ).fetchone()
        return row["n"]


def list_rosters(workspace_id: int, db_path: Path | None = None) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT r.id, r.title, r.created_at, "
            "(SELECT COUNT(*) FROM scenarios s WHERE s.roster_id = r.id AND s.is_baseline = 0) AS scenario_count "
            "FROM rosters r WHERE r.workspace_id = ? ORDER BY r.created_at DESC",
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


def player_records_from_players(players: Iterable) -> List[PlayerRecord]:
    """Convert solver.Player objects (e.g. from solver.read_roster) to PlayerRecords."""
    return [
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


def save_as_new_roster(
    workspace_id: int, title: str, players: List[PlayerRecord], db_path: Path | None = None
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute("INSERT INTO rosters (workspace_id, title) VALUES (?, ?)", (workspace_id, title))
        roster_id = cur.lastrowid
        _insert_players(conn, roster_id, players)
        return roster_id


# --- Scenarios --------------------------------------------------------
#
# A scenario is an immutable snapshot (players + solve settings + the
# result they produced) - players_json/result_json are opaque JSON text as
# far as this module is concerned; studio/app.py owns their shape.


def upsert_baseline_scenario(
    roster_id: int,
    forwards: int,
    defense: int,
    time_limit: int,
    players_json: str,
    result_json: str,
    db_path: Path | None = None,
) -> int:
    """Keep the roster's one is_baseline=1 scenario in sync with what "Save
    to roster" just persisted, creating it on first save."""
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM scenarios WHERE roster_id = ? AND is_baseline = 1", (roster_id,)
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE scenarios SET forwards = ?, defense = ?, time_limit = ?, "
                "players_json = ?, result_json = ?, created_at = datetime('now') WHERE id = ?",
                (forwards, defense, time_limit, players_json, result_json, existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO scenarios (roster_id, title, is_baseline, forwards, defense, time_limit, "
            "players_json, result_json) VALUES (?, 'Baseline', 1, ?, ?, ?, ?, ?)",
            (roster_id, forwards, defense, time_limit, players_json, result_json),
        )
        return cur.lastrowid


def create_scenario(
    roster_id: int,
    title: str,
    description: str,
    forwards: int,
    defense: int,
    time_limit: int,
    players_json: str,
    result_json: str,
    db_path: Path | None = None,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scenarios (roster_id, title, description, forwards, defense, time_limit, "
            "players_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (roster_id, title, description, forwards, defense, time_limit, players_json, result_json),
        )
        return cur.lastrowid


def list_scenarios(roster_id: int, db_path: Path | None = None) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, description, created_at, is_baseline, forwards, defense, time_limit, "
            "algo_version FROM scenarios WHERE roster_id = ? ORDER BY is_baseline DESC, created_at DESC",
            (roster_id,),
        ).fetchall()


def get_scenario(scenario_id: int, roster_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, description, created_at, is_baseline, forwards, defense, time_limit, "
            "algo_version, players_json, result_json FROM scenarios WHERE id = ? AND roster_id = ?",
            (scenario_id, roster_id),
        ).fetchone()


def delete_scenario(scenario_id: int, roster_id: int, db_path: Path | None = None) -> None:
    # is_baseline = 0 guard: the baseline row isn't user-deletable, it's
    # kept in sync by Save and only ever goes away with its roster.
    with get_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM scenarios WHERE id = ? AND roster_id = ? AND is_baseline = 0",
            (scenario_id, roster_id),
        )
