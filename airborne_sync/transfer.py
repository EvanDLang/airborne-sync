"""S3 upload, download, and directory sync logic."""

import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from boto3.s3.transfer import TransferConfig

from . import config


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _fmt_duration(seconds: float) -> str:
    if not seconds or seconds != seconds:  # 0 or NaN/inf guard
        return "--:--"
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _transfer_config() -> TransferConfig:
    return TransferConfig(
        multipart_threshold=config.MULTIPART_THRESHOLD,
        multipart_chunksize=config.MULTIPART_CHUNKSIZE,
        max_concurrency=config.MAX_CONCURRENCY,
        use_threads=True,
    )


# ---------------------------------------------------------------------------
# ETag comparison (skip already-synced files)
# ---------------------------------------------------------------------------

def _etag_for_file(path: Path, chunk_size: int) -> str:
    """
    Compute the ETag S3 would assign to this file.
    Single-part: MD5 of file. Multipart: MD5 of concatenated part MD5s + part count.
    """
    size = path.stat().st_size
    if size < chunk_size:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return f'"{h.hexdigest()}"'

    part_md5s = []
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            part_md5s.append(hashlib.md5(chunk).digest())

    combined = b"".join(part_md5s)
    return f'"{hashlib.md5(combined).hexdigest()}-{len(part_md5s)}"'


# ---------------------------------------------------------------------------
# S3 listing
# ---------------------------------------------------------------------------

def _list_s3_objects(s3, bucket: str, prefix: str) -> dict[str, dict]:
    """Return {key: {etag, size, last_modified}} for all objects under prefix."""
    objects = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            objects[obj["Key"]] = {
                "etag":          obj["ETag"],
                "size":          obj["Size"],
                "last_modified": obj["LastModified"],
            }
    return objects


# ---------------------------------------------------------------------------
# Single file transfer
# ---------------------------------------------------------------------------

def upload_file(s3, local_path: Path, bucket: str, key: str, size: int) -> None:
    """Upload a single local file to S3 with progress output."""
    uploaded = [0]
    start = time.monotonic()

    def _progress(chunk: int) -> None:
        uploaded[0] += chunk
        elapsed = time.monotonic() - start
        speed = uploaded[0] / elapsed if elapsed > 0 else 0
        pct = uploaded[0] / size * 100 if size else 100
        eta = (size - uploaded[0]) / speed if speed > 0 else 0
        sys.stdout.write(
            f"\r  {key}  {pct:5.1f}%  "
            f"{_fmt_bytes(uploaded[0])}/{_fmt_bytes(size)}  "
            f"{_fmt_bytes(int(speed))}/s  "
            f"eta {_fmt_duration(eta):<10}"
        )
        sys.stdout.flush()

    s3.upload_file(str(local_path), bucket, key, Config=_transfer_config(), Callback=_progress)
    elapsed = time.monotonic() - start
    sys.stdout.write(f"\r  {key}  done  {_fmt_bytes(size)}  {_fmt_duration(elapsed):<20}\n")
    sys.stdout.flush()


