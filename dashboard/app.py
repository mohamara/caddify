"""caddify dashboard — manage domain → port routes with auto SSL."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

import docker
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.sessions import SessionMiddleware

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ROUTES_FILE = DATA_DIR / "routes.conf"
CADDYFILE = DATA_DIR / "caddy" / "Caddyfile"
ENV_FILE = DATA_DIR / ".env"
CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "caddify")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("DASHBOARD_SECRET", secrets.token_hex(32))

DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
DEFAULT_HOST = "host.docker.internal"
SSL_OFF_TOKENS = {"nossl", "http", "no-ssl", "ssl=false", "ssl=0", "false"}
SSL_ON_TOKENS = {"ssl", "https", "ssl=true", "ssl=1", "true"}


def normalize_host(host: str) -> str:
    host = (host or "").strip()
    if not host or host in {"localhost", "127.0.0.1", DEFAULT_HOST}:
        return DEFAULT_HOST
    return host


def parse_ssl_form(value: str | None) -> bool:
    """Checkbox: present/on → True; missing/empty → False."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "on", "yes", "ssl"}


app = FastAPI(title="caddify", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="ap_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
signer = URLSafeSerializer(SECRET_KEY, salt="flash")


# ── routes file ──────────────────────────────────────────────────────


def ensure_routes_file() -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ROUTES_FILE.exists():
        ROUTES_FILE.write_text(
            "# domain  port  [host]  [ssl|nossl]\n# managed by caddify dashboard\n",
            encoding="utf-8",
        )


def read_acme_email() -> str:
    if not ENV_FILE.exists():
        return os.environ.get("ACME_EMAIL", "admin@example.com")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ACME_EMAIL="):
            return line.split("=", 1)[1].strip() or "admin@example.com"
    return os.environ.get("ACME_EMAIL", "admin@example.com")


def list_routes() -> list[dict[str, Any]]:
    ensure_routes_file()
    routes: list[dict[str, Any]] = []
    for raw in ROUTES_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        domain, port = parts[0], parts[1]
        host = DEFAULT_HOST
        ssl = True
        for token in parts[2:]:
            low = token.lower()
            if low in SSL_OFF_TOKENS:
                ssl = False
            elif low in SSL_ON_TOKENS:
                ssl = True
            else:
                host = token
        routes.append({"domain": domain, "port": port, "host": host, "ssl": ssl})
    return routes


def format_route_line(route: dict[str, Any]) -> str:
    host = route.get("host") or DEFAULT_HOST
    ssl = bool(route.get("ssl", True))
    parts = [route["domain"], str(route["port"])]
    if host != DEFAULT_HOST:
        parts.append(host)
    if not ssl:
        parts.append("nossl")
    return "  ".join(parts)


def write_routes(routes: list[dict[str, Any]]) -> None:
    ensure_routes_file()
    lines = [
        "# managed by caddify dashboard / CLI",
        "# domain  port  [host]  [ssl|nossl]",
        "",
    ]
    for r in routes:
        lines.append(format_route_line(r))
    lines.append("")
    ROUTES_FILE.write_text("\n".join(lines), encoding="utf-8")


def generate_caddyfile(routes: list[dict[str, Any]]) -> None:
    email = read_acme_email()
    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    chunks = ["{", f"\temail {email}", "}", ""]
    if not routes:
        chunks += [
            ":80 {",
            '\trespond "caddify ready — open the dashboard to add domains" 200',
            "}",
            "",
        ]
    else:
        for r in routes:
            host = r.get("host") or DEFAULT_HOST
            site = r["domain"] if r.get("ssl", True) else f"http://{r['domain']}"
            chunks += [
                f"{site} {{",
                f"\treverse_proxy {host}:{r['port']}",
                "}",
                "",
            ]
    CADDYFILE.write_text("\n".join(chunks), encoding="utf-8")


def reload_caddy() -> tuple[bool, str]:
    try:
        client = docker.from_env()
        container = client.containers.get(CADDY_CONTAINER)
        if container.status != "running":
            return False, "کانتینر Caddy روشن نیست"
        result = container.exec_run(
            ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
            user="root",
        )
        if result.exit_code != 0:
            out = (result.output or b"").decode("utf-8", errors="replace")
            return False, out.strip() or "reload failed"
        return True, "اعمال شد"
    except docker.errors.NotFound:
        return False, f"کانتینر «{CADDY_CONTAINER}» پیدا نشد"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def apply_routes(routes: list[dict[str, Any]]) -> tuple[bool, str]:
    write_routes(routes)
    generate_caddyfile(routes)
    return reload_caddy()


def validate_domain(domain: str) -> str | None:
    if not DOMAIN_RE.match(domain):
        return "دامنه نامعتبر است"
    return None


def validate_port(port: str) -> str | None:
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        return "پورت نامعتبر است"
    return None


def caddy_status() -> dict[str, Any]:
    try:
        client = docker.from_env()
        container = client.containers.get(CADDY_CONTAINER)
        return {"running": container.status == "running", "status": container.status}
    except Exception:  # noqa: BLE001
        return {"running": False, "status": "unknown"}


# ── auth helpers ─────────────────────────────────────────────────────


def is_authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request) -> None:
    if not is_authed(request):
        raise HTTPException(401, "unauthorized")


