#!/usr/bin/env python3
"""Convertit un export Cookie-Editor (tv_cookies_raw.json) en storage_state.json Playwright."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "secrets" / "tv_cookies_raw.json"
OUT = ROOT / "secrets" / "storage_state.json"

SAMESITE_MAP = {
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
}


def convert() -> None:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    cookies: list[dict[str, object]] = []
    for c in raw:
        cookie: dict[str, object] = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": SAMESITE_MAP.get(
                str(c.get("sameSite", "lax")).lower(), "Lax"
            ),
        }
        if not c.get("session", False) and "expirationDate" in c:
            cookie["expires"] = float(c["expirationDate"])
        cookies.append(cookie)

    storage = {"cookies": cookies, "origins": []}
    OUT.write_text(json.dumps(storage, indent=2), encoding="utf-8")
    print(f"✅ {len(cookies)} cookies convertis → {OUT}")
    critical = [
        c["name"]
        for c in cookies
        if c["name"] in ("sessionid", "sessionid_sign", "device_t")
    ]
    print(f"🔑 Cookies de session présents : {critical}")


if __name__ == "__main__":
    convert()
