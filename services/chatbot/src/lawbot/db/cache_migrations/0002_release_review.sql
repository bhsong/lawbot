CREATE TABLE cache_render_inputs (
    answer_id TEXT PRIMARY KEY REFERENCES cache_answers(answer_id), raw_answer TEXT NOT NULL
);

CREATE TABLE cache_release_decisions (
    activation_event INTEGER NOT NULL, answer_id TEXT NOT NULL REFERENCES cache_answers(answer_id),
    previous_answer_id TEXT NOT NULL REFERENCES cache_answers(answer_id),
    release_id TEXT NOT NULL, previous_release_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('keep','pending','off','revoked')),
    reason TEXT NOT NULL, changes TEXT NOT NULL,
    PRIMARY KEY (activation_event, answer_id)
);

CREATE TABLE idempotency_keys (
    actor TEXT NOT NULL, endpoint TEXT NOT NULL, key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    status_code INTEGER NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (actor, endpoint, key)
);
