# caddify log layers

| Path | Layer | Source |
|------|-------|--------|
| `caddy/access/<domain>.log` | access | Per-domain HTTP access (level from dashboard / `--log`) |
| `caddy/error.log` | error | Caddy process / TLS / config errors |
| `app/caddify.log` | app | Dashboard + MCP (`LOG_LEVEL`) |
| `app/audit.log` | audit | Login, route CRUD, cert, reload |
| `archive/*.tar.gz` | — | Bundles created before/after S3 upload |

Upload: set `LOG_S3_ENABLED=1` in `.env`, then `./proxy up`.  
Manual: `./proxy logs upload`.
