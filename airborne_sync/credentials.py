"""STS credential management via the Airborne SMCE credentials API."""

import time
import threading
from datetime import datetime

import requests

from . import config
from .auth import TokenManager


class CredentialManager:
    """
    Fetches STS credentials from the Airborne SMCE credentials Lambda,
    refetching them when they are close to expiry. Thread-safe.
    """

    def __init__(self, token_manager: TokenManager):
        self._tm = token_manager
        self._lock = threading.Lock()
        self._creds: dict | None = None
        self._expires_at: float = 0.0
        self._buckets: list[str] = []

    # ------------------------------------------------------------------
    # Internal fetch
    # ------------------------------------------------------------------

    def _fetch(self) -> None:
        token = self._tm.access_token
        resp = requests.post(
            config.CREDENTIALS_API,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Credentials API error {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        self._creds   = data
        self._buckets = data.get("buckets", [])
        expiry = datetime.fromisoformat(data["expiration"])
        self._expires_at = expiry.timestamp() - config.CRED_REFRESH_BUFFER_SECS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def buckets(self) -> list[str]:
        """Return the list of S3 buckets the user has access to."""
        with self._lock:
            if self._creds is None:
                self._fetch()
        return self._buckets

    def credential_process_output(self) -> dict:
        """
        Return credentials in the AWS credential_process JSON format.

        See https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html
        """
        with self._lock:
            if self._creds is None or time.time() >= self._expires_at:
                self._fetch()
            return {
                "Version":         1,
                "AccessKeyId":     self._creds["accessKeyId"],
                "SecretAccessKey": self._creds["secretAccessKey"],
                "SessionToken":    self._creds["sessionToken"],
                "Expiration":      self._creds["expiration"],
            }
