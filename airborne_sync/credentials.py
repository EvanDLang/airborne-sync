"""STS credential management with automatic refresh via botocore RefreshableCredentials."""

import time
import threading
from datetime import datetime

import boto3
import botocore.session
import requests
from botocore.credentials import RefreshableCredentials

from . import config
from .auth import TokenManager


class CredentialManager:
    """
    Fetches STS credentials from the Airborne SMCE credentials Lambda and
    refreshes them automatically before they expire.

    Uses botocore RefreshableCredentials so that every S3 API call
    transparently gets fresh credentials without any manual intervention.
    Thread-safe.
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

    def get_credentials(self) -> dict:
        """
        Return a botocore-compatible credentials dict, refreshing if needed.

        This is passed as the refresh_using callable to RefreshableCredentials,
        so botocore calls it automatically before every S3 request when
        credentials are close to expiry.
        """
        with self._lock:
            if self._creds is None or time.time() >= self._expires_at:
                self._fetch()
            return {
                "access_key":  self._creds["accessKeyId"],
                "secret_key":  self._creds["secretAccessKey"],
                "token":       self._creds["sessionToken"],
                "expiry_time": self._creds["expiration"],
            }

    def build_s3_client(self):
        """
        Build a boto3 S3 client backed by RefreshableCredentials.

        The client will automatically refresh STS credentials (by calling
        get_credentials) before they expire, with no upload interruption.
        """
        refreshable = RefreshableCredentials.create_from_metadata(
            metadata=self.get_credentials(),
            refresh_using=self.get_credentials,
            method="custom-refresh",
        )

        botocore_sess = botocore.session.get_session()
        botocore_sess._credentials = refreshable

        session = boto3.Session(
            botocore_session=botocore_sess,
            region_name=config.AWS_REGION,
        )
        return session.client("s3")
