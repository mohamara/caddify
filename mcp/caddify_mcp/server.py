"""caddify MCP server — guidance + route management via ./proxy."""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from caddify_mcp.guidance import EDGE_RULES, INSTRUCTIONS, QUICKSTART
from caddify_mcp.proxy_cli import ProxyError, find_root, run_proxy


def _build_mcp() -> FastMCP:
    host = os.environ.get("CADDIFY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("CADDIFY_MCP_PORT", "9100"))
    allowed_hosts = [
        h.strip()
        for h in os.environ.get(
            "CADDIFY_MCP_ALLOWED_HOSTS",
            "127.0.0.1:*,localhost:*,[::1]:*",
        ).split(",")
        if h.strip()
    ]
    # Remote HTTP: set CADDIFY_MCP_DISABLE_DNS_PROTECTION=1 or expand allowed hosts.
    disable_dns = os.environ.get("CADDIFY_MCP_DISABLE_DNS_PROTECTION", "").lower() in {
        "1",
        "true",
        "yes",
    }

    mcp = FastMCP(
        "caddify",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/mohamara/caddify",
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=not disable_dns,
            allowed_hosts=allowed_hosts,
            allowed_origins=[],
        ),
    )

    @mcp.resource("caddify://docs/edge-rules")
    def edge_rules() -> str:
        """Rules for apps behind caddify — what NOT to implement in the app."""
        return EDGE_RULES

    @mcp.resource("caddify://docs/quickstart")
    def quickstart() -> str:
        """Install and expose-an-app quickstart for agents."""
        return QUICKSTART

    @mcp.prompt()
    def deploy_behind_caddify(domain: str, port: int) -> str:
        """Prompt template: deploy an app behind caddify without reinventing the edge."""
        return (
            f"Deploy the application so it listens on 127.0.0.1:{port} over plain HTTP. "
            f"Do not add nginx/Caddy/certbot to the app project. "
            f"Then register HTTPS with caddify: add route {domain} → port {port} "
            f"(use caddify_add_route or ./proxy add). "
            f"Confirm DNS for {domain} points at this edge server."
        )

    @mcp.tool()
    def caddify_guidance() -> str:
        """
        Read mandatory edge rules: apps must not implement SSL/reverse-proxy when
        caddify is the server edge. Call this before scaffolding deploy/TLS code.
        """
        return f"{INSTRUCTIONS}\n\n{EDGE_RULES}"

    @mcp.tool()
    def caddify_status() -> str:
        """Show Docker/container status, routes, and config summary."""
        try:
            root = find_root()
            return f"root: {root}\n\n{run_proxy('status')}"
        except ProxyError as exc:
            return f"error: {exc}"

    @mcp.tool()
    def caddify_list_routes() -> str:
        """List domain → port routes managed by caddify."""
        try:
            return run_proxy("list")
        except ProxyError as exc:
            return f"error: {exc}"

    @mcp.tool()
    def caddify_add_route(
        domain: Annotated[str, Field(description="Public hostname, e.g. app.example.com")],
        port: Annotated[int, Field(description="Backend port on the host", ge=1, le=65535)],
        host: Annotated[
            str | None,
            Field(description="Backend host; default is host.docker.internal (localhost)"),
        ] = None,
        ssl: Annotated[
            bool | None,
            Field(
                description=(
                    "True = Let's Encrypt HTTPS; False = HTTP only. "
                    "Ignored when cert_file and key_file are set (manual TLS)."
                )
            ),
        ] = True,
        cert_file: Annotated[
            str | None,
            Field(description="Path to fullchain/certificate PEM for manual TLS"),
        ] = None,
        key_file: Annotated[
            str | None,
            Field(description="Path to private key PEM for manual TLS"),
        ] = None,
    ) -> str:
        """
        Map a domain to a local port. Prefer this over adding TLS/proxy code in the app.
        DNS must point here for HTTPS. App should already listen on the given port.
        For a custom certificate, pass cert_file + key_file (copies into certs/<domain>/).
        """
        args = ["add", domain, str(port)]
        if host:
            args.append(host)
        if cert_file or key_file:
            if not cert_file or not key_file:
                return "error: cert_file and key_file must both be provided for manual TLS"
            args.extend(["--cert", cert_file, "--key", key_file])
        elif ssl is False:
            args.append("--no-ssl")
        else:
            args.append("--ssl")
        try:
            return run_proxy(*args)
        except ProxyError as exc:
            return f"error: {exc}"

    @mcp.tool()
    def caddify_set_route(
        domain: Annotated[str, Field(description="Existing hostname to update")],
        port: Annotated[int, Field(description="New backend port", ge=1, le=65535)],
        host: Annotated[
            str | None,
            Field(description="Optional new backend host"),
        ] = None,
        ssl: Annotated[
            bool | None,
            Field(description="Optional SSL flag; omit to leave unchanged via CLI default path"),
        ] = None,
        cert_file: Annotated[
            str | None,
            Field(description="Optional fullchain PEM path to switch to manual TLS"),
        ] = None,
        key_file: Annotated[
            str | None,
            Field(description="Optional private key PEM path to switch to manual TLS"),
        ] = None,
    ) -> str:
        """Update an existing route's port/host/SSL (or install a manual certificate)."""
        args = ["set", domain, str(port)]
        if host:
            args.append(host)
        if cert_file or key_file:
            if not cert_file or not key_file:
                return "error: cert_file and key_file must both be provided for manual TLS"
            args.extend(["--cert", cert_file, "--key", key_file])
        elif ssl is True:
            args.append("--ssl")
        elif ssl is False:
            args.append("--no-ssl")
        try:
            return run_proxy(*args)
        except ProxyError as exc:
            return f"error: {exc}"

    @mcp.tool()
    def caddify_set_certificate(
        domain: Annotated[str, Field(description="Existing hostname")],
        cert_file: Annotated[str, Field(description="Path to fullchain/certificate PEM")],
        key_file: Annotated[str, Field(description="Path to private key PEM")],
    ) -> str:
        """Install a manual TLS certificate for a domain and switch the route to manual SSL."""
        try:
            return run_proxy("cert", domain, "--cert", cert_file, "--key", key_file)
        except ProxyError as exc:
            return f"error: {exc}"
    @mcp.tool()
    def caddify_remove_route(
        domain: Annotated[str, Field(description="Hostname to remove")],
    ) -> str:
        """Remove a domain route from caddify."""
        try:
            return run_proxy("rm", domain)
        except ProxyError as exc:
            return f"error: {exc}"

    @mcp.tool()
    def caddify_apply() -> str:
        """Regenerate the Caddyfile from routes.conf and reload Caddy."""
        try:
            return run_proxy("apply")
        except ProxyError as exc:
            return f"error: {exc}"

    return mcp


mcp = _build_mcp()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <CADDIFY_MCP_TOKEN> for HTTP transport."""

    def __init__(self, app, token: str):  # noqa: ANN001
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        if request.method == "OPTIONS":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {self._token}"
        if not secrets.compare_digest(auth, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def run_http(host: str, port: int, token: str | None) -> None:
    import uvicorn

    # Rebuild with requested bind settings
    os.environ["CADDIFY_MCP_HOST"] = host
    os.environ["CADDIFY_MCP_PORT"] = str(port)
    server = _build_mcp()
    app = server.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
    else:
        # Fail closed for non-loopback binds
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit(
                "CADDIFY_MCP_TOKEN is required when binding HTTP beyond localhost"
            )
    uvicorn.run(app, host=host, port=port, log_level="info")
