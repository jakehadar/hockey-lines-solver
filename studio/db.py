"""Thin Postgres repository for studio's persisted rosters/players.

Only the "truth" fields round-trip here: player_key, name, experience,
preferred_positions, secondary_positions, unwilling_positions. available,
optional_position_override, and optional_player_link are scenario-only
levers that never reach this layer (studio/app.py strips them before
calling in).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, List, TypedDict

import psycopg
from psycopg.rows import dict_row

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Set by Vercel's Neon Marketplace integration in production; point it at a
# local Postgres for dev (see README) or a test database for tests.
DATABASE_URL = os.environ.get("DATABASE_URL")


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


def _resolve(database_url: str | None) -> str:
    # NB: default args bind at def-time, so callers that want to point at a
    # different DATABASE_URL (tests) must pass database_url=None (the
    # default) *after* monkeypatching the module-level DATABASE_URL, not
    # rely on a bound default.
    resolved = database_url if database_url is not None else DATABASE_URL
    if not resolved:
        raise RuntimeError("DATABASE_URL is not set.")
    return resolved


@contextmanager
def get_connection(database_url: str | None = None) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(_resolve(database_url), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(database_url: str | None = None) -> None:
    """Applies schema.sql - idempotent (every statement is CREATE ... IF NOT
    EXISTS), but deliberately NOT called automatically at app import: on a
    server that keeps its schema in a shared database (e.g. Postgres/Neon,
    reachable from multiple concurrent instances), running DDL on every cold
    start is a lock-contention hazard, not just unnecessary work. Run this
    explicitly, once, as its own step:

        python -m studio.migrate
    """
    with get_connection(database_url) as conn:
        conn.execute(SCHEMA_PATH.read_text())


def create_workspace(token: str, client_ip: str | None = None, database_url: str | None = None) -> int:
    with get_connection(database_url) as conn:
        row = conn.execute(
            "INSERT INTO workspaces (token, client_ip) VALUES (%s, %s) RETURNING id", (token, client_ip)
        ).fetchone()
        return row["id"]


def get_workspace_by_token(token: str, database_url: str | None = None) -> dict[str, Any] | None:
    with get_connection(database_url) as conn:
        return conn.execute("SELECT id, token, created_at FROM workspaces WHERE token = %s", (token,)).fetchone()


def count_recent_workspaces_from_ip(client_ip: str, window_seconds: int, database_url: str | None = None) -> int:
    with get_connection(database_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM workspaces WHERE client_ip = %s "
            "AND created_at >= to_char(now() - (%s || ' seconds')::interval, 'YYYY-MM-DD HH24:MI:SS')",
            (client_ip, str(window_seconds)),
        ).fetchone()
        return row["n"]


def list_rosters(workspace_id: int, database_url: str | None = None) -> List[dict[str, Any]]:
    with get_connection(database_url) as conn:
        return conn.execute(
            "SELECT r.id, r.title, r.created_at, "
            "(SELECT COUNT(*) FROM scenarios s WHERE s.roster_id = r.id) AS scenario_count "
            "FROM rosters r WHERE r.workspace_id = %s ORDER BY r.created_at DESC",
            (workspace_id,),
        ).fetchall()


def get_roster(roster_id: int, workspace_id: int, database_url: str | None = None) -> dict[str, Any] | None:
    with get_connection(database_url) as conn:
        return conn.execute(
            "SELECT id, title, created_at FROM rosters WHERE id = %s AND workspace_id = %s",
            (roster_id, workspace_id),
        ).fetchone()


def delete_roster(roster_id: int, workspace_id: int, database_url: str | None = None) -> None:
    with get_connection(database_url) as conn:
        conn.execute("DELETE FROM rosters WHERE id = %s AND workspace_id = %s", (roster_id, workspace_id))


def rename_roster(roster_id: int, workspace_id: int, title: str, database_url: str | None = None) -> None:
    with get_connection(database_url) as conn:
        conn.execute(
            "UPDATE rosters SET title = %s WHERE id = %s AND workspace_id = %s",
            (title, roster_id, workspace_id),
        )


def create_roster(workspace_id: int, title: str, database_url: str | None = None) -> int:
    with get_connection(database_url) as conn:
        row = conn.execute(
            "INSERT INTO rosters (workspace_id, title) VALUES (%s, %s) RETURNING id", (workspace_id, title)
        ).fetchone()
        return row["id"]


def list_players(roster_id: int, database_url: str | None = None) -> List[dict[str, Any]]:
    with get_connection(database_url) as conn:
        return conn.execute(
            "SELECT player_key, name, experience, preferred_positions, "
            "secondary_positions, unwilling_positions FROM players WHERE roster_id = %s "
            "ORDER BY player_key",
            (roster_id,),
        ).fetchall()


def _insert_players(conn: psycopg.Connection, roster_id: int, players: List[PlayerRecord]) -> None:
    conn.cursor().executemany(
        "INSERT INTO players (roster_id, player_key, name, experience, "
        "preferred_positions, secondary_positions, unwilling_positions) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
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


def replace_players(roster_id: int, players: List[PlayerRecord], database_url: str | None = None) -> None:
    with get_connection(database_url) as conn:
        conn.execute("DELETE FROM players WHERE roster_id = %s", (roster_id,))
        _insert_players(conn, roster_id, players)


def _split_positions(raw: str) -> List[str]:
    return [p.strip().upper() for p in (raw or "").replace("|", ";").split(";") if p.strip()]


def player_records_from_csv_rows(rows: Iterable[dict]) -> List[PlayerRecord]:
    """Parses solver.py's roster CSV format (id,name,available,experience,
    preferred_positions,secondary_positions,unwilling_positions,...) into
    PlayerRecords - only the columns PlayerRecord actually stores are kept;
    available, optional_position_override, and optional_player_link are
    ephemeral scenario-only levers that never reach this layer (see module
    docstring). Kept independent of solver.py so studio has no import-time
    dependency on the solver service's codebase."""
    records: List[PlayerRecord] = []
    for r in rows:
        player_key = (r.get("id") or r.get("name") or "").strip()
        if not player_key:
            continue
        records.append(
            {
                "player_key": player_key,
                "name": r.get("name") or player_key,
                "experience": int(r.get("experience") or "1"),
                "preferred_positions": _split_positions(r.get("preferred_positions", "")),
                "secondary_positions": _split_positions(r.get("secondary_positions", "")),
                "unwilling_positions": _split_positions(r.get("unwilling_positions", "")),
            }
        )
    return records


