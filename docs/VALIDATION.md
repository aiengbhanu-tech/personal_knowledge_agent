# Validation record — October 27, 2026

Environment: Python 3.12 on Linux, local SQLite with FTS5, local Tesseract. All test content is synthetic.

## Checks completed

- Editable package installation with `python -m pip install -e ".[dev]"` succeeded.
- `python -m pytest -q`: **40 passed**, including actual local screenshot OCR. One upstream Starlette TestClient deprecation warning reports that its httpx integration will change; it did not fail any test.
- `python evaluate.py --output docs/evaluation.json`: **16/16 hand-authored synthetic regression cases passed**. Expected-source-and-quote hit at 5: 12/12 positive cases. Abstention: 4/4 negative cases. Exact citation spans were checked against canonical units.
- A real Uvicorn process was exercised over localhost HTTP: API authentication, multipart ingestion, date-filtered retrieval, private-response cache headers, persistence across process restart, source deletion and subsequent abstention all passed.
- The evaluation report records local in-process retrieval timing. These small-corpus timings exclude HTTP, ingestion and cloud latency and are not throughput or production SLA measurements.

The regression corpus was used during development. A broad OR keyword match initially returned an unrelated excerpt on one negative query; the offline search now requires all meaningful content terms. This is a precision/recall tradeoff, not a held-out benchmark improvement claim.

## Scope and limits

- Provider tests mock cloud responses and validate the request/response contracts, hybrid ranking integration, source span linkage and atomic failure behavior. No paid transcription, vision or embedding requests were made.
- Actual file extraction was checked for PDF, DOCX, EML, exported HTML, text, transcript JSON and an OCR screenshot.
- Deletion tests inspect canonical rows, original access, extracted units, FTS entries, embeddings, events, graph mentions and dependent memory. They establish logical application deletion, not forensic erasure or external backup/provider deletion.
- Windows setup commands are provided; validation itself ran on Linux.
- Docker files are included. Docker was unavailable, so the image and Compose runtime were not validated.
- The prototype is single-user and local. There is no tenant-isolation test because it has no multi-tenant model.

## Versions used

| Component | Version |
|---|---|
| FastAPI | 0.162.0 |
| Uvicorn | 0.54.0 |
| Starlette | 1.10.0 |
| Pydantic | 2.14.1 |
| httpx | 0.28.1 |
| python-multipart | 0.0.32 |
| pypdf | 6.10.0 |
| python-dotenv | 1.2.4 |
| pytest | 9.1.1 |

The direct dependencies are pinned in `pyproject.toml` except timezone data. Transitive dependencies are resolved at install time; this is not a fully locked cross-platform environment.
