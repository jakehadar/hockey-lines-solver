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
    experience INTEGER NOT NULL,
    preferred_positions TEXT NOT NULL DEFAULT '',
    secondary_positions TEXT NOT NULL DEFAULT '',
    unwilling_positions TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_players_roster_id ON players(roster_id);

-- A scenario is an immutable snapshot: a roster's players + solve settings
-- at some point in time, plus the solve result that snapshot produced.
-- Scenarios are never edited in place - "saving" a loaded scenario replaces
-- its own row, but changing course creates a new one (a "branch") via
-- parent_scenario_id, which points at the scenario it was branched from.
-- A scenario created with nothing loaded (not a branch of anything) has a
-- null parent; roster_id alone is enough to list a roster's scenarios, so
-- there's no need for a special "baseline" scenario to anchor them to.
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_id INTEGER NOT NULL REFERENCES rosters(id) ON DELETE CASCADE,
    parent_scenario_id INTEGER REFERENCES scenarios(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    forwards INTEGER NOT NULL,
    defense INTEGER NOT NULL,
    time_limit INTEGER NOT NULL,
    -- Whether a position neither preferred nor secondary (nor unwilling) for
    -- a player could be used to fill a slot at all - see solver.py's
    -- allow_oop. Nullable so old rows (saved before this existed) fall back
    -- to the historical behavior (1, i.e. true) rather than NULL/0.
    allow_oop INTEGER NOT NULL DEFAULT 1,
    -- Whether optional_position_override may force a player onto a position
    -- they marked unwilling - see solver.py's allow_unwilling. Unlike
    -- allow_oop, this defaults to 0 (forbidden) for brand-new rows: unwilling
    -- is a stronger signal than an untagged position, so overriding it needs
    -- explicit opt-in. (The migration path for pre-existing databases
    -- backfills existing rows as 1 instead - see db.py's _migrate() - since
    -- historically an override into an unwilling position was always
    -- honored unconditionally, before this column existed to gate it.)
    allow_unwilling INTEGER NOT NULL DEFAULT 0,
    -- Bumped only when solver.py's algorithm changes in a way that could
    -- change results for the same inputs; 0 until that first happens. Lets a
    -- future comparison view flag scenarios computed under an older
    -- algorithm instead of silently treating them as current.
    algo_version INTEGER NOT NULL DEFAULT 0,
    players_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    -- Cached degrees-of-freedom analysis (see dof.py), computed client-side
    -- and sent along at save time rather than recomputed server-side - it's
    -- many times more expensive than a single solve. Null for scenarios
    -- saved before this existed, or saved before the analysis finished.
    dof_json TEXT,
    -- Ordered list of {key, enabled} objects (see schemas.ObjectiveSetting)
    -- describing the solver's priority order for this snapshot - order is
    -- itself meaningful data, so it's one JSON blob rather than a column per
    -- objective. Null for rows saved before this existed; those were solved
    -- under the historical fixed order, so they're treated as
    -- schemas.DEFAULT_OBJECTIVES (assigned > preference > balance, all
    -- enabled) wherever this is read.
    objectives_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_scenarios_roster_id ON scenarios(roster_id);
CREATE INDEX IF NOT EXISTS idx_scenarios_parent_scenario_id ON scenarios(parent_scenario_id);
