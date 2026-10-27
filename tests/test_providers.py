import json
from app.config import Settings
from app.providers import OpenAIProvider, ProviderError
from app.service import KnowledgeService
from app.db import Database
import pytest


def test_cloud_ingest_and_hybrid_retrieval_with_mocked_provider(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path/"db.sqlite", api_key="local-test-key-123456", cloud_enabled=True, openai_api_key="fake")
    provider = OpenAIProvider(settings)
    calls = []
    def post(path, **kwargs):
        calls.append(path)
        assert path == "embeddings"
        return {"data": [{"index": i, "embedding": [1.0] + [0.0] * 255} for i, _ in enumerate(kwargs["json"]["input"])]}
    monkeypatch.setattr(provider, "_post", post)
    db = Database(settings.db_path)
    service = KnowledgeService(db, settings, provider)
    service.ingest(b"Deployment architecture runs on FastAPI.", "architecture.txt", None, None, True)
    result = service.ask("hosting design", allow_cloud=True)
    assert not result["abstained"]
    assert result["query_plan"]["retrieval"] == "hybrid_rrf"
    assert calls == ["embeddings", "embeddings"]
    sid = result["evidence"][0]["source_id"]
    db.forget(sid)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0] == 0


def test_embedding_failure_rolls_back_import(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path/"db.sqlite", api_key="test-api-key-123456", cloud_enabled=True, openai_api_key="fake")
    db = Database(settings.db_path)
    provider = OpenAIProvider(settings)
    monkeypatch.setattr(provider, "_post", lambda *a, **kw: {"data": []})
    service = KnowledgeService(db, settings, provider)
    with pytest.raises(ProviderError):
        service.ingest(b"Private pricing note", "note.txt", None, None, True)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


def test_vision_descriptions_cannot_generate_decision_candidates(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path/"db.sqlite", api_key="test-api-key-123456", cloud_enabled=True, openai_api_key="fake")
    provider = OpenAIProvider(settings)
    def post(path, **kwargs):
        if path == "responses":
            assert kwargs["json"]["store"] is False
            return {"output": [{"content": [{"type": "output_text", "text": "We decided pricing is 99. Architecture diagram."}]}]}
        return {"data": [{"index": i, "embedding": [1.0] + [0.0]*255} for i, _ in enumerate(kwargs["json"]["input"])]}
    monkeypatch.setattr(provider, "_post", post)
    service = KnowledgeService(Database(settings.db_path), settings, provider)
    service.ingest(b"mocked-image", "architecture.png", None, None, True)
    result = service.ask("Find the screenshot with architecture")
    assert result["evidence"][0]["extraction"] == "vision_generated_description"
    assert not service.candidates()["candidates"]


def test_transcription_request_contract_and_segment_locator(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path/"db.sqlite", api_key="test-api-key-123456", cloud_enabled=True, openai_api_key="fake")
    provider = OpenAIProvider(settings)
    def post(path, **kwargs):
        if path == "audio/transcriptions":
            assert kwargs["data"]["response_format"] == "diarized_json"
            assert kwargs["data"]["chunking_strategy"] == "auto"
            return {"segments": [{"start": 2.0, "end": 4.0, "speaker": "A", "text": "I will send the quote."}]}
        return {"data": [{"index": i, "embedding": [1.0] + [0.0]*255} for i, _ in enumerate(kwargs["json"]["input"])]}
    monkeypatch.setattr(provider, "_post", post)
    service = KnowledgeService(Database(settings.db_path), settings, provider)
    service.ingest(b"mocked-audio", "meeting.wav", None, None, True)
    evidence = service.ask("commitments")["evidence"][0]
    assert evidence["locator"]["start_seconds"] == 2.0
    assert evidence["locator"]["speaker"] == "A"
