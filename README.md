# airborne-sync

Keycloak-authenticated access to Airborne SMCE S3 buckets for people who do not have their own AWS credentials. You log in with your Keycloak account, and `airborne-sync` supplies short-lived AWS credentials to standard S3 tools, which do the actual transfer: the [AWS CLI](https://aws.amazon.com/cli/), [s5cmd](https://github.com/peak/s5cmd) and [rclone](https://rclone.org/). All of them are installed together in a [pixi](https://pixi.sh) environment. Credentials are refreshed automatically for the length of your Keycloak session (up to 10 hours).

**Downloading large amounts of data from these buckets can incur high inter-regional data transfer costs. If you need to extract large amounts of data from a supported bucket, please contact admin.**

## Installation

1. Install [pixi](https://pixi.sh/latest/#installation) if you don't have it:

   ```bash
   curl -fsSL https://pixi.sh/install.sh | bash
   ```

   On Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"`

2. Clone the repository and install the environment:

   ```bash
   git clone https://github.com/EvanDLang/airborne-sync.git
   cd airborne-sync
   pixi install
   ```

   This installs `airborne-sync`, the AWS CLI v2, s5cmd and rclone into `.pixi/` inside the repository. Nothing is installed system-wide, and you do not need an AWS account or to run `aws configure`.

3. Activate the environment in your shell:

   ```bash
   pixi shell
   ```

   All commands below assume an activated environment. Alternatively, prefix a single command with `pixi run`, for example `pixi run airborne-sync login`.

## Quick start

```bash
airborne-sync login            # log in with Keycloak (once per session)
airborne-sync setup-profile    # one time: adds an "airborne" profile to ~/.aws/config
airborne-sync list-buckets     # see which buckets you can use

aws s3 sync ./data s3://airborne-smce-prod-user-bucket/mydata --profile airborne
```

`setup-profile` records the full path to `airborne-sync` inside the pixi environment, so the profile keeps working in shells where the environment is not activated. If you move or delete the repository, run `setup-profile` again (after removing the old `[profile airborne]` section).

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

Sessions last up to **10 hours**. The access token (5 min) is silently refreshed in the background, so you are not prompted to re-authenticate mid-upload. After 10 hours the session expires and you need to run `airborne-sync login` again.

If a long `aws s3 sync` is interrupted by session expiry, log in again and re-run the same command: files that were already uploaded are skipped.

If a command is run without a valid session:

```
Not logged in. Run 'airborne-sync login' first.
```

If the session has expired:

```
Session expired. Run 'airborne-sync login' to authenticate.
```

## Setting up the AWS profile

```bash
airborne-sync setup-profile
```

This appends the following to `~/.aws/config` (or `$AWS_CONFIG_FILE`):

```ini
[profile airborne]
region = us-west-2
credential_process = /path/to/airborne-sync/.pixi/envs/default/bin/airborne-sync credential-process
```

[`credential_process`](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html) tells the AWS CLI, s5cmd and rclone to run `airborne-sync credential-process` whenever they need credentials. That command uses your cached Keycloak session to fetch temporary AWS credentials and prints them as JSON. You never need to run it yourself.

Use `--profile NAME` to pick a different profile name. If the profile already exists, the command does not modify your config; it prints what the section should contain so you can edit it manually.

Instead of passing `--profile airborne` to every command, you can set it for your shell:

```bash
export AWS_PROFILE=airborne
```

## How credentials work

There are two credential layers, both managed automatically:

**Keycloak tokens**: your access token is valid for 5 minutes. `airborne-sync` holds a refresh token and silently renews the access token when needed. This continues for up to 10 hours (the SSO session maximum), after which you need to run `airborne-sync login` again.

**AWS STS credentials**: when an AWS tool needs credentials, `airborne-sync credential-process` calls the Airborne SMCE credentials API with your Keycloak token to obtain short-lived AWS STS credentials (1 hour TTL). These are scoped by a session policy built from your Keycloak group memberships: you can only access the S3 buckets your groups permit, regardless of what path you provide.

**Automatic refresh during transfers**: the AWS CLI, s5cmd and rclone track the credential expiry and run `credential_process` again before it is reached, so long transfers continue without interruption:

```
aws s3 sync (credentials close to expiry)
  → runs airborne-sync credential-process
    → check Keycloak token expiry
      → if expiring: refresh via refresh_token grant
    → POST /s3-credentials with fresh Keycloak token
      → Lambda builds session policy from your Keycloak groups
      → STS issues new 1-hour credentials
  → transfer continues
```

## Access control

Access is determined entirely by your Keycloak group memberships. The credentials Lambda translates your groups into an AWS session policy at credential-fetch time. Users with no group memberships can only access the base shared buckets. You cannot access buckets outside your policy regardless of what S3 URI you provide.

---

## Usage

### List your accessible buckets

```bash
airborne-sync list-buckets
```

### List objects at a prefix

```bash
aws s3 ls s3://airborne-smce-prod-user-bucket/mydata/ --profile airborne
```

### Upload a single file

```bash
aws s3 cp ./data/flight_20240301.nc s3://airborne-smce-prod-user-bucket/mydata/ --profile airborne
```

### Upload a directory

```bash
aws s3 sync ./data s3://airborne-smce-prod-user-bucket/mydata --profile airborne
```

Only files that are missing on S3, or whose size or modification time differ, are uploaded. Re-running the same command is safe and efficient.

Useful options (see `aws s3 sync help` for all of them):

```bash
--dryrun                 # show what would be uploaded without transferring
--delete                 # delete files on S3 that no longer exist locally
--exclude "*.tmp"        # skip matching files
--exclude "*" --include "*.nc"   # only upload matching files
```

---

## Performance tuning for large uploads

### AWS CLI

The AWS CLI's transfer settings live in the profile. For multi-terabyte uploads on a fast connection, add an `s3` block to the `airborne` profile in `~/.aws/config`:

```ini
[profile airborne]
region = us-west-2
credential_process = /path/to/airborne-sync/.pixi/envs/default/bin/airborne-sync credential-process
s3 =
  max_concurrent_requests = 32
  multipart_chunksize = 64MB
```

| Setting | AWS CLI default | Description |
|---|---|---|
| `max_concurrent_requests` | `10` | Parallel requests across all files and parts |
| `multipart_chunksize` | `8MB` | Part size for multipart uploads (raised automatically for very large files) |
| `multipart_threshold` | `8MB` | Files larger than this are uploaded in parallel parts |

Peak memory is roughly `max_concurrent_requests × multipart_chunksize`. Increasing concurrency stops helping once your network connection is saturated. See the [AWS CLI S3 configuration docs](https://docs.aws.amazon.com/cli/latest/topic/s3-config.html) for more settings.

## Using s5cmd

[s5cmd](https://github.com/peak/s5cmd) is a high-performance S3 client that is usually much faster than the AWS CLI, particularly for directories containing many files.

s5cmd's `--profile` flag only reads `~/.aws/credentials` and ignores `credential_process`. Select the profile with environment variables instead:

```bash
export AWS_PROFILE=airborne
export AWS_SDK_LOAD_CONFIG=1
```

Then:

```bash
# upload a directory (the trailing slash on the source means "the contents of")
s5cmd sync ./data/ s3://airborne-smce-prod-user-bucket/mydata/

# upload a single file
s5cmd cp ./data/flight_20240301.nc s3://airborne-smce-prod-user-bucket/mydata/

# list objects
s5cmd ls s3://airborne-smce-prod-user-bucket/mydata/
```

Useful options:

```bash
s5cmd --dry-run sync ...                 # show what would be uploaded
s5cmd sync --delete ...                  # delete files on S3 that no longer exist locally
s5cmd sync --size-only ...               # compare by size only
s5cmd --numworkers 64 sync ...           # parallel files (default 256)
s5cmd cp --concurrency 16 --part-size 64 ...   # parts per file (default 5) and part size in MB (default 50)
```

Always try a new `sync` command with `--dry-run` first.

Unlike the AWS CLI, s5cmd requires a trailing `/` on an S3 destination that is a prefix: `s3://bucket/mydata/` works, while `s3://bucket/mydata` fails with `target ... must be a bucket or a prefix`. Global options such as `--dry-run` and `--numworkers` go **before** the command (`s5cmd --dry-run sync ...`), not after it.

## Using rclone

[rclone](https://rclone.org/) can use the same profile. Create a remote once:

```bash
rclone config create airborne s3 provider=AWS env_auth=true profile=airborne region=us-west-2
```

Then refer to buckets as `airborne:bucket/prefix`:

```bash
# upload new and changed files (never deletes anything on S3)
rclone copy ./data airborne:airborne-smce-prod-user-bucket/mydata -P

# list objects
rclone ls airborne:airborne-smce-prod-user-bucket/mydata
```

**Note:** `rclone sync` makes the destination an exact mirror of the source, which **deletes** files on S3 that are not present locally. It behaves like `aws s3 sync --delete`. Use `rclone copy` unless you want that.

Useful options: `--dry-run`, `-P` (progress), `--transfers 16` (parallel files, default 4), `--s3-upload-concurrency 8` (parts per file, default 4), `--s3-chunk-size 64M` (part size, default 5M).
