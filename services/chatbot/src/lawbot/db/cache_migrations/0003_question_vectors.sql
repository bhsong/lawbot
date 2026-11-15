CREATE TABLE cache_question_vectors (
    query_id TEXT NOT NULL REFERENCES cache_queries(query_id),
    embedding_model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    max_seq_length INTEGER NOT NULL CHECK (max_seq_length > 0),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    vector BLOB NOT NULL CHECK (length(vector) = dimension * 4),
    PRIMARY KEY (query_id, embedding_model)
);
