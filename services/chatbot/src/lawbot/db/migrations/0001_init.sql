CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
);

-- ---------- corpus ----------
CREATE TABLE laws (
  law_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,                 -- CSV `law`, e.g. '근로기준법 시행령'
  kind TEXT NOT NULL CHECK (kind IN ('법률','시행령','시행규칙')),
  parent_name TEXT,                          -- CSV `parent_law` for decrees; NULL for statutes
  source TEXT NOT NULL                       -- e.g. 'Legalize KR'
);

-- One row per distinct content of one article. All CSV values kept as exact strings.
CREATE TABLE article_versions (
  version_id INTEGER PRIMARY KEY,
  law_id INTEGER NOT NULL REFERENCES laws(law_id),
  article_no TEXT NOT NULL,
  article_id TEXT NOT NULL, title TEXT NOT NULL, chapter TEXT NOT NULL,
  deleted TEXT NOT NULL CHECK (deleted IN ('0','1')),
  text TEXT NOT NULL, effective_date TEXT NOT NULL, related_ids TEXT NOT NULL,
  kind TEXT NOT NULL, parent_law TEXT NOT NULL, parent_ids TEXT NOT NULL, child_ids TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,              -- sha256 of the 13 values joined with '\x1f'
  UNIQUE (law_id, article_no, content_sha256)
);
CREATE INDEX article_versions_law_no ON article_versions(law_id, article_no);

CREATE TABLE corpus_releases (
  release_id TEXT PRIMARY KEY,               -- e.g. 'v5', 'v5-live-2026-11-08', 'v4-decree'
  profile TEXT NOT NULL CHECK (profile IN ('v4','v5')),
  dataset_version TEXT NOT NULL,
  reference_date TEXT NOT NULL, collected_date TEXT,
  source_name TEXT, source_url TEXT, source_ref TEXT,
  law_csv_name TEXT NOT NULL, combined_csv_name TEXT NOT NULL,
  law_columns TEXT NOT NULL, combined_columns TEXT NOT NULL,   -- JSON arrays (column order)
  law_csv_sha256 TEXT NOT NULL, combined_csv_sha256 TEXT NOT NULL,
  law_manifest TEXT NOT NULL, combined_manifest TEXT NOT NULL, -- manifest JSON text, byte-exact
  law_manifest_name TEXT NOT NULL, combined_manifest_name TEXT NOT NULL,
  origin TEXT NOT NULL,                      -- where it came from: path or 'articles-update'
  status TEXT NOT NULL CHECK (status IN ('staged','validated','failed','archived')),
  created_at TEXT NOT NULL, validated_at TEXT, note TEXT
);

CREATE TABLE corpus_release_articles (
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  position INTEGER NOT NULL,                 -- 0-based row order in the combined CSV
  version_id INTEGER NOT NULL REFERENCES article_versions(version_id),
  PRIMARY KEY (release_id, position),
  UNIQUE (release_id, version_id)
);
-- searchable = (deleted = '0'); in law CSV = (kind = '법률'). Both derived, not stored.

CREATE TABLE active_corpus (
  profile TEXT PRIMARY KEY CHECK (profile IN ('v4','v5')),
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  activated_at TEXT NOT NULL, activated_by TEXT NOT NULL
);

CREATE TABLE corpus_events (
  event_id INTEGER PRIMARY KEY,
  release_id TEXT REFERENCES corpus_releases(release_id),
  profile TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('imported','staged','validated','failed','activated','rolled_back','restart_failed')),
  previous_release_id TEXT, at TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT  -- detail: JSON
);

