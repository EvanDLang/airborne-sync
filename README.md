# airborne-sync

A command-line tool for uploading files to Airborne SMCE S3 buckets. Authenticates via Keycloak using a browser-based device flow. Credentials are automatically refreshed throughout the upload when using tools such as boto3 and s3fs, AWS CLI uploads can be done in 10 hour blocks. **Currently only uploading data is supported. Syncing data to a local machine from S3 is not yet supported due the potential for high inter-regional data transfer costs. If you need to extract large amounts of data from a supported bucket, please contact admin.**

## Installation

```bash
pip install git+https://github.com/EvanDLang/airborne-sync.git@v0.1.0
```

## Authentication

Authentication is a one-time step per terminal session. Run login once and all subsequent commands use the cached session without prompting again.

### Login

```bash
airborne-sync login
```

This opens a URL in your browser via the [OAuth 2.0 Device Authorization Flow](https://www.rfc-editor.org/rfc/rfc8628):

```
  Open this URL in your browser:

    https://auth.airborne.smce.nasa.gov/auth/realms/airborne-smce/device?user_code=MJQJ-GHKL

  Or go to https://auth.airborne.smce.nasa.gov/auth/realms/airborne-smce/device and enter code: MJQJ-GHKL

  Authenticated.

Login successful. Session saved to ~/.airborne/token.json
```

Log in with your Keycloak account and the tool continues automatically. The session is saved to `~/.airborne/token.json` (permissions `600`) and reused by all subsequent commands until it expires or you log out.

### Logout

```bash
airborne-sync logout
```

Deletes the cached session file.

### Session lifetime

Sessions last up to **10 hours**. The access token (5 min) is silently refreshed in the background - you will never be prompted to re-authenticate mid-upload. After 10 hours the session expires and you need to run `airborne-sync login` again.

If a command is run without a valid session:

```
Not logged in. Run 'airborne-sync login' first.
```

If the session has expired:

```
Session expired. Run 'airborne-sync login' to authenticate.
```

## How credentials work

There are two credential layers, both managed automatically:

**Keycloak tokens** — your access token is valid for 5 minutes. The tool holds a refresh token and silently renews the access token before it expires. This continues for up to 10 hours (the SSO session maximum), after which you need to run `airborne-sync login` again.

**AWS STS credentials** — on each command the tool calls the Airborne SMCE credentials API with your Keycloak token to obtain short-lived AWS STS credentials (1 hour TTL). These are scoped by a session policy built from your Keycloak group memberships - you can only access the S3 buckets your groups permit, regardless of what path you provide.

**Automatic refresh during uploads** — the tool uses `botocore.RefreshableCredentials`, which checks expiry before every S3 API call. When credentials are within 5 minutes of expiring it transparently fetches a new set before the request proceeds. For multipart uploads this check happens before every individual part, so a 10 hour upload of a large dataset will never fail mid-transfer.

The full refresh chain on every S3 request:

```
S3 part upload
  → botocore checks STS credential expiry
    → if expiring: fetch new credentials
      → check Keycloak token expiry
        → if expiring: refresh via refresh_token grant
      → POST /s3-credentials with fresh Keycloak token
        → Lambda builds session policy from your Keycloak groups
        → STS issues new 1-hour credentials
  → proceed with upload
```

## Access control

Access is determined entirely by your Keycloak group memberships. The credentials Lambda translates your groups into an AWS session policy at credential-fetch time. Users with no group memberships can only access the base shared buckets. You cannot access buckets outside your policy regardless of what S3 URI you provide.

Downloads are not supported by this tool to avoid egress costs.

---

## Usage

### List your accessible buckets

```bash
airborne-sync --list-buckets
```

### List objects at a prefix

```bash
airborne-sync --list s3://airborne-smce-prod-user-bucket/mydata/
```

Output:

```
  Key                                                          Size  Last Modified
  ------------------------------------------------------------ ----  --------------------
  mydata/flight_20240301.nc                                  8.2 GB  2024-03-01 14:22:01
  mydata/flight_20240302.nc                                  7.9 GB  2024-03-02 09:11:44

  2 object(s)
```

### Upload a single file

```bash
airborne-sync ./data/flight_20240301.nc s3://airborne-smce-prod-user-bucket/mydata/flight_20240301.nc
```

If the destination ends with `/` or has no file extension, the filename is appended automatically:

```bash
airborne-sync ./data/flight_20240301.nc s3://airborne-smce-prod-user-bucket/mydata/
# uploads to s3://airborne-smce-prod-user-bucket/mydata/flight_20240301.nc
```

### Upload a directory

```bash
airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata
```

Only files that are missing or different on S3 are uploaded - files already present with matching content are skipped. Re-running the same command is safe and efficient.

To also delete files on S3 that no longer exist locally:

```bash
airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata --delete
```

### Preview without uploading

```bash
airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata --dry-run
```

---

## Performance tuning

Three flags control upload parallelism:

| Flag | Default | Description |
|---|---|---|
| `--concurrency N` | `4` | Parallel part uploads per file |
| `--file-concurrency N` | `2` | Parallel files during directory sync |
| `--chunk-size MB` | `100` | Size of each multipart part |

**These multiply.** Total concurrent S3 connections = `--concurrency × --file-concurrency`. Peak RAM usage = `--concurrency × --file-concurrency × --chunk-size`.

With the defaults:

```
4 parts × 2 files × 100 MB = 800 MB peak RAM, 8 concurrent S3 connections
```

For a single file upload `--file-concurrency` has no effect - only `--concurrency` matters:

```
4 parts × 100 MB = 400 MB peak RAM
```

**Recommended settings by scenario:**

```bash
# default - safe for any machine, good for most connections
airborne-sync ./data s3://bucket/prefix

# fast connection (>500 Mbps), workstation with 16 GB+ RAM
airborne-sync ./data s3://bucket/prefix --concurrency 8 --file-concurrency 4
# → 8 × 4 × 100 MB = 3.2 GB peak RAM, 32 connections

# server or HPC node, 10 Gbps network
airborne-sync ./data s3://bucket/prefix --concurrency 16 --file-concurrency 4
# → 16 × 4 × 100 MB = 6.4 GB peak RAM, 64 connections
```

**Increasing concurrency stops helping once your network pipe is saturated.** Watch the speed output - if you are already hitting your connection's limit, adding more threads adds memory pressure with no throughput gain. If you exceed available RAM the OS will start swapping and performance will collapse.

