CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
