import csv
import io
import os

from fastapi.testclient import TestClient

import solver
from api import app

client = TestClient(app)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_ROSTER = os.path.join(REPO_ROOT, "rosters", "sample_roster.csv")

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
    assert set(body.keys()) == {"status", "summary", "forward_lines", "defense_pairs", "objectives"}
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


def test_solve_json_forwards_allow_oop_and_allow_unwilling():
    # Nobody prefers/lists RW as secondary, and exactly 3 players for 3
    # slots - allow_oop=True (the default) must fill RW anyway (OOP);
    # allow_oop=False for the identical roster must go infeasible instead.
    # Regression test: solve_json used to silently drop allow_oop/
    # allow_unwilling/objectives before calling solver.solve_lines().
    players = [
        {"id": "p1", "name": "A", "available": 1, "experience": 3, "preferred_positions": ["C"], "secondary_positions": []},
        {"id": "p2", "name": "B", "available": 1, "experience": 3, "preferred_positions": ["C"], "secondary_positions": []},
        {"id": "p3", "name": "C", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": []},
    ]
    resp_allowed = client.post(
        "/solve", json={"players": players, "forwards": 1, "defense": 0, "time_limit": 5, "allow_oop": True}
    )
    assert resp_allowed.json()["status"] in ("OPTIMAL", "FEASIBLE")

    resp_forbidden = client.post(
        "/solve", json={"players": players, "forwards": 1, "defense": 0, "time_limit": 5, "allow_oop": False}
    )
    assert resp_forbidden.json()["status"] == "NO_SOLUTION"

    unwilling_players = [
        {"id": "p1", "name": "A", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": [], "unwilling_positions": ["C"], "optional_position_override": "C"},
        {"id": "p2", "name": "B", "available": 1, "experience": 3, "preferred_positions": ["C"], "secondary_positions": []},
        {"id": "p3", "name": "C", "available": 1, "experience": 3, "preferred_positions": ["RW"], "secondary_positions": []},
    ]
    resp_default = client.post(
        "/solve", json={"players": unwilling_players, "forwards": 1, "defense": 0, "time_limit": 5}
    )
    assert resp_default.json()["status"] == "NO_SOLUTION"

    resp_allowed_unwilling = client.post(
        "/solve",
        json={"players": unwilling_players, "forwards": 1, "defense": 0, "time_limit": 5, "allow_unwilling": True},
    )
    assert resp_allowed_unwilling.json()["status"] in ("OPTIMAL", "FEASIBLE")


# No test for a CP-SAT "no solution" outcome: with this model, the number of
# slots built is always <= the number of available players (see solve_lines'
# allocation math in solver.py), so a feasible assignment always exists and
# NO_SOLUTION is not practically reachable through the public API.


def test_degrees_of_freedom_reports_zero_flexibility_for_an_exactly_sized_roster():
    # 3 players, 1 forward line (3 slots) - every player is already needed
    # just to fill the line, so there's no room for any substitution.
    resp = client.post(
        "/degrees-of-freedom",
        json={"players": SMALL_PLAYERS[:3], "forwards": 1, "defense": 0, "time_limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert body["total_filled_slots"] == 3
    assert body["total_extra_options"] == 0
    assert body["score_per_slot"] == 0
    assert {pf["position"] for pf in body["by_position"]} == {"LW", "C", "RW"}
    assert all(pf["extra_options"] == 0 for pf in body["by_position"])


def test_degrees_of_freedom_reports_no_solution_when_baseline_is_infeasible():
    # Two players both locked to the same position, with only one slot for
    # it - the baseline solve itself is infeasible, so there's nothing to score.
    players = [
        {"id": "p1", "name": "A", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": [], "optional_position_override": "LW"},
        {"id": "p2", "name": "B", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": [], "optional_position_override": "LW"},
    ]
    resp = client.post(
        "/degrees-of-freedom",
        json={"players": players, "forwards": 1, "defense": 0, "time_limit": 5},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "NO_SOLUTION"}


def test_degrees_of_freedom_cancel_is_a_harmless_noop_for_an_unknown_job():
    resp = client.post("/degrees-of-freedom/cancel", json={"job_id": "not-a-real-job"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_degrees_of_freedom_cleans_up_its_job_registry_entry():
    import dof as dof_module

    resp = client.post(
        "/degrees-of-freedom",
        json={"players": SMALL_PLAYERS[:3], "forwards": 1, "defense": 0, "time_limit": 5, "job_id": "test-job-1"},
    )
    assert resp.status_code == 200
    # The job must not linger in the registry after the request completes -
    # otherwise a stale entry would grow unbounded over the app's uptime.
    assert "test-job-1" not in dof_module._jobs
