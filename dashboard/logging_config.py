"""Structured logging for caddify dashboard — app + audit layers."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_LEVEL_NAME = os.environ.get("LOG_LEVEL", "INFO").upper().strip()


def _default_logs_dir() -> Path:
    if os.environ.get("LOGS_DIR"):
        return Path(os.environ["LOGS_DIR"])
    # Inside the dashboard container DATA_DIR is /data; locally prefer ./logs/app
    data = Path(os.environ.get("DATA_DIR", ""))
    if data.parts and data.as_posix() != "." and (data / "logs").exists():
        return data / "logs" / "app"
    if Path("/data/logs").exists() or Path("/data").is_dir():
        return Path("/data/logs/app")
    return Path(__file__).resolve().parent.parent / "logs" / "app"


LOGS_DIR = _default_logs_dir()

# Layer names used in JSON output
LAYER_APP = "app"
LAYER_AUDIT = "audit"
LAYER_ACCESS = "access"  # reserved for Caddy (file-only)
LAYER_ERROR = "error"  # reserved for Caddy (file-only)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "layer": getattr(record, "layer", LAYER_APP),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("event", "domain", "port", "host", "ssl_mode", "ok", "detail", "client"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def _level() -> int:
    return getattr(logging, LOG_LEVEL_NAME, logging.INFO)


def setup_logging() -> None:
    """Configure root + named loggers once at process start."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if getattr(root, "_caddify_configured", False):
        return
    root.setLevel(_level())

    fmt = JsonFormatter()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(_level())
    console.setFormatter(fmt)
    root.addHandler(console)

    app_file = RotatingFileHandler(
        LOGS_DIR / "caddify.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_file.setLevel(_level())
    app_file.setFormatter(fmt)
    root.addHandler(app_file)

    audit_logger = logging.getLogger("caddify.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = True
    audit_file = RotatingFileHandler(
        LOGS_DIR / "audit.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    audit_file.setLevel(logging.INFO)
    audit_file.setFormatter(fmt)
    audit_logger.addHandler(audit_file)

    # Quiet noisy libs unless DEBUG
    if _level() > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("docker").setLevel(logging.WARNING)

    root._caddify_configured = True  # type: ignore[attr-defined]
    logging.getLogger("caddify").info(
        "logging ready",
        extra={"layer": LAYER_APP, "event": "logging_ready", "detail": LOG_LEVEL_NAME},
    )


def get_logger(name: str = "caddify") -> logging.Logger:
    return logging.getLogger(name)


def audit(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Write an audit-layer event (also mirrored to app log via propagate)."""
    logger = logging.getLogger("caddify.audit")
    extra: dict[str, Any] = {"layer": LAYER_AUDIT, "event": event}
    extra.update(fields)
    logger.log(level, event, extra=extra)


def caddy_log_level() -> str:
    """Map LOG_LEVEL to a Caddy log level."""
    mapping = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARN",
        "WARN": "WARN",
        "ERROR": "ERROR",
        "CRITICAL": "ERROR",
    }
    return mapping.get(LOG_LEVEL_NAME, "INFO")
