"""価格データの取得とキャッシュ.

bitFlyer 自身は過去のローソク足を返す公開 API を持たないため、
JPY 建ての参照価格は CoinGecko の公開 API から取得する。
（無料枠の制限で「直近365日」「日足」までしか遡れない）

bitFlyer の現在値・スプレッドは板 API (getticker) から実測する。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# CoinGecko の coin id
COINS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BAT": "basic-attention-token",
}

# bitFlyer Lightning(板取引)で扱いのある銘柄。ここに無いものは販売所のみ。
BITFLYER_LIGHTNING = {"BTC": "BTC_JPY", "ETH": "ETH_JPY"}

CG_BASE = "https://api.coingecko.com/api/v3"
UA = {"User-Agent": "bitflyer-backtest/1.0"}


def _get_json(url: str, retries: int = 4, pause: float = 12.0):
    """レート制限(429)を考慮した GET。"""
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503):
                wait = pause * (i + 1)
                print(f"  レート制限。{wait:.0f}秒待機して再試行 ({i+1}/{retries})")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"取得失敗: {url} ({last})")


def fetch_prices(symbol: str, days: int = 365, force: bool = False,
                 max_age_h: float = 12.0) -> pd.DataFrame:
    """JPY建ての日次終値を DataFrame(date, close) で返す。ローカルにキャッシュする。

    日足は1日の中で動かないので、max_age_h 以内のキャッシュはそのまま使う。
    CoinGecko の無料枠はレート制限が厳しく、取得に失敗することがある。
    その場合は古いキャッシュがあればそれを使う。少し古い履歴で回すほうが、
    ルーティンごと止まるよりましなので。
    """
    cache = DATA_DIR / f"{symbol}_JPY_{days}d.csv"
    age_h = ((time.time() - cache.stat().st_mtime) / 3600) if cache.exists() else None

    if age_h is not None and not force and age_h < max_age_h:
        return pd.read_csv(cache, parse_dates=["date"])

    coin_id = COINS[symbol]
    url = f"{CG_BASE}/coins/{coin_id}/market_chart?vs_currency=jpy&days={days}&interval=daily"
    print(f"取得中: {symbol}/JPY 直近{days}日 ...")
    try:
        raw = _get_json(url)
    except Exception as e:  # noqa: BLE001
        if age_h is not None:
            print(f"  取得失敗のため {age_h:.1f}時間前のキャッシュを使う ({symbol}): {e}")
            return pd.read_csv(cache, parse_dates=["date"])
        raise

    rows = [
        {
            "date": datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date(),
            "close": float(px),
        }
        for ms, px in raw["prices"]
    ]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    # 同一日が重複する場合は最後の値を採用
    df = df.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def load_all(days: int = 365, force: bool = False,
             max_age_h: float = 12.0) -> dict[str, pd.DataFrame]:
    out = {}
    fetched = 0
    for sym in COINS:
        if fetched:
            # 実際に API を叩いたときだけ間を空ける(キャッシュ命中なら待たない)
            time.sleep(6.0)
        before = time.time()
        out[sym] = fetch_prices(sym, days=days, force=force, max_age_h=max_age_h)
        if time.time() - before > 0.3:   # ネットワークに行った
            fetched += 1
    return out


def bitflyer_ticker(product_code: str) -> dict | None:
    """bitFlyer 板取引の現在値。スプレッド実測用。"""
    try:
        return _get_json(
            f"https://api.bitflyer.com/v1/getticker?product_code={product_code}",
            retries=2,
            pause=2,
        )
    except Exception:
        return None


def measured_spreads() -> dict[str, float]:
    """板取引の実測スプレッド(片道 = 半値幅/mid)を返す。"""
    out = {}
    for sym, code in BITFLYER_LIGHTNING.items():
        t = bitflyer_ticker(code)
        if not t or not t.get("best_ask"):
            continue
        bid, ask = float(t["best_bid"]), float(t["best_ask"])
        mid = (bid + ask) / 2
        out[sym] = (ask - mid) / mid
    return out


if __name__ == "__main__":
    data = load_all()
    for sym, df in data.items():
        print(
            f"{sym}: {len(df)}本  {df['date'].min().date()} 〜 {df['date'].max().date()}  "
            f"最新 {df['close'].iloc[-1]:,.2f} JPY"
        )
    print("\nbitFlyer 板取引の実測スプレッド(片道):")
    for sym, s in measured_spreads().items():
        print(f"  {sym}: {s*100:.4f}%")
