"""Thin wrapper around the caddify `./proxy` CLI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class ProxyError(RuntimeError):
    """Raised when the proxy CLI fails."""


def find_root() -> Path:
    env = os.environ.get("CADDIFY_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "proxy").is_file():
            return root
        raise ProxyError(f"CADDIFY_ROOT={root} has no ./proxy script")

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "proxy"
        if candidate.is_file():
            return parent
    raise ProxyError(
        "Cannot find caddify root. Set CADDIFY_ROOT to the directory that contains ./proxy"
    )


def run_proxy(*args: str, timeout: int = 120) -> str:
    root = find_root()
    cmd = [str(root / "proxy"), *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProxyError(f"proxy timed out: {' '.join(cmd)}") from exc

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = err or out or f"exit {proc.returncode}"
        raise ProxyError(detail)
    if out and err:
        return f"{out}\n{err}"
    return out or err or "ok"
