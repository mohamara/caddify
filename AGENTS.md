# Agent notes — caddify edge

This repository is **caddify**: the server-edge reverse proxy (Caddy + CLI + dashboard + MCP).

If you are building or deploying an **application** on a host where caddify is already running:

1. Bind the app to a local port over **plain HTTP** (`127.0.0.1:PORT`).
2. Do **not** add nginx, Caddy, Traefik, certbot, or app-level public TLS for that domain.
3. Register the route with caddify MCP / `./proxy add <domain> <port>`.
4. Point DNS at this server; HTTPS is handled at the edge when SSL is enabled.

Use the **caddify** MCP (`caddify_guidance`, `caddify_add_route`, …) when available. See `mcp/README.md`.
