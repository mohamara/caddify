# Logging + S3 design (approved)

Date: 2026-07-12  
Approach: **B** — layered local logs + optional `log-shipper` sidecar

## Layers

| Layer | Writer | Path |
|-------|--------|------|
| access | Caddy site `log` | `logs/caddy/access.log` |
| error | Caddy global `log` | `logs/caddy/error.log` |
| app | dashboard / MCP (`LOG_LEVEL`) | `logs/app/caddify.log` |
| audit | dashboard auth + route mutations | `logs/app/audit.log` |

Levels: `DEBUG` | `INFO` | `WARNING` | `ERROR` via `LOG_LEVEL`.

## S3

- Service: `log-shipper` (boto3), always in compose; no-ops when `LOG_S3_ENABLED=0`
- Manual: `./proxy logs upload` → `--once --force`
- Compatible with AWS S3, MinIO, R2 (`LOG_S3_ENDPOINT`)

## Operator UX

- `./proxy logs [access|error|app|audit|upload]`
- Env documented in `.env.example` and README
