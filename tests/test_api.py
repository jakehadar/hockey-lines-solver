import csv
import io
import os

from fastapi.testclient import TestClient

import solver
from api import app

client = TestClient(app)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_ROSTER = os.path.join(REPO_ROOT, "roster_sample.csv")

SMALL_PLAYERS = [
    {"id": "p1", "name": "Alice", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": []},
    {"id": "p2", "name": "Bob", "available": 1, "experience": 2, "preferred_positions": ["C"], "secondary_positions": []},
    {"id": "p3", "name": "Cy", "available": 1, "experience": 1, "preferred_positions": ["RW"], "secondary_positions": []},
    {"id": "p4", "name": "Dee", "available": 1, "experience": 2, "preferred_positions": ["LD"], "secondary_positions": []},
    {"id": "p5", "name": "Eve", "available": 1, "experience": 3, "preferred_positions": ["RD"], "secondary_positions": []},
]


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_solve_json_returns_json():
    resp = client.post("/solve", json={"players": SMALL_PLAYERS, "forwards": 1, "defense": 1, "time_limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert set(body.keys()) == {"status", "summary", "forward_lines", "defense_pairs"}
    assert body["summary"]["total_assigned"] == 5
    assert body["summary"]["total_primary"] == 5
    assert len(body["forward_lines"]) == 1
    assert len(body["defense_pairs"]) == 1
    assert body["defense_pairs"][0]["partial"] is False


def test_solve_json_returns_csv():
    resp = client.post("/solve?format=csv", json={"players": SMALL_PLAYERS, "forwards": 1, "defense": 1, "time_limit": 5})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 5
    assert set(rows[0].keys()) == {"section", "line_number", "slot", "position", "player_id", "player_name", "experience", "status"}
    assert {r["section"] for r in rows} == {"forward", "defense"}


def test_solve_csv_upload_returns_json():
    with open(SAMPLE_ROSTER, "rb") as f:
        resp = client.post(
            "/solve/csv",
            files={"file": ("roster_sample.csv", f, "text/csv")},
            data={"forwards": 3, "defense": 3, "time_limit": 20},
        )
    assert resp.status_code == 200
    body = resp.json()

    expected = solver.solve_lines(solver.read_roster(SAMPLE_ROSTER), 3, 3, 20)
    assert body["summary"]["total_assigned"] == expected.summary.total_assigned
    assert body["summary"]["total_primary"] == expected.summary.total_primary
    assert body["summary"]["available_players"] == expected.summary.available_players
    assert len(body["forward_lines"]) == len(expected.forward_lines)
    assert len(body["defense_pairs"]) == len(expected.defense_pairs)


def test_solve_csv_upload_returns_csv():
    with open(SAMPLE_ROSTER, "rb") as f:
        resp = client.post(
            "/solve/csv?format=csv",
            files={"file": ("roster_sample.csv", f, "text/csv")},
            data={"forwards": 3, "defense": 3, "time_limit": 20},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) > 0


def test_solve_rejects_empty_roster():
    resp = client.post("/solve", json={"players": [], "forwards": 3, "defense": 3})
    assert resp.status_code == 400


# No test for a CP-SAT "no solution" outcome: with this model, the number of
# slots built is always <= the number of available players (see solve_lines'
# allocation math in solver.py), so a feasible assignment always exists and
# NO_SOLUTION is not practically reachable through the public API.