def save_as_new_roster(
    workspace_id: int, title: str, players: List[PlayerRecord], database_url: str | None = None
) -> int:
    with get_connection(database_url) as conn:
        row = conn.execute(
            "INSERT INTO rosters (workspace_id, title) VALUES (%s, %s) RETURNING id", (workspace_id, title)
        ).fetchone()
        roster_id = row["id"]
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
    dof_json: str | None = None,
    allow_oop: bool = True,
    allow_unwilling: bool = False,
    objectives_json: str | None = None,
    database_url: str | None = None,
) -> int:
    with get_connection(database_url) as conn:
        row = conn.execute(
            "INSERT INTO scenarios (roster_id, parent_scenario_id, title, description, forwards, defense, "
            "time_limit, players_json, result_json, dof_json, allow_oop, allow_unwilling, objectives_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (roster_id, parent_scenario_id, title, description, forwards, defense, time_limit, players_json, result_json, dof_json, int(allow_oop), int(allow_unwilling), objectives_json),
        ).fetchone()
        return row["id"]


def replace_scenario(
    scenario_id: int,
    forwards: int,
    defense: int,
    time_limit: int,
    players_json: str,
    result_json: str,
    dof_json: str | None = None,
    allow_oop: bool = True,
    allow_unwilling: bool = False,
    objectives_json: str | None = None,
    database_url: str | None = None,
) -> None:
    """Overwrite an already-loaded scenario in place (Save scenario, not Branch)."""
    with get_connection(database_url) as conn:
        conn.execute(
            "UPDATE scenarios SET forwards = %s, defense = %s, time_limit = %s, players_json = %s, "
            "result_json = %s, dof_json = %s, allow_oop = %s, allow_unwilling = %s, objectives_json = %s, "
            "created_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
            (forwards, defense, time_limit, players_json, result_json, dof_json, int(allow_oop), int(allow_unwilling), objectives_json, scenario_id),
        )


def list_scenarios(roster_id: int, database_url: str | None = None) -> List[dict[str, Any]]:
    with get_connection(database_url) as conn:
        return conn.execute(
            "SELECT s.id, s.title, s.description, s.created_at, s.parent_scenario_id, s.forwards, "
            "s.defense, s.time_limit, s.algo_version, s.dof_json, s.allow_oop, s.allow_unwilling, s.objectives_json, p.title AS parent_title "
            "FROM scenarios s LEFT JOIN scenarios p ON p.id = s.parent_scenario_id "
            "WHERE s.roster_id = %s ORDER BY s.created_at DESC",
            (roster_id,),
        ).fetchall()


def get_scenario(scenario_id: int, roster_id: int, database_url: str | None = None) -> dict[str, Any] | None:
    with get_connection(database_url) as conn:
        return conn.execute(
            "SELECT id, title, description, created_at, parent_scenario_id, forwards, defense, time_limit, "
            "algo_version, players_json, result_json, dof_json, allow_oop, allow_unwilling, objectives_json "
            "FROM scenarios WHERE id = %s AND roster_id = %s",
            (scenario_id, roster_id),
        ).fetchone()


def delete_scenario(scenario_id: int, roster_id: int, database_url: str | None = None) -> None:
    with get_connection(database_url) as conn:
        conn.execute("DELETE FROM scenarios WHERE id = %s AND roster_id = %s", (scenario_id, roster_id))
