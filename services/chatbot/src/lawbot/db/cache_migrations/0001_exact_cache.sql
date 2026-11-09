CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
);

CREATE TABLE cache_namespaces (
    namespace_id TEXT PRIMARY KEY, spec TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE cache_answers (
    answer_id TEXT PRIMARY KEY, namespace_id TEXT NOT NULL REFERENCES cache_namespaces(namespace_id),
    answer_key TEXT NOT NULL, raw_sha256 TEXT NOT NULL, answer_text TEXT NOT NULL, text_sha256 TEXT NOT NULL,
    source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','revoked')),
    reviewed_by TEXT, reviewed_at TEXT, valid_from TEXT, valid_until TEXT, expires_at TEXT,
    citation_count INTEGER NOT NULL CHECK (citation_count > 0),
    UNIQUE (namespace_id, answer_id)
);

CREATE TABLE cache_queries (
    query_id TEXT PRIMARY KEY, namespace_id TEXT NOT NULL REFERENCES cache_namespaces(namespace_id),
    answer_id TEXT NOT NULL, source_id TEXT NOT NULL, original TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
    normalized TEXT NOT NULL, normalized_sha256 TEXT NOT NULL, source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','revoked')),
    reviewed_by TEXT, reviewed_at TEXT, valid_from TEXT, valid_until TEXT, expires_at TEXT,
    exact_enabled INTEGER NOT NULL DEFAULT 0 CHECK (exact_enabled IN (0,1)),
    semantic_enabled INTEGER NOT NULL DEFAULT 0 CHECK (semantic_enabled = 0),
    FOREIGN KEY (namespace_id, answer_id) REFERENCES cache_answers(namespace_id, answer_id),
    UNIQUE (namespace_id, normalized_sha256)
);
CREATE INDEX cache_queries_answer ON cache_queries(answer_id);

CREATE TABLE cache_citations (
    answer_id TEXT NOT NULL REFERENCES cache_answers(answer_id), position INTEGER NOT NULL CHECK (position >= 0),
    article_id TEXT NOT NULL, text_sha256 TEXT NOT NULL,
    PRIMARY KEY (answer_id, position), UNIQUE (answer_id, article_id)
);

CREATE TABLE cache_control (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    mode TEXT NOT NULL CHECK (mode IN ('off','shadow','on')),
    generation INTEGER NOT NULL DEFAULT 0,
    namespace_id TEXT REFERENCES cache_namespaces(namespace_id),
    activation_event INTEGER, protection_hash TEXT, approval_id TEXT,
    CHECK (mode = 'off' OR (namespace_id IS NOT NULL AND activation_event IS NOT NULL
                           AND protection_hash IS NOT NULL AND approval_id IS NOT NULL))
);
INSERT INTO cache_control (singleton, mode) VALUES (1, 'off');

CREATE TABLE cache_audit_events (
    event_id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
    actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL
);

CREATE TRIGGER cache_answer_content_immutable
BEFORE UPDATE OF answer_id,namespace_id,answer_key,raw_sha256,answer_text,text_sha256,source,citation_count ON cache_answers
BEGIN SELECT RAISE(ABORT, 'answer revision is immutable'); END;

CREATE TRIGGER cache_citation_immutable BEFORE UPDATE ON cache_citations
BEGIN SELECT RAISE(ABORT, 'citation is immutable'); END;

CREATE TRIGGER cache_audit_no_update BEFORE UPDATE ON cache_audit_events
BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END;

CREATE TRIGGER cache_audit_no_delete BEFORE DELETE ON cache_audit_events
BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END;
