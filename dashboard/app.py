"""caddify dashboard — manage domain → port routes with auto / manual SSL."""

from __future__ import annotations

import os
import re
import secrets
import logging
from pathlib import Path
from typing import Any

import docker
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.sessions import SessionMiddleware

from i18n import get_lang, make_t, set_lang_cookie, supported, template_ctx, translate
from logging_config import audit, caddy_log_level, get_logger, setup_logging

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ROUTES_FILE = DATA_DIR / "routes.conf"
CADDYFILE = DATA_DIR / "caddy" / "Caddyfile"
CERTS_DIR = DATA_DIR / "certs"
ENV_FILE = DATA_DIR / ".env"
CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "caddify")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("DASHBOARD_SECRET", secrets.token_hex(32))
CERTS_CONTAINER = "/certs"
CADDY_LOG_DIR = "/var/log/caddy"

DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
DEFAULT_HOST = "host.docker.internal"
SSL_OFF_TOKENS = {"nossl", "http", "no-ssl", "ssl=false", "ssl=0", "false", "off"}
SSL_ON_TOKENS = {"ssl", "https", "ssl=true", "ssl=1", "true", "auto"}
SSL_MANUAL_TOKENS = {"manual", "custom", "cert", "tls-manual"}
SSL_MODES = {"auto", "off", "manual"}
LOG_LEVELS = ("off", "debug", "info", "warn", "error")
DEFAULT_LOG_LEVEL = "info"

log = get_logger("caddify.dashboard")


def parse_log_level(value: str | None) -> str:
    if value is None or not str(value).strip():
        return DEFAULT_LOG_LEVEL
    low = value.strip().lower()
    if low in {"warning", "warn"}:
        return "warn"
    if low in LOG_LEVELS:
        return low
    return DEFAULT_LOG_LEVEL


def caddy_access_level(log_level: str) -> str:
    return {
        "debug": "DEBUG",
        "info": "INFO",
        "warn": "WARN",
        "error": "ERROR",
    }.get(log_level, "INFO")


def _access_log_snippet(domain: str, log_level: str) -> list[str]:
    """Per-domain access log; empty when log_level is off."""
    if log_level == "off":
        return []
    safe = domain.replace("/", "_")
    level = caddy_access_level(log_level)
    return [
        "\tlog {",
        f"\t\toutput file {CADDY_LOG_DIR}/access/{safe}.log {{",
        "\t\t\troll_size 50mb",
        "\t\t\troll_keep 10",
        "\t\t}",
        "\t\tformat json",
        f"\t\tlevel {level}",
        "\t}",
    ]


def normalize_host(host: str) -> str:
    host = (host or "").strip()
    if not host or host in {"localhost", "127.0.0.1", DEFAULT_HOST}:
        return DEFAULT_HOST
    return host


def parse_ssl_mode(value: str | None) -> str:
    """Form select: auto | off | manual. Legacy checkbox 'on' → auto."""
    if value is None or not str(value).strip():
        return "off"
    low = value.strip().lower()
    if low in {"on", "1", "true", "yes", "ssl"}:
        return "auto"
    if low in SSL_MODES:
        return low
    return "off"


def cert_paths(domain: str) -> tuple[Path, Path]:
    base = CERTS_DIR / domain
    return base / "fullchain.pem", base / "privkey.pem"


def has_manual_certs(domain: str) -> bool:
    fullchain, privkey = cert_paths(domain)
    return fullchain.is_file() and privkey.is_file()


async def save_uploaded_certs(
    domain: str,
    cert_file: UploadFile | None,
    key_file: UploadFile | None,
) -> bool:
    """Save uploaded PEM pair. Returns True if both files were written."""
    if cert_file is None or key_file is None:
        return False
    if not cert_file.filename or not key_file.filename:
        return False

    dest = CERTS_DIR / domain
    dest.mkdir(parents=True, exist_ok=True)
    fullchain, privkey = cert_paths(domain)

    cert_bytes = await cert_file.read()
    key_bytes = await key_file.read()
    if not cert_bytes or not key_bytes:
        return False

    fullchain.write_bytes(cert_bytes)
    privkey.write_bytes(key_bytes)
    fullchain.chmod(0o644)
    privkey.chmod(0o600)
    return True


app = FastAPI(title="caddify", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="ap_session")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
signer = URLSafeSerializer(SECRET_KEY, salt="flash")


