#!/usr/bin/env python3
"""Archive local caddify logs and upload to S3-compatible storage."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

LOGS_ROOT = Path(os.environ.get("LOGS_ROOT", "/logs"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
S3_ENABLED = os.environ.get("LOG_S3_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
S3_ENDPOINT = os.environ.get("LOG_S3_ENDPOINT", "").strip() or None
S3_BUCKET = os.environ.get("LOG_S3_BUCKET", "").strip()
S3_ACCESS_KEY = os.environ.get("LOG_S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.environ.get("LOG_S3_SECRET_KEY", "").strip()
S3_REGION = os.environ.get("LOG_S3_REGION", "us-east-1").strip() or "us-east-1"
S3_PREFIX = os.environ.get("LOG_S3_PREFIX", "caddify/logs").strip().strip("/")
S3_INTERVAL = int(os.environ.get("LOG_S3_INTERVAL", "3600"))
S3_KEEP_LOCAL = os.environ.get("LOG_S3_KEEP_LOCAL", "1").lower() in {"1", "true", "yes", "on"}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [logship] %(message)s",
)
log = logging.getLogger("logship")


def collect_sources() -> list[Path]:
    """Return log files worth archiving (skip empty / archive dir)."""
    sources: list[Path] = []
    for sub in ("caddy", "app"):
        d = LOGS_ROOT / sub
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.stat().st_size == 0:
                continue
            sources.append(path)
    return sources


def build_archive(sources: list[Path]) -> Path | None:
    if not sources:
        log.info("nothing to archive")
        return None

    archive_dir = LOGS_ROOT / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = archive_dir / f"caddify-logs-{stamp}.tar.gz"

    with tarfile.open(dest, "w:gz") as tar:
        for path in sources:
            arcname = path.relative_to(LOGS_ROOT).as_posix()
            tar.add(path, arcname=arcname)

    log.info("created archive %s (%d files)", dest, len(sources))
    return dest


def upload_archive(path: Path) -> str:
    try:
        import boto3
        from botocore.client import Config
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("boto3 is required for S3 upload") from exc

    if not S3_BUCKET:
        raise SystemExit("LOG_S3_BUCKET is required when LOG_S3_ENABLED=1")
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        raise SystemExit("LOG_S3_ACCESS_KEY and LOG_S3_SECRET_KEY are required")

    key = f"{S3_PREFIX}/{path.name}" if S3_PREFIX else path.name
    client_kwargs: dict = {
        "service_name": "s3",
        "aws_access_key_id": S3_ACCESS_KEY,
        "aws_secret_access_key": S3_SECRET_KEY,
        "region_name": S3_REGION,
        "config": Config(signature_version="s3v4"),
    }
    if S3_ENDPOINT:
        client_kwargs["endpoint_url"] = S3_ENDPOINT

    client = boto3.client(**client_kwargs)
    extra = {"ContentType": "application/gzip"}
    client.upload_file(str(path), S3_BUCKET, key, ExtraArgs=extra)
    log.info("uploaded s3://%s/%s", S3_BUCKET, key)
    return key


def run_once(*, force: bool = False) -> int:
    if not S3_ENABLED and not force:
        log.info("LOG_S3_ENABLED is off — skip (use --force to upload anyway)")
        return 0

    if not S3_ENABLED and force:
        log.warning("forcing upload while LOG_S3_ENABLED is off")

    sources = collect_sources()
    archive = build_archive(sources)
    if archive is None:
        return 0

    try:
        upload_archive(archive)
    except Exception:
        log.exception("upload failed")
        return 1

    if not S3_KEEP_LOCAL:
        archive.unlink(missing_ok=True)
        log.info("removed local archive (LOG_S3_KEEP_LOCAL=0)")
    return 0


def run_loop() -> int:
    log.info(
        "log-shipper started (enabled=%s interval=%ss root=%s)",
        S3_ENABLED,
        S3_INTERVAL,
        LOGS_ROOT,
    )
    while True:
        if S3_ENABLED:
            code = run_once(force=False)
            if code != 0:
                log.error("upload cycle failed (exit %s)", code)
        else:
            log.debug("waiting — S3 upload disabled")
        time.sleep(max(60, S3_INTERVAL))


def main() -> None:
    parser = argparse.ArgumentParser(description="caddify log archive → S3")
    parser.add_argument("--once", action="store_true", help="run one archive+upload cycle and exit")
    parser.add_argument(
        "--force",
        action="store_true",
        help="upload even when LOG_S3_ENABLED is off (requires S3 credentials)",
    )
    args = parser.parse_args()

    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    (LOGS_ROOT / "caddy").mkdir(parents=True, exist_ok=True)
    (LOGS_ROOT / "app").mkdir(parents=True, exist_ok=True)

    if args.once:
        raise SystemExit(run_once(force=args.force))
    raise SystemExit(run_loop())


if __name__ == "__main__":
    main()
