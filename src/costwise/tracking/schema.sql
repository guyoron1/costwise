-- Costwise tracking schema

CREATE TABLE IF NOT EXISTS routing_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    session_id      TEXT,
    request_model   TEXT    NOT NULL,
    routed_model    TEXT,
    tier            TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    cost_usd        REAL,
    saved_usd       REAL,
    latency_ms      REAL,
    classification  TEXT,
    provider        TEXT,
    endpoint        TEXT    NOT NULL,
    status_code     INTEGER,
    error           TEXT,
    tokens_pruned   INTEGER,
    messages_pruned INTEGER
);

CREATE TABLE IF NOT EXISTS provider_health (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    provider        TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    latency_ms      REAL,
    status_code     INTEGER,
    rate_limited    INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS budget_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    alert_type      TEXT    NOT NULL,
    threshold_usd   REAL,
    current_usd     REAL,
    action_taken    TEXT
);

CREATE TABLE IF NOT EXISTS retry_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    session_id           TEXT    NOT NULL,
    original_request_id  INTEGER NOT NULL,
    retry_request_id     INTEGER NOT NULL,
    content_hash         TEXT    NOT NULL,
    similarity_score     REAL    NOT NULL,
    original_tier        TEXT    NOT NULL,
    original_model       TEXT    NOT NULL,
    time_delta_s         REAL    NOT NULL,
    was_downgraded       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS threshold_adjustments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    field               TEXT    NOT NULL,
    old_value           REAL    NOT NULL,
    new_value           REAL    NOT NULL,
    reason              TEXT    NOT NULL,
    retry_event_id      INTEGER,
    window_retry_rate   REAL,
    window_requests     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_routing_timestamp ON routing_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_routing_session ON routing_decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_health_provider ON provider_health(provider, timestamp);
CREATE INDEX IF NOT EXISTS idx_retry_session ON retry_events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_retry_timestamp ON retry_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_threshold_adj_timestamp ON threshold_adjustments(timestamp);
