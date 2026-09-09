"""毎回確認する情報ソースの一覧。

ここが唯一の正。`python src/sources.py` で
  - SOURCES.md を書き直す
  - Supabase の bf_sources テーブルへ同期する
の両方を行う。サイトを足したり消したりするときはこのファイルを直す。

api=True  … ルーティンが自動で叩いている
api=False … 人が目視で確認する用（自動化していない）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent

SOURCES = [
    # ---------------------------------------------------------------- 価格・板
    dict(key="bitflyer_ticker_btc", name="bitFlyer getticker BTC_JPY",
         url="https://api.bitflyer.com/v1/getticker?product_code=BTC_JPY",
         category="価格・板", api=True,
         purpose="BTC の板の現在値・最良気配。仮売買の約定価格とスプレッド実測に使う"),
    dict(key="bitflyer_ticker_eth", name="bitFlyer getticker ETH_JPY",
         url="https://api.bitflyer.com/v1/getticker?product_code=ETH_JPY",
         category="価格・板", api=True,
         purpose="ETH の板の現在値・最良気配"),
    dict(key="bitflyer_ticker_fx", name="bitFlyer getticker FX_BTC_JPY",
         url="https://api.bitflyer.com/v1/getticker?product_code=FX_BTC_JPY",
         category="価格・板", api=True,
         purpose="証拠金取引の値段。現物との乖離をセンチメント指標として見る"),
    dict(key="bitflyer_markets", name="bitFlyer getmarkets",
         url="https://api.bitflyer.com/v1/getmarkets",
         category="価格・板", api=True,
         purpose="板取引できる銘柄の一覧。ここに無い銘柄は販売所のみ＝往復コストが重い"),
    dict(key="coingecko_price", name="CoinGecko simple/price",
         url="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,basic-attention-token&vs_currencies=jpy&include_24hr_change=true",
         category="価格・板", api=True,
         purpose="JPY 建ての現在値と24時間変化率。板の無い BAT の実勢値はこれを使う"),
    dict(key="coingecko_chart", name="CoinGecko market_chart(日足365日)",
         url="https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=jpy&days=365&interval=daily",
         category="価格・板", api=True,
         purpose="シグナル計算のもとになる過去の日足。bitFlyer に公開ローソク足 API が無いため代用",
         note="無料枠は直近365日・日足まで"),

    # ---------------------------------------------------------------- 市況
    dict(key="coingecko_global", name="CoinGecko /global",
         url="https://api.coingecko.com/api/v3/global",
         category="市況", api=True,
         purpose="暗号資産全体の時価総額と BTC/ETH ドミナンス"),
    dict(key="fear_greed", name="Crypto Fear & Greed Index",
         url="https://api.alternative.me/fng/?limit=1",
         category="市況", api=True,
         purpose="市場心理の指標(0=極度の恐怖 〜 100=極度の強欲)"),

    # ---------------------------------------------------------------- ニュース
    dict(key="coinpost_rss", name="CoinPost RSS",
         url="https://coinpost.jp/?feed=rss2",
         category="ニュース", api=True, purpose="国内の暗号資産ニュース(日本語)"),
    dict(key="cointelegraph_rss", name="Cointelegraph RSS",
         url="https://cointelegraph.com/rss",
         category="ニュース", api=True, purpose="海外の暗号資産ニュース"),
    dict(key="coindesk_rss", name="CoinDesk RSS",
         url="https://www.coindesk.com/arc/outboundfeeds/rss/",
         category="ニュース", api=True, purpose="海外の暗号資産ニュース"),

    # ---------------------------------------------------------------- 目視確認
    dict(key="bitflyer_news", name="bitFlyer お知らせ",
         url="https://bitflyer.com/ja-jp/news",
         category="目視確認", api=False,
         purpose="取扱銘柄の追加/廃止、システムメンテ、仕様変更。前提が変わったらコードを直す"),
    dict(key="bitflyer_commission", name="bitFlyer 手数料一覧",
         url="https://bitflyer.com/ja-jp/commission",
         category="目視確認", api=False,
         purpose="板取引の手数料レート。コード内の 0.15% 前提が正しいか確認する"),
    dict(key="bitflyer_hanbaijo", name="bitFlyer 販売所の価格",
         url="https://bitflyer.com/ja-jp/",
         category="目視確認", api=False,
         purpose="販売所スプレッドの実測。BAT の往復4%という仮定を検算する"),
    dict(key="bitflyer_status", name="bitFlyer 稼働状況",
         url="https://status.bitflyer.com/",
         category="目視確認", api=False,
         purpose="障害・メンテで値段が取れなかった回の原因確認"),
    dict(key="fsa_crypto", name="金融庁 暗号資産関連",
         url="https://www.fsa.go.jp/policy/virtual_currency/index.html",
         category="目視確認", api=False,
         purpose="規制の変更。取扱銘柄や税制の前提が変わる"),
    dict(key="jvcea_stats", name="JVCEA 統計情報",
         url="https://jvcea.or.jp/about-crypto/statistics/",
         category="目視確認", api=False,
         purpose="国内取引所全体の出来高・預託金。板の薄さの目安"),
    dict(key="coinglass_liq", name="Coinglass 清算マップ",
         url="https://www.coinglass.com/ja/LiquidationData",
         category="目視確認", api=False,
         purpose="レバレッジの偏り。急変の前後で確認する"),
]


def to_markdown() -> str:
    cats: dict[str, list[dict]] = {}
    for s in SOURCES:
        cats.setdefault(s["category"], []).append(s)

    lines = [
        "# 毎回確認している情報ソース",
        "",
        "朝昼晩のルーティン（`src/routine.py`）がどこから何を取っているかの一覧。",
        "`src/sources.py` が原本で、このファイルは自動生成されます。",
        "",
        "| 印 | 意味 |",
        "|---|---|",
        "| 自動 | ルーティンが毎回叩いている |",
        "| 目視 | 自動化していない。前提が変わっていないか人が定期的に見る |",
        "",
    ]
    for cat, items in cats.items():
        lines += [f"## {cat}", "", "| ソース | 取得 | 何のために見るか |", "|---|---|---|"]
        for s in items:
            mark = "自動" if s["api"] else "目視"
            note = f"<br>※ {s['note']}" if s.get("note") else ""
            lines.append(f"| [{s['name']}]({s['url']}) | {mark} | {s['purpose']}{note} |")
        lines.append("")

    lines += [
        "## 前提として置いていること",
        "",
        "| 項目 | 値 | 出どころ |",
        "|---|---|---|",
        "| 板取引の手数料(片道) | 0.15% | bitFlyer 手数料一覧の最も不利なレート |",
        "| 板のスプレッド(片道) | 毎回実測 | `getticker` の best_bid / best_ask |",
        "| スリッページ(片道) | 0.05% | 仮定値 |",
        "| 販売所のスプレッド(片道) | 2% | 公表値が無いための仮定。BAT はこちら |",
        "| 元手 | 1,000,000円 / 口座 | 戦略 × 銘柄ごとに独立 |",
        "",
        "販売所スプレッドは実測して直す価値があります（BAT は往復4%の仮定が結論を左右する）。",
        "",
    ]
    return "\n".join(lines)


def sync_db() -> int:
    import db as dbmod
    sb = dbmod.client()
    if not sb.enabled:
        print("SUPABASE_SERVICE_ROLE_KEY が無いので DB 同期はスキップ")
        return 0
    rows = [{"key": s["key"], "name": s["name"], "url": s["url"],
             "category": s["category"], "purpose": s["purpose"],
             "api": s["api"], "auth_needed": False, "active": True,
             "note": s.get("note")} for s in SOURCES]
    sb.insert("bf_sources", rows, upsert_on="key", returning=False)
    keys = ",".join(f'"{s["key"]}"' for s in SOURCES)
    sb.update("bf_sources", {"active": False}, key=f"not.in.({keys})")
    print(f"bf_sources に {len(rows)}件 同期")
    return len(rows)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = ROOT / "SOURCES.md"
    p.write_text(to_markdown(), encoding="utf-8")
    print(f"書き出し: {p.name} ({len(SOURCES)}件)")
    sync_db()
