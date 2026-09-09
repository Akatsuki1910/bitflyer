"""仮想通貨ニュースの収集(RSS)。APIキー不要。

BTC / ETH / BAT に触れている記事だけを抜き出し、
強気・弱気っぽい単語の出現数で雑にスコアを付ける。
※ あくまで語句のカウントであって、相場予測ではない。
"""
from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

JST = timezone(timedelta(hours=9))

FEEDS = [
    ("CoinPost", "https://coinpost.jp/?feed=rss2", "ja"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "en"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "en"),
]

# 銘柄の呼び名
COIN_WORDS = {
    "BTC": ["btc", "bitcoin", "ビットコイン"],
    "ETH": ["eth", "ethereum", "イーサリアム", "イーサ"],
    "BAT": ["bat", "basic attention", "ベーシックアテンション", "brave"],
}

BULL = ["surge", "rally", "soar", "jump", "gain", "bullish", "record high", "all-time high",
        "adoption", "approval", "inflow", "upgrade", "breakout",
        "上昇", "急騰", "高値", "最高値", "強気", "買い", "流入", "好材料", "承認", "上場"]
BEAR = ["crash", "plunge", "slump", "drop", "fall", "bearish", "selloff", "sell-off",
        "hack", "outflow", "ban", "lawsuit", "liquidation", "warning",
        "下落", "急落", "暴落", "安値", "弱気", "売り", "流出", "規制", "ハッキング", "訴訟", "清算"]

UA = {"User-Agent": "Mozilla/5.0 (compatible; bitflyer-dashboard/1.0)"}


def _text(el, *tags) -> str:
    for t in tags:
        found = el.find(t)
        if found is not None and found.text:
            return html.unescape(found.text.strip())
    return ""


def _parse_date(s: str):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(JST)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s.strip(), fmt)
            return d.astimezone(JST) if d.tzinfo else d.replace(tzinfo=timezone.utc).astimezone(JST)
        except Exception:
            continue
    return None


def fetch_feed(name: str, url: str, lang: str) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            xml = r.read()
    except Exception as e:
        print(f"  ニュース取得失敗 {name}: {e}")
        return []

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        print(f"  RSS解析失敗 {name}: {e}")
        return []

    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    out = []
    for it in items:
        title = _text(it, "title", "{http://www.w3.org/2005/Atom}title")
        link = _text(it, "link", "{http://www.w3.org/2005/Atom}id")
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            if le is not None:
                link = le.get("href", "")
        desc = _text(it, "description", "{http://www.w3.org/2005/Atom}summary")
        desc = re.sub(r"<[^>]+>", "", desc)[:300]
        pub = _parse_date(_text(it, "pubDate", "published", "updated",
                                "{http://purl.org/dc/elements/1.1/}date"))
        if not title:
            continue
        out.append({"source": name, "lang": lang, "title": title, "link": link,
                    "summary": desc,
                    "published": pub.isoformat() if pub else None})
    return out


def score(text: str) -> int:
    t = text.lower()
    return sum(t.count(w) for w in BULL) - sum(t.count(w) for w in BEAR)


def coins_in(text: str) -> list[str]:
    t = text.lower()
    hits = []
    for sym, words in COIN_WORDS.items():
        if any(wd in t for wd in words):
            hits.append(sym)
    return hits


def collect(hours: int = 36, limit: int = 40) -> dict:
    """直近 hours 時間のニュースを集めて、銘柄別に整理して返す。"""
    articles = []
    for name, url, lang in FEEDS:
        articles += fetch_feed(name, url, lang)

    cutoff = datetime.now(JST) - timedelta(hours=hours)
    picked = []
    for a in articles:
        blob = f"{a['title']} {a['summary']}"
        a["coins"] = coins_in(blob)
        a["score"] = score(blob)
        if a["published"]:
            try:
                if datetime.fromisoformat(a["published"]) < cutoff:
                    continue
            except Exception:
                pass
        if a["coins"]:
            picked.append(a)

    picked.sort(key=lambda a: (a["published"] or ""), reverse=True)
    picked = picked[:limit]

    by_coin = {}
    for sym in COIN_WORDS:
        rel = [a for a in picked if sym in a["coins"]]
        tone = sum(a["score"] for a in rel)
        by_coin[sym] = {
            "count": len(rel),
            "tone": tone,
            "mood": "強気寄り" if tone > 1 else ("弱気寄り" if tone < -1 else "中立"),
        }

    return {"articles": picked, "by_coin": by_coin,
            "fetched_at": datetime.now(JST).isoformat()}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    n = collect()
    print(f"取得 {len(n['articles'])}件\n")
    for sym, v in n["by_coin"].items():
        print(f"{sym}: {v['count']}件  トーン {v['tone']:+d} ({v['mood']})")
    print()
    for a in n["articles"][:10]:
        print(f"[{a['source']}] {','.join(a['coins'])} ({a['score']:+d}) {a['title'][:70]}")
