"""Studio: a Flask app for building rosters and running what-if scenarios
against solver.py's CP-SAT solver.

Run locally:
    source ./venv/bin/activate
    python -m studio.app          # dev server, http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

import solver
from schemas import PlayerIn, RosterSave, ScenarioSave, SolveRequest
from studio import db

POSITIONS = ["LW", "C", "RW", "LD", "RD"]
WORKSPACE_COOKIE = "workspace_token"
WORKSPACE_COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~400 days, the browser-enforced ceiling

# Probe cookie used to confirm cookies actually round-trip before minting a
# workspace: without this, a browser with cookies blocked would mint (and
# immediately orphan) a brand-new workspace on every single request to "/".
COOKIE_CHECK_COOKIE = "cookie_check"

# Throttle for minting new workspaces: without it, a script that never keeps
# cookies could create unbounded rows just by hitting "/" in a loop.
WORKSPACE_CREATE_LIMIT = 20
WORKSPACE_CREATE_WINDOW_SECONDS = 60 * 60

SAMPLE_ROSTER_CSV = Path(__file__).resolve().parent.parent / "rosters" / "sample_roster.csv"
SAMPLE_ROSTER_TITLE = "Sample Roster"

app = Flask(__name__)


@app.url_value_preprocessor
def _pull_workspace_token(endpoint: str | None, values: dict[str, Any] | None) -> None:
    if values is not None:
        g.workspace_token = values.pop("token", None)


@app.url_defaults
def _inject_workspace_token(endpoint: str, values: dict[str, Any]) -> None:
    if "token" in values or not getattr(g, "workspace_token", None):
        return
    if app.url_map.is_endpoint_expecting(endpoint, "token"):
        values["token"] = g.workspace_token


def _require_workspace() -> sqlite3.Row:
    token = getattr(g, "workspace_token", None)
    workspace = db.get_workspace_by_token(token) if token else None
    if workspace is None:
        abort(404, description="Workspace not found. Check the link, or start a new workspace from the home page.")
    return workspace


def _seed_sample_roster(workspace_id: int) -> None:
    players = solver.read_roster(str(SAMPLE_ROSTER_CSV))
    db.save_as_new_roster(workspace_id, SAMPLE_ROSTER_TITLE, db.player_records_from_players(players))


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


ROSTER_TEMPLATE_CSV = (
    "name,preferred_positions,secondary_positions,unwilling_positions,rank\n"
    "Wayne Gretzky,C,W,,5\n"
    "Bobby Orr,D,,,5\n"
    "Brent Burns,D/W,,,4\n"
)

# Shortcut symbols accepted in position fields, expanded to our internal
# LW/C/RW/LD/RD codes. A field can combine several with any of ,;/| - e.g.
# "W/C" expands and unions to LW, RW, C.
POSITION_SHORTCUTS = {
    "W": ["LW", "RW"],
    "D": ["LD", "RD"],
    "C": ["C"],
    "U": ["LW", "RW", "C", "LD", "RD"],
}
POSITION_FIELD_DELIMITERS = re.compile(r"[,;/|]")


def _parse_positions(raw: str | None) -> list[str]:
    tokens = [t.strip() for t in POSITION_FIELD_DELIMITERS.split((raw or "").upper()) if t.strip()]
    expanded: set[str] = set()
    for t in tokens:
        expanded.update(POSITION_SHORTCUTS.get(t, [t]))
    # Canonical position order first, then any unrecognized leftovers.
    return [p for p in POSITIONS if p in expanded] + sorted(expanded - set(POSITIONS))


def _players_from_csv_upload(text: str) -> list[db.PlayerRecord]:
    """Best-effort CSV -> PlayerRecord: only `name` is required, everything
    else falls back to a sensible default so users can fix it up by hand
    afterward. Rows without a name and columns we don't recognize are
    silently skipped/ignored rather than treated as errors. Uploaded
    rosters are always all-available; there's no "available" column."""
    records: list[db.PlayerRecord] = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        try:
            rank = int((row.get("rank") or row.get("experience") or "").strip())
        except ValueError:
            rank = 1
        records.append(
            {
                "player_key": f"P{len(records) + 1:02d}",
                "name": name,
                "available": 1,
                "experience": rank,
                "preferred_positions": _parse_positions(row.get("preferred_positions")),
                "secondary_positions": _parse_positions(row.get("secondary_positions")),
                "unwilling_positions": _parse_positions(row.get("unwilling_positions")),
            }
        )
    return records


