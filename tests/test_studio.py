import importlib
import io
import json
import re

import pytest


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from studio import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_studio.db")

    import studio.app as app_module

    importlib.reload(app_module)  # re-run module-level db.init_db() against the patched path
    app_module.app.testing = True
    return app_module, db_module


@pytest.fixture
def client(studio):
    app_module, _ = studio
    with app_module.app.test_client() as c:
        yield c


SMALL_PLAYERS = [
    {"id": "P1", "name": "Alice", "available": 1, "experience": 3, "preferred_positions": ["LW"], "secondary_positions": []},
    {"id": "P2", "name": "Bob", "available": 1, "experience": 2, "preferred_positions": ["C"], "secondary_positions": []},
    {"id": "P3", "name": "Cy", "available": 1, "experience": 1, "preferred_positions": ["RW"], "secondary_positions": []},
]


def _workspace_token(client):
    # "/" does a cookie round-trip check before minting a workspace and
    # redirecting into it; follow_redirects chases both hops like a browser
    # would. The test client keeps cookies, so later bare requests reuse the
    # same workspace.
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    return resp.request.path.split("/w/", 1)[1].split("/", 1)[0]


def _create_roster(client, title="Test Roster"):
    token = _workspace_token(client)
    resp = client.post(f"/w/{token}/rosters/new", data={"title": title}, follow_redirects=False)
    assert resp.status_code == 302
    roster_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    return token, roster_id


def _fake_result(status="NO_SOLUTION"):
    return {
        "status": status,
        "summary": {
            "available_players": 0,
            "forwards_requested": 0,
            "forwards_used": 0,
            "defense_requested": 0,
            "defense_pairs_used": 0,
            "defense_last_partial": False,
            "total_assigned": 0,
            "total_primary": 0,
            "total_secondary": 0,
            "total_oop": 0,
        },
        "forward_lines": [],
        "defense_pairs": [],
    }


def _save_roster(client, token, roster_id, players):
    return client.post(f"/w/{token}/studio/{roster_id}/save", json={"players": players})


