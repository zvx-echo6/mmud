-- Epoch announcement messages (JSON array of 3 strings)
ALTER TABLE epoch ADD COLUMN announcements TEXT DEFAULT '';
