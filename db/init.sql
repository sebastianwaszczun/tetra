CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS campaigns (
    id            BIGSERIAL PRIMARY KEY,
    label         TEXT,
    method        TEXT,
    summary       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
