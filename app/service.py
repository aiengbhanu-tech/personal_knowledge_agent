from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import sqlite3
from uuid import uuid4

from .config import Settings
from .db import Database, utcnow
from .ingestion import extract, chunks, event_candidates, entities
from .providers import OpenAIProvider
from .temporal import aware_iso, window


def identifier():
    return uuid4().hex


class KnowledgeService:
    def __init__(self, db: Database, settings: Settings, provider=None):
        self.db, self.settings = db, settings
        self.provider = provider or OpenAIProvider(settings)

    def ingest(self, data: bytes, filename: str, occurred_at: str | None,
               retention_days: int | None, allow_cloud: bool):
        if not data or len(data) > self.settings.max_upload_bytes:
            raise ValueError("File must contain 1 byte to 20 MiB")
        # Display-only filename; original bytes never become a filesystem path.
        name = PurePosixPath(filename.replace("\\", "/")).name[:200] or "upload.txt"
        digest = hashlib.sha256(data).hexdigest()
        when = aware_iso(occurred_at)
        with self.db.connect() as c:
            existing = c.execute("SELECT id FROM sources WHERE sha256=?", (digest,)).fetchone()
        if existing:
            return {"source_id": existing["id"], "duplicate": True,
                    "note": "Identical bytes already exist; metadata and cloud indexing were not changed"}
        extracted = extract(data, name, self.settings, self.provider, allow_cloud)
        when = when or aware_iso(extracted.occurred_at)
        if retention_days is not None and not 1 <= retention_days <= 3650:
            raise ValueError("retention_days must be 1–3650, or omitted for no automatic expiry")
        now = utcnow()
        expires = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat() if retention_days else None
        source_id = identifier()
        prepared = []
        for ordinal, unit in enumerate(extracted.units):
            unit_id = identifier()
            for start, end, text in chunks(unit.text):
                prepared.append((identifier(), unit_id, ordinal, unit, start, end, text))
        vectors = self.provider.embed([p[-1] for p in prepared], True) if allow_cloud else [None] * len(prepared)
        try:
            with self.db.connect() as c:
                c.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)", (
                    source_id, name, digest, extracted.modality, when,
                    "user_provided" if occurred_at else ("source_metadata" if when else "unknown"),
                    now, expires, data,
                ))
                for ordinal, unit in enumerate(extracted.units):
                    unit_id = next(p[1] for p in prepared if p[2] == ordinal)
                    c.execute("INSERT INTO units VALUES (?,?,?,?,?,?)", (
                        unit_id, source_id, ordinal, json.dumps(unit.locator), unit.extraction, unit.text))
                for p, vector in zip(prepared, vectors, strict=True):
                    chunk_id, unit_id, _, unit, start, end, text = p
                    c.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", (
                        chunk_id, unit_id, start, end, text,
                        json.dumps(vector) if vector is not None else None,
                        self.settings.embedding_model if vector is not None else None))
                    # Vision descriptions are generated interpretations: no event promotion.
                    if unit.extraction != "vision_generated_description":
                        for kind, quote in event_candidates(text):
                            c.execute("INSERT INTO events(id,chunk_id,kind,quote) VALUES (?,?,?,?)",
                                      (identifier(), chunk_id, kind, quote))
                    for label, kind in entities(text):
                        c.execute("INSERT OR IGNORE INTO entities(label,kind) VALUES (?,?)", (label, kind))
                        c.execute("INSERT OR IGNORE INTO mentions VALUES ((SELECT id FROM entities WHERE label=?),?)",
                                  (label, chunk_id))
        except sqlite3.IntegrityError:
            # Concurrent identical import: the entire losing transaction rolls back.
            with self.db.connect() as c:
                existing = c.execute("SELECT id FROM sources WHERE sha256=?", (digest,)).fetchone()
            if existing:
                return {"source_id": existing["id"], "duplicate": True}
            raise
        return {"source_id": source_id, "duplicate": False, "modality": extracted.modality,
                "units": len(extracted.units), "chunks": len(prepared), "occurred_at": when,
                "cloud_used": allow_cloud, "expires_at": expires}

    def citation(self, c, chunk_id: str):
        row = c.execute("""SELECT ch.*, u.locator, u.extraction, u.source_id, u.text unit_text,
                          s.filename,s.sha256,s.occurred_at,s.time_basis,s.modality
                          FROM chunks ch JOIN units u ON u.id=ch.unit_id
                          JOIN sources s ON s.id=u.source_id WHERE ch.id=?""", (chunk_id,)).fetchone()
        if not row:
            raise ValueError("Unknown evidence chunk")
        # Verify against persisted unit, never trust a generated citation.
        if row["unit_text"][row["start_char"]:row["end_char"]] != row["text"]:
            raise RuntimeError("Evidence span integrity check failed")
        return {"chunk_id": chunk_id, "source_id": row["source_id"], "filename": row["filename"],
                "source_sha256": row["sha256"], "unit_id": row["unit_id"],
                "locator": json.loads(row["locator"]), "start_char": row["start_char"],
                "end_char": row["end_char"], "quote": row["text"],
                "extraction": row["extraction"], "occurred_at": row["occurred_at"],
                "time_basis": row["time_basis"], "modality": row["modality"],
                "original_url": f"/v1/sources/{row['source_id']}/original"}

    def ask(self, question: str, top_k=5, start_date=None, end_date=None,
            modality=None, allow_cloud=False, speaker=None):
        temporal = window(question, self.settings.timezone, start_date, end_date)
        q = question.lower()
        if modality is None and re.search(r"\bscreenshot\b", q):
            modality = "image"
        event_kind = "commitment" if re.search(r"\b(commitments?|action items?)\b", q) else (
            "decision" if re.search(r"\b(decisions?|decide|decided)\b", q) else None)
        stop = set("what did do we i you about the a an to of for in on is are was were and or my our me find show tell where when how which it this that with make made during from last month yesterday today meeting meetings discussed discuss screenshot screenshots commitment commitments decision decisions decide decided action items".split())
        terms = list(dict.fromkeys(t for t in re.findall(r"[^\W_]+", q, re.UNICODE)
                                   if t not in stop and len(t) > 1))[:24]
        filters, params = [], []
        for expression, value in (("s.occurred_at>=?", temporal["start"]),
                                  ("s.occurred_at<?", temporal["end_exclusive"]),
                                  ("s.modality=?", modality),
                                  ("json_extract(u.locator,'$.speaker')=?", speaker)):
            if value is not None:
                filters.append(expression)
                params.append(value)
        if event_kind:
            filters.append("EXISTS (SELECT 1 FROM events e WHERE e.chunk_id=ch.id AND e.kind=?)")
            params.append(event_kind)
        clause = " AND ".join(filters) or "1=1"
        base = "FROM chunks ch JOIN units u ON u.id=ch.unit_id JOIN sources s ON s.id=u.source_id"
        scores = defaultdict(float)
        vector_count = 0
        query_vector = self.provider.embed([question], True)[0] if allow_cloud else None
        with self.db.connect() as c:
            if terms:
                # Precision-first local baseline: every meaningful query term must match.
                # Paraphrase recall is provided separately by opt-in semantic retrieval.
                expression = " AND ".join('"' + t + '"' for t in terms)
                rows = c.execute(f"""SELECT ch.id {base}
                    JOIN chunks_fts f ON f.chunk_id=ch.id
                    WHERE {clause} AND chunks_fts MATCH ?
                    ORDER BY bm25(chunks_fts), ch.id LIMIT 100""", (*params, expression)).fetchall()
            elif event_kind:
                rows = c.execute(f"SELECT ch.id {base} WHERE {clause} ORDER BY s.occurred_at DESC,ch.id LIMIT 100", params).fetchall()
            else:
                rows = []
            for rank, row in enumerate(rows, 1):
                scores[row["id"]] += 1 / (60 + rank)
            if query_vector is not None:
                vectors = c.execute(f"""SELECT ch.id,ch.embedding {base}
                    WHERE {clause} AND ch.embedding_model=? ORDER BY ch.id LIMIT 5001""",
                    (*params, self.settings.embedding_model)).fetchall()
                if len(vectors) > 5000:
                    raise ValueError("Prototype semantic scan limit reached; migrate vectors to Qdrant")
                ranked = []
                for row in vectors:
                    v = json.loads(row["embedding"])
                    dot = sum(a*b for a, b in zip(v, query_vector, strict=True))
                    norm = math.sqrt(sum(a*a for a in v) * sum(b*b for b in query_vector))
                    similarity = dot / norm if norm else 0
                    if similarity >= 0.30:
                        ranked.append((similarity, row["id"]))
                vector_count = len(ranked)
                for rank, (_, cid) in enumerate(sorted(ranked, reverse=True)[:100], 1):
                    scores[cid] += 1 / (60 + rank)
            ids = sorted(scores, key=lambda cid: (-scores[cid], cid))[:top_k]
            evidence = [self.citation(c, cid) for cid in ids]
            confirmed = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                confirmed = [dict(r) for r in c.execute(f"""SELECT DISTINCT m.id,m.kind,m.key,m.value
                    FROM memories m JOIN memory_support ms ON ms.memory_id=m.id
                    WHERE m.active=1 AND ms.chunk_id IN ({placeholders})""", ids)]
        # Working memory is only this request's evidence bundle. No stored conversation cache.
        answer = "No matching evidence found. Try different terms or provide an explicit date range."
        if evidence:
            answer = "Matching source excerpts (verify attribution and context):\n" + "\n".join(
                f"[{i}] {item['quote']}" for i, item in enumerate(evidence, 1))
        return {"answer": answer, "mode": "extractive", "evidence": evidence,
                "confirmed_memory_context": confirmed,
                "query_plan": {"time_window": temporal, "modality": modality,
                               "speaker": speaker,
                               "event_kind": event_kind, "lexical_terms": terms,
                               "retrieval": "hybrid_rrf" if allow_cloud else "fts5_bm25",
                               "vector_candidates": vector_count},
                "abstained": not bool(evidence)}

    def remember(self, kind: str, key: str, value: str, support_ids: list[str], retention_days=None):
        support_ids = list(dict.fromkeys(support_ids))
        if kind == "semantic" and not support_ids:
            raise ValueError("A semantic memory requires at least one evidence chunk")
        memory_id = identifier()
        expires = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat() if retention_days else None
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            evidence = [self.citation(c, cid) for cid in support_ids]
            previous = c.execute("SELECT id FROM memories WHERE kind=? AND key=? AND active=1", (kind, key)).fetchone()
            if previous:
                c.execute("UPDATE memories SET active=0 WHERE id=?", (previous["id"],))
            c.execute("INSERT INTO memories VALUES (?,?,?,?,1,?,?,?)", (
                memory_id, kind, key, value, utcnow(), expires, previous["id"] if previous else None))
            c.executemany("INSERT INTO memory_support VALUES (?,?)", [(memory_id, cid) for cid in support_ids])
        return {"memory_id": memory_id, "confirmed": True, "supersedes": previous["id"] if previous else None,
                "evidence": evidence}

    def candidates(self):
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id LIMIT 1000").fetchall()
            groups = {}
            for row in rows:
                key = row["kind"], " ".join(row["quote"].lower().split())
                group = groups.setdefault(key, {"kind": row["kind"], "proposed_value": row["quote"],
                                                "status": "candidate", "support_chunk_ids": []})
                if row["chunk_id"] not in group["support_chunk_ids"]:
                    group["support_chunk_ids"].append(row["chunk_id"])
            return {"candidates": list(groups.values()), "policy": "Exact normalized quote deduplication; confirmation required"}
