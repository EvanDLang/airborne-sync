"""CLI entry point for airborne-sync."""

import argparse
import configparser
import json
import os
import shutil
import sys
from pathlib import Path

from .auth import device_flow, load_session, SessionExpiredError
from .credentials import CredentialManager
from . import config
from . import token_cache


def _aws_config_path() -> Path:
    return Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config")).expanduser()


def _credential_process_command() -> str:
    """Command line the AWS CLI should run to fetch credentials."""
    exe = shutil.which("airborne-sync")
    parts = [exe] if exe else [sys.executable, "-m", "airborne_sync.cli"]
    parts.append("credential-process")
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def _cmd_credential_process(args) -> None:
    # stdout must contain only the credentials JSON; errors go to stderr.
    try:
        cred_manager = CredentialManager(load_session())
        output = cred_manager.credential_process_output()
    except SessionExpiredError as e:
        sys.exit(str(e))
    except Exception as e:
        sys.exit(f"airborne-sync: failed to fetch credentials: {e}")
    json.dump(output, sys.stdout)


def _cmd_setup_profile(args) -> None:
    path = _aws_config_path()
    section = "default" if args.profile == "default" else f"profile {args.profile}"
    command = _credential_process_command()

    parser = configparser.RawConfigParser()
    if path.exists():
        parser.read(path)
    if parser.has_section(section):
        sys.exit(
            f"Profile '{args.profile}' already exists in {path}. Edit it manually so it contains:\n\n"
            f"  [{section}]\n"
            f"  region = {config.AWS_REGION}\n"
            f"  credential_process = {command}\n"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    separator = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with open(path, "a") as f:
        f.write(
            f"{separator}[{section}]\n"
            f"region = {config.AWS_REGION}\n"
            f"credential_process = {command}\n"
        )
    print(f"Added profile '{args.profile}' to {path}")
    print(f"\nTry it:  aws s3 ls --profile {args.profile}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="airborne-sync",
        description="Keycloak-authenticated AWS credentials for Airborne SMCE S3 buckets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  airborne-sync login
  airborne-sync setup-profile
  airborne-sync list-buckets
  aws s3 sync ./data s3://airborne-smce-prod-user-bucket/mydata --profile airborne
  airborne-sync logout
        """,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("login", help="Log in via Keycloak and cache the session")
    sub.add_parser("logout", help="Delete the cached session")
    sub.add_parser("list-buckets", help="List buckets you have access to")
    sub.add_parser(
        "credential-process",
        help="Print temporary AWS credentials as JSON (used by the AWS credential_process setting)",
    )
    setup = sub.add_parser("setup-profile", help="Add an AWS CLI profile that uses airborne-sync credentials")
    setup.add_argument(
        "--profile",
        default=config.AWS_PROFILE,
        help=f"AWS profile name to create (default: {config.AWS_PROFILE})",
    )

    args = parser.parse_args()

    if args.command == "login":
        token_response = device_flow()
        token_cache.save(token_response)
        print("Login successful. Session saved to ~/.airborne/token.json")
        return

    if args.command == "logout":
        token_cache.delete()
        print("Logged out.")
        return

    if args.command == "credential-process":
        _cmd_credential_process(args)
        return

    if args.command == "setup-profile":
        _cmd_setup_profile(args)
        return

    if args.command == "list-buckets":
        try:
            cred_manager = CredentialManager(load_session())
            buckets = cred_manager.buckets
        except SessionExpiredError as e:
            sys.exit(str(e))
        print("\nBuckets you have access to:")
        for b in buckets:
            print(f"  s3://{b}")
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
