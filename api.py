"""FastAPI service wrapping solver.py.

Run locally:
    uvicorn api:app --reload

Interactive docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

import solver
from schemas import PlayerIn, SolveRequest, SolveResponse

app = FastAPI(
    title="Hockey Lines Solver API",
    description=(
        "Assigns a roster of players to hockey forward lines (LW/C/RW) and "
        "defensive pairs (LD/RD), optimizing for position preference and "
        "experience balance. Submit a roster as JSON (`POST /solve`) or as "
        "a CSV file upload (`POST /solve/csv`); get results back as JSON or "
        "as a flat CSV table via the `format` query parameter."
    ),
    version="0.1.0",
)

# Dev-friendly default so a frontend running on any local port can call this
# API directly. Restrict allow_origins before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CSV_COLUMNS = ["section", "line_number", "slot", "position", "player_id", "player_name", "experience", "status"]


def solve_result_to_csv(result: SolveResponse) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for fl in result.forward_lines:
        for a in fl.slots:
            writer.writerow(["forward", fl.line_number, a.slot, a.position, a.player_id, a.player_name, a.experience, a.status])
    for dp in result.defense_pairs:
        for a in dp.slots:
            writer.writerow(["defense", dp.pair_number, a.slot, a.position, a.player_id, a.player_name, a.experience, a.status])
    return buf.getvalue()


def _respond(result: SolveResponse, format: str) -> Response | SolveResponse:
    if format == "csv":
        return Response(content=solve_result_to_csv(result), media_type="text/csv")
    return result


def _players_from_player_in(players_in: list[PlayerIn]) -> list[solver.Player]:
    return [
        solver.Player(
            id=p.id,
            name=p.name,
            available=p.available,
            experience=p.experience,
            prefs=[pos.upper() for pos in p.preferred_positions],
            secondary=[pos.upper() for pos in p.secondary_positions],
        )
        for p in players_in
    ]


FormatParam = Query("json", description="Response format: 'json' for a structured SolveResponse, 'csv' for a flat assignment table.")

CSV_RESPONSE_DOC = {
    200: {
        "description": "OK. Body is a SolveResponse (application/json) unless ?format=csv, in which case it's a flat text/csv assignment table.",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}


@app.get("/health", summary="Liveness check")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/solve",
    summary="Solve lines from a JSON roster",
    description="Accepts a roster as a JSON array of players and returns the optimized line assignments.",
    response_model=SolveResponse,
    responses=CSV_RESPONSE_DOC,
)
def solve_json(
    request: SolveRequest,
    format: Literal["json", "csv"] = FormatParam,
) -> Response | SolveResponse:
    if not request.players:
        raise HTTPException(status_code=400, detail="players must not be empty.")
    if request.forwards < 0 or request.defense < 0:
        raise HTTPException(status_code=400, detail="forwards and defense must be >= 0.")

    players = _players_from_player_in(request.players)
    result = solver.solve_lines(players, request.forwards, request.defense, request.time_limit)
    return _respond(result, format)


@app.post(
    "/solve/csv",
    summary="Solve lines from an uploaded CSV roster",
    description=(
        "Accepts a roster CSV file upload (header: "
        "id,name,available,experience,preferred_positions,secondary_positions) "
        "and returns the optimized line assignments."
    ),
    response_model=SolveResponse,
    responses=CSV_RESPONSE_DOC,
)
async def solve_csv(
    file: UploadFile,
    forwards: int = Form(3, description="Requested number of forward lines."),
    defense: int = Form(3, description="Requested number of defensive pairs."),
    time_limit: int = Form(20, description="Solver time limit in seconds."),
    format: Literal["json", "csv"] = FormatParam,
) -> Response | SolveResponse:
    if forwards < 0 or defense < 0:
        raise HTTPException(status_code=400, detail="forwards and defense must be >= 0.")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Could not decode file as UTF-8: {e}") from e

    rows = csv.DictReader(io.StringIO(text))
    players = solver.players_from_rows(rows)
    if not players:
        raise HTTPException(status_code=400, detail="Roster CSV contained no players.")

    result = solver.solve_lines(players, forwards, defense, time_limit)
    return _respond(result, format)
