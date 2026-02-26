-- Death log table for NPC memory (Maren remembers player deaths)
CREATE TABLE IF NOT EXISTS death_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    floor INTEGER NOT NULL,
    monster_name TEXT NOT NULL,
    died_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_death_log_player ON death_log(player_id);
