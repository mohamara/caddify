"""Agent-facing guidance: what caddify owns at the server edge."""

INSTRUCTIONS = """
You are connected to the caddify MCP on an edge server.

Critical rule — DO NOT reinvent the edge in application projects:
- Do NOT add nginx, Caddy, Traefik, certbot, Let's Encrypt, or custom TLS
  termination inside the app repo when this server already runs caddify.
- Do NOT implement reverse-proxy / domain routing logic in the app.
- The app should listen on localhost (or a private host) on a plain TCP port.
- Use caddify tools (or `./proxy`) to map domain → port with optional HTTPS.

Typical agent workflow for a new service:
1. Call caddify_guidance (or read resource caddify://docs/edge-rules).
2. Deploy/run the app bound to 127.0.0.1:PORT (HTTP only).
3. Ensure DNS A/AAAA for the domain points at this server.
4. Call caddify_add_route(domain, port, ssl=true).
5. Verify with caddify_status / caddify_list_routes.

Only build in-app TLS or a local reverse proxy if the user explicitly wants
an offline/dev setup WITHOUT caddify on this host.
""".strip()

EDGE_RULES = """
# caddify edge rules

## What caddify owns
- Public ports 80 / 443
- HTTPS certificates when SSL is enabled for a route:
  - automatic Let's Encrypt, or
  - manual PEM files via `./proxy cert` / dashboard upload / `caddify_set_certificate`
- Reverse proxy: `domain` → `host:port`
- Route CRUD via CLI (`./proxy`), web dashboard, or this MCP

## What application projects must NOT implement
When deploying behind caddify on this server, skip:
- Embedding Caddy / nginx / Traefik configs in the app
- certbot / acme.sh / app-level public TLS termination
- Homegrown domain routers or "SSL middleware" for public TLS
- Exposing the app directly on 80/443

## What the app SHOULD do
1. Listen on a local port (prefer `127.0.0.1:PORT` or `0.0.0.0:PORT` on the host)
2. Serve plain HTTP to that port
3. Register the route with caddify:
   - MCP: `caddify_add_route`
   - CLI: `./proxy add app.example.com 3000`
   - Optional: `--no-ssl` / `ssl=false` for HTTP-only
   - Optional manual cert: `--cert fullchain.pem --key privkey.pem`
     or `caddify_set_certificate` / `./proxy cert <domain> --cert … --key …`

## Checklist before adding a route
- [ ] App is running and reachable on the chosen port from the Docker host
- [ ] DNS points at this server (required for Let's Encrypt / HTTPS)
- [ ] Ports 80 and 443 are open on the firewall (for HTTPS routes)
- [ ] For manual TLS: PEM files ready (fullchain + private key)
""".strip()

QUICKSTART = """
# caddify quickstart (for agents)

## Install / start (once on the edge server)
```bash
git clone https://github.com/mohamara/caddify.git
cd caddify
./proxy setup you@example.com
# set DASHBOARD_PASSWORD + DASHBOARD_SECRET in .env
```

## Expose an app
```bash
# app on localhost:3000
./proxy add app.example.com 3000          # HTTPS auto (default)
./proxy add local.test 9000 --no-ssl      # HTTP only
./proxy add secure.example.com 4430 --cert ./fullchain.pem --key ./privkey.pem
./proxy list
./proxy status
```

## MCP
- stdio: `python -m caddify_mcp` (from `mcp/` venv, with CADDIFY_ROOT set)
- HTTP:  `python -m caddify_mcp --http --host 0.0.0.0 --port 9100`
  Header: `Authorization: Bearer $CADDIFY_MCP_TOKEN`

Prefer MCP tools over editing Caddyfiles by hand.
""".strip()
