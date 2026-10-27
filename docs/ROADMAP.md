# RecallGraph implementation roadmap

## Milestone 1 — evidence and forgetting foundation: implemented

FastAPI, SQLite originals and metadata, typed ingestion, located units, exact chunk spans, FTS search, optional embedding/transcription/vision adapters, relative-date windows, speaker filters, candidate events, mention graph, reviewed memory creation, supersession and cascading source deletion.

The current prototype is deliberately small. It demonstrates provenance and memory lifecycle behavior; it does not represent a completed production agent.

## Milestone 2 — structured multimodal extraction

- Queue-based ingestion with idempotent job IDs, retries, timeouts and explicit failure stages.
- Structured extraction output with subject, predicate, object, event time, due date, modality, confidence, quote spans and extraction model version.
- Verified speaker-to-person mapping per recording; user-controlled identity resolution.
- OCR bounding boxes and confidence; scanned PDF page rendering; video frame sampling and time-aligned citations.
- Separate observed text, model interpretation and user confirmation in schema and UI.
- Evaluation labels for entity precision, event extraction recall, speaker attribution and OCR/transcription errors.

Acceptance: every candidate has valid source locations; ambiguous identity is explicit; unsupported fields stay unknown; processing retries do not duplicate evidence.

## Milestone 3 — temporal memory and consolidation

- PostgreSQL canonical store and Alembic migrations; Qdrant vector index with source and tenant payload filters.
- Bitemporal facts: `valid_from/valid_to` describe when a fact applies; `recorded_at/retracted_at` describe when the system learned or withdrew it.
- Meaning-based consolidation that proposes merges with supporting quotes; confirmation required for preference or policy changes.
- Contradiction groups distinguish updates, disagreement, retractions and uncertain extraction.
- Preserve past pricing decisions while maintaining an explicitly confirmed current view.
- Retention classes, pinning, sensitivity labels and expiration jobs; source tombstones and deletion propagation across storage systems.
- Transactional outbox for index writes and deletions; retryable purge state, reconciliation, and backup-retention documentation.

Acceptance: "what did we believe in September?" and "what is the confirmed current price?" produce different, correctly dated evidence. Deleted evidence cannot remain in vectors, summaries or cached context after the deletion job succeeds.

## Milestone 4 — agent retrieval, synthesis and interface

- Planner emits a typed query plan, resolving time, modality, entity and intent.
- Query-time working memory stays separate from durable user memory.
- Hybrid retrieval, graph traversal and reranking, evaluated separately.
- Generated claims each reference known evidence spans; validate citation IDs and test entailment/faithfulness. Span integrity alone is insufficient.
- Abstain or ask for identity/date clarification when necessary.
- Chat UI with source panel: PDF page viewer, image region highlights, timed media playback and a memory review inbox.
- User can inspect "why remembered", update or forget from every memory card.

Acceptance: the three showcase questions work across genuinely heterogeneous files and speaker mappings. Retrieved instructions cannot activate tools or alter durable memory.

## Milestone 5 — production hardening and portfolio benchmark

- Authenticated users, per-owner authorization on every row/blob/vector/graph traversal; no shared global API key.
- Sandboxed media parsers, ingress limits, malware checks appropriate to deployment, per-user quotas and rate limits.
- Encryption, audit events without personal document payloads, deletion observability and provider data-handling settings.
- Tracing, extraction versions, token usage, latency/cost budgets, job metrics and deployment health checks.
- Held-out, versioned synthetic evaluation set: at least 100 questions spanning time, modalities, conflicts, identity, injection attempts and deletion.
- Report retrieval hit/recall at k, supported-claim rate, citation-location accuracy, commitment attribution, update correctness, abstention quality, deletion propagation latency, p50/p95 latency and cost per answered question.

Use one graph implementation initially. PostgreSQL tables can hold provenance-rich entities/relationships; add Neo4j only after measured multi-hop traversal needs justify another operational system. Qdrant stores retrieval vectors; it does not replace the canonical memory database.

## Portfolio demo narrative

1. Import September pricing notes, an October update, the deployment screenshot and a speaker-labelled meeting.
2. Ask about September pricing and inspect a citation, then query October to see the changed price.
3. Find the deployment screenshot and open its original.
4. Ask for a verified speaker's commitments and inspect the transcript interval.
5. Review a candidate and confirm a semantic memory; update the same key and inspect history.
6. Delete the source and show that the original, evidence, search result, graph link and dependent memory all disappear.
7. Present measured evaluation scope and failures alongside the successful demo.