def t_req(request: Request, key: str, **kwargs: Any) -> str:
    return translate(get_lang(request), key, **kwargs)


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200) -> Any:
    ctx = template_ctx(request)
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# ── routes file ──────────────────────────────────────────────────────


def ensure_routes_file() -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ROUTES_FILE.exists():
        ROUTES_FILE.write_text(
            "# domain  port  [host]  [ssl|nossl|manual]  [log=off|debug|info|warn|error]\n"
            "# managed by caddify dashboard\n",
            encoding="utf-8",
        )


def ensure_certs_dir() -> None:
    CERTS_DIR.mkdir(parents=True, exist_ok=True)


def read_acme_email() -> str:
    if not ENV_FILE.exists():
        return os.environ.get("ACME_EMAIL", "admin@example.com")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ACME_EMAIL="):
            return line.split("=", 1)[1].strip() or "admin@example.com"
    return os.environ.get("ACME_EMAIL", "admin@example.com")


def _parse_log_token(token: str) -> str | None:
    low = token.lower()
    if low in {"nolog", "log=off", "log=false", "log=0", "log=no"}:
        return "off"
    if low.startswith("log="):
        return parse_log_level(low.split("=", 1)[1])
    return None


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
        ssl_mode = "auto"
        log_level = DEFAULT_LOG_LEVEL
        for token in parts[2:]:
            low = token.lower()
            parsed_log = _parse_log_token(token)
            if parsed_log is not None:
                log_level = parsed_log
            elif low in SSL_OFF_TOKENS:
                ssl_mode = "off"
            elif low in SSL_MANUAL_TOKENS:
                ssl_mode = "manual"
            elif low in SSL_ON_TOKENS:
                ssl_mode = "auto"
            else:
                host = token
        routes.append(
            {
                "domain": domain,
                "port": port,
                "host": host,
                "ssl_mode": ssl_mode,
                "ssl": ssl_mode != "off",
                "log_level": log_level,
                "has_certs": has_manual_certs(domain),
            }
        )
    return routes


def format_route_line(route: dict[str, Any]) -> str:
    host = route.get("host") or DEFAULT_HOST
    mode = route.get("ssl_mode") or ("auto" if route.get("ssl", True) else "off")
    log_level = parse_log_level(route.get("log_level"))
    parts = [route["domain"], str(route["port"])]
    if host != DEFAULT_HOST:
        parts.append(host)
    if mode == "off":
        parts.append("nossl")
    elif mode == "manual":
        parts.append("manual")
    if log_level != DEFAULT_LOG_LEVEL:
        parts.append(f"log={log_level}")
    return "  ".join(parts)


def write_routes(routes: list[dict[str, Any]]) -> None:
    ensure_routes_file()
    lines = [
        "# managed by caddify dashboard / CLI",
        "# domain  port  [host]  [ssl|nossl|manual]  [log=off|debug|info|warn|error]",
        "",
    ]
    for r in routes:
        lines.append(format_route_line(r))
    lines.append("")
    ROUTES_FILE.write_text("\n".join(lines), encoding="utf-8")


def generate_caddyfile(routes: list[dict[str, Any]], lang: str = "en") -> None:
    email = read_acme_email()
    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    ensure_certs_dir()
    level = caddy_log_level()
    chunks = [
        "{",
        f"\temail {email}",
        "\tlog {",
        f"\t\toutput file {CADDY_LOG_DIR}/error.log {{",
        "\t\t\troll_size 20mb",
        "\t\t\troll_keep 5",
        "\t\t}",
        "\t\tformat json",
        f"\t\tlevel {level}",
        "\t}",
        "}",
        "",
    ]
    if not routes:
        chunks += [
            ":80 {",
            *_access_log_snippet("_default", DEFAULT_LOG_LEVEL),
            '\trespond "caddify ready — open the dashboard to add domains" 200',
            "}",
            "",
        ]
    else:
        for r in routes:
            host = r.get("host") or DEFAULT_HOST
            mode = r.get("ssl_mode") or ("auto" if r.get("ssl", True) else "off")
            log_level = parse_log_level(r.get("log_level"))
            access = _access_log_snippet(r["domain"], log_level)
            if mode == "off":
                site_lines = [
                    f"http://{r['domain']} {{",
                    *access,
                    f"\treverse_proxy {host}:{r['port']}",
                    "}",
                ]
            elif mode == "manual":
                if not has_manual_certs(r["domain"]):
                    raise ValueError(
                        translate(lang, "err_manual_certs_missing", domain=r["domain"])
                    )
                c_path = f"{CERTS_CONTAINER}/{r['domain']}/fullchain.pem"
                k_path = f"{CERTS_CONTAINER}/{r['domain']}/privkey.pem"
                site_lines = [
                    f"{r['domain']} {{",
                    f"\ttls {c_path} {k_path}",
                    *access,
                    f"\treverse_proxy {host}:{r['port']}",
                    "}",
                ]
            else:
                site_lines = [
                    f"{r['domain']} {{",
                    *access,
                    f"\treverse_proxy {host}:{r['port']}",
                    "}",
                ]
            chunks += site_lines + [""]
    CADDYFILE.write_text("\n".join(chunks), encoding="utf-8")
    log.debug("caddyfile generated", extra={"layer": "app", "event": "caddyfile_write"})


