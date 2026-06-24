"""DDL constants: schema SQL and kind-field table names."""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('source','note','dossier','concept','decision','view')),
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  file_path TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  freshness TEXT NOT NULL,
  confidence TEXT,
  schema_version TEXT NOT NULL,
  domain TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  frontmatter_json TEXT NOT NULL,
  body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);
CREATE INDEX IF NOT EXISTS idx_entries_freshness ON entries(freshness);
CREATE INDEX IF NOT EXISTS idx_entries_domain ON entries(domain);
CREATE INDEX IF NOT EXISTS idx_entries_updated_at ON entries(updated_at);

CREATE TABLE IF NOT EXISTS aliases (
  alias_nfc TEXT NOT NULL,
  entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  PRIMARY KEY (alias_nfc, entry_id)
);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias_nfc);

CREATE TABLE IF NOT EXISTS entry_tags (
  entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  PRIMARY KEY (entry_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON entry_tags(tag);

CREATE TABLE IF NOT EXISTS entry_search_terms (
  entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  term TEXT NOT NULL,
  PRIMARY KEY (entry_id, term)
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts_cn USING fts5(
  id UNINDEXED,
  title,
  aliases,
  search_terms,
  tags,
  body,
  tokenize = "unicode61 tokenchars '-' remove_diacritics 0"
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts_en USING fts5(
  id UNINDEXED,
  title,
  aliases,
  search_terms,
  body,
  tokenize = "unicode61 tokenchars '+-./#$%^&*_=' remove_diacritics 0"
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts_tri USING fts5(
  id UNINDEXED,
  title,
  aliases,
  search_terms,
  body,
  tokenize = 'trigram'
);

CREATE TABLE IF NOT EXISTS relations (
  source_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'confirmed',
  PRIMARY KEY (source_id, target_id, type)
);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id, type);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(type);

CREATE TABLE IF NOT EXISTS source_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_url TEXT NOT NULL,
  captured_at TEXT,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  extracted_at TEXT
);

CREATE TABLE IF NOT EXISTS dossier_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  reviewed_at TEXT,
  review_due_at TEXT
);

CREATE TABLE IF NOT EXISTS concept_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  evidence_status TEXT NOT NULL DEFAULT 'weak'
);

CREATE TABLE IF NOT EXISTS decision_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  decided_at TEXT,
  decision_status TEXT NOT NULL DEFAULT 'active',
  superseded_by TEXT REFERENCES entries(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS view_fields (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  generator TEXT NOT NULL,
  generated_at TEXT,
  source_query TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_metrics (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  evidence_strength REAL NOT NULL DEFAULT 0,
  drift_score REAL NOT NULL DEFAULT 0,
  compression_ratio INTEGER NOT NULL DEFAULT 0,
  action_relevance INTEGER NOT NULL DEFAULT 0,
  retrieval_heat REAL NOT NULL DEFAULT 0,
  authority_score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  op TEXT NOT NULL,
  accessed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_entry ON access_log(entry_id);
CREATE INDEX IF NOT EXISTS idx_access_time ON access_log(accessed_at);

CREATE TABLE IF NOT EXISTS registry_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dense_embeddings (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL,
  embedding_content_hash TEXT NOT NULL,
  model_id TEXT NOT NULL DEFAULT 'bge-m3',
  model_revision TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dense_hash
  ON dense_embeddings(embedding_content_hash);

CREATE TABLE IF NOT EXISTS semantic_index_meta (
  index_name TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  model_revision TEXT NOT NULL,
  index_schema_version TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_queue (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  entry_id TEXT,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL,
  details_json TEXT,
  origin TEXT NOT NULL DEFAULT 'human',
  proposed_op TEXT,
  proposed_payload_json TEXT,
  agent_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_mq_status_channel ON maintenance_queue(status, channel);
CREATE INDEX IF NOT EXISTS idx_mq_entry ON maintenance_queue(entry_id);
CREATE INDEX IF NOT EXISTS idx_mq_created ON maintenance_queue(created_at);

CREATE TABLE IF NOT EXISTS external_watch (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  target TEXT NOT NULL,
  cursor TEXT,
  last_checked_at TEXT,
  linked_dossiers_json TEXT,
  consecutive_404_count INTEGER NOT NULL DEFAULT 0,
  last_404_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ew_target ON external_watch(target);
"""

KIND_FIELD_TABLES: tuple[str, ...] = (
    "source_fields",
    "note_fields",
    "dossier_fields",
    "concept_fields",
    "decision_fields",
    "view_fields",
)