def download_file(s3, bucket: str, key: str, local_path: Path, size: int) -> None:
    """Download a single S3 object to a local path with progress output."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    downloaded = [0]
    start = time.monotonic()

    def _progress(chunk: int) -> None:
        downloaded[0] += chunk
        elapsed = time.monotonic() - start
        speed = downloaded[0] / elapsed if elapsed > 0 else 0
        pct = downloaded[0] / size * 100 if size else 100
        eta = (size - downloaded[0]) / speed if speed > 0 else 0
        sys.stdout.write(
            f"\r  {key}  {pct:5.1f}%  "
            f"{_fmt_bytes(downloaded[0])}/{_fmt_bytes(size)}  "
            f"{_fmt_bytes(int(speed))}/s  "
            f"eta {_fmt_duration(eta):<10}"
        )
        sys.stdout.flush()

    s3.download_file(bucket, key, str(local_path), Config=_transfer_config(), Callback=_progress)
    elapsed = time.monotonic() - start
    sys.stdout.write(f"\r  {key}  done  {_fmt_bytes(size)}  {_fmt_duration(elapsed):<20}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------

def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Expected s3://bucket/prefix, got: {uri}")
    parts = uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix


# ---------------------------------------------------------------------------
# Directory sync
# ---------------------------------------------------------------------------

def list_objects(s3, bucket: str, prefix: str) -> None:
    """Print the immediate contents (files and folders) at a bucket/prefix."""
    remote_prefix = prefix.rstrip("/") + "/" if prefix else ""

    paginator = s3.get_paginator("list_objects_v2")
    folders = []
    files = []

    for page in paginator.paginate(Bucket=bucket, Prefix=remote_prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
        for obj in page.get("Contents", []):
            if obj["Key"] == remote_prefix:
                continue  # skip the prefix key itself
            files.append(obj)

    if not folders and not files:
        print(f"  No objects found at s3://{bucket}/{remote_prefix}")
        return

    print(f"\n  {'Name':<60} {'Size':>12}  Last Modified")
    print(f"  {'-'*60} {'-'*12}  {'-'*20}")

    for folder in sorted(folders):
        name = folder[len(remote_prefix):]
        print(f"  {name:<60} {'DIR':>12}")

    for obj in sorted(files, key=lambda o: o["Key"]):
        name = obj["Key"][len(remote_prefix):]
        modified = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {name:<60} {_fmt_bytes(obj['Size']):>12}  {modified}")

    print(f"\n  {len(folders)} folder(s), {len(files)} file(s)")


def sync_local_to_s3(s3, local_dir: Path, bucket: str, prefix: str, delete: bool, dry_run: bool) -> None:
    print(f"Scanning {local_dir} ...")
    local_files: dict[str, Path] = {
        p.relative_to(local_dir).as_posix(): p
        for p in local_dir.rglob("*")
        if p.is_file()
    }

    remote_prefix = prefix.rstrip("/") + "/" if prefix else ""
    print(f"Scanning s3://{bucket}/{remote_prefix} ...")
    remote_objects = _list_s3_objects(s3, bucket, remote_prefix)

    to_upload = []
    for rel, local_path in local_files.items():
        key  = remote_prefix + rel
        size = local_path.stat().st_size
        if key in remote_objects:
            remote = remote_objects[key]
            if remote["size"] == size:
                if _etag_for_file(local_path, config.MULTIPART_CHUNKSIZE) == remote["etag"]:
                    continue
        to_upload.append((rel, local_path, key, size))

    to_delete = [
        key for key in remote_objects
        if delete and key[len(remote_prefix):] not in local_files
    ]

    if not to_upload and not to_delete:
        print("Everything up to date.")
        return

    print(f"\n  {len(to_upload)} file(s) to upload, {len(to_delete)} to delete\n")

    if dry_run:
        for _, _, key, size in to_upload:
            print(f"  upload  {key}  ({_fmt_bytes(size)})")
        for key in to_delete:
            print(f"  delete  {key}")
        return

    def _do_upload(item):
        _, local_path, key, size = item
        try:
            upload_file(s3, local_path, bucket, key, size)
        except Exception as e:
            print(f"\n  ERROR uploading {key}: {e}")
            raise

    with ThreadPoolExecutor(max_workers=config.MAX_FILE_CONCURRENCY) as executor:
        futures = {executor.submit(_do_upload, item): item for item in to_upload}
        for future in as_completed(futures):
            future.result()

    for key in to_delete:
        print(f"  delete  {key}")
        s3.delete_object(Bucket=bucket, Key=key)


def sync_s3_to_local(s3, bucket: str, prefix: str, local_dir: Path, delete: bool, dry_run: bool) -> None:
    remote_prefix = prefix.rstrip("/") + "/" if prefix else ""
    print(f"Scanning s3://{bucket}/{remote_prefix} ...")
    remote_objects = _list_s3_objects(s3, bucket, remote_prefix)

    print(f"Scanning {local_dir} ...")
    local_files: dict[str, Path] = {
        p.relative_to(local_dir).as_posix(): p
        for p in local_dir.rglob("*")
        if p.is_file()
    }

    to_download = []
    for key, meta in remote_objects.items():
        rel        = key[len(remote_prefix):]
        local_path = local_dir / rel
        size       = meta["size"]
        if local_path.exists() and local_path.stat().st_size == size:
            if _etag_for_file(local_path, config.MULTIPART_CHUNKSIZE) == meta["etag"]:
                continue
        to_download.append((key, local_path, size))

    to_delete = [
        local_files[rel]
        for rel in local_files
        if delete and (remote_prefix + rel) not in remote_objects
    ]

    if not to_download and not to_delete:
        print("Everything up to date.")
        return

    print(f"\n  {len(to_download)} file(s) to download, {len(to_delete)} to delete\n")

    if dry_run:
        for key, _, size in to_download:
            print(f"  download  {key}  ({_fmt_bytes(size)})")
        for p in to_delete:
            print(f"  delete  {p}")
        return

    def _do_download(item):
        key, local_path, size = item
        try:
            download_file(s3, bucket, key, local_path, size)
        except Exception as e:
            print(f"\n  ERROR downloading {key}: {e}")
            raise

    with ThreadPoolExecutor(max_workers=config.MAX_FILE_CONCURRENCY) as executor:
        futures = {executor.submit(_do_download, item): item for item in to_download}
        for future in as_completed(futures):
            future.result()

    for p in to_delete:
        print(f"  delete  {p}")
        p.unlink()
