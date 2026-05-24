--liquibase formatted sql

--changeset padhai:000-search-path runAlways:true
SET search_path TO public;
--rollback SELECT 1;

--changeset padhai:001-pgcrypto
CREATE EXTENSION IF NOT EXISTS pgcrypto;
--rollback SELECT 1;

--changeset padhai:002-users
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT,
    external_id         TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    subscription_tier   TEXT NOT NULL DEFAULT 'M1',
    subscription_level  TEXT NOT NULL DEFAULT 'L3'
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id
    ON users (external_id) WHERE external_id IS NOT NULL;
--rollback DROP TABLE IF EXISTS users CASCADE;

--changeset padhai:003-lessons
CREATE TABLE IF NOT EXISTS lessons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_hash      TEXT NOT NULL,
    language_code   TEXT NOT NULL,
    level           TEXT NOT NULL,
    model           TEXT NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (image_hash, language_code, level, model)
);
CREATE INDEX IF NOT EXISTS idx_lessons_lookup
    ON lessons (image_hash, language_code, level, model);
--rollback DROP TABLE IF EXISTS lessons CASCADE;

--changeset padhai:004-audio-clips
CREATE TABLE IF NOT EXISTS audio_clips (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text_hash       TEXT NOT NULL,
    language_code   TEXT NOT NULL,
    provider        TEXT NOT NULL,
    storage_key     TEXT NOT NULL,
    duration_s      NUMERIC(10, 3),
    size_bytes      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (text_hash, language_code, provider)
);
--rollback DROP TABLE IF EXISTS audio_clips CASCADE;

--changeset padhai:005-videos
CREATE TABLE IF NOT EXISTS videos (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_hash              TEXT NOT NULL,
    language_code           TEXT NOT NULL,
    level                   TEXT NOT NULL,
    theme                   TEXT NOT NULL,
    talking_head_provider   TEXT NOT NULL,
    render_mode             TEXT NOT NULL,
    storage_key             TEXT NOT NULL,
    duration_s              NUMERIC(10, 3),
    size_bytes              BIGINT,
    view_count              BIGINT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_served_at          TIMESTAMPTZ,
    UNIQUE (image_hash, language_code, level, theme,
            talking_head_provider, render_mode)
);
CREATE INDEX IF NOT EXISTS idx_videos_lookup
    ON videos (image_hash, language_code, level, theme,
               talking_head_provider, render_mode);
CREATE INDEX IF NOT EXISTS idx_videos_popular
    ON videos (view_count DESC, last_served_at DESC);
--rollback DROP TABLE IF EXISTS videos CASCADE;

--changeset padhai:006-jobs
CREATE TABLE IF NOT EXISTS jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload     JSONB NOT NULL,
    result      JSONB,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user
    ON jobs (user_id, created_at DESC);
--rollback DROP TABLE IF EXISTS jobs CASCADE;

--changeset padhai:007-usage-daily
CREATE TABLE IF NOT EXISTS usage_daily (
    user_id              UUID REFERENCES users(id) ON DELETE CASCADE,
    day                  DATE NOT NULL,
    videos_generated     INTEGER NOT NULL DEFAULT 0,
    videos_cached        INTEGER NOT NULL DEFAULT 0,
    minutes_generated    NUMERIC(10, 2) NOT NULL DEFAULT 0,
    cost_inr             NUMERIC(10, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
--rollback DROP TABLE IF EXISTS usage_daily CASCADE;
