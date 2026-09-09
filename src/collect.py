"""ルーティンの「情報をかき集める」担当。

集めるもの:
  - bitFlyer 板(Lightning)の現在値・気配・出来高          … BTC / ETH
  - bitFlyer 証拠金取引 FX_BTC_JPY の現在値(現物との乖離)   … センチメント
  - CoinGecko の JPY 建て現在値と24時間変化率              … 全銘柄(BAT の実勢値もこれ)
  - CoinGecko /global の時価総額・BTC ドミナンス
  - Fear & Greed Index (alternative.me)
  - ニュース RSS (news.py に委譲)

どれか一つ落ちても全体は止めない。取れなかったものは None のまま返す。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import news as newsmod

JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "bitflyer-routine/1.0"}

# 板取引がある銘柄。ここに無い銘柄は販売所のみ(=往復コストが重い)
LIGHTNING = {"BTC": "BTC_JPY", "ETH": "ETH_JPY"}
CG_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "BAT": "basic-attention-token"}


def _json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _try(label: str, fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  取得失敗 {label}: {e}", flush=True)
        return default


# ------------------------------------------------------------------ bitFlyer
def bitflyer_ticker(product_code: str) -> dict | None:
    return _try(product_code, lambda: _json(
        f"https://api.bitflyer.com/v1/getticker?product_code={product_code}"))


def bitflyer_markets() -> list[str]:
    m = _try("getmarkets", lambda: _json("https://api.bitflyer.com/v1/getmarkets"), [])
    return [x.get("product_code", "") for x in (m or [])]


def board_snapshot() -> dict[str, dict]:
    """板がある銘柄の現在値・気配・実測スプレッドをまとめる。"""
    out: dict[str, dict] = {}
    for sym, code in LIGHTNING.items():
        t = bitflyer_ticker(code)
        if not t or not t.get("best_ask"):
            continue
        bid, ask = float(t["best_bid"]), float(t["best_ask"])
        mid = (bid + ask) / 2
        out[sym] = {
            "venue": "板(Lightning)",
            "product_code": code,
            "price": mid,
            "best_bid": bid,
            "best_ask": ask,
            "spread": (ask - mid) / mid if mid else None,
            "volume_24h": float(t.get("volume_by_product") or 0) or None,
            "ltp": float(t.get("ltp") or 0) or None,
            "raw": {k: t.get(k) for k in
                    ("timestamp", "ltp", "best_bid", "best_ask", "volume", "volume_by_product")},
        }
    return out


def fx_premium(spot_btc: float | None) -> dict | None:
    """FX_BTC_JPY(証拠金)と現物の乖離。買い越し/売り越しの目安。"""
    t = bitflyer_ticker("FX_BTC_JPY")
    if not t or not t.get("ltp") or not spot_btc:
        return None
    fx = float(t["ltp"])
    return {"fx_price": fx, "spot": spot_btc, "premium": fx / spot_btc - 1}


# ------------------------------------------------------------------ CoinGecko
def coingecko_prices() -> dict[str, dict]:
    ids = ",".join(CG_IDS.values())
    d = _try("coingecko simple/price", lambda: _json(
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=jpy&include_24hr_change=true&include_24hr_vol=true"), {})
    out = {}
    for sym, cid in CG_IDS.items():
        v = (d or {}).get(cid)
        if not v:
            continue
        out[sym] = {
            "venue": "参考(CoinGecko)",
            "price": float(v["jpy"]),
            "change_24h": (v.get("jpy_24h_change") or 0) / 100,
            "volume_24h": v.get("jpy_24h_vol"),
        }
    return out


def coingecko_global() -> dict | None:
    d = _try("coingecko global", lambda: _json("https://api.coingecko.com/api/v3/global"))
    if not d or "data" not in d:
        return None
    g = d["data"]
    return {
        "total_market_cap_jpy": (g.get("total_market_cap") or {}).get("jpy"),
        "market_cap_change_24h": g.get("market_cap_change_percentage_24h_usd"),
        "btc_dominance": (g.get("market_cap_percentage") or {}).get("btc"),
        "eth_dominance": (g.get("market_cap_percentage") or {}).get("eth"),
    }


# ------------------------------------------------------------------ センチメント
def fear_greed() -> dict | None:
    d = _try("fear&greed", lambda: _json("https://api.alternative.me/fng/?limit=1"))
    if not d or not d.get("data"):
        return None
    x = d["data"][0]
    return {"value": int(x["value"]), "label": x.get("value_classification")}


# ------------------------------------------------------------------ まとめ
def collect_all(symbols=("BTC", "ETH", "BAT"), news_hours: int = 36) -> dict:
    print("情報収集", flush=True)

    board = board_snapshot()
    print(f"  bitFlyer 板: {len(board)}銘柄", flush=True)

    cg = coingecko_prices()
    print(f"  CoinGecko: {len(cg)}銘柄", flush=True)

    markets = bitflyer_markets()
    hanbaijo_only = [s for s in symbols if f"{s}_JPY" not in markets]

    # 実際に売買に使う値段: 板があれば板の mid、無ければ CoinGecko
    market: dict[str, dict] = {}
    for sym in symbols:
        base = dict(board.get(sym) or {})
        ref = cg.get(sym) or {}
        if not base:
            base = dict(ref)
            base.setdefault("venue", "販売所(参考値 CoinGecko)")
        base.setdefault("change_24h", ref.get("change_24h"))
        base.setdefault("volume_24h", ref.get("volume_24h"))
        base["reference_price"] = ref.get("price")
        base["hanbaijo_only"] = sym in hanbaijo_only
        if base.get("price"):
            market[sym] = base

    glob = coingecko_global()
    fg = fear_greed()
    fx = fx_premium((market.get("BTC") or {}).get("price"))
    if fg:
        print(f"  Fear&Greed: {fg['value']} ({fg['label']})", flush=True)
    if fx:
        print(f"  FX乖離: {fx['premium']*100:+.2f}%", flush=True)

    nws = newsmod.collect(hours=news_hours)
    print(f"  ニュース: {len(nws['articles'])}件", flush=True)

    indicators = {}
    if fg:
        indicators["fear_greed"] = {
            "label": f"Fear & Greed Index ({fg['label']})",
            "value": fg["value"], "text": fg["label"], "source": "alternative.me"}
    if glob:
        if glob.get("btc_dominance") is not None:
            indicators["btc_dominance"] = {
                "label": "BTC ドミナンス(%)", "value": glob["btc_dominance"],
                "source": "CoinGecko /global"}
        if glob.get("total_market_cap_jpy") is not None:
            indicators["total_market_cap_jpy"] = {
                "label": "暗号資産の時価総額合計(円)",
                "value": glob["total_market_cap_jpy"], "source": "CoinGecko /global"}
        if glob.get("market_cap_change_24h") is not None:
            indicators["market_cap_change_24h"] = {
                "label": "時価総額の24時間変化(%)",
                "value": glob["market_cap_change_24h"], "source": "CoinGecko /global"}
    if fx:
        indicators["fx_premium"] = {
            "label": "FX_BTC_JPY の現物乖離(%)", "value": fx["premium"] * 100,
            "source": "bitFlyer getticker"}
    for sym, v in market.items():
        if v.get("spread") is not None:
            indicators[f"spread_{sym}"] = {
                "label": f"{sym} 板スプレッド片道(%)", "value": v["spread"] * 100,
                "source": "bitFlyer getticker"}
    for sym, v in nws["by_coin"].items():
        indicators[f"news_tone_{sym}"] = {
            "label": f"{sym} ニュースのトーン", "value": v["tone"],
            "text": f"{v['mood']} / {v['count']}件", "source": "RSS"}

    return {
        "fetched_at": datetime.now(JST).isoformat(),
        "market": market,
        "board": board,
        "coingecko": cg,
        "global": glob,
        "fear_greed": fg,
        "fx": fx,
        "news": nws,
        "indicators": indicators,
        "bitflyer_markets": markets,
        "hanbaijo_only": hanbaijo_only,
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    c = collect_all()
    for sym, v in c["market"].items():
        print(f"{sym}: {v['price']:,.2f} JPY  {v['venue']}  "
              f"24h {(v.get('change_24h') or 0)*100:+.2f}%")
    for k, v in c["indicators"].items():
        print(f"  {k}: {v.get('value')} {v.get('text') or ''}")
