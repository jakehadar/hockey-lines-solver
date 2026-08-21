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

import dof
import solver
from schemas import PlayerIn, ScenarioSave, ScenarioUpdate, SolveRequest
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
        "available": 1,  # ephemeral, not part of the roster - every scenario starts all-available
        "experience": row["experience"],
        "preferred_positions": [p for p in row["preferred_positions"].split(";") if p],
        "secondary_positions": [p for p in row["secondary_positions"].split(";") if p],
        "unwilling_positions": [p for p in row["unwilling_positions"].split(";") if p],
        "optional_position_override": None,
        "optional_player_link": None,
    }


def _players_to_records(players: list[PlayerIn]) -> list[db.PlayerRecord]:
    records: list[db.PlayerRecord] = []
    for p in players:
        preferred = list(p.preferred_positions)
        secondary = list(p.secondary_positions)
        unwilling = list(p.unwilling_positions)
        # Safety net alongside studio.js's own dedupePositions - the DB should
        # never end up with a position claimed by more than one column, even
        # if a client posted here directly instead of through the UI.
        _dedupe_position_columns(preferred, secondary, unwilling)
        records.append(
            {
                "player_key": p.id,
                "name": p.name,
                "experience": p.experience,
                "preferred_positions": preferred,
                "secondary_positions": secondary,
                "unwilling_positions": unwilling,
            }
        )
    return records


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


def _dedupe_position_columns(preferred: list[str], secondary: list[str], unwilling: list[str]) -> None:
    """A position can only be claimed by one of the three columns - resolved
    with preferred > secondary > unwilling if the source CSV's three columns
    disagreed (parsed independently, so nothing stops them overlapping).
    Mutates secondary/unwilling in place; mirrors studio.js's dedupePositions."""
    for pos in preferred:
        if pos in secondary:
            secondary.remove(pos)
        if pos in unwilling:
            unwilling.remove(pos)
    for pos in secondary:
        if pos in unwilling:
            unwilling.remove(pos)


