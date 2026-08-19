"""Studio: a Flask app for building rosters and running what-if scenarios
against solver.py's CP-SAT solver.

Run locally:
    source ./venv/bin/activate
    python -m studio.app          # dev server, http://127.0.0.1:5000
"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

import solver
from schemas import PlayerIn, SolveRequest
from studio import db

POSITIONS = ["LW", "C", "RW", "LD", "RD"]

app = Flask(__name__)


def _row_to_player_in_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["player_key"],
        "name": row["name"],
        "available": row["available"],
        "experience": row["experience"],
        "preferred_positions": [p for p in row["preferred_positions"].split(";") if p],
        "secondary_positions": [p for p in row["secondary_positions"].split(";") if p],
        "unwilling_positions": [p for p in row["unwilling_positions"].split(";") if p],
        "optional_position_override": None,
        "optional_player_link": None,
    }


def _players_to_records(players: list[PlayerIn]) -> list[db.PlayerRecord]:
    return [
        {
            "player_key": p.id,
            "name": p.name,
            "available": p.available,
            "experience": p.experience,
            "preferred_positions": p.preferred_positions,
            "secondary_positions": p.secondary_positions,
            "unwilling_positions": p.unwilling_positions,
        }
        for p in players
    ]


def _parse_players(payload: Any) -> list[PlayerIn]:
    if not isinstance(payload, list):
        abort(400, description="players must be a list.")
    try:
        return [PlayerIn.model_validate(p) for p in payload]
    except ValidationError as e:
        abort(400, description=str(e))


@app.get("/")
def index():
    return redirect(url_for("rosters_list"))


@app.get("/rosters")
def rosters_list():
    rosters = db.list_rosters()
    return render_template("rosters_list.html", rosters=rosters)


@app.post("/rosters/new")
def rosters_new():
    title = (request.form.get("title") or "").strip()
    if not title:
        abort(400, description="title is required.")
    roster_id = db.create_roster(title)
    return redirect(url_for("studio_view", roster_id=roster_id))


@app.post("/rosters/<int:roster_id>/delete")
def rosters_delete(roster_id: int):
    if db.get_roster(roster_id) is None:
        abort(404)
    db.delete_roster(roster_id)
    return redirect(url_for("rosters_list"))


@app.get("/studio/<int:roster_id>")
def studio_view(roster_id: int):
    roster = db.get_roster(roster_id)
    if roster is None:
        abort(404)
    players = [_row_to_player_in_dict(r) for r in db.list_players(roster_id)]
    return render_template(
        "studio.html",
        roster=roster,
        players=players,
        positions=POSITIONS,
    )


@app.post("/studio/<int:roster_id>/solve")
def studio_solve(roster_id: int):
    if db.get_roster(roster_id) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        req = SolveRequest.model_validate(body)
    except ValidationError as e:
        abort(400, description=str(e))
    if not req.players:
        abort(400, description="players must not be empty.")
    players = solver.players_from_player_in(req.players)
    result = solver.solve_lines(players, req.forwards, req.defense, req.time_limit)
    return jsonify(result.model_dump())


@app.post("/studio/<int:roster_id>/save")
def studio_save(roster_id: int):
    if db.get_roster(roster_id) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    players = _parse_players(body.get("players"))
    db.replace_players(roster_id, _players_to_records(players))
    return jsonify({"roster_id": roster_id})


@app.post("/studio/<int:roster_id>/save-as")
def studio_save_as(roster_id: int):
    if db.get_roster(roster_id) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        abort(400, description="title is required.")
    players = _parse_players(body.get("players"))
    new_roster_id = db.save_as_new_roster(title, _players_to_records(players))
    return jsonify({"roster_id": new_roster_id})


db.init_db()

if __name__ == "__main__":
    app.run(debug=True)
