CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Client IP at creation time, kept only to throttle mass workspace
    -- creation (see studio/app.py's WORKSPACE_CREATE_LIMIT). Not used for
    -- anything else, and never shown to users.
    client_ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_workspaces_client_ip_created_at ON workspaces(client_ip, created_at);

CREATE TABLE IF NOT EXISTS rosters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rosters_workspace_id ON rosters(workspace_id);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_id INTEGER NOT NULL REFERENCES rosters(id) ON DELETE CASCADE,
    player_key TEXT NOT NULL,
    name TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    experience INTEGER NOT NULL,
    preferred_positions TEXT NOT NULL DEFAULT '',
    secondary_positions TEXT NOT NULL DEFAULT '',
    unwilling_positions TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_players_roster_id ON players(roster_id);

-- A scenario is an immutable snapshot: a roster's players + solve settings
-- at some point in time, plus the solve result that snapshot produced. It's
-- never edited in place - if you want to iterate, go back to the roster,
-- change it, and save a new scenario.
--
-- Every roster always has at most one is_baseline=1 scenario, kept in sync
-- by "Save to roster": it represents the roster's own current persisted
-- state as just another scenario, so a comparison view never needs to
-- special-case "the roster itself" vs. "a named scenario."
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_id INTEGER NOT NULL REFERENCES rosters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_baseline INTEGER NOT NULL DEFAULT 0,
    forwards INTEGER NOT NULL,
    defense INTEGER NOT NULL,
    time_limit INTEGER NOT NULL,
    -- Bumped only when solver.py's algorithm changes in a way that could
    -- change results for the same inputs; 0 until that first happens. Lets a
    -- future comparison view flag scenarios computed under an older
    -- algorithm instead of silently treating them as current.
    algo_version INTEGER NOT NULL DEFAULT 0,
    players_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenarios_roster_id ON scenarios(roster_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenarios_one_baseline_per_roster
    ON scenarios(roster_id) WHERE is_baseline = 1;
