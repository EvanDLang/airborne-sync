"""Token cache - persists Keycloak tokens to ~/.airborne/token.json."""

import json
import os
import stat
from pathlib import Path

CACHE_DIR  = Path.home() / ".airborne"
CACHE_FILE = CACHE_DIR / "token.json"


def save(token_response: dict) -> None:
    """Write token response to disk with tight permissions."""
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(token_response))
    CACHE_FILE.chmod(0o600)


def load() -> dict | None:
    """Return cached token response or None if not found."""
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def delete() -> None:
    """Remove the cached token file."""
    try:
        CACHE_FILE.unlink()
    except FileNotFoundError:
        pass
