CREATE SCHEMA IF NOT EXISTS markettool;

CREATE TABLE IF NOT EXISTS markettool.firestore_docs (
  collection_name text NOT NULL,
  doc_id text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (collection_name, doc_id)
);

CREATE INDEX IF NOT EXISTS firestore_docs_collection_updated_idx
  ON markettool.firestore_docs (collection_name, updated_at DESC);

CREATE INDEX IF NOT EXISTS firestore_docs_data_gin_idx
  ON markettool.firestore_docs USING gin (data);
