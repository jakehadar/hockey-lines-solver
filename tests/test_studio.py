import importlib

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
    client.post(f"/w/{token}/studio/{roster_id}/save", json={"players": SMALL_PLAYERS})
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
    _, db_module = studio
    token, roster_id = _create_roster(client)

    players = [
        {
            "id": "P1",
            "name": "Alice",
            "available": 1,
            "experience": 3,
            "preferred_positions": ["LW"],
            "secondary_positions": ["RW"],
            "unwilling_positions": ["C"],
            "optional_position_override": "LW",
            "optional_player_link": "P2",
        }
    ]
    resp = client.post(f"/w/{token}/studio/{roster_id}/save", json={"players": players})
    assert resp.status_code == 200

    rows = db_module.list_players(roster_id)
    assert len(rows) == 1
    assert rows[0]["unwilling_positions"] == "C"

    # Reloading the page hydrates from the DB, where override/link never lived.
    resp = client.get(f"/w/{token}/studio/{roster_id}")
    assert b'"optional_position_override": null' in resp.data
    assert b'"optional_player_link": null' in resp.data


def test_save_as_creates_an_independent_roster(client, studio):
    _, db_module = studio
    token, roster_id = _create_roster(client, title="Original")
    client.post(f"/w/{token}/studio/{roster_id}/save", json={"players": SMALL_PLAYERS})

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