def test_new_blank_roster_has_no_players(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    assert db_module.list_players(roster_id) == []

    resp = client.get(f"/w/{token}/studio/{roster_id}")
    assert resp.status_code == 200
    assert b"INITIAL_ROSTER = []" in resp.data


def test_delete_roster_removes_it_and_its_players(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    workspace = db_module.get_workspace_by_token(token)
    _save_roster(client, token, roster_id, SMALL_PLAYERS)
    assert len(db_module.list_players(roster_id)) == 3

    resp = client.post(f"/w/{token}/rosters/{roster_id}/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/rosters")

    assert db_module.get_roster(roster_id, workspace["id"]) is None
    assert db_module.list_players(roster_id) == []
    assert client.get(f"/w/{token}/studio/{roster_id}").status_code == 404


def test_solve_endpoint_is_stateless(client):
    token, roster_id = _create_roster(client)
    resp = client.post(
        f"/w/{token}/studio/{roster_id}/solve",
        json={"players": SMALL_PLAYERS, "forwards": 1, "defense": 0, "time_limit": 5},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert body["summary"]["total_assigned"] == 3
    assert body["summary"]["total_primary"] == 3


def test_save_persists_unwilling_positions_but_not_ephemeral_fields(client, studio):
    # available, optional_position_override, and optional_player_link are all
    # ephemeral - none of them round-trip through the roster.
    _, db_module = studio
    token, roster_id = _create_roster(client)

    players = [
        {
            "id": "P1",
            "name": "Alice",
            "available": 0,
            "experience": 3,
            "preferred_positions": ["LW"],
            "secondary_positions": ["RW"],
            "unwilling_positions": ["C"],
            "optional_position_override": "LW",
            "optional_player_link": "P2",
        }
    ]
    resp = _save_roster(client, token, roster_id, players)
    assert resp.status_code == 200

    rows = db_module.list_players(roster_id)
    assert len(rows) == 1
    assert rows[0]["unwilling_positions"] == "C"
    assert "available" not in rows[0].keys()  # ephemeral, never persisted to the roster

    # Reloading the page hydrates from the DB, where override/link/available never lived -
    # available in particular always comes back available (1), regardless of what was
    # sent above, since a fresh (unbranched) scenario always starts all-available.
    resp = client.get(f"/w/{token}/studio/{roster_id}")
    assert b'"optional_position_override": null' in resp.data
    assert b'"optional_player_link": null' in resp.data
    assert b'"available": 1' in resp.data


def test_save_as_creates_an_independent_roster(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client, title="Original")
    _save_roster(client, token, roster_id, SMALL_PLAYERS)

    resp = client.post(
        f"/w/{token}/studio/{roster_id}/save-as",
        json={"title": "Copy", "players": SMALL_PLAYERS[:2]},
    )
    assert resp.status_code == 200
    new_id = resp.get_json()["roster_id"]
    assert new_id != roster_id

    assert len(db_module.list_players(roster_id)) == 3
    assert len(db_module.list_players(new_id)) == 2


def test_workspaces_are_isolated_from_each_other(client, studio):
    _, db_module = studio
    token_a, roster_a = _create_roster(client, title="Workspace A roster")

    client.delete_cookie("workspace_token")
    token_b = _workspace_token(client)
    assert token_b != token_a

    # Workspace B can't see or touch workspace A's roster, even by guessing its id.
    assert client.get(f"/w/{token_b}/studio/{roster_a}").status_code == 404
    assert client.post(f"/w/{token_b}/rosters/{roster_a}/delete").status_code == 404
    resp = client.get(f"/w/{token_b}/rosters")
    assert str(roster_a).encode() not in resp.data or b"Workspace A roster" not in resp.data


def test_unknown_workspace_token_404s(client):
    assert client.get("/w/not-a-real-token/rosters").status_code == 404


def test_new_workspace_is_seeded_with_the_sample_roster(client, studio):
    _, db_module = studio
    token = _workspace_token(client)
    workspace = db_module.get_workspace_by_token(token)

    rosters = db_module.list_rosters(workspace["id"])
    assert len(rosters) == 1
    assert rosters[0]["title"] == "Sample Roster"
    assert len(db_module.list_players(rosters[0]["id"])) > 0


def test_workspace_creation_is_rate_limited_per_ip(client, studio, monkeypatch):
    app_module, _ = studio
    monkeypatch.setattr(app_module, "WORKSPACE_CREATE_LIMIT", 2)

    for _ in range(2):
        client.delete_cookie("workspace_token")
        _workspace_token(client)

    client.delete_cookie("workspace_token")
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 429


def test_blocked_cookies_get_a_helpful_page_instead_of_a_new_workspace_every_time(client, studio):
    _, db_module = studio
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302  # sets the cookie-check cookie

    client.delete_cookie("cookie_check")  # simulate a browser that never stored it
    resp = client.get("/?cc=1", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Cookies are blocked" in resp.data

    with db_module.get_connection(db_module.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM workspaces").fetchone()["n"]
    assert count == 0


def _upload_csv(client, token, csv_bytes, filename="My Team.csv"):
    return client.post(
        f"/w/{token}/rosters/upload",
        data={"file": (io.BytesIO(csv_bytes), filename)},
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def test_csv_upload_creates_a_roster_with_defaults_and_generated_ids(client, studio):
    _, db_module = studio
    token = _workspace_token(client)

    csv_bytes = (
        b"name,rank,preferred_positions,unrelated_column\n"
        b"Alice,,LW;C,ignored\n"
        b",3,RW,ignored\n"  # no name -> skipped
        b"Bob,4,RD,ignored\n"
    )
    resp = _upload_csv(client, token, csv_bytes)
    assert resp.status_code == 302
    roster_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])

    rosters = db_module.list_rosters(db_module.get_workspace_by_token(token)["id"])
    assert {r["title"] for r in rosters} == {"Sample Roster", "My Team"}

    players = db_module.list_players(roster_id)
    assert len(players) == 2

    alice = next(p for p in players if p["name"] == "Alice")
    assert alice["player_key"] == "P01"
    assert alice["experience"] == 1  # blank rank -> defaulted
    assert alice["preferred_positions"] == "LW;C"

    bob = next(p for p in players if p["name"] == "Bob")
    assert bob["player_key"] == "P02"
    assert bob["experience"] == 4


def test_csv_upload_falls_back_to_an_experience_column_when_rank_is_absent(client, studio):
    _, db_module = studio
    token = _workspace_token(client)

    resp = _upload_csv(client, token, b"name,experience\nAlice,5\n")
    roster_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    assert db_module.list_players(roster_id)[0]["experience"] == 5


def test_csv_upload_expands_position_shortcuts_and_slash_combos(client, studio):
    _, db_module = studio
    token = _workspace_token(client)

    csv_bytes = (
        b"name,preferred_positions\n"
        b"Winger,W\n"
        b"Dman,D\n"
        b"Center,C\n"
        b"Utility,U\n"
        b"Hybrid,W/C\n"
        b'MixedDelims,"LW,RW|C"\n'  # comma is a valid in-field delimiter, but must be quoted per CSV rules
    )
    resp = _upload_csv(client, token, csv_bytes)
    roster_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    players = {p["name"]: p["preferred_positions"] for p in db_module.list_players(roster_id)}

    assert players["Winger"] == "LW;RW"
    assert players["Dman"] == "LD;RD"
    assert players["Center"] == "C"
    assert players["Utility"] == "LW;C;RW;LD;RD"
    assert players["Hybrid"] == "LW;C;RW"
    assert players["MixedDelims"] == "LW;C;RW"


def test_csv_upload_without_a_name_column_shows_an_error_and_creates_nothing(client, studio):
    _, db_module = studio
    token = _workspace_token(client)

    csv_bytes = b"foo,bar\n1,2\n"
    resp = client.post(
        f"/w/{token}/rosters/upload",
        data={"file": (io.BytesIO(csv_bytes), "not_a_roster.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "upload_error" in resp.headers["Location"]
    rosters = db_module.list_rosters(db_module.get_workspace_by_token(token)["id"])
    assert [r["title"] for r in rosters] == ["Sample Roster"]  # nothing new created


def test_csv_upload_without_a_file_shows_an_error(client, studio):
    token = _workspace_token(client)
    resp = client.post(f"/w/{token}/rosters/upload", data={}, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302
    assert "upload_error" in resp.headers["Location"]


def _create_scenario(client, token, roster_id, title="Scenario 1", description="", players=None, result=None):
    return client.post(
        f"/w/{token}/studio/{roster_id}/scenarios",
        json={
            "title": title,
            "description": description,
            "players": players if players is not None else SMALL_PLAYERS,
            "forwards": 1,
            "defense": 1,
            "time_limit": 5,
            "result": result or _fake_result(),
        },
    )


def test_saving_the_roster_never_creates_a_scenario(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)

    _save_roster(client, token, roster_id, SMALL_PLAYERS[:2])
    _save_roster(client, token, roster_id, SMALL_PLAYERS)
    assert db_module.list_scenarios(roster_id) == []


def test_create_scenario_defaults_to_no_parent(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    _save_roster(client, token, roster_id, SMALL_PLAYERS)

    resp = _create_scenario(client, token, roster_id, title="Bench Bob", description="what if Bob sits")
    assert resp.status_code == 200
    scenario_id = resp.get_json()["scenario_id"]

    scenario = db_module.get_scenario(scenario_id, roster_id)
    assert scenario["title"] == "Bench Bob"
    assert scenario["parent_scenario_id"] is None

    # The roster's own players are untouched by saving a scenario.
    assert len(db_module.list_players(roster_id)) == 3


def test_create_scenario_with_a_parent_branches_it(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    parent_id = _create_scenario(client, token, roster_id, title="Parent").get_json()["scenario_id"]

    resp = client.post(
        f"/w/{token}/studio/{roster_id}/scenarios",
        json={
            "title": "Child",
            "description": "",
            "players": SMALL_PLAYERS,
            "forwards": 1,
            "defense": 1,
            "time_limit": 5,
            "result": _fake_result(),
            "parent_scenario_id": parent_id,
        },
    )
    assert resp.status_code == 200
    child_id = resp.get_json()["scenario_id"]
    assert db_module.get_scenario(child_id, roster_id)["parent_scenario_id"] == parent_id


def test_deselecting_available_persists_through_a_branch(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)

    deselected = [dict(p, available=1) for p in SMALL_PLAYERS]
    deselected[0]["available"] = 0  # Alice benched in this scenario

    parent_id = _create_scenario(client, token, roster_id, title="Parent", players=deselected).get_json()["scenario_id"]
    parent = db_module.get_scenario(parent_id, roster_id)
    assert json.loads(parent["players_json"])[0]["available"] == 0

    # Branching carries the same players (and their available flags) forward.
    branch_resp = client.post(
        f"/w/{token}/studio/{roster_id}/scenarios",
        json={
            "title": "Branch",
            "description": "",
            "players": deselected,
            "forwards": 1,
            "defense": 1,
            "time_limit": 5,
            "result": _fake_result(),
            "parent_scenario_id": parent_id,
        },
    )
    branch_id = branch_resp.get_json()["scenario_id"]
    branch = db_module.get_scenario(branch_id, roster_id)
    assert json.loads(branch["players_json"])[0]["available"] == 0


def test_create_scenario_rejects_a_parent_from_another_roster(client, studio):
    token, roster_a = _create_roster(client, title="A")
    other_scenario_id = _create_scenario(client, token, roster_a, title="A's scenario").get_json()["scenario_id"]
    _, roster_b = _create_roster(client, title="B")

    resp = client.post(
        f"/w/{token}/studio/{roster_b}/scenarios",
        json={
            "title": "Child",
            "description": "",
            "players": SMALL_PLAYERS,
            "forwards": 1,
            "defense": 1,
            "time_limit": 5,
            "result": _fake_result(),
            "parent_scenario_id": other_scenario_id,
        },
    )
    assert resp.status_code == 400


def test_updating_a_loaded_scenario_overwrites_it_in_place(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    scenario_id = _create_scenario(client, token, roster_id, title="Original", players=SMALL_PLAYERS).get_json()["scenario_id"]

    resp = client.post(
        f"/w/{token}/studio/{roster_id}/scenarios/{scenario_id}/save",
        json={
            "players": SMALL_PLAYERS[:1],
            "forwards": 2,
            "defense": 2,
            "time_limit": 10,
            "result": _fake_result("OPTIMAL"),
        },
    )
    assert resp.status_code == 200

    scenarios = db_module.list_scenarios(roster_id)
    assert len(scenarios) == 1  # updated in place, not a new row
    assert scenarios[0]["id"] == scenario_id
    assert scenarios[0]["title"] == "Original"  # title/description untouched by an update

    full = db_module.get_scenario(scenario_id, roster_id)
    assert len(json.loads(full["players_json"])) == 1
    assert json.loads(full["result_json"])["status"] == "OPTIMAL"


def test_updating_an_unknown_scenario_404s(client, studio):
    token, roster_id = _create_roster(client)
    resp = client.post(
        f"/w/{token}/studio/{roster_id}/scenarios/999/save",
        json={"players": SMALL_PLAYERS, "forwards": 3, "defense": 3, "time_limit": 5, "result": _fake_result()},
    )
    assert resp.status_code == 404


def test_create_scenario_requires_a_title(client, studio):
    token, roster_id = _create_roster(client)
    resp = _create_scenario(client, token, roster_id, title="   ")
    assert resp.status_code == 400


def test_delete_scenario_removes_it(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    scenario_id = _create_scenario(client, token, roster_id, title="Extra").get_json()["scenario_id"]

    client.post(f"/w/{token}/studio/{roster_id}/scenarios/{scenario_id}/delete")
    assert db_module.get_scenario(scenario_id, roster_id) is None


def test_deleting_a_scenario_orphans_its_children_instead_of_cascading(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    parent_id = _create_scenario(client, token, roster_id, title="Parent").get_json()["scenario_id"]
    child_resp = client.post(
        f"/w/{token}/studio/{roster_id}/scenarios",
        json={
            "title": "Child",
            "description": "",
            "players": SMALL_PLAYERS,
            "forwards": 1,
            "defense": 1,
            "time_limit": 5,
            "result": _fake_result(),
            "parent_scenario_id": parent_id,
        },
    )
    child_id = child_resp.get_json()["scenario_id"]

    client.post(f"/w/{token}/studio/{roster_id}/scenarios/{parent_id}/delete")
    assert db_module.get_scenario(parent_id, roster_id) is None
    child = db_module.get_scenario(child_id, roster_id)
    assert child is not None  # the child survives...
    assert child["parent_scenario_id"] is None  # ...just orphaned, not deleted with its parent


def test_deleting_a_roster_cascades_its_scenarios(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    _save_roster(client, token, roster_id, SMALL_PLAYERS)
    _create_scenario(client, token, roster_id, title="Extra")
    assert len(db_module.list_scenarios(roster_id)) == 1

    client.post(f"/w/{token}/rosters/{roster_id}/delete")
    assert db_module.list_scenarios(roster_id) == []


def test_roster_delete_confirm_warns_about_named_scenario_count(client, studio):
    token, roster_id = _create_roster(client, title="Has Scenarios")
    _save_roster(client, token, roster_id, SMALL_PLAYERS)
    _create_scenario(client, token, roster_id, title="A")
    _create_scenario(client, token, roster_id, title="B")

    resp = client.get(f"/w/{token}/rosters")
    assert b"permanently delete 2 saved scenarios" in resp.data


def test_compare_view_renders_selected_scenarios(client, studio):
    token, roster_id = _create_roster(client)
    scenario_id = _create_scenario(client, token, roster_id, title="What If").get_json()["scenario_id"]

    resp = client.get(f"/w/{token}/studio/{roster_id}/compare?ids={scenario_id}")
    assert resp.status_code == 200
    assert b"What If" in resp.data
    assert b"Plan B" not in resp.data  # only the requested id was included

    other_id = _create_scenario(client, token, roster_id, title="Plan B").get_json()["scenario_id"]
    resp = client.get(f"/w/{token}/studio/{roster_id}/compare?ids={scenario_id}&ids={other_id}")
    assert resp.status_code == 200
    assert b"What If" in resp.data
    assert b"Plan B" in resp.data


def test_compare_view_with_no_valid_ids_errors(client, studio):
    token, roster_id = _create_roster(client)
    resp = client.get(f"/w/{token}/studio/{roster_id}/compare?ids=999")
    assert resp.status_code == 400


def _extract_json_var(body, var_name):
    m = re.search(r"window\." + var_name + r" = (.*?);\n", body, re.DOTALL)
    assert m is not None, f"{var_name} not found on the page"
    return json.loads(m.group(1))


def test_loading_a_scenario_seeds_players_but_leaves_the_roster_untouched(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client)
    _save_roster(client, token, roster_id, SMALL_PLAYERS)

    scenario_id = _create_scenario(
        client, token, roster_id, title="Bench Bob", players=SMALL_PLAYERS[:1], result=_fake_result("OPTIMAL")
    ).get_json()["scenario_id"]

    resp = client.get(f"/w/{token}/studio/{roster_id}?load_scenario={scenario_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<span id="loaded-scenario-title">Bench Bob</span>' in body

    initial_roster = _extract_json_var(body, "INITIAL_ROSTER")
    loaded_scenario = _extract_json_var(body, "LOADED_SCENARIO")

    assert [p["id"] for p in initial_roster] == ["P1", "P2", "P3"]  # the roster's real saved players
    assert [p["id"] for p in loaded_scenario["players"]] == ["P1"]  # the scenario's snapshot
    assert loaded_scenario["result"]["status"] == "OPTIMAL"

    # Loading a scenario for viewing never modifies the roster itself.
    assert len(db_module.list_players(roster_id)) == 3


def test_loading_an_unknown_scenario_404s(client, studio):
    token, roster_id = _create_roster(client)
    resp = client.get(f"/w/{token}/studio/{roster_id}?load_scenario=999")
    assert resp.status_code == 404


def test_scenarios_list_has_a_load_link_for_every_scenario(client, studio):
    token, roster_id = _create_roster(client)
    scenario_id = _create_scenario(client, token, roster_id, title="Named").get_json()["scenario_id"]

    resp = client.get(f"/w/{token}/studio/{roster_id}/scenarios")
    body = resp.get_data(as_text=True)
    assert f"load_scenario={scenario_id}" in body


def test_compare_view_has_a_load_link_for_every_scenario(client, studio):
    token, roster_id = _create_roster(client)
    scenario_id = _create_scenario(client, token, roster_id, title="Named").get_json()["scenario_id"]
    other_id = _create_scenario(client, token, roster_id, title="Other").get_json()["scenario_id"]

    resp = client.get(f"/w/{token}/studio/{roster_id}/compare?ids={scenario_id}&ids={other_id}")
    body = resp.get_data(as_text=True)
    assert f"load_scenario={scenario_id}" in body
    assert f"load_scenario={other_id}" in body


# Editor mode (Roster vs Scenario) and auto-solve are per-browser preferences
# kept in localStorage now, not server state - see studio.js. They're covered
# by live browser checks, not here.
