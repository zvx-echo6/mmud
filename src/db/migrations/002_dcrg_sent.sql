-- Migration 002: Add dcrg_sent column to broadcasts table
-- Tracks whether a broadcast has been sent via the DCRG node
ALTER TABLE broadcasts ADD COLUMN dcrg_sent INTEGER DEFAULT 0;
