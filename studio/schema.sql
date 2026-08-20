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
