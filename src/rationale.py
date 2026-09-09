"""「なぜ買った/売った/様子見なのか」を数字つきの日本語にする。

strategies.py が返すのは 0/1 のシグナルだけなので、
判断のもとになった指標をここで計算し直して根拠として残す。
DB の bf_signals.rationale / indicators に入る。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import strategies as st


def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) or np.isinf(v) else v


def _yen(v) -> str:
    v = _f(v)
    return "—" if v is None else f"{v:,.0f}円"


def indicators_for(name: str, px: pd.Series) -> tuple[dict, str]:
    """戦略名と価格系列から (指標dict, 根拠テキスト) を返す。"""
    p = _f(px.iloc[-1])

    if name == "買い持ち(Buy&Hold)":
        return ({"price": p}, f"買い持ちは常に保有。現在値 {_yen(p)}。")

    if name.startswith("SMA") and "クロス" in name:
        short, long = (int(x) for x in name.split()[1].split("/"))
        s = _f(px.rolling(short).mean().iloc[-1])
        l = _f(px.rolling(long).mean().iloc[-1])
        ind = {"price": p, f"sma{short}": s, f"sma{long}": l,
               "gap_pct": (s / l - 1) * 100 if s and l else None}
        if s is None or l is None:
            return ind, f"移動平均を出すのに必要な{long}日分のデータがまだ足りない。"
        rel = "上" if s > l else "下"
        return ind, (f"短期{short}日線 {_yen(s)} が長期{long}日線 {_yen(l)} の{rel}"
                     f"（乖離 {(s/l-1)*100:+.2f}%）。上なら保有、下なら現金。")

    if name == "100日線より上で保有":
        ma = _f(px.rolling(100).mean().iloc[-1])
        ind = {"price": p, "sma100": ma,
               "gap_pct": (p / ma - 1) * 100 if p and ma else None}
        if ma is None:
            return ind, "100日分のデータがまだ足りない。"
        rel = "上回っている" if p > ma else "下回っている"
        return ind, (f"現在値 {_yen(p)} が100日線 {_yen(ma)} を{rel}"
                     f"（{(p/ma-1)*100:+.2f}%）。上なら保有。")

    if name.startswith("RSI14"):
        r = _f(st._rsi(px, 14).iloc[-1])
        ind = {"price": p, "rsi14": r, "buy_below": 30, "sell_above": 70}
        if r is None:
            return ind, "RSI を出すデータが足りない。"
        if r < 30:
            state = "売られすぎ(30未満)なので買いに入る水準"
        elif r > 70:
            state = "買われすぎ(70超)なので手仕舞う水準"
        else:
            state = "30〜70の中立ゾーンなので前回の状態を引き継ぐ"
        return ind, f"RSI14 は {r:.1f}。{state}。"

    if name.startswith("ドンチャン"):
        hi = _f(px.rolling(20).max().shift(1).iloc[-1])
        lo = _f(px.rolling(10).min().shift(1).iloc[-1])
        ind = {"price": p, "high20": hi, "low10": lo}
        if hi is None or lo is None:
            return ind, "20日分のデータがまだ足りない。"
        if p > hi:
            state = "直近20日高値を上抜けたので買い"
        elif p < lo:
            state = "直近10日安値を割ったので手仕舞い"
        else:
            state = "高値と安値の間なので前回の状態を継続"
        return ind, (f"現在値 {_yen(p)} / 20日高値 {_yen(hi)} / 10日安値 {_yen(lo)}。{state}。")

    if name == "3か月モメンタム":
        past = _f(px.shift(90).iloc[-1])
        ind = {"price": p, "price_90d_ago": past,
               "change_pct": (p / past - 1) * 100 if p and past else None}
        if past is None:
            return ind, "90日前のデータがまだ足りない。"
        rel = "高い" if p > past else "安い"
        return ind, (f"90日前 {_yen(past)} に対して現在値 {_yen(p)} は{rel}"
                     f"（{(p/past-1)*100:+.2f}%）。高ければ保有。")

    if name.startswith("MACD"):
        ema_f = px.ewm(span=12, adjust=False).mean()
        ema_s = px.ewm(span=26, adjust=False).mean()
        line = ema_f - ema_s
        sigl = line.ewm(span=9, adjust=False).mean()
        lv, sv = _f(line.iloc[-1]), _f(sigl.iloc[-1])
        ind = {"price": p, "macd": lv, "macd_signal": sv,
               "histogram": (lv - sv) if (lv is not None and sv is not None) else None}
        if lv is None or sv is None:
            return ind, "MACD を出すデータが足りない。"
        rel = "上" if lv > sv else "下"
        return ind, (f"MACD線 {lv:,.0f} がシグナル線 {sv:,.0f} の{rel}"
                     f"（ヒストグラム {lv-sv:+,.0f}）。上なら保有。")

    return ({"price": p}, "")


def action_label(prev: int | None, cur: int) -> str:
    if prev is None:
        return "新規買い" if cur == 1 else "様子見"
    if prev == 0 and cur == 1:
        return "買い"
    if prev == 1 and cur == 0:
        return "売り"
    return "保有継続" if cur == 1 else "様子見"