def reload_caddy(lang: str = "en") -> tuple[bool, str]:
    t = make_t(lang)
    try:
        client = docker.from_env()
        container = client.containers.get(CADDY_CONTAINER)
        if container.status != "running":
            return False, t("err_caddy_not_running")
        result = container.exec_run(
            ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
            user="root",
        )
        if result.exit_code != 0:
            out = (result.output or b"").decode("utf-8", errors="replace")
            return False, out.strip() or "reload failed"
        return True, t("msg_applied")
    except docker.errors.NotFound:
        return False, t("err_caddy_not_found", name=CADDY_CONTAINER)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def apply_routes(routes: list[dict[str, Any]], lang: str = "en") -> tuple[bool, str]:
    write_routes(routes)
    try:
        generate_caddyfile(routes, lang)
    except ValueError as exc:
        return False, str(exc)
    return reload_caddy(lang)


def validate_domain(domain: str, lang: str = "en") -> str | None:
    if not DOMAIN_RE.match(domain):
        return translate(lang, "err_invalid_domain")
    return None


def validate_port(port: str, lang: str = "en") -> str | None:
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        return translate(lang, "err_invalid_port")
    return None


def caddy_status() -> dict[str, Any]:
    try:
        client = docker.from_env()
        container = client.containers.get(CADDY_CONTAINER)
        return {"running": container.status == "running", "status": container.status}
    except Exception:  # noqa: BLE001
        return {"running": False, "status": "unknown"}


def mode_label(lang: str, mode: str) -> str:
    if mode == "manual":
        return translate(lang, "mode_ssl_manual")
    if mode == "off":
        return translate(lang, "mode_nossl")
    return translate(lang, "mode_ssl")


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


@app.get("/lang/{code}")
def set_language(request: Request, code: str) -> Any:
    lang = code if supported(code) else "en"
    referer = request.headers.get("referer") or "/"
    response = RedirectResponse(referer, status_code=303)
    set_lang_cookie(response, lang)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Any:
    if is_authed(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)) -> Any:
    client = request.client.host if request.client else None
    if secrets.compare_digest(password, DASHBOARD_PASSWORD):
        request.session["auth"] = True
        audit("login_ok", client=client)
        return RedirectResponse("/", status_code=303)
    audit("login_fail", level=logging.WARNING, client=client)
    return render(
        request,
        "login.html",
        {"error": t_req(request, "err_bad_password")},
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request) -> Any:
    client = request.client.host if request.client else None
    audit("logout", client=client)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Any:
    if not is_authed(request):
        return RedirectResponse("/login", status_code=303)

    flash = pop_flash(request)
    response = render(
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
async def add_route(
    request: Request,
    domain: str = Form(...),
    port: str = Form(...),
    host: str = Form(""),
    ssl_mode: str = Form("auto"),
    log_level: str = Form("info"),
    cert_file: UploadFile | None = File(None),
    key_file: UploadFile | None = File(None),
) -> Any:
    require_auth(request)
    lang = get_lang(request)
    domain = domain.strip().lower()
    port = port.strip()
    host = normalize_host(host)
    mode = parse_ssl_mode(ssl_mode)
    access_log = parse_log_level(log_level)

    err = validate_domain(domain, lang) or validate_port(port, lang)
    if err:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, err, "err")
        return response

    routes = list_routes()
    if any(r["domain"] == domain for r in routes):
        response = RedirectResponse("/", status_code=303)
        set_flash(response, translate(lang, "err_domain_exists"), "err")
        return response

    if mode == "manual":
        saved = await save_uploaded_certs(domain, cert_file, key_file)
        if not saved and not has_manual_certs(domain):
            response = RedirectResponse("/", status_code=303)
            set_flash(response, translate(lang, "err_manual_certs_required"), "err")
            return response

    routes.append(
        {
            "domain": domain,
            "port": port,
            "host": host,
            "ssl_mode": mode,
            "log_level": access_log,
        }
    )
    ok, msg = apply_routes(routes, lang)
    audit(
        "route_add",
        domain=domain,
        port=port,
        host=host,
        ssl_mode=mode,
        detail=f"log={access_log}" if ok else msg,
        ok=ok,
    )

    response = RedirectResponse("/", status_code=303)
    set_flash(
        response,
        translate(lang, "msg_route_added", domain=domain, mode=mode_label(lang, mode)) if ok else msg,
        "ok" if ok else "err",
    )
    return response


