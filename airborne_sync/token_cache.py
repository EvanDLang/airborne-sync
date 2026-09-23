"""Token cache - persists Keycloak tokens to ~/.airborne/token.json."""

import json
import os
import time
from pathlib import Path

CACHE_DIR  = Path.home() / ".airborne"
CACHE_FILE = CACHE_DIR / "token.json"


def save(token_response: dict) -> None:
    """
    Write token response to disk with tight permissions.

    A saved_at timestamp is added so that expires_in / refresh_expires_in
    can be interpreted correctly when the cache is loaded later.
    """
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {**token_response, "saved_at": time.time()}
    fd = os.open(CACHE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


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
