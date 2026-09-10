-- Initial schema for the attendance control system (PostgreSQL).
-- Mirrors app/models.py. Safe for a greenfield database.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'funcionario',
    password_hash VARCHAR(60),
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    supervisor_id INTEGER REFERENCES users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shift_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    start_time VARCHAR(5) NOT NULL,
    end_time VARCHAR(5) NOT NULL,
    weekday_mask VARCHAR(7) NOT NULL DEFAULT '1111100',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assigned_shifts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    template_id INTEGER NOT NULL REFERENCES shift_templates (id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users (id),
    event_type VARCHAR(16) NOT NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'qr',
    outcome VARCHAR(32) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    token_hash VARCHAR(64),
    shift_id INTEGER REFERENCES assigned_shifts (id),
    is_extra BOOLEAN NOT NULL DEFAULT FALSE,
    delay_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(64) PRIMARY KEY,
    value VARCHAR(255) NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_token_hash
    ON attendance_events (token_hash);
CREATE INDEX IF NOT EXISTS idx_events_user_ts
    ON attendance_events (user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_outcome
    ON attendance_events (outcome);
CREATE INDEX IF NOT EXISTS idx_users_supervisor
    ON users (supervisor_id);