"""
Configuration for the Airborne SMCE S3 sync tool.

All values can be overridden by environment variables with the same name,
prefixed with AIRBORNE_. For example:
    AIRBORNE_KEYCLOAK_URL=https://... airborne-sync ...
"""

import os

# ---------------------------------------------------------------------------
# Keycloak
# ---------------------------------------------------------------------------
KEYCLOAK_URL      = os.environ.get("AIRBORNE_KEYCLOAK_URL",      "https://auth.airborne.smce.nasa.gov/auth")
KEYCLOAK_REALM    = os.environ.get("AIRBORNE_KEYCLOAK_REALM",    "airborne-smce")
KEYCLOAK_CLIENT_ID = os.environ.get("AIRBORNE_KEYCLOAK_CLIENT_ID", "s3_upload")

# ---------------------------------------------------------------------------
# Credentials API (API Gateway → Lambda)
# ---------------------------------------------------------------------------
CREDENTIALS_API   = os.environ.get("AIRBORNE_CREDENTIALS_API",   "https://upload.airborne.smce.nasa.gov/s3-credentials")

# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------
AWS_REGION        = os.environ.get("AIRBORNE_AWS_REGION",        "us-west-2")

# ---------------------------------------------------------------------------
# Credential refresh buffer
# Fetch new STS credentials when this many seconds remain before expiry
# ---------------------------------------------------------------------------
CRED_REFRESH_BUFFER_SECS = int(os.environ.get("AIRBORNE_CRED_REFRESH_BUFFER_SECS", 5 * 60))

# ---------------------------------------------------------------------------
# Transfer tuning
# ---------------------------------------------------------------------------
MULTIPART_THRESHOLD  = int(os.environ.get("AIRBORNE_MULTIPART_THRESHOLD",  100 * 1024 * 1024))  # 100 MB
MULTIPART_CHUNKSIZE  = int(os.environ.get("AIRBORNE_MULTIPART_CHUNKSIZE",  100 * 1024 * 1024))  # 100 MB parts
MAX_CONCURRENCY      = int(os.environ.get("AIRBORNE_MAX_CONCURRENCY",      8))   # parallel parts per file
MAX_FILE_CONCURRENCY = int(os.environ.get("AIRBORNE_MAX_FILE_CONCURRENCY", 4))   # parallel files in directory sync
