-- Add reveal columns to rooms, player_reveals tracking, and spell_names to epoch
ALTER TABLE rooms ADD COLUMN reveal_gold INTEGER DEFAULT 0;
ALTER TABLE rooms ADD COLUMN reveal_lore TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS player_reveals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    revealed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, room_id)
);

ALTER TABLE epoch ADD COLUMN spell_names TEXT DEFAULT '';