def _players_from_csv_upload(text: str) -> list[db.PlayerRecord]:
    """Best-effort CSV -> PlayerRecord: only `name` is required, everything
    else falls back to a sensible default so users can fix it up by hand
    afterward. Rows without a name and columns we don't recognize are
    silently skipped/ignored rather than treated as errors. There's no
    "available" column - it's ephemeral, scenario-only, never part of the
    roster.

    A source `id` is honored when present - notably, an "A01"-style id is
    how a row is marked as an alt (see isAlt() in studio.js), so discarding
    it here would silently turn alts into regular players. Rows without one
    get a generated "P01"-style id instead."""
    records: list[db.PlayerRecord] = []
    used_keys: set[str] = set()
    next_seq = 1

    def next_generated_key() -> str:
        nonlocal next_seq
        while True:
            key = f"P{next_seq:02d}"
            next_seq += 1
            if key not in used_keys:
                return key

    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        try:
            rank = int((row.get("rank") or row.get("experience") or "").strip())
        except ValueError:
            rank = 1
        preferred = _parse_positions(row.get("preferred_positions"))
        secondary = _parse_positions(row.get("secondary_positions"))
        unwilling = _parse_positions(row.get("unwilling_positions"))
        _dedupe_position_columns(preferred, secondary, unwilling)
        player_key = (row.get("id") or "").strip() or next_generated_key()
        used_keys.add(player_key)
        records.append(
            {
                "player_key": player_key,
                "name": name,
                "experience": rank,
                "preferred_positions": preferred,
                "secondary_positions": secondary,
                "unwilling_positions": unwilling,
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
    scenario_titles = [s["title"] for s in db.list_scenarios(roster_id)]

    loaded_scenario = None
    load_scenario_id = request.args.get("load_scenario")
    if load_scenario_id is not None:
        scenario = db.get_scenario(int(load_scenario_id), roster_id) if load_scenario_id.isdigit() else None
        if scenario is None:
            abort(404, description="Scenario not found.")
        loaded_scenario = {
            "id": scenario["id"],
            "title": scenario["title"],
            "players": json.loads(scenario["players_json"]),
            "forwards": scenario["forwards"],
            "defense": scenario["defense"],
            "time_limit": scenario["time_limit"],
            "result": json.loads(scenario["result_json"]),
            "dof": json.loads(scenario["dof_json"]) if scenario["dof_json"] else None,
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


@app.post("/w/<token>/studio/<int:roster_id>/degrees-of-freedom")
def studio_degrees_of_freedom(roster_id: int):
    """Prototype: how many other available players could substitute at each
    position without dropping the solve below its current best objective -
    see dof.py for the full explanation. Expensive relative to /solve (many
    re-solves), so the client fires this only after a solve lands, and
    cancels/ignores it if a newer one supersedes it before this returns."""
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
    result = dof.compute_degrees_of_freedom(players, req.forwards, req.defense, req.time_limit)
    if result is None:
        return jsonify({"status": "NO_SOLUTION"})
    return jsonify(
        {
            "status": result.baseline.status,
            "total_extra_options": result.total_extra_options,
            "total_filled_slots": result.total_filled_slots,
            "score_per_slot": result.score_per_slot,
            "by_position": [
                {
                    "position": pf.position,
                    "slots_filled": pf.slots_filled,
                    "extra_options": pf.extra_options,
                    "candidates_checked": pf.candidates_checked,
                }
                for pf in result.by_position
            ],
        }
    )


@app.post("/w/<token>/studio/<int:roster_id>/save")
def studio_save(roster_id: int):
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    players = _parse_players(body.get("players"))
    db.replace_players(roster_id, _players_to_records(players))
    title = (body.get("title") or "").strip()
    if title:
        db.rename_roster(roster_id, workspace["id"], title)
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
    if req.parent_scenario_id is not None and db.get_scenario(req.parent_scenario_id, roster_id) is None:
        abort(400, description="parent_scenario_id does not belong to this roster.")
    scenario_id = db.create_scenario(
        roster_id,
        title,
        req.description.strip(),
        req.forwards,
        req.defense,
        req.time_limit,
        json.dumps([p.model_dump() for p in req.players]),
        req.result.model_dump_json(),
        parent_scenario_id=req.parent_scenario_id,
        dof_json=req.dof.model_dump_json() if req.dof else None,
    )
    return jsonify({"scenario_id": scenario_id})


@app.post("/w/<token>/studio/<int:roster_id>/scenarios/<int:scenario_id>/save")
def scenarios_update(roster_id: int, scenario_id: int):
    """Overwrite an already-loaded scenario in place - the Scenario-mode
    counterpart to Save roster. Title/description/parent don't change here;
    use Branch scenario for that."""
    workspace = _require_workspace()
    if db.get_roster(roster_id, workspace["id"]) is None:
        abort(404)
    if db.get_scenario(scenario_id, roster_id) is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        req = ScenarioUpdate.model_validate(body)
    except ValidationError as e:
        abort(400, description=str(e))
    db.replace_scenario(
        scenario_id,
        req.forwards,
        req.defense,
        req.time_limit,
        json.dumps([p.model_dump() for p in req.players]),
        req.result.model_dump_json(),
        dof_json=req.dof.model_dump_json() if req.dof else None,
    )
    return jsonify({"scenario_id": scenario_id})


@app.get("/w/<token>/studio/<int:roster_id>/scenarios")
def scenarios_view(roster_id: int):
    workspace = _require_workspace()
    roster = db.get_roster(roster_id, workspace["id"])
    if roster is None:
        abort(404)
    scenarios = db.list_scenarios(roster_id)
    dof_by_scenario_id = {s["id"]: json.loads(s["dof_json"]) for s in scenarios if s["dof_json"]}
    return render_template("scenarios.html", roster=roster, scenarios=scenarios, dof_by_scenario_id=dof_by_scenario_id)


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
            "forwards": s["forwards"],
            "defense": s["defense"],
            "time_limit": s["time_limit"],
            "load_url": url_for("studio_view", roster_id=roster_id, load_scenario=s["id"]),
            "result": json.loads(s["result_json"]),
            "dof": json.loads(s["dof_json"]) if s["dof_json"] else None,
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
