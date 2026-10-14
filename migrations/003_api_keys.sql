-- migrations/003_api_keys.sql
-- Self-service API key management schema.

CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    key_prefix  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ
);

-- Index for listing keys by owner
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id
    ON api_keys (user_id);

-- Unique index for authenticating incoming API requests via key_hash
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash
    ON api_keys (key_hash);
