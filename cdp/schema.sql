-- ============================================================
-- ELEMPLEO AI GROWTH ENGINE — CDP Schema (POC)
-- ============================================================

-- ── Extensiones ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- búsqueda fuzzy en texto

-- ── USERS ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE,
    phone           VARCHAR(20),
    full_name       VARCHAR(255),
    source          VARCHAR(50) NOT NULL DEFAULT 'organic',
    -- 'organic' | 'whatsapp' | 'referral' | 'paid_meta' | 'paid_google' | 'seo'

    -- Perfil laboral
    current_title   VARCHAR(255),
    current_company VARCHAR(255),
    experience_years SMALLINT DEFAULT 0,
    education_level VARCHAR(50),
    -- 'bachillerato' | 'tecnico' | 'tecnologo' | 'profesional' | 'especializacion' | 'maestria' | 'doctorado'
    city            VARCHAR(100),
    desired_salary  INTEGER,  -- en COP
    skills          TEXT[],   -- array de skills
    profile_data    JSONB DEFAULT '{}',

    -- Estado
    profile_completion SMALLINT DEFAULT 0,  -- 0-100%
    is_active       BOOLEAN DEFAULT TRUE,
    last_active_at  TIMESTAMPTZ,
    onboarding_completed BOOLEAN DEFAULT FALSE,

    -- Tracking
    referrer_id     UUID REFERENCES users(id),
    utm_source      VARCHAR(100),
    utm_medium      VARCHAR(100),
    utm_campaign    VARCHAR(100),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── JOBS ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id     VARCHAR(100) UNIQUE,  -- ID en elempleo (cuando se integre)
    title           VARCHAR(255) NOT NULL,
    company         VARCHAR(255) NOT NULL,
    city            VARCHAR(100),
    country         VARCHAR(50) DEFAULT 'Colombia',
    description     TEXT,
    requirements    TEXT,
    benefits        TEXT,
    salary_min      INTEGER,   -- en COP
    salary_max      INTEGER,
    salary_currency VARCHAR(10) DEFAULT 'COP',
    contract_type   VARCHAR(50),
    -- 'indefinido' | 'fijo' | 'prestacion_servicios' | 'practicas'
    modality        VARCHAR(50),
    -- 'presencial' | 'remoto' | 'hibrido'
    experience_years SMALLINT,
    education_level VARCHAR(50),
    category        VARCHAR(100),
    -- 'tecnologia' | 'finanzas' | 'marketing' | 'operaciones' | 'salud' | 'educacion' | 'ventas' | 'rrhh'
    skills_required TEXT[],
    is_active       BOOLEAN DEFAULT TRUE,

    -- Vector DB reference
    vector_id       VARCHAR(100),  -- ID del embedding en Qdrant

    -- Métricas
    view_count      INTEGER DEFAULT 0,
    application_count INTEGER DEFAULT 0,

    published_at    TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── APPLICATIONS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applications (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status          VARCHAR(50) DEFAULT 'applied',
    -- 'applied' | 'viewed_by_employer' | 'shortlisted' | 'rejected' | 'hired'
    source          VARCHAR(50),
    -- 'recommendation' | 'search' | 'whatsapp' | 'email'
    match_score     NUMERIC(4,3),  -- 0.000 - 1.000
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

-- ── EVENTS (CDP Core) ─────────────────────────────────────────
-- Tabla append-only: NUNCA actualizar, solo insertar
CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    session_id      UUID,
    event_type      VARCHAR(100) NOT NULL,
    -- Estándar: user.registered | user.logged_in | user.profile_updated
    --           job.viewed | job.applied | job.saved
    --           whatsapp.message_received | whatsapp.message_sent
    --           email.sent | email.opened | email.clicked
    --           agent.triggered | agent.completed
    --           trend.detected | demand.signal
    agent_id        VARCHAR(100),  -- qué agente generó el evento
    properties      JSONB DEFAULT '{}',  -- datos específicos del evento
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── AGENT LOGS ────────────────────────────────────────────────
-- Registro de cada llamada LLM por agente (para tracking de costos)
CREATE TABLE IF NOT EXISTS agent_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id        VARCHAR(100) NOT NULL,
    task_type       VARCHAR(50),
    -- 'generation' | 'classification' | 'extraction' | 'reasoning'
    model_used      VARCHAR(100) NOT NULL,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    cost_usd        NUMERIC(10,6),
    latency_ms      INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    error_message   TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── ONBOARDING SEQUENCES ──────────────────────────────────────
-- Estado de la secuencia de 72h del Early Activation Agent
CREATE TABLE IF NOT EXISTS onboarding_sequences (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step            VARCHAR(50) NOT NULL,
    -- 'welcome' | 'cv_tip' | 'employer_signal' | 'reminder'
    scheduled_at    TIMESTAMPTZ NOT NULL,
    sent_at         TIMESTAMPTZ,
    channel         VARCHAR(20) NOT NULL,
    -- 'email' | 'whatsapp' | 'push'
    status          VARCHAR(20) DEFAULT 'pending',
    -- 'pending' | 'sent' | 'failed' | 'skipped'
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── REFERRALS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS referrals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    referrer_id     UUID NOT NULL REFERENCES users(id),
    referee_id      UUID REFERENCES users(id),
    referral_code   VARCHAR(20) UNIQUE NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',
    -- 'pending' | 'registered' | 'activated' | 'rewarded'
    reward_amount   INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    converted_at    TIMESTAMPTZ
);

-- ── ÍNDICES ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_source ON users(source);
CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at);
CREATE INDEX IF NOT EXISTS idx_users_city ON users(city);

CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_title_trgm ON jobs USING GIN (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);

CREATE INDEX IF NOT EXISTS idx_agent_logs_agent ON agent_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created ON agent_logs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_user ON onboarding_sequences(user_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_scheduled ON onboarding_sequences(scheduled_at)
    WHERE status = 'pending';

-- ── FUNCIÓN auto-update updated_at ────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_applications_updated_at
    BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
