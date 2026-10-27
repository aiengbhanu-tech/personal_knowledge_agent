from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 modality TEXT NOT NULL, occurred_at TEXT, time_basis TEXT NOT NULL,
 ingested_at TEXT NOT NULL, expires_at TEXT, original BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, locator TEXT NOT NULL, extraction TEXT NOT NULL, text TEXT NOT NULL,
 UNIQUE(source_id, ordinal)
);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units(id) ON DELETE CASCADE,
 start_char INTEGER NOT NULL, end_char INTEGER NOT NULL, text TEXT NOT NULL,
 embedding TEXT, embedding_model TEXT,
 CHECK(start_char >= 0 AND end_char > start_char)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
 chunk_id UNINDEXED, text, tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
 INSERT INTO chunks_fts(chunk_id, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
 DELETE FROM chunks_fts WHERE chunk_id = old.id;
END;
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('decision','commitment')),
 quote TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS entities (
 id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
 entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
 PRIMARY KEY(entity_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('semantic','preference')),
 key TEXT NOT NULL, value TEXT NOT NULL, active INTEGER NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT,
 supersedes TEXT REFERENCES memories(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_active_key ON memories(kind,key) WHERE active=1;
CREATE TABLE IF NOT EXISTS memory_support (
 memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
 PRIMARY KEY(memory_id,chunk_id)
);
CREATE TRIGGER IF NOT EXISTS remove_supported_memory BEFORE DELETE ON chunks BEGIN
 DELETE FROM memories WHERE id IN (SELECT memory_id FROM memory_support WHERE chunk_id=old.id);
END;
CREATE INDEX IF NOT EXISTS sources_occurred ON sources(occurred_at);
CREATE INDEX IF NOT EXISTS sources_expires ON sources(expires_at);
CREATE INDEX IF NOT EXISTS unit_source ON units(source_id);
CREATE INDEX IF NOT EXISTS chunk_unit ON chunks(unit_id);
CREATE INDEX IF NOT EXISTS event_chunk ON events(chunk_id);
CREATE INDEX IF NOT EXISTS mention_chunk ON mentions(chunk_id);
PRAGMA user_version=1;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema; do not downgrade this database")
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA secure_delete=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def prune(self) -> dict:
        with self.connect() as c:
            now = utcnow()
            sources = c.execute("DELETE FROM sources WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).rowcount
            memories = c.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).rowcount
            c.execute("DELETE FROM entities WHERE NOT EXISTS (SELECT 1 FROM mentions WHERE entity_id=entities.id)")
        return {"sources_deleted": sources, "memories_deleted": memories}

    def forget(self, source_id: str) -> bool:
        with self.connect() as c:
            deleted = c.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount
            c.execute("DELETE FROM entities WHERE NOT EXISTS (SELECT 1 FROM mentions WHERE entity_id=entities.id)")
        return bool(deleted)

