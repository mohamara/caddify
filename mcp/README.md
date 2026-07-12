# caddify MCP

Give this MCP to an agent so it knows: **on an edge server running caddify, do not implement reverse proxy / TLS / Caddy / nginx / certbot inside the app.** Just bind the app to a local port and register `domain → port` here.

## What it exposes

### Guidance
- Server `instructions` + tool `caddify_guidance`
- Resources: `caddify://docs/edge-rules`, `caddify://docs/quickstart`
- Prompt: `deploy_behind_caddify`

### Route tools
| Tool | Action |
|------|--------|
| `caddify_status` | containers + routes |
| `caddify_list_routes` | list routes |
| `caddify_add_route` | add domain → port (`ssl` default true; optional `cert_file`/`key_file`) |
| `caddify_set_route` | update route |
| `caddify_set_certificate` | install manual TLS PEMs for a domain |
| `caddify_remove_route` | remove route |
| `caddify_apply` | regenerate Caddyfile + reload |

All mutating tools shell out to `./proxy` in `CADDIFY_ROOT`.

## Setup

```bash
cd /path/to/caddify
python3 -m venv mcp/.venv
mcp/.venv/bin/pip install -r mcp/requirements.txt
```

## stdio (Cursor on the edge server)

`~/.cursor/mcp.json` (or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "caddify": {
      "command": "/path/to/caddify/bin/mcp",
      "env": {
        "CADDIFY_ROOT": "/path/to/caddify"
      }
    }
  }
}
```

Or:

```json
{
  "mcpServers": {
    "caddify": {
      "command": "/path/to/caddify/mcp/.venv/bin/python",
      "args": ["-m", "caddify_mcp"],
      "cwd": "/path/to/caddify/mcp",
      "env": {
        "CADDIFY_ROOT": "/path/to/caddify"
      }
    }
  }
}
```

## HTTP (remote agents)

```bash
export CADDIFY_MCP_TOKEN="$(openssl rand -hex 32)"
# allow non-local Host headers when exposing beyond loopback:
export CADDIFY_MCP_DISABLE_DNS_PROTECTION=1

./bin/mcp --http --host 0.0.0.0 --port 9100
# endpoint: http://SERVER_IP:9100/mcp
```

Client config example:

```json
{
  "mcpServers": {
    "caddify": {
      "url": "http://SERVER_IP:9100/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

`CADDIFY_MCP_TOKEN` is **required** when `--host` is not localhost.

## Agent contract (short)

1. App listens on `127.0.0.1:PORT` (plain HTTP).
2. DNS → this server.
3. `caddify_add_route(domain, port, ssl=true)`.
4. Do **not** add edge TLS/proxy stacks into the application repository.
