"""Supabase を anon キーで読むだけの小さなクライアント。

anon キーは公開・読み取り専用で、docs/db.html に埋まっているものと同じ。
URL とキーは db.html を唯一の出どころにして二重管理を避ける。
書き込みは src/db.py（service_role）の担当で、こちらからは一切しない。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
PAGE = 1000  # PostgREST が1回に返す上限


def _config() -> tuple[str, str]:
    html = (ROOT / "docs" / "db.html").read_text(encoding="utf-8")
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', html).group(1)
    key = re.search(r'SUPABASE_ANON_KEY\s*=\s*"([^"]+)"', html).group(1)
    return url, key


URL, KEY = _config()


def q(table: str, **params) -> list[dict]:
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='*.,()')}"
                  for k, v in params.items())
    req = urllib.request.Request(
        f"{URL}/rest/v1/{table}?{qs}",
        headers={"apikey": KEY, "Authorization": "Bearer " + KEY,
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def q_all(table: str, **params) -> list[dict]:
    """1000件を超える表をページを送りながら全部読む。order の指定が必要。"""
    out, offset = [], 0
    while True:
        rows = q(table, **params, limit=str(PAGE), offset=str(offset))
        out += rows
        if len(rows) < PAGE:
            return out
        offset += PAGE


def ts(s: str | None) -> datetime | None:
    """Supabase の時刻文字列を JST の datetime にする。"""
    if not s:
        return None
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(JST)


def jst(s: str | None, fmt: str = "%m/%d %H:%M") -> str:
    """表示用。Supabase は UTC で返すので、そのまま読むと日付を取り違える。"""
    t = ts(s)
    return t.strftime(fmt) + " JST" if t else "—"
