from dataclasses import dataclass, field
import os
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("data/recallgraph.sqlite3")
    api_key: str = field(default="", repr=False)
    timezone: str = "Asia/Kolkata"
    cloud_enabled: bool = False
    openai_api_key: str = field(default="", repr=False)
    vision_model: str = "gpt-4.1-mini"
    transcription_model: str = "gpt-4o-transcribe-diarize"
    embedding_model: str = "text-embedding-3-small"
    max_upload_bytes: int = 20 * 1024 * 1024
    max_extracted_chars: int = 500_000

    def validate(self):
        if len(self.api_key) < 16:
            raise ValueError("Set APP_API_KEY to a random secret of at least 16 characters")
        ZoneInfo(self.timezone)
        if self.cloud_enabled and not self.openai_api_key:
            raise ValueError("CLOUD_ENABLED requires OPENAI_API_KEY")

    @classmethod
    def from_env(cls):
        return cls(
            db_path=Path(os.getenv("DB_PATH", "data/recallgraph.sqlite3")),
            api_key=os.getenv("APP_API_KEY", ""),
            timezone=os.getenv("APP_TIMEZONE", "Asia/Kolkata"),
            cloud_enabled=os.getenv("CLOUD_ENABLED", "false").lower() == "true",
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            vision_model=os.getenv("VISION_MODEL", "gpt-4.1-mini"),
            transcription_model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-transcribe-diarize"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        )
