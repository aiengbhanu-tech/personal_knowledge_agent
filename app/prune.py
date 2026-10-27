"""Run with `python -m app.prune` for maintenance while the API is idle."""
import json
from dotenv import load_dotenv
from .config import Settings
from .db import Database

if __name__ == "__main__":
    load_dotenv(".env")
    settings = Settings.from_env()
    settings.validate()
    print(json.dumps(Database(settings.db_path).prune()))
