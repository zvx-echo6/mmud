-- LLM backend configuration (runtime-editable from admin panel)
CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    backend TEXT NOT NULL DEFAULT 'dummy',  -- dummy, anthropic, openai, google
    api_key TEXT DEFAULT '',
    model TEXT DEFAULT '',
    base_url TEXT DEFAULT '',               -- OpenAI-compatible endpoints only
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT ''
);
INSERT OR IGNORE INTO llm_config (id, backend) VALUES (1, 'dummy');
