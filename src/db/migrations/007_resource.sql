-- Add class resource columns (Focus/Tricks/Mana, max 5)
ALTER TABLE players ADD COLUMN resource INTEGER DEFAULT 5;
ALTER TABLE players ADD COLUMN resource_max INTEGER DEFAULT 5;
