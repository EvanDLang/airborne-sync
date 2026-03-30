"""Keycloak device flow authentication and token refresh management."""

import sys
import time
import threading

import requests

from . import config
from . import token_cache


def device_flow() -> dict:
    """
    Run Keycloak OAuth2 device authorization flow.

    Prints a URL for the user to open in a browser, then polls until
    the user completes authentication.

    Returns the full token response dict including access_token and refresh_token.
    """
    device_url = (
        f"{config.KEYCLOAK_URL}/realms/{config.KEYCLOAK_REALM}"
        f"/protocol/openid-connect/auth/device"
    )
    token_url = (
        f"{config.KEYCLOAK_URL}/realms/{config.KEYCLOAK_REALM}"
        f"/protocol/openid-connect/token"
    )

    resp = requests.post(
        device_url,
        data={"client_id": config.KEYCLOAK_CLIENT_ID, "scope": "openid"},
        timeout=10,
    )
    if not resp.ok:
        raise RuntimeError(f"Device flow request failed ({resp.status_code}): {resp.text}")
    device = resp.json()

    print(f"\n  Open this URL in your browser:\n\n    {device['verification_uri_complete']}\n")
    print(f"  Or go to {device['verification_uri']} and enter code: {device['user_code']}\n")

    interval   = device.get("interval", 5)
    expires_in = device.get("expires_in", 300)
    deadline   = time.monotonic() + expires_in

    while time.monotonic() < deadline:
        time.sleep(interval)
        poll = requests.post(
            token_url,
            data={
                "client_id":   config.KEYCLOAK_CLIENT_ID,
                "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device["device_code"],
            },
            timeout=10,
        )
        body = poll.json()
        if poll.status_code == 200:
            print("  Authenticated.\n")
            return body
        error = body.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise RuntimeError(f"Device flow error: {error} - {body.get('error_description', '')}")

    raise TimeoutError("Device flow timed out. Please try again.")


def load_session() -> "TokenManager":
    """
    Load a TokenManager from the cached token file.

    Exits with a clear message if no session exists or the session has expired.
    Call this from every command that needs authentication.
    """
    cached = token_cache.load()
    if cached is None:
        sys.exit("Not logged in. Run 'airborne-sync login' first.")
    try:
        return TokenManager(cached)
    except SessionExpiredError:
        sys.exit("Session expired. Run 'airborne-sync login' to authenticate.")


class SessionExpiredError(RuntimeError):
    pass


class TokenManager:
    """
    Keeps the Keycloak access token fresh using the refresh token.

    Thread-safe: safe to call access_token from multiple upload threads.
    """

    def __init__(self, token_response: dict):
        self._lock = threading.Lock()
        self._access_token: str  = token_response["access_token"]
        self._refresh_token: str = token_response["refresh_token"]
        self._access_expires_at: float  = time.monotonic() + token_response.get("expires_in", 300) - 30
        self._refresh_expires_at: float = time.monotonic() + token_response.get("refresh_expires_in", 1800) - 30

    @property
    def access_token(self) -> str:
        with self._lock:
            if time.monotonic() >= self._access_expires_at:
                self._refresh()
            return self._access_token

    def _refresh(self) -> None:
        if time.monotonic() >= self._refresh_expires_at:
            raise SessionExpiredError(
                "Session expired. Run 'airborne-sync login' to authenticate."
            )
        token_url = (
            f"{config.KEYCLOAK_URL}/realms/{config.KEYCLOAK_REALM}"
            f"/protocol/openid-connect/token"
        )
        resp = requests.post(
            token_url,
            data={
                "client_id":     config.KEYCLOAK_CLIENT_ID,
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token      = body["access_token"]
        self._refresh_token     = body.get("refresh_token", self._refresh_token)
        self._access_expires_at = time.monotonic() + body.get("expires_in", 300) - 30
        self._refresh_expires_at = (
            time.monotonic()
            + body.get("refresh_expires_in", self._refresh_expires_at - time.monotonic())
            - 30
        )
        # Persist refreshed token back to cache
        token_cache.save({
            "access_token":      self._access_token,
            "refresh_token":     self._refresh_token,
            "expires_in":        body.get("expires_in", 300),
            "refresh_expires_in": body.get("refresh_expires_in", 1800),
        })

