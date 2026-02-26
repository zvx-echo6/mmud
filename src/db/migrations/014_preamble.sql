-- Epoch preamble: rich prose header for the web dashboard (web-only, no char limit)
ALTER TABLE epoch ADD COLUMN preamble TEXT DEFAULT '';
