CREATE TABLE consults (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    user_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer_shown TEXT,
    answer_verdict TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'cancelled')),
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('not_prepared', 'interrupted', 'sent')),
    error_type TEXT,
    retention_policy TEXT NOT NULL,
    app_commit TEXT,
    corpus_release_id TEXT,
    writer_system_sha256 TEXT,
    feedback_turn_id TEXT,
    redaction_count INTEGER NOT NULL,
    redaction_reasons TEXT NOT NULL
) STRICT;

CREATE INDEX consults_time ON consults (created_at DESC, id DESC);
CREATE INDEX consults_by_user ON consults (user_hash, created_at DESC, id DESC);
CREATE INDEX consults_by_status ON consults (status, created_at DESC, id DESC);
CREATE INDEX consults_by_request ON consults (request_id);

CREATE TABLE consult_steps (
    consult_id TEXT NOT NULL REFERENCES consults(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (
        'router', 'search', 'rerank', 'decree_search', 'decree_rerank', 'definitions',
        'clarify', 'writer', 'citation', 'display'
    )),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    result TEXT NOT NULL,
    redaction_count INTEGER NOT NULL,
    redaction_reasons TEXT NOT NULL,
    PRIMARY KEY (consult_id, seq)
) STRICT, WITHOUT ROWID;

CREATE TABLE model_calls (
    consult_id TEXT NOT NULL REFERENCES consults(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('embedding', 'rerank', 'router_select', 'router_label', 'clarify', 'writer')),
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    model_id TEXT,
    model_revision TEXT,
    request_body BLOB NOT NULL,
    send_status TEXT NOT NULL CHECK (send_status IN ('sent', 'failed_before_response', 'unknown')),
    status_code INTEGER,
    response_body BLOB,
    response_streaming INTEGER NOT NULL CHECK (response_streaming IN (0, 1)),
    response_decoded INTEGER NOT NULL CHECK (response_decoded IN (0, 1)),
    started_at TEXT NOT NULL,
    first_byte_at TEXT,
    ended_at TEXT NOT NULL,
    usage TEXT,
    error_type TEXT,
    redaction_count INTEGER NOT NULL,
    redaction_reasons TEXT NOT NULL,
    CHECK (response_streaming = 0 OR response_body IS NULL),
    PRIMARY KEY (consult_id, seq)
) STRICT, WITHOUT ROWID;

CREATE TABLE model_call_chunks (
    consult_id TEXT NOT NULL,
    call_seq INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    offset_ns INTEGER NOT NULL,
    body BLOB NOT NULL,
    FOREIGN KEY (consult_id, call_seq) REFERENCES model_calls(consult_id, seq) ON DELETE CASCADE,
    PRIMARY KEY (consult_id, call_seq, seq)
) STRICT, WITHOUT ROWID;

CREATE TABLE consult_trace_deletions (
    id INTEGER PRIMARY KEY,
    deleted_at TEXT NOT NULL,
    admin_hash TEXT NOT NULL,
    conditions TEXT NOT NULL,
    consult_count INTEGER NOT NULL,
    step_count INTEGER NOT NULL,
    call_count INTEGER NOT NULL
) STRICT;
