"""Create .env with a fresh local secret; preserve existing configuration."""
from pathlib import Path
import secrets

if __name__ == "__main__":
    root = Path(__file__).parent
    target = root / ".env"
    if target.exists():
        print("Existing .env preserved.")
    else:
        template = (root / ".env.example").read_text()
        template = template.replace("replace-with-a-random-secret-at-least-32-characters", secrets.token_urlsafe(32))
        # Exclusive creation prevents accidental overwrite in concurrent setup runs.
        with target.open("x") as f:
            f.write(template)
        target.chmod(0o600)
        print("Created .env with a random local API key. Open it for Swagger Authorize.")
