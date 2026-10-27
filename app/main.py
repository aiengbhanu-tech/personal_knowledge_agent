from contextlib import asynccontextmanager
from datetime import date
from enum import Enum
import hmac
import logging
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .providers import ProviderError
from .service import KnowledgeService


class Modality(str, Enum):
    document = "document"
    email = "email"
    image = "image"
    meeting = "meeting"
    web_export = "web_export"


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    start_date: date | None = None
    end_date: date | None = None
    modality: Modality | None = None
    speaker: str | None = Field(default=None, min_length=1, max_length=200)
    allow_cloud: bool = False


class MemoryRequest(BaseModel):
    kind: Literal["semantic", "preference"]
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=2000)
    support_chunk_ids: list[str] = Field(default_factory=list, max_length=50)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        app.state.service = KnowledgeService(Database(settings.db_path), settings)
        app.state.service.db.prune()
        yield

    app = FastAPI(title="RecallGraph", version="0.1.0", lifespan=lifespan,
                  description="Single-user evidence-first multimodal memory prototype. Use Authorize with APP_API_KEY.")
    header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authorized(request: Request, key: Annotated[str | None, Depends(header)]):
        if key is None or not hmac.compare_digest(key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "Invalid API key")
        service = request.app.state.service
        service.db.prune()
        return service

    service_dep = Annotated[KnowledgeService, Depends(authorized)]
    router = APIRouter(prefix="/v1")

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @router.post("/sources", status_code=201)
    def ingest(service: service_dep, file: UploadFile,
               occurred_at: Annotated[str | None, Form()] = None,
               retention_days: Annotated[int | None, Form(ge=1, le=3650)] = None,
               allow_cloud: Annotated[bool, Form()] = False):
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "Upload exceeds 20 MiB")
        try:
            return service.ingest(data, file.filename or "upload.txt", occurred_at, retention_days, allow_cloud)
        except (ValueError, ProviderError):
            raise
        except Exception as exc:
            # Parser error details can contain personal document contents.
            logging.getLogger("recallgraph").warning("Import failed (%s)", type(exc).__name__)
            raise HTTPException(422, "Import failed; check the file format and encoding") from exc

    @router.get("/sources")
    def list_sources(service: service_dep):
        with service.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT id,filename,sha256,modality,occurred_at,
                    time_basis,ingested_at,expires_at FROM sources ORDER BY ingested_at DESC LIMIT 500""")]

    @router.get("/sources/{source_id}")
    def source_detail(source_id: str, service: service_dep):
        with service.db.connect() as c:
            source = c.execute("""SELECT id,filename,sha256,modality,occurred_at,time_basis,
                    ingested_at,expires_at FROM sources WHERE id=?""", (source_id,)).fetchone()
            if source is None:
                raise HTTPException(404, "Source not found")
            units = [dict(r) for r in c.execute("SELECT id,ordinal,locator,extraction,text FROM units WHERE source_id=? ORDER BY ordinal", (source_id,))]
            chunk_ids = [r[0] for r in c.execute("SELECT ch.id FROM chunks ch JOIN units u ON u.id=ch.unit_id WHERE u.source_id=? ORDER BY u.ordinal,ch.start_char", (source_id,))]
            return {**dict(source), "units": units, "chunk_ids": chunk_ids}

    @router.get("/sources/{source_id}/original")
    def source_original(source_id: str, service: service_dep):
        with service.db.connect() as c:
            row = c.execute("SELECT filename,original FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Source not found")
        return Response(content=row["original"], media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"]),
                                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @router.delete("/sources/{source_id}")
    def forget(source_id: str, service: service_dep):
        if not service.db.forget(source_id):
            raise HTTPException(404, "Source not found")
        return {"deleted": source_id, "scope": "Original, extracts, chunks, vectors, events, mentions and supported memories"}

    @router.post("/ask")
    def ask(body: AskRequest, service: service_dep):
        return service.ask(body.question, body.top_k, body.start_date, body.end_date,
                           body.modality.value if body.modality else None, body.allow_cloud,
                           speaker=body.speaker)

    @router.get("/evidence/{chunk_id}")
    def evidence(chunk_id: str, service: service_dep):
        try:
            with service.db.connect() as c:
                return service.citation(c, chunk_id)
        except ValueError as exc:
            raise HTTPException(404, "Evidence not found") from exc

    @router.get("/memory/candidates")
    def candidates(service: service_dep):
        return service.candidates()

    @router.post("/memories", status_code=201)
    def remember(body: MemoryRequest, service: service_dep):
        return service.remember(body.kind, body.key, body.value, body.support_chunk_ids, body.retention_days)

    @router.get("/memories")
    def memories(service: service_dep, include_history: bool = False):
        with service.db.connect() as c:
            rows = c.execute("SELECT * FROM memories WHERE active=1 OR ? ORDER BY created_at DESC", (include_history,)).fetchall()
            return [{**dict(r), "support_chunk_ids": [s[0] for s in c.execute("SELECT chunk_id FROM memory_support WHERE memory_id=?", (r["id"],))]} for r in rows]

    @router.delete("/memories/{memory_id}")
    def forget_memory(memory_id: str, service: service_dep):
        with service.db.connect() as c:
            count = c.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount
        if not count:
            raise HTTPException(404, "Memory not found")
        return {"deleted": memory_id, "note": "Source evidence remains; delete sources to forget underlying evidence"}

    @router.post("/retention/prune")
    def prune(service: service_dep):
        # Auth normally prunes first; this endpoint still guarantees an idempotent maintenance run.
        return {"completed": True, **service.db.prune()}

    @router.get("/graph")
    def graph(service: service_dep):
        with service.db.connect() as c:
            nodes = [dict(r) for r in c.execute("SELECT * FROM entities ORDER BY id LIMIT 200")]
            edges = [dict(r) for r in c.execute("""SELECT m.entity_id,m.chunk_id,'mentioned_in' relation
                FROM mentions m JOIN entities e ON e.id=m.entity_id
                WHERE e.id IN (SELECT id FROM entities ORDER BY id LIMIT 200)
                ORDER BY m.entity_id,m.chunk_id LIMIT 500""")]
        return {"entities": nodes, "edges": edges, "limits": {"nodes": 200, "edges": 500},
                "semantics": "Rule-derived topic/email mentions; no inferred person-to-person relationships"}

    app.include_router(router)
    return app


app = create_app()
