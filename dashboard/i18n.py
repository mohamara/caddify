"""Lightweight locale catalogs for the caddify dashboard."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANG = "en"
COOKIE_NAME = "ap_lang"

# code → (native label, text direction)
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "ltr"),
    "fa": ("فارسی", "rtl"),
    "zh": ("中文", "ltr"),
    "ar": ("العربية", "rtl"),
    "ru": ("Русский", "ltr"),
    "hi": ("हिन्दी", "ltr"),
}


@lru_cache(maxsize=1)
def _load_all() -> dict[str, dict[str, str]]:
    catalogs: dict[str, dict[str, str]] = {}
    for code in LANGUAGES:
        path = LOCALES_DIR / f"{code}.json"
        catalogs[code] = json.loads(path.read_text(encoding="utf-8"))
    return catalogs


def supported(code: str | None) -> bool:
    return bool(code) and code in LANGUAGES


def get_lang(request: Any) -> str:
    raw = request.cookies.get(COOKIE_NAME) or request.query_params.get("lang")
    if supported(raw):
        return raw  # type: ignore[return-value]
    return DEFAULT_LANG


def get_dir(lang: str) -> str:
    return LANGUAGES.get(lang, LANGUAGES[DEFAULT_LANG])[1]


def set_lang_cookie(response: Any, lang: str) -> None:
    if not supported(lang):
        lang = DEFAULT_LANG
    response.set_cookie(
        COOKIE_NAME,
        lang,
        max_age=365 * 24 * 60 * 60,
        httponly=False,
        samesite="lax",
    )


def translate(lang: str, key: str, **kwargs: Any) -> str:
    catalogs = _load_all()
    catalog = catalogs.get(lang) or catalogs[DEFAULT_LANG]
    text = catalog.get(key) or catalogs[DEFAULT_LANG].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def make_t(lang: str):
    def t(key: str, **kwargs: Any) -> str:
        return translate(lang, key, **kwargs)

    return t


def template_ctx(request: Any) -> dict[str, Any]:
    lang = get_lang(request)
    return {
        "lang": lang,
        "dir": get_dir(lang),
        "t": make_t(lang),
        "languages": [
            {"code": code, "label": label, "dir": direction}
            for code, (label, direction) in LANGUAGES.items()
        ],
    }
