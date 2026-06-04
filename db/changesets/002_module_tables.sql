--liquibase formatted sql
--
-- 002 — module-specific tables that production needs in Postgres.
--
-- Most padhai modules wrote their own SQLite SCHEMA constants for
-- single-server dev mode. When the deployment runs with
-- DATABASE_URL set + PADHAI_DB_PATH unset, those tables don't
-- exist and the modules fail on first write. This changeset gives
-- Postgres a parity copy of the ten most-touched tables.
--
-- Coverage:
--   • ai_answer_provenance + ai_citations  (RAG provenance)
--   • llm_calls + llm_alerts               (cost tracking)
--   • parent_consent_tokens                 (DPDP §9)
--   • exam_packs + exam_pack_enrollments    (curriculum picker)
--   • essay_submissions                     (essay grader)
--   • mock_interviews + mock_interview_turns(mock interview)
--   • doubt_requests                        (doubt clearing)
--
-- The remaining ~55 module tables still run in SQLite (via
-- PADHAI_DB_PATH). Migrate them here as production needs each one.

--changeset padhai:002-search-path runAlways:true
SET search_path TO public;
--rollback SELECT 1;

--changeset padhai:002-ai-answer-provenance
CREATE TABLE IF NOT EXISTS ai_answer_provenance (
    id              TEXT PRIMARY KEY,
    ai_call_id      TEXT,
    user_id         TEXT,
    surface         TEXT NOT NULL,          -- 'tutor'|'lesson'|'quiz'|'essay'|'doubt'|'mock_interview'
    question_text   TEXT NOT NULL,
    answer_text     TEXT NOT NULL,
    answer_mode     TEXT NOT NULL DEFAULT 'general',
    grounded        INTEGER NOT NULL,       -- 0/1; Pg accepts INT into smallint check
    confidence      DOUBLE PRECISION,
    fallback_reason TEXT,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aap_user
    ON ai_answer_provenance(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aap_surface
    ON ai_answer_provenance(surface, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aap_grounded
    ON ai_answer_provenance(grounded, surface);
--rollback DROP TABLE IF EXISTS ai_answer_provenance CASCADE;

--changeset padhai:002-ai-citations
CREATE TABLE IF NOT EXISTS ai_citations (
    id              TEXT PRIMARY KEY,
    provenance_id   TEXT NOT NULL,
    source_kind     TEXT NOT NULL,          -- 'upload'|'question_bank'|'lesson'|'official_doc'
    source_id       TEXT NOT NULL,
    page_number     INTEGER,
    section         TEXT,
    citation_text   TEXT NOT NULL,
    relevance       DOUBLE PRECISION,
    position        INTEGER NOT NULL DEFAULT 1,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aic_prov
    ON ai_citations(provenance_id, position);
CREATE INDEX IF NOT EXISTS idx_aic_source
    ON ai_citations(source_kind, source_id);
--rollback DROP TABLE IF EXISTS ai_citations CASCADE;

--changeset padhai:002-llm-calls
CREATE TABLE IF NOT EXISTS llm_calls (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    org_id          TEXT,
    module          TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL,
    tokens_out      INTEGER NOT NULL,
    cost_inr_paise  INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    cached          INTEGER NOT NULL DEFAULT 0,
    request_id      TEXT,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_user_time ON llm_calls(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_org_time  ON llm_calls(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_module    ON llm_calls(module, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_model     ON llm_calls(model, created_at DESC);
--rollback DROP TABLE IF EXISTS llm_calls CASCADE;

--changeset padhai:002-llm-alerts
CREATE TABLE IF NOT EXISTS llm_alerts (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    day             TEXT NOT NULL,          -- 'YYYY-MM-DD' UTC
    bucket          TEXT NOT NULL,          -- '80' | '100'
    cap_paise       INTEGER NOT NULL,
    spent_paise_at_crossing INTEGER NOT NULL,
    subscription_tier TEXT,
    created_at      DOUBLE PRECISION NOT NULL,
    UNIQUE (user_id, day, bucket)
);
CREATE INDEX IF NOT EXISTS idx_llm_alerts_day
    ON llm_alerts(day, created_at DESC);
--rollback DROP TABLE IF EXISTS llm_alerts CASCADE;

--changeset padhai:002-parent-consent-tokens
CREATE TABLE IF NOT EXISTS parent_consent_tokens (
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,          -- minor's id (auth.users)
    parent_email    TEXT NOT NULL,
    issued_at       DOUBLE PRECISION NOT NULL,
    expires_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pct_user
    ON parent_consent_tokens(user_id);
--rollback DROP TABLE IF EXISTS parent_consent_tokens CASCADE;

--changeset padhai:002-parent-consent-outbox
CREATE TABLE IF NOT EXISTS parent_consent_outbox (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    parent_email    TEXT NOT NULL,
    verify_url      TEXT NOT NULL,
    queued_at       DOUBLE PRECISION NOT NULL,
    delivery        TEXT NOT NULL DEFAULT 'pending',
    delivered_at    DOUBLE PRECISION
);
--rollback DROP TABLE IF EXISTS parent_consent_outbox CASCADE;

--changeset padhai:002-exam-packs
CREATE TABLE IF NOT EXISTS exam_packs (
    code            TEXT PRIMARY KEY,
    exam_code       TEXT NOT NULL,
    title           TEXT NOT NULL,
    year            INTEGER,
    description     TEXT,
    syllabus_url    TEXT,
    pattern_summary TEXT,
    cutoff_summary  TEXT,
    estimated_hours INTEGER,
    status          TEXT NOT NULL DEFAULT 'active',
    enrollment_count INTEGER NOT NULL DEFAULT 0,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epacks_exam
    ON exam_packs(exam_code, status);
--rollback DROP TABLE IF EXISTS exam_packs CASCADE;

--changeset padhai:002-exam-pack-enrollments
CREATE TABLE IF NOT EXISTS exam_pack_enrollments (
    id              TEXT PRIMARY KEY,
    pack_code       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    target_date     DOUBLE PRECISION,
    daily_minutes   INTEGER NOT NULL DEFAULT 60,
    status          TEXT NOT NULL DEFAULT 'active',
    enrolled_at     DOUBLE PRECISION NOT NULL,
    completed_at    DOUBLE PRECISION,
    UNIQUE (pack_code, user_id)
);
CREATE INDEX IF NOT EXISTS idx_epe_user
    ON exam_pack_enrollments(user_id, status);
--rollback DROP TABLE IF EXISTS exam_pack_enrollments CASCADE;

--changeset padhai:002-essay-rubrics
CREATE TABLE IF NOT EXISTS essay_rubrics (
    id              TEXT PRIMARY KEY,
    exam            TEXT NOT NULL,
    paper           TEXT NOT NULL,
    topic           TEXT,
    criteria_json   TEXT NOT NULL,
    max_marks       INTEGER NOT NULL,
    model_answer    TEXT,
    created_by      TEXT,
    created_at      DOUBLE PRECISION NOT NULL,
    UNIQUE (exam, paper, topic)
);
CREATE INDEX IF NOT EXISTS idx_rubric_exam ON essay_rubrics(exam, paper);
--rollback DROP TABLE IF EXISTS essay_rubrics CASCADE;

--changeset padhai:002-essay-submissions
CREATE TABLE IF NOT EXISTS essay_submissions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    rubric_id       TEXT NOT NULL,
    text            TEXT NOT NULL,
    ai_score        DOUBLE PRECISION,
    ai_feedback_json TEXT,
    ai_call_id      TEXT,
    human_reviewed  INTEGER NOT NULL DEFAULT 0,
    human_score     DOUBLE PRECISION,
    human_note      TEXT,
    submitted_at    DOUBLE PRECISION NOT NULL,
    graded_at       DOUBLE PRECISION,
    reviewed_at     DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_essub_user ON essay_submissions(user_id, submitted_at DESC);
--rollback DROP TABLE IF EXISTS essay_submissions CASCADE;

--changeset padhai:002-mock-interviews
CREATE TABLE IF NOT EXISTS mock_interviews (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    track           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'in_progress',
    started_at      DOUBLE PRECISION NOT NULL,
    ended_at        DOUBLE PRECISION,
    overall_score   DOUBLE PRECISION,
    summary_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mi_user ON mock_interviews(user_id, started_at DESC);
--rollback DROP TABLE IF EXISTS mock_interviews CASCADE;

--changeset padhai:002-mock-interview-turns
CREATE TABLE IF NOT EXISTS mock_interview_turns (
    id              TEXT PRIMARY KEY,
    interview_id    TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    question_text   TEXT NOT NULL,
    answer_text     TEXT,
    answer_audio_url TEXT,
    feedback_json   TEXT,
    ai_call_id      TEXT,
    created_at      DOUBLE PRECISION NOT NULL,
    answered_at     DOUBLE PRECISION,
    UNIQUE (interview_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_mit_interview ON mock_interview_turns(interview_id, turn_index);
--rollback DROP TABLE IF EXISTS mock_interview_turns CASCADE;

--changeset padhai:002-doubt-requests
CREATE TABLE IF NOT EXISTS doubt_requests (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    org_id            TEXT,
    subject           TEXT,
    question_text     TEXT NOT NULL,
    image_url         TEXT,
    audio_url         TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    assigned_tutor_id TEXT,
    claimed_at        DOUBLE PRECISION,
    response_text     TEXT,
    response_image_url TEXT,
    response_audio_url TEXT,
    response_method   TEXT,
    response_at       DOUBLE PRECISION,
    ai_call_id        TEXT,
    created_at        DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doubt_user_time
    ON doubt_requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doubt_status_age
    ON doubt_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_doubt_tutor
    ON doubt_requests(assigned_tutor_id, status);
--rollback DROP TABLE IF EXISTS doubt_requests CASCADE;
