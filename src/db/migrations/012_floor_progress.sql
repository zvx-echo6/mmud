-- Floor progress tracking for boss gates and fast travel
CREATE TABLE IF NOT EXISTS floor_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    floor INTEGER NOT NULL,
    boss_killed INTEGER DEFAULT 0,
    boss_killed_at DATETIME,
    UNIQUE(player_id, floor)
);
CREATE INDEX IF NOT EXISTS idx_floor_progress_player ON floor_progress(player_id);

-- Track deepest floor each player has unlocked for fast travel
ALTER TABLE players ADD COLUMN deepest_floor_reached INTEGER DEFAULT 1;
