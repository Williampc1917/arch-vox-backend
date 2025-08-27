from urllib.parse import quote

import requests

from app.config import settings


def _hdr():
    return {"Authorization": f"Bearer {settings.UPSTASH_REDIS_REST_TOKEN}"}


def ping() -> bool:
    r = requests.post(f"{settings.UPSTASH_REDIS_REST_URL}/ping", headers=_hdr(), timeout=3)
    return r.ok and "PONG" in r.text.upper()


def set_with_ttl(key: str, value: str, ttl_s: int | None = None) -> bool:
    url = f"{settings.UPSTASH_REDIS_REST_URL}/set/{quote(key)}/{quote(value)}"
    if ttl_s:
        url += f"?EX={int(ttl_s)}"
    return requests.post(url, headers=_hdr(), timeout=3).ok


def get(key: str) -> str | None:
    url = f"{settings.UPSTASH_REDIS_REST_URL}/get/{quote(key)}"
    r = requests.post(url, headers=_hdr(), timeout=3)
    return r.json() if r.ok else None
