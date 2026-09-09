"""bitFlyer で BTC / ETH / BAT を売買したら儲かったのかを検証する。

使い方:
    python src/run.py                       # 標準設定で全戦略を検証
    python src/run.py --capital 500000      # 元手50万円
    python src/run.py --hanbaijo-spread 3   # 販売所スプレッドを片道3%と仮定
    python src/run.py --days 180            # 直近180日だけ
"""
from __future__ import annotations

import argparse
import sys
import unicodedata

import numpy as np
import pandas as pd

import data as datamod
import strategies as st
from engine import (HANBAIJO, LIGHTNING_BTC, LIGHTNING_ETH, Costs, run_dca,
                    run_rebalance, run_signal)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 表示ユーティリティ
def w(s: str) -> int:
    """全角を2桁として数えた表示幅"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def pad(s: str, n: int, right: bool = False) -> str:
    s = str(s)
    fill = " " * max(0, n - w(s))
    return fill + s if right else s + fill


def table(headers: list[str], rows: list[list[str]], aligns: str = "") -> str:
    aligns = aligns or "l" * len(headers)
    widths = [max(w(h), *(w(r[i]) for r in rows)) if rows else w(h)
              for i, h in enumerate(headers)]
    line = "-+-".join("-" * x for x in widths)
    out = [" | ".join(pad(h, widths[i], aligns[i] == "r") for i, h in enumerate(headers)), line]
    out += [" | ".join(pad(c, widths[i], aligns[i] == "r") for i, c in enumerate(r)) for r in rows]
    return "\n".join(out)


def yen(x: float) -> str:
    return f"{x:,.0f}"


def pct(x: float, plus: bool = False) -> str:
    if x != x:  # NaN
        return "-"
    sign = "+" if plus and x > 0 else ""
    return f"{sign}{x*100:.2f}%"


# ---------------------------------------------------------------- 本体
def costs_for(symbol: str, args) -> tuple[Costs, str]:
    """銘柄ごとに実際に使える取引所と、そのコストを返す。"""
    hanbaijo = Costs(fee=0.0, spread=args.hanbaijo_spread / 100, slippage=0.0)
    if args.venue == "hanbaijo" or symbol not in datamod.BITFLYER_LIGHTNING:
        return hanbaijo, "販売所"
    base = LIGHTNING_BTC if symbol == "BTC" else LIGHTNING_ETH
    return Costs(fee=args.fee / 100, spread=base.spread, slippage=args.slippage / 100), "板(Lightning)"


def analyze_symbol(sym: str, px: pd.Series, costs: Costs, venue: str, capital: float):
    print(f"\n{'='*78}")
    print(f"■ {sym}/JPY  取引場所: {venue}  売買コスト: {costs.label()}")
    period_ret = px.iloc[-1] / px.iloc[0] - 1
    print(f"  期間: {px.index[0].date()} 〜 {px.index[-1].date()}  "
          f"価格 {px.iloc[0]:,.2f} → {px.iloc[-1]:,.2f} JPY ({pct(period_ret, True)})")
    print("=" * 78)

    results = []
    for name in st.CATALOG:
        sig = st.build(name, px)
        results.append(run_signal(px, sig, costs, initial=capital, name=name, symbol=sym))
    results.append(run_dca(px, costs, every_days=7, initial=capital,
                           name="毎週積立(DCA)", symbol=sym))

    rows = []
    for r in sorted(results, key=lambda r: -r.total_return):
        rows.append([
            r.name,
            yen(r.final),
            f"{'+' if r.final >= capital else ''}{yen(r.final - capital)}",
            pct(r.total_return, True),
            pct(r.max_drawdown),
            f"{r.sharpe:.2f}",
            str(r.n_trades),
            yen(r.cost_paid),
            pct(r.win_rate) if r.n_trades else "-",
            f"{r.exposure*100:.0f}%",
        ])
    print(table(
        ["戦略", "最終資産", "損益", "リターン", "最大DD", "シャープ", "売買", "コスト", "勝率", "建玉率"],
        rows, aligns="lrrrrrrrrr"))
    return results


def breakeven_table(sym: str, px: pd.Series, capital: float):
    """片道コストを振って、どこまでなら利益が残るかを見る。"""
    grid = [0.0, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0, 3.0]
    rows = []
    for name in st.CATALOG:
        sig = st.build(name, px)
        cells, be = [], "常に赤字"
        for g in grid:
            r = run_signal(px, sig, Costs(fee=g / 100, spread=0, slippage=0),
                           initial=capital, name=name)
            cells.append(pct(r.total_return, True))
            if r.total_return > 0:
                be = f"{g}%まで"
        rows.append([name, *cells, be])
    print(table(["戦略", *[f"{g}%" for g in grid], "黒字ライン"], rows,
                aligns="l" + "r" * (len(grid) + 1)))


def main():
    ap = argparse.ArgumentParser(description="bitFlyer 売買シミュレーション")
    ap.add_argument("--capital", type=float, default=1_000_000, help="元手(円)")
    ap.add_argument("--days", type=int, default=365, help="検証日数(無料APIは最大365)")
    ap.add_argument("--fee", type=float, default=0.15, help="板取引の手数料 %% (片道)")
    ap.add_argument("--slippage", type=float, default=0.05, help="スリッページ %% (片道)")
    ap.add_argument("--hanbaijo-spread", type=float, default=2.0,
                    help="販売所スプレッド %% (片道)")
    ap.add_argument("--venue", choices=["auto", "hanbaijo"], default="auto",
                    help="auto=板がある銘柄は板を使う")
    ap.add_argument("--refresh", action="store_true", help="価格を再取得")
    args = ap.parse_args()

    prices = datamod.load_all(days=args.days, force=args.refresh)
    px = {s: df.set_index("date")["close"] for s, df in prices.items()}

    print(f"\n元手 {yen(args.capital)} 円 / 現物ロングのみ / 最終日は必ず全部売却して精算")

    all_res = {}
    costs_map = {}
    for sym in ("BTC", "ETH", "BAT"):
        c, venue = costs_for(sym, args)
        costs_map[sym] = c
        all_res[sym] = analyze_symbol(sym, px[sym], c, venue, args.capital)

    # ---- 3銘柄ポートフォリオ ----
    print(f"\n{'='*78}")
    print("■ 3銘柄を等金額で持つポートフォリオ")
    print("=" * 78)
    pdf = pd.DataFrame({s: px[s] for s in ("BTC", "ETH", "BAT")}).dropna()
    rows = []
    for label, every in [("月次リバランス", 30), ("四半期リバランス", 90), ("放置(リバランス無し)", 10**6)]:
        r = run_rebalance(pdf, costs_map, every_days=every, initial=args.capital, name=label)
        rows.append([label, yen(r.final), f"{'+' if r.final >= args.capital else ''}{yen(r.final-args.capital)}",
                     pct(r.total_return, True), pct(r.max_drawdown), f"{r.sharpe:.2f}", yen(r.cost_paid)])
    print(table(["構成", "最終資産", "損益", "リターン", "最大DD", "シャープ", "コスト"],
                rows, aligns="lrrrrrr"))

    # ---- コスト感応度 ----
    print(f"\n{'='*78}")
    print("■ 片道コストをいくらまで払えるか (BTC。手数料以外はゼロと仮定した理論値)")
    print("=" * 78)
    breakeven_table("BTC", px["BTC"], args.capital)

    print("\n※ 過去1年の値動きに対する結果であり、将来の損益を保証するものではありません。")


if __name__ == "__main__":
    main()
