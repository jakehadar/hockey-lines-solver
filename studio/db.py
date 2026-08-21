"""Thin sqlite3 repository for studio's persisted rosters/players.

Only the "truth" fields round-trip here: player_key, name, experience,
preferred_positions, secondary_positions, unwilling_positions. available,
optional_position_override, and optional_player_link are scenario-only
levers that never reach this layer (studio/app.py strips them before
calling in).
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
            "(SELECT COUNT(*) FROM scenarios s WHERE s.roster_id = r.id) AS scenario_count "
            "FROM rosters r WHERE r.workspace_id = ? ORDER BY r.created_at DESC",
            (workspace_id,),
        ).fetchall()


def get_roster(roster_id: int, workspace_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, created_at, last_mode FROM rosters WHERE id = ? AND workspace_id = ?",
            (roster_id, workspace_id),
        ).fetchone()


def set_roster_mode(roster_id: int, mode: str, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE rosters SET last_mode = ? WHERE id = ?", (mode, roster_id))


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
            "SELECT player_key, name, experience, preferred_positions, "
            "secondary_positions, unwilling_positions FROM players WHERE roster_id = ? "
            "ORDER BY player_key",
            (roster_id,),
        ).fetchall()


def _insert_players(conn: sqlite3.Connection, roster_id: int, players: List[PlayerRecord]) -> None:
    conn.executemany(
        "INSERT INTO players (roster_id, player_key, name, experience, "
        "preferred_positions, secondary_positions, unwilling_positions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                roster_id,
                p["player_key"],
                p["name"],
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


def create_scenario(
    roster_id: int,
    title: str,
    description: str,
    forwards: int,
    defense: int,
    time_limit: int,
    players_json: str,
    result_json: str,
    parent_scenario_id: int | None = None,
    db_path: Path | None = None,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scenarios (roster_id, parent_scenario_id, title, description, forwards, defense, "
            "time_limit, players_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (roster_id, parent_scenario_id, title, description, forwards, defense, time_limit, players_json, result_json),
        )
        return cur.lastrowid


def replace_scenario(
    scenario_id: int,
    forwards: int,
    defense: int,
    time_limit: int,
    players_json: str,
    result_json: str,
    db_path: Path | None = None,
) -> None:
    """Overwrite an already-loaded scenario in place (Save scenario, not Branch)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE scenarios SET forwards = ?, defense = ?, time_limit = ?, players_json = ?, "
            "result_json = ?, created_at = datetime('now') WHERE id = ?",
            (forwards, defense, time_limit, players_json, result_json, scenario_id),
        )


def list_scenarios(roster_id: int, db_path: Path | None = None) -> List[sqlite3.Row]:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT s.id, s.title, s.description, s.created_at, s.parent_scenario_id, s.forwards, "
            "s.defense, s.time_limit, s.algo_version, p.title AS parent_title "
            "FROM scenarios s LEFT JOIN scenarios p ON p.id = s.parent_scenario_id "
            "WHERE s.roster_id = ? ORDER BY s.created_at DESC",
            (roster_id,),
        ).fetchall()


def get_scenario(scenario_id: int, roster_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, title, description, created_at, parent_scenario_id, forwards, defense, time_limit, "
            "algo_version, players_json, result_json FROM scenarios WHERE id = ? AND roster_id = ?",
            (scenario_id, roster_id),
        ).fetchone()


def delete_scenario(scenario_id: int, roster_id: int, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM scenarios WHERE id = ? AND roster_id = ?", (scenario_id, roster_id))
