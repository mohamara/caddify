"""CLI entry: python -m caddify_mcp [--http] [--host] [--port]."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="caddify_mcp",
        description="caddify MCP server (stdio or streamable HTTP)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run Streamable HTTP transport instead of stdio",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CADDIFY_MCP_HOST", "127.0.0.1"),
        help="HTTP bind address (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CADDIFY_MCP_PORT", "9100")),
        help="HTTP port (default 9100)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("CADDIFY_MCP_TOKEN"),
        help="Bearer token for HTTP (or set CADDIFY_MCP_TOKEN)",
    )
    args = parser.parse_args(argv)

    # Ensure package imports resolve when run as __main__
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    if args.http:
        from caddify_mcp.server import run_http

        run_http(args.host, args.port, args.token)
    else:
        from caddify_mcp.server import mcp

        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