CREATE TABLE embedding_sets (              -- metadata of .npz caches (vectors stay in cache/embeddings)
  cache_key TEXT PRIMARY KEY, model_id TEXT NOT NULL, model_revision TEXT,
  max_seq INTEGER NOT NULL, dim INTEGER NOT NULL, n_docs INTEGER NOT NULL,
  docs_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE release_embedding_sets (
  release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  part TEXT NOT NULL CHECK (part IN ('law','decree')),
  cache_key TEXT NOT NULL REFERENCES embedding_sets(cache_key),
  PRIMARY KEY (release_id, part, cache_key)
);

-- ---------- feedback ----------
CREATE TABLE feedbacks (
  feedback_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL, ts TEXT NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('like','flag','cleared')),
  reason TEXT CHECK (reason IS NULL OR reason IN ('retrieval','answer','false_refusal','other')),
  question TEXT NOT NULL, answer_raw TEXT, answer_shown TEXT,
  model TEXT, profile TEXT, dataset_version TEXT, corpus_release_id TEXT, articles_sha256 TEXT,
  commit_sha TEXT, request_id TEXT, instance TEXT,
  record TEXT NOT NULL,                      -- the full original row as JSON (lossless)
  origin TEXT NOT NULL,                      -- 'web' or 'import:<file name>:<line no>'
  CHECK ((label = 'flag') = (reason IS NOT NULL))
);
CREATE INDEX feedbacks_turn ON feedbacks(turn_id, ts);
CREATE INDEX feedbacks_ts ON feedbacks(ts);
CREATE TRIGGER feedbacks_append_only BEFORE UPDATE ON feedbacks
BEGIN SELECT RAISE(ABORT, 'feedbacks are append-only'); END;

CREATE TABLE feedback_reviews (            -- append-only history; the latest row per feedback wins
  review_id INTEGER PRIMARY KEY,
  feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  reviewer TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('correct','incorrect','uncertain')),
  decision TEXT NOT NULL CHECK (decision IN ('approved','hold','rejected')),
  scope TEXT CHECK (scope IS NULL OR scope IN ('in','out')),
  gold_articles TEXT NOT NULL,               -- JSON [{"law": "...", "article_no": "제N조"}]
  corrected_question TEXT, corrected_answer TEXT, note TEXT,
  created_at TEXT NOT NULL
);
CREATE TRIGGER feedback_reviews_append_only BEFORE UPDATE ON feedback_reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;

CREATE TABLE feedback_promotions (
  promotion_id INTEGER PRIMARY KEY,
  feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  review_id INTEGER NOT NULL REFERENCES feedback_reviews(review_id),
  kind TEXT NOT NULL CHECK (kind IN ('up','down')),
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  preference_id INTEGER REFERENCES qa_preferences(preference_id),
  approver TEXT NOT NULL, created_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','revoked')),
  revoked_at TEXT, revoked_by TEXT, revoke_note TEXT,
  snapshot_version TEXT                      -- dataset release that first included it
);
CREATE UNIQUE INDEX one_active_promotion ON feedback_promotions(feedback_id) WHERE state = 'active';

CREATE TABLE idempotency_keys (
  actor TEXT NOT NULL, endpoint TEXT NOT NULL, key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL, status_code INTEGER NOT NULL, response TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (actor, endpoint, key)
);

CREATE TABLE audit_events (
  event_id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL,                      -- 'login_ok','login_fail','review','promote_up','promote_down','revoke',...
  target_type TEXT, target_id TEXT,
  summary TEXT                               -- JSON; never question/answer text or secrets
);

-- ---------- QA datasets ----------
CREATE TABLE answer_keys (
  key_set TEXT NOT NULL,                     -- 'v4','v5','feedback'
  answer_key TEXT NOT NULL,
  law TEXT NOT NULL, article_id TEXT NOT NULL, answer TEXT NOT NULL,
  must_include TEXT NOT NULL, must_not_include TEXT NOT NULL, must_not_in_conclusion TEXT NOT NULL,
  row_no INTEGER,                            -- source CSV order (NULL for feedback)
  PRIMARY KEY (key_set, answer_key)
);

