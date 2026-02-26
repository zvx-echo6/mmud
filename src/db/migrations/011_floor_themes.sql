-- Floor sub-themes: per-epoch floor identity with narrative descent arc
CREATE TABLE IF NOT EXISTS floor_themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor INTEGER NOT NULL,
    floor_name TEXT NOT NULL,
    atmosphere TEXT NOT NULL,
    narrative_beat TEXT NOT NULL,
    floor_transition TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_floor_themes_floor ON floor_themes(floor);