def set_flash(response: Response, message: str, kind: str = "ok") -> None:
    response.set_cookie("ap_flash", signer.dumps({"m": message, "k": kind}), max_age=8, httponly=True)


def pop_flash(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get("ap_flash")
    if not raw:
        return None
    try:
        data = signer.loads(raw)
        return {"message": data["m"], "kind": data["k"]}
    except (BadSignature, KeyError, TypeError):
        return None


# ── pages ────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Any:
    if is_authed(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)) -> Any:
    if secrets.compare_digest(password, DASHBOARD_PASSWORD):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "رمز عبور اشتباه است"},
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request) -> Any:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Any:
    if not is_authed(request):
        return RedirectResponse("/login", status_code=303)

    flash = pop_flash(request)
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "routes": list_routes(),
            "email": read_acme_email(),
            "caddy": caddy_status(),
            "flash": flash,
            "default_password": DASHBOARD_PASSWORD == "changeme",
        },
    )
    if flash:
        response.delete_cookie("ap_flash")
    return response


# ── API / form actions ───────────────────────────────────────────────


@app.post("/routes")
def add_route(
    request: Request,
    domain: str = Form(...),
    port: str = Form(...),
    host: str = Form(""),
    ssl: str | None = Form(None),
) -> Any:
    require_auth(request)
    domain = domain.strip().lower()
    port = port.strip()
    host = normalize_host(host)
    use_ssl = parse_ssl_form(ssl)

    err = validate_domain(domain) or validate_port(port)
    if err:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, err, "err")
        return response

    routes = list_routes()
    if any(r["domain"] == domain for r in routes):
        response = RedirectResponse("/", status_code=303)
        set_flash(response, "این دامنه از قبل وجود دارد", "err")
        return response

    routes.append({"domain": domain, "port": port, "host": host, "ssl": use_ssl})
    ok, msg = apply_routes(routes)

    mode = "با SSL" if use_ssl else "بدون SSL (HTTP)"
    response = RedirectResponse("/", status_code=303)
    set_flash(
        response,
        f"دامنه {domain} اضافه شد — {mode}" if ok else msg,
        "ok" if ok else "err",
    )
    return response


@app.post("/routes/{domain}/update")
def update_route(
    request: Request,
    domain: str,
    port: str = Form(...),
    host: str = Form(""),
    ssl: str | None = Form(None),
) -> Any:
    require_auth(request)
    domain = domain.strip().lower()
    port = port.strip()
    host = normalize_host(host)
    use_ssl = parse_ssl_form(ssl)

    err = validate_port(port)
    if err:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, err, "err")
        return response

    routes = list_routes()
    found = False
    for r in routes:
        if r["domain"] == domain:
            r["port"] = port
            r["host"] = host
            r["ssl"] = use_ssl
            found = True
            break
    if not found:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, "دامنه پیدا نشد", "err")
        return response

    ok, msg = apply_routes(routes)
    response = RedirectResponse("/", status_code=303)
    set_flash(response, f"دامنه {domain} به‌روز شد" if ok else msg, "ok" if ok else "err")
    return response


@app.post("/routes/{domain}/delete")
def delete_route(request: Request, domain: str) -> Any:
    require_auth(request)
    domain = domain.strip().lower()
    before = list_routes()
    routes = [r for r in before if r["domain"] != domain]
    if len(routes) == len(before):
        response = RedirectResponse("/", status_code=303)
        set_flash(response, "دامنه پیدا نشد", "err")
        return response

    ok, msg = apply_routes(routes)
    response = RedirectResponse("/", status_code=303)
    set_flash(response, f"دامنه {domain} حذف شد" if ok else msg, "ok" if ok else "err")
    return response


@app.post("/reload")
def reload_now(request: Request) -> Any:
    require_auth(request)
    routes = list_routes()
    generate_caddyfile(routes)
    ok, msg = reload_caddy()
    response = RedirectResponse("/", status_code=303)
    set_flash(response, msg, "ok" if ok else "err")
    return response


@app.get("/api/status")
def api_status(request: Request) -> Any:
    require_auth(request)
    return {
        "caddy": caddy_status(),
        "routes": list_routes(),
        "email": read_acme_email(),
    }


@app.on_event("startup")
def on_startup() -> None:
    ensure_routes_file()
    generate_caddyfile(list_routes())