@app.post("/routes/{domain}/update")
async def update_route(
    request: Request,
    domain: str,
    port: str = Form(...),
    host: str = Form(""),
    ssl_mode: str = Form("auto"),
    log_level: str = Form("info"),
    cert_file: UploadFile | None = File(None),
    key_file: UploadFile | None = File(None),
) -> Any:
    require_auth(request)
    lang = get_lang(request)
    domain = domain.strip().lower()
    port = port.strip()
    host = normalize_host(host)
    mode = parse_ssl_mode(ssl_mode)
    access_log = parse_log_level(log_level)

    err = validate_port(port, lang)
    if err:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, err, "err")
        return response

    if mode == "manual":
        await save_uploaded_certs(domain, cert_file, key_file)
        if not has_manual_certs(domain):
            response = RedirectResponse("/", status_code=303)
            set_flash(response, translate(lang, "err_manual_certs_required"), "err")
            return response

    routes = list_routes()
    found = False
    for r in routes:
        if r["domain"] == domain:
            r["port"] = port
            r["host"] = host
            r["ssl_mode"] = mode
            r["log_level"] = access_log
            found = True
            break
    if not found:
        response = RedirectResponse("/", status_code=303)
        set_flash(response, translate(lang, "err_domain_not_found"), "err")
        return response

    ok, msg = apply_routes(routes, lang)
    audit(
        "route_update",
        domain=domain,
        port=port,
        host=host,
        ssl_mode=mode,
        detail=f"log={access_log}" if ok else msg,
        ok=ok,
    )
    response = RedirectResponse("/", status_code=303)
    set_flash(
        response,
        translate(lang, "msg_route_updated", domain=domain) if ok else msg,
        "ok" if ok else "err",
    )
    return response


@app.post("/routes/{domain}/delete")
def delete_route(request: Request, domain: str) -> Any:
    require_auth(request)
    lang = get_lang(request)
    domain = domain.strip().lower()
    before = list_routes()
    routes = [r for r in before if r["domain"] != domain]
    if len(routes) == len(before):
        response = RedirectResponse("/", status_code=303)
        set_flash(response, translate(lang, "err_domain_not_found"), "err")
        return response

    ok, msg = apply_routes(routes, lang)
    audit("route_delete", domain=domain, ok=ok, detail=None if ok else msg)
    response = RedirectResponse("/", status_code=303)
    set_flash(
        response,
        translate(lang, "msg_route_deleted", domain=domain) if ok else msg,
        "ok" if ok else "err",
    )
    return response


@app.post("/reload")
def reload_now(request: Request) -> Any:
    require_auth(request)
    lang = get_lang(request)
    routes = list_routes()
    try:
        generate_caddyfile(routes, lang)
    except ValueError as exc:
        audit("reload", ok=False, detail=str(exc))
        response = RedirectResponse("/", status_code=303)
        set_flash(response, str(exc), "err")
        return response
    ok, msg = reload_caddy(lang)
    audit("reload", ok=ok, detail=None if ok else msg)
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
    setup_logging()
    ensure_routes_file()
    ensure_certs_dir()
    try:
        generate_caddyfile(list_routes())
    except ValueError as exc:
        log.warning("startup caddyfile skipped: %s", exc, extra={"layer": "app", "event": "startup"})
    log.info("dashboard started", extra={"layer": "app", "event": "startup"})