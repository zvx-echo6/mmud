-- Migration 015: Track last day tick date for wall-clock daily reset
ALTER TABLE epoch ADD COLUMN last_tick_date TEXT DEFAULT NULL;