CREATE TABLE qa_items (
  qa_uid TEXT PRIMARY KEY,                   -- '<dataset>:<id>' or '<dataset>:#<row_no>' if ids are not unique
  dataset TEXT NOT NULL,                     -- 'v4','v5','decree_v4','reasoning_v5','oos','scope','refusal_hard','labor_eval','feedback'
  source_id TEXT NOT NULL, row_no INTEGER,
  question TEXT NOT NULL, question_norm TEXT NOT NULL,
  law TEXT, article_id TEXT, question_type TEXT, eval_cat TEXT,
  key_set TEXT, answer_key TEXT,
  row TEXT NOT NULL,                         -- full original row JSON (column order kept)
  origin TEXT NOT NULL,                      -- 'import:data/v5/qa_tax_v5.csv' or 'feedback:<feedback_id>'
  status TEXT NOT NULL CHECK (status IN ('active','retired')),
  created_at TEXT NOT NULL,
  FOREIGN KEY (key_set, answer_key) REFERENCES answer_keys(key_set, answer_key)
);
CREATE INDEX qa_items_norm ON qa_items(question_norm);
CREATE UNIQUE INDEX qa_items_row ON qa_items(dataset, row_no) WHERE row_no IS NOT NULL;

CREATE TABLE qa_gold_articles (            -- for feedback items (imported sets keep law/article_id)
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  law TEXT NOT NULL, article_no TEXT NOT NULL,
  PRIMARY KEY (qa_uid, law, article_no)
);

CREATE TABLE split_sets (
  split_set TEXT PRIMARY KEY,                -- 'v4','v5','decree_v4','feedback'
  frozen INTEGER NOT NULL CHECK (frozen IN (0,1)),
  policy TEXT NOT NULL                       -- JSON: rule, seed, group key, source file + sha256
);

CREATE TABLE qa_splits (
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  split_set TEXT NOT NULL REFERENCES split_sets(split_set),
  purpose TEXT NOT NULL CHECK (purpose IN ('emb','sllm','eval')),
  split TEXT NOT NULL CHECK (split IN ('train','val','test','unassigned')),
  group_key TEXT NOT NULL,
  row_no INTEGER,                            -- source split CSV order
  assigned_at TEXT NOT NULL,
  PRIMARY KEY (qa_uid, split_set, purpose)
);
CREATE TRIGGER qa_splits_frozen_insert BEFORE INSERT ON qa_splits
WHEN (SELECT frozen FROM split_sets WHERE split_set = NEW.split_set) = 1
BEGIN SELECT RAISE(ABORT, 'split set is frozen'); END;
CREATE TRIGGER qa_splits_frozen_update BEFORE UPDATE ON qa_splits
WHEN (SELECT frozen FROM split_sets WHERE split_set = OLD.split_set) = 1 OR OLD.split <> 'unassigned'
BEGIN SELECT RAISE(ABORT, 'assigned or frozen split cannot change'); END;
CREATE TRIGGER qa_splits_no_delete BEFORE DELETE ON qa_splits
BEGIN SELECT RAISE(ABORT, 'splits are never deleted'); END;
CREATE TRIGGER split_sets_no_unfreeze BEFORE UPDATE OF frozen ON split_sets
WHEN OLD.frozen = 1 BEGIN SELECT RAISE(ABORT, 'cannot unfreeze'); END;

CREATE TABLE qa_preferences (
  preference_id INTEGER PRIMARY KEY,
  qa_uid TEXT NOT NULL REFERENCES qa_items(qa_uid),
  chosen TEXT NOT NULL, rejected TEXT NOT NULL,
  rejected_feedback_id TEXT NOT NULL REFERENCES feedbacks(feedback_id),
  verdict TEXT NOT NULL CHECK (verdict = 'incorrect'),
  tasks TEXT NOT NULL,                       -- JSON, e.g. ["dpo","kto"]; never embedding negatives
  created_at TEXT NOT NULL
);

CREATE TABLE dataset_releases (
  version TEXT PRIMARY KEY,                  -- e.g. 'v6'
  corpus_release_id TEXT NOT NULL REFERENCES corpus_releases(release_id),
  created_at TEXT NOT NULL,
  manifest TEXT NOT NULL,                    -- the manifest.json text written with the snapshot
  manifest_sha256 TEXT NOT NULL
);
