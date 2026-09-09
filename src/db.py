"""Supabase (PostgREST) の最小クライアント。

追加の依存を増やしたくないので urllib だけで書いている。
書き込みは service_role キーが要る(RLS を通す)。キーが無い場合は
`Supabase.enabled` が False になり、呼び出し側は DB 保存を丸ごと飛ばせる。

環境変数:
  SUPABASE_URL                プロジェクト URL (https://xxxx.supabase.co)
  SUPABASE_SERVICE_ROLE_KEY   書き込み用キー(GitHub Secrets に入れる)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

DEFAULT_URL = "https://edqwvckcywbmakkvwiuc.supabase.co"


class SupabaseError(RuntimeError):
    pass


class Supabase:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL") or DEFAULT_URL).rstrip("/")
        self.key = (
            key
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_SERVICE_KEY")
            or ""
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    # ------------------------------------------------------------------ 低レベル
    def _request(self, method: str, path: str, *, body: Any = None,
                 params: dict[str, str] | None = None,
                 prefer: str | None = None, retries: int = 3) -> Any:
        if not self.enabled:
            raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        qs = ""
        if params:
            qs = "?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe='*.,()')}"
                                for k, v in params.items())
        url = f"{self.url}/rest/v1/{path}{qs}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    raw = r.read()
                    if not raw:
                        return None
                    return json.loads(raw.decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                last = SupabaseError(f"{method} {path} -> {e.code} {detail}")
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise last
            except urllib.error.URLError as e:
                last = SupabaseError(f"{method} {path} -> {e}")
                time.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    # ------------------------------------------------------------------ API
    def select(self, table: str, **params) -> list[dict]:
        return self._request("GET", table, params=params) or []

    def insert(self, table: str, rows: dict | Iterable[dict], *,
               upsert_on: str | None = None, returning: bool = True) -> list[dict]:
        if isinstance(rows, dict):
            rows = [rows]
        rows = [r for r in rows]
        if not rows:
            return []
        prefer = ["return=representation" if returning else "return=minimal"]
        params = {}
        if upsert_on:
            prefer.append("resolution=merge-duplicates")
            params["on_conflict"] = upsert_on
        out = self._request("POST", table, body=rows, params=params or None,
                            prefer=",".join(prefer))
        return out or []

    def update(self, table: str, patch: dict, **filters) -> list[dict]:
        return self._request("PATCH", table, body=patch, params=filters,
                             prefer="return=representation") or []


def client() -> Supabase:
    return Supabase()


if __name__ == "__main__":
    sb = client()
    print("URL:", sb.url)
    print("書き込みキー:", "あり" if sb.enabled else "なし")
    if sb.enabled:
        print("runs:", len(sb.select("bf_runs", select="id", limit="5")))
