"""CLI entry point for airborne-sync."""

import argparse
import sys
from pathlib import Path

from .auth import device_flow, load_session, SessionExpiredError
from .credentials import CredentialManager
from .transfer import (
    upload_file,
    sync_local_to_s3,
    list_objects,
    parse_s3_uri,
    _fmt_bytes,
)
from . import config
from . import token_cache


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="airborne-sync",
        description="Upload files to Airborne SMCE S3 buckets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  airborne-sync login
  airborne-sync logout
  airborne-sync --list-buckets
  airborne-sync --list s3://airborne-smce-prod-user-bucket/mydata/
  airborne-sync ./data/flight.nc s3://airborne-smce-prod-user-bucket/mydata/flight.nc
  airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata
  airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata --delete
  airborne-sync ./data s3://airborne-smce-prod-user-bucket/mydata --dry-run
        """,
    )
    parser.add_argument("command_or_source", nargs="?", help="'login', 'logout', or local source path")
    parser.add_argument("dest",              nargs="?", help="Destination S3 URI (s3://bucket/prefix)")
    parser.add_argument("--delete",       action="store_true", help="Delete files in S3 not present locally")
    parser.add_argument("--dry-run",      action="store_true", help="Show what would be uploaded without transferring")
    parser.add_argument("--list-buckets", action="store_true", help="List buckets you have access to and exit")
    parser.add_argument("--list",         metavar="S3_URI",    help="List objects at s3://bucket/prefix and exit")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=config.MAX_CONCURRENCY,
        metavar="N",
        help=f"Parallel part uploads per file (default: {config.MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--file-concurrency",
        type=int,
        default=config.MAX_FILE_CONCURRENCY,
        metavar="N",
        help=f"Parallel files during directory sync (default: {config.MAX_FILE_CONCURRENCY})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=config.MULTIPART_CHUNKSIZE // (1024 * 1024),
        metavar="MB",
        help=f"Multipart chunk size in MB (default: {config.MULTIPART_CHUNKSIZE // (1024 * 1024)})",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # login / logout - handled before anything else
    # ------------------------------------------------------------------
    if args.command_or_source == "login":
        cached = token_cache.load()
        if cached is not None:
            try:
                load_session()
                print("Already logged in. Run 'airborne-sync logout' to clear your session.")
                return
            except SystemExit:
                pass  # session expired, fall through to re-authenticate
        token_response = device_flow()
        token_cache.save(token_response)
        print("Login successful. Session saved to ~/.airborne/token.json")
        return

    if args.command_or_source == "logout":
        token_cache.delete()
        print("Logged out.")
        return

    # ------------------------------------------------------------------
    # All other commands require a valid cached session
    # ------------------------------------------------------------------
    if not args.list_buckets and not args.list and (not args.command_or_source or not args.dest):
        parser.print_help()
        sys.exit(1)

    # Apply CLI overrides to config
    config.MAX_CONCURRENCY      = args.concurrency
    config.MAX_FILE_CONCURRENCY = args.file_concurrency
    config.MULTIPART_CHUNKSIZE  = args.chunk_size * 1024 * 1024

    try:
        token_manager = load_session()
    except SessionExpiredError:
        sys.exit("Session expired. Run 'airborne-sync login' to authenticate.")

    cred_manager = CredentialManager(token_manager)

    if args.list_buckets:
        print("\nBuckets you have access to:")
        for b in cred_manager.buckets:
            print(f"  s3://{b}")
        return

    if args.list:
        bucket, prefix = parse_s3_uri(args.list)
        s3 = cred_manager.build_s3_client()
        list_objects(s3, bucket, prefix)
        return

    # Enforce upload-only
    if args.command_or_source and args.command_or_source.startswith("s3://"):
        sys.exit("Error: downloads are not supported. Source must be a local path.")

    if not args.dest.startswith("s3://"):
        sys.exit("Error: destination must be an S3 URI (s3://bucket/prefix).")

    s3 = cred_manager.build_s3_client()

    local_path = Path(args.command_or_source)
    if not local_path.exists():
        sys.exit(f"Error: source path does not exist: {local_path}")

    bucket, key_or_prefix = parse_s3_uri(args.dest)

    if local_path.is_file():
        config.MAX_FILE_CONCURRENCY = 1
        if key_or_prefix.endswith("/") or "." not in Path(key_or_prefix).name:
            key = key_or_prefix.rstrip("/") + "/" + local_path.name
        else:
            key = key_or_prefix
        size = local_path.stat().st_size
        print(f"\n  Uploading {local_path.name} → s3://{bucket}/{key} ({_fmt_bytes(size)})\n")
        upload_file(s3, local_path, bucket, key, size)
    else:
        sync_local_to_s3(s3, local_path, bucket, key_or_prefix, args.delete, args.dry_run)

    print("\nSync complete.")


if __name__ == "__main__":
    main()
