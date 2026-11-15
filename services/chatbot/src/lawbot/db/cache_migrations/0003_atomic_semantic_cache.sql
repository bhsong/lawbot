CREATE TABLE atoms (
    atom_revision_id TEXT PRIMARY KEY NOT NULL, atom_id TEXT NOT NULL,
    namespace_id TEXT NOT NULL REFERENCES cache_namespaces(namespace_id),
    corpus_snapshot_id TEXT NOT NULL, parent_chunk_id TEXT NOT NULL, parent_sha256 TEXT NOT NULL,
    proposition TEXT NOT NULL, source_start INTEGER NOT NULL CHECK (source_start >= 0),
    source_end INTEGER NOT NULL CHECK (source_end > source_start),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','revoked')),
    created_at TEXT NOT NULL, created_by TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT,
    UNIQUE (atom_revision_id, namespace_id)
);
CREATE INDEX atoms_snapshot ON atoms(namespace_id, corpus_snapshot_id);

CREATE TABLE atomic_questions (
    query_revision_id TEXT PRIMARY KEY NOT NULL, query_id TEXT NOT NULL,
    atom_revision_id TEXT NOT NULL REFERENCES atoms(atom_revision_id),
    question TEXT NOT NULL, question_sha256 TEXT NOT NULL, expression_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','revoked')),
    created_at TEXT NOT NULL, created_by TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT,
    UNIQUE (query_revision_id, atom_revision_id), UNIQUE (atom_revision_id, question_sha256)
);

CREATE TABLE semantic_reuse_policies (
    policy_id TEXT NOT NULL, version TEXT NOT NULL, conditions TEXT NOT NULL CHECK (json_valid(conditions)),
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    PRIMARY KEY (policy_id, version)
);

CREATE TABLE atom_answer_bindings (
    binding_id TEXT PRIMARY KEY NOT NULL, namespace_id TEXT NOT NULL,
    atom_revision_id TEXT NOT NULL, query_revision_id TEXT NOT NULL, answer_id TEXT NOT NULL,
    policy_id TEXT NOT NULL, policy_version TEXT NOT NULL,
    candidate_query_id TEXT REFERENCES cache_queries(query_id),
    activation_event INTEGER, semantic_enabled INTEGER NOT NULL DEFAULT 0 CHECK (semantic_enabled IN (0,1)),
    reviewed_at TEXT, reviewed_by TEXT, valid_from TEXT, valid_until TEXT,
    carry_status TEXT NOT NULL DEFAULT 'off' CHECK (carry_status IN ('keep','pending','off')),
    CHECK (semantic_enabled = 0 OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)),
    FOREIGN KEY (atom_revision_id, namespace_id) REFERENCES atoms(atom_revision_id, namespace_id),
    FOREIGN KEY (query_revision_id, atom_revision_id) REFERENCES atomic_questions(query_revision_id, atom_revision_id),
    FOREIGN KEY (namespace_id, answer_id) REFERENCES cache_answers(namespace_id, answer_id),
    FOREIGN KEY (policy_id, policy_version) REFERENCES semantic_reuse_policies(policy_id, version),
    UNIQUE (query_revision_id, answer_id, policy_id, policy_version)
);
CREATE INDEX atom_answer_bindings_answer ON atom_answer_bindings(answer_id);

CREATE TABLE semantic_binding_decisions (
    binding_id TEXT NOT NULL REFERENCES atom_answer_bindings(binding_id),
    activation_event INTEGER NOT NULL, previous_binding_id TEXT REFERENCES atom_answer_bindings(binding_id),
    from_snapshot_id TEXT NOT NULL, to_snapshot_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('keep','pending','off')),
    reason TEXT NOT NULL, changes TEXT NOT NULL CHECK (json_valid(changes)),
    actor TEXT NOT NULL, decided_at TEXT NOT NULL,
    PRIMARY KEY (binding_id, activation_event)
);

CREATE TABLE semantic_answer_evidence (
    answer_id TEXT NOT NULL REFERENCES cache_answers(answer_id),
    kind TEXT NOT NULL CHECK (kind IN ('dependency','generation_context')),
    position INTEGER NOT NULL CHECK (position >= 0), article_id TEXT NOT NULL, text_sha256 TEXT NOT NULL,
    PRIMARY KEY (answer_id, kind, position), UNIQUE (answer_id, kind, article_id)
);

CREATE TABLE semantic_index_manifests (
    index_revision TEXT PRIMARY KEY NOT NULL,
    namespace_id TEXT NOT NULL REFERENCES cache_namespaces(namespace_id), corpus_snapshot_id TEXT NOT NULL,
    embedding_fingerprint TEXT NOT NULL, query_revisions TEXT NOT NULL CHECK (json_valid(query_revisions)),
    row_count INTEGER NOT NULL CHECK (row_count > 0), dimension INTEGER NOT NULL CHECK (dimension > 0),
    vectors_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
    CHECK (json_type(query_revisions) = 'array' AND json_array_length(query_revisions) = row_count)
);

CREATE TRIGGER atoms_revision_immutable
BEFORE UPDATE OF atom_revision_id,atom_id,namespace_id,corpus_snapshot_id,parent_chunk_id,parent_sha256,
    proposition,source_start,source_end,created_at,created_by ON atoms
BEGIN SELECT RAISE(ABORT, 'atom revision is immutable'); END;

CREATE TRIGGER atomic_questions_revision_immutable
BEFORE UPDATE OF query_revision_id,query_id,atom_revision_id,question,question_sha256,expression_type,created_at,created_by
ON atomic_questions
BEGIN SELECT RAISE(ABORT, 'atomic question revision is immutable'); END;

CREATE TRIGGER semantic_policy_revision_immutable BEFORE UPDATE ON semantic_reuse_policies
BEGIN SELECT RAISE(ABORT, 'reuse policy revision is immutable'); END;

CREATE TRIGGER semantic_binding_identity_immutable
BEFORE UPDATE OF binding_id,namespace_id,atom_revision_id,query_revision_id,answer_id,policy_id,policy_version,candidate_query_id
ON atom_answer_bindings
BEGIN SELECT RAISE(ABORT, 'binding identity is immutable'); END;

CREATE TRIGGER semantic_binding_decisions_no_update BEFORE UPDATE ON semantic_binding_decisions
BEGIN SELECT RAISE(ABORT, 'binding decisions are append-only'); END;

CREATE TRIGGER semantic_binding_decisions_no_delete BEFORE DELETE ON semantic_binding_decisions
BEGIN SELECT RAISE(ABORT, 'binding decisions are append-only'); END;

CREATE TRIGGER semantic_index_manifest_immutable BEFORE UPDATE ON semantic_index_manifests
BEGIN SELECT RAISE(ABORT, 'index manifest is immutable'); END;
