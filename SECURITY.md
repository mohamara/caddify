# Security Policy

## Supported versions

Security fixes are applied to the latest commit on the default branch. If you
are running an older checkout, update before reporting issues that may already
be fixed.

## Reporting a vulnerability

Please **do not** open a public GitHub/GitLab issue for security vulnerabilities.

Instead, report privately via one of:

- GitHub/GitLab **private vulnerability report** (preferred, if enabled on the repo)
- Email or contact linked from the maintainer profile: **mohamara**

Include as much detail as you can:

- Affected version / commit
- Steps to reproduce
- Impact (e.g. unauthenticated access, SSRF, container escape)
- Any suggested fix

You should receive an acknowledgment within a few days. After a fix is available,
we will coordinate disclosure timing with you.

## Security model (what to know before deploying)

caddify is designed for a **trusted single-server** setup. Treat it like a
privileged admin tool, not a multi-tenant SaaS control plane.

### Dashboard

- Protected by a shared password (`DASHBOARD_PASSWORD` in `.env`).
- Default password is `changeme` — **change it before exposing the host**.
- Set a strong `DASHBOARD_SECRET` for session cookies.
- Prefer binding the dashboard to a private network, VPN, or SSH tunnel rather
  than the public internet. The default port is `9090`.

### Docker socket

The dashboard container mounts `/var/run/docker.sock` so it can reload Caddy.
Anyone who can authenticate to the dashboard (or break into that container)
effectively has **Docker control** on the host.

Mitigations:

- Use a strong dashboard password and secret
- Restrict who can reach port `9090`
- Keep the host and Docker Engine patched
- Do not share `.env` or `routes.conf` in public repos

### TLS / ACME

- With SSL enabled (default), Caddy obtains certificates from Let's Encrypt.
- Ports **80** and **443** must be reachable from the internet for HTTP-01.
- Use a real `ACME_EMAIL` so you receive expiry notices.
- `--no-ssl` / `nossl` routes serve **plain HTTP** only — use only on trusted
  networks or behind another TLS terminator.

### MCP (optional)

If you expose the MCP HTTP transport (`./bin/mcp --http`):

- Set a long random `CADDIFY_MCP_TOKEN`
- Prefer binding to localhost / VPN; token is required for non-loopback binds
- Treat MCP access like dashboard access — it can add/remove public routes

### Secrets and local files

Never commit:

- `.env`
- Production `routes.conf` with internal hostnames/IPs (if sensitive)
- Caddy volume data (`caddy_data`)

`.gitignore` already ignores `.env`.

## Hardening checklist

- [ ] Change `DASHBOARD_PASSWORD` and `DASHBOARD_SECRET`
- [ ] Set `ACME_EMAIL` to a mailbox you monitor
- [ ] Firewall: only expose 80/443 publicly; lock down 9090
- [ ] Point DNS only for domains you intend to serve
- [ ] Keep Docker, the OS, and this repo updated
