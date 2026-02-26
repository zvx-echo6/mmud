-- Migration 009: Add npc_name column to rooms for Floor 0 town NPCs
ALTER TABLE rooms ADD COLUMN npc_name TEXT;