def _parse_players(payload: Any) -> list[PlayerIn]:
    if not isinstance(payload, list):
        abort(400, description="players must be a list.")
    try:
        return [PlayerIn.model_validate(p) for p in payload]
    except ValidationError as e:
        abort(400, description=str(e))


@app.get("/")
def index():
    token = request.cookies.get(WORKSPACE_COOKIE)
    workspace = db.get_workspace_by_token(token) if token else None
    if workspace is not None:
        return redirect(url_for("rosters_list", token=token))

    if request.args.get("cc") != "1":
        resp = redirect(url_for("index", cc="1"))
        resp.set_cookie(COOKIE_CHECK_COOKIE, "1", max_age=300, httponly=True, samesite="Lax")
        return resp
    if request.cookies.get(COOKIE_CHECK_COOKIE) != "1":
        return render_template("cookies_required.html"), 200

    client_ip = request.remote_addr or "unknown"
    recent = db.count_recent_workspaces_from_ip(client_ip, WORKSPACE_CREATE_WINDOW_SECONDS)
    if recent >= WORKSPACE_CREATE_LIMIT:
        abort(429, description="Too many workspaces created from this address recently. Try again later.")

    token = secrets.token_urlsafe(16)
    workspace_id = db.create_workspace(token, client_ip)
    _seed_sample_roster(workspace_id)
    resp = redirect(url_for("rosters_list", token=token, new="1"))
    resp.set_cookie(WORKSPACE_COOKIE, token, max_age=WORKSPACE_COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    return resp


@app.get("/w/<token>/rosters")
def rosters_list():
    workspace = _require_workspace()
    rosters = db.list_rosters(workspace["id"])
    is_new_workspace = request.args.get("new") == "1"
    upload_error = request.args.get("upload_error")
    return render_template(
        "rosters_list.html", rosters=rosters, is_new_workspace=is_new_workspace, upload_error=upload_error
    )


@app.post("/w/<token>/rosters/new")
def rosters_new():
    workspace = _require_workspace()
    title = (request.form.get("title") or "").strip()
    if not title:
        abort(400, description="title is required.")
    roster_id = db.create_roster(workspace["id"], title)
    return redirect(url_for("studio_view", roster_id=roster_id))


@app.post("/w/<token>/rosters/upload")
def rosters_upload():
    workspace = _require_workspace()
    file = request.files.get("file")
    if file is None or not file.filename:
        return redirect(url_for("rosters_list", upload_error="Choose a CSV file to upload."))

    try:
        text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return redirect(url_for("rosters_list", upload_error="That file isn't readable as text. Please upload a CSV."))

    records = _players_from_csv_upload(text)
    if not records:
        return redirect(
            url_for(
                "rosters_list",
                upload_error="No players found. The CSV needs a ‘name’ column with at least one value.",
            )
        )

    title = Path(file.filename).stem.strip() or "Uploaded Roster"
    roster_id = db.save_as_new_roster(workspace["id"], title, records)
    return redirect(url_for("studio_view", roster_id=roster_id))


@app.get("/roster-template.csv")
def roster_template():
    return Response(
        ROSTER_TEMPLATE_CSV,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster-template.csv"},
    )


@app.post("/w/<token>/rosters/<int:roster_id>/delete")
def rosters_delete(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    db.delete_roster(roster_id, workspace["id"])
    return redirect(url_for("rosters_list"))


@app.get("/w/<token>/studio/<int:roster_id>")
def studio_view(roster_id: int):
    workspace = _require_workspace()
    roster = db.get_roster(roster_id, workspace["id"])
    if roster is None:
        abort(404)
    players = [_row_to_player_in_dict(r) for r in db.list_players(roster_id)]
    scenario_titles = [s["title"] for s in db.list_scenarios(roster_id) if not s["is_baseline"]]

    loaded_scenario = None
    load_scenario_id = request.args.get("load_scenario")
    if load_scenario_id is not None:
        # The baseline scenario's players_json is the stripped roster-truth
        # shape (no id/override/link), not the full PlayerIn shape a named
        # scenario stores - loading it here wouldn't parse right, and it's
        # redundant anyway (it's just the roster's own current players).
        scenario = db.get_scenario(int(load_scenario_id), roster_id) if load_scenario_id.isdigit() else None
        if scenario is None or scenario["is_baseline"]:
            abort(404, description="Scenario not found.")
        loaded_scenario = {
            "title": scenario["title"],
            "players": json.loads(scenario["players_json"]),
            "forwards": scenario["forwards"],
            "defense": scenario["defense"],
            "time_limit": scenario["time_limit"],
            "result": json.loads(scenario["result_json"]),
        }

    return render_template(
        "studio.html",
        roster=roster,
        players=players,
        positions=POSITIONS,
        scenario_titles=scenario_titles,
        loaded_scenario=loaded_scenario,
    )


@app.post("/w/<token>/studio/<int:roster_id>/solve")
def studio_solve(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
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


@app.post("/w/<token>/studio/<int:roster_id>/save")
def studio_save(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        req = RosterSave.model_validate(body)
    except ValidationError as e:
        abort(400, description=str(e))
    records = _players_to_records(req.players)
    db.replace_players(roster_id, records)
    db.upsert_baseline_scenario(
        roster_id, req.forwards, req.defense, req.time_limit, json.dumps(records), req.result.model_dump_json()
    )
    return jsonify({"roster_id": roster_id})


@app.post("/w/<token>/studio/<int:roster_id>/save-as")
def studio_save_as(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        abort(400, description="title is required.")
    players = _parse_players(body.get("players"))
    new_roster_id = db.save_as_new_roster(workspace["id"], title, _players_to_records(players))
    return jsonify({"roster_id": new_roster_id})


@app.post("/w/<token>/studio/<int:roster_id>/scenarios")
def scenarios_create(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        req = ScenarioSave.model_validate(body)
    except ValidationError as e:
        abort(400, description=str(e))
    title = req.title.strip()
    if not title:
        abort(400, description="title is required.")
    scenario_id = db.create_scenario(
        roster_id,
        title,
        req.description.strip(),
        req.forwards,
        req.defense,
        req.time_limit,
        json.dumps([p.model_dump() for p in req.players]),
        req.result.model_dump_json(),
    )
    return jsonify({"scenario_id": scenario_id})


@app.get("/w/<token>/studio/<int:roster_id>/scenarios")
def scenarios_view(roster_id: int):
    workspace = _require_workspace()
    roster = db.get_roster(roster_id, workspace["id"])
    if roster is None:
        abort(404)
    scenarios = db.list_scenarios(roster_id)
    return render_template("scenarios.html", roster=roster, scenarios=scenarios)


@app.post("/w/<token>/studio/<int:roster_id>/scenarios/<int:scenario_id>/delete")
def scenarios_delete(roster_id: int, scenario_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    db.delete_scenario(scenario_id, roster_id)
    return redirect(url_for("scenarios_view", roster_id=roster_id))


@app.get("/w/<token>/studio/<int:roster_id>/compare")
def scenarios_compare(roster_id: int):
    workspace = _require_workspace()
    roster = db.get_roster(roster_id, workspace["id"])
    if roster is None:
        abort(404)

    ids = [int(raw) for raw in request.args.getlist("ids") if raw.isdigit()]
    scenarios = [row for sid in ids if (row := db.get_scenario(sid, roster_id)) is not None]
    if not scenarios:
        abort(400, description="No valid scenarios selected to compare.")

    compare_data = [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s["description"],
            "is_baseline": bool(s["is_baseline"]),
            "forwards": s["forwards"],
            "defense": s["defense"],
            "time_limit": s["time_limit"],
            "load_url": None if s["is_baseline"] else url_for("studio_view", roster_id=roster_id, load_scenario=s["id"]),
            "result": json.loads(s["result_json"]),
        }
        for s in scenarios
    ]
    return render_template("compare.html", roster=roster, scenarios=compare_data)


db.init_db()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port)
