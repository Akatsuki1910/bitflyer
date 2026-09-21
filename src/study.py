"""直近1年の答え合わせ。

「判断に使ってきた材料が、翌日の方向を当てられていたか」を日足で検定し、
往復コストを越えるのに必要な的中率を出す。
docs/index.html の「1年ぶんの答え合わせ」と docs/data/study.json の中身になる。

採点の決まりは playbook D-02 に合わせる: 翌日の変化率が +0.5%超なら上、
−0.5%未満なら下、その間は判定なしとして数えない。
"""
from __future__ import annotations

import json
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "knowledge" / "events.json"

BAND = 0.5          # ±0.5% 以内は判定なし(playbook D-02)
HOLD_DAYS = (1, 3, 5, 10, 20, 60)


def binom_p(k: int, n: int) -> float:
    """「どちらも五分五分」を帰無仮説にした両側の p 値。"""
    if n == 0:
        return 1.0
    pk = comb(n, k) * 0.5 ** n
    return min(1.0, sum(comb(n, i) * 0.5 ** n
                        for i in range(n + 1) if comb(n, i) * 0.5 ** n <= pk * 1.000001))


def conditions(px: pd.Series) -> dict[str, pd.Series]:
    """判断の根拠に使ってきた「地合い」の条件。値は各日が条件に当てはまるか。"""
    r = px.pct_change() * 100
    sma20 = px.rolling(20).mean()
    sma100 = px.rolling(100).mean()
    return {
        "全日（基準）": pd.Series(True, index=px.index),
        "20日線の上にいる": px > sma20,
        "20日線の下にいる": px < sma20,
        "20日線を割った日": (px < sma20) & (px.shift(1) >= sma20.shift(1)),
        "20日線を上抜けた日": (px > sma20) & (px.shift(1) <= sma20.shift(1)),
        "100日線の上にいる": px > sma100,
        "100日線の下にいる": px < sma100,
        "前日が +2%超": r > 2,
        "前日が −2%超の下げ": r < -2,
        "20日高値を更新": px >= px.rolling(20).max(),
        "20日安値を更新": px <= px.rolling(20).min(),
    }


def edge(px: pd.Series, mask: pd.Series) -> dict | None:
    """条件に当てはまった日の「翌日の方向」を数える。"""
    nxt = (px.pct_change() * 100).shift(-1)
    m = mask.fillna(False) & nxt.notna()
    sub = nxt[m]
    up = int((sub > BAND).sum())
    dn = int((sub < -BAND).sum())
    n = up + dn
    if len(sub) < 10 or n == 0:
        return None
    side = "上" if up >= dn else "下"
    return {"days": int(len(sub)), "judged": n, "up": up, "down": dn,
            "side": side, "rate": max(up, dn) / n,
            "mean": float(sub.mean()), "p": binom_p(max(up, dn), n)}


def edge_table(px: dict[str, pd.Series]) -> list[dict]:
    labels = list(conditions(next(iter(px.values()))).keys())
    rows = []
    for label in labels:
        cells = {}
        for sym, s in px.items():
            cells[sym] = edge(s, conditions(s)[label])
        rows.append({"label": label, "cells": cells})
    return rows


def cost_table(px: dict[str, pd.Series], round_trip: dict[str, float]) -> list[dict]:
    """保有期間ごとの平均値幅と、往復コストを取り返すのに必要な的中率。

    的中率 p で当てて平均 m% 動くなら、期待値は (2p−1)×m。これがコストを
    上回る必要がある → p > 0.5 + コスト /(2m)。1 を超えたら的中率では取り返せない。
    """
    rows = []
    for sym, s in px.items():
        cost = round_trip[sym] * 100
        entry = {"sym": sym, "cost": cost, "holds": []}
        for n in HOLD_DAYS:
            m = float((s.pct_change(n).abs().dropna() * 100).mean())
            need = 0.5 + cost / (2 * m) if m > 0 else 9.9
            entry["holds"].append({"days": n, "move": m,
                                   "need": need if need <= 1 else None})
        rows.append(entry)
    return rows


def base_rates(px: dict[str, pd.Series]) -> list[dict]:
    out = []
    for sym, s in px.items():
        r = (s.pct_change() * 100).dropna()
        sign = np.sign(r)
        same = int((sign.shift(1) * sign > 0).sum())
        tot = int((sign.shift(1) * sign != 0).sum())
        out.append({"sym": sym, "days": int(len(r)),
                    "up": float((r > BAND).mean()), "down": float((r < -BAND).mean()),
                    "flat": float((r.abs() <= BAND).mean()),
                    "sd": float(r.std()), "abs_mean": float(r.abs().mean()),
                    "follow": same / tot if tot else 0.0})
    return out


def big_moves(px: dict[str, pd.Series], top: int = 10) -> list[dict]:
    """BTC が大きく動いた日を、手書きの出来事メモと突き合わせる。"""
    notes = {}
    if EVENTS.exists():
        notes = json.loads(EVENTS.read_text(encoding="utf-8")).get("events", {})
    r = {s: px[s].pct_change() * 100 for s in px}
    base = r["BTC"].dropna()
    idx = base.abs().sort_values(ascending=False).head(top).index
    out = []
    for d in sorted(idx, reverse=True):
        key = d.strftime("%Y-%m-%d")
        nxt = base.shift(-1).get(d, np.nan)
        out.append({
            "date": key,
            "moves": {s: float(r[s].get(d, np.nan)) for s in px},
            "next": None if pd.isna(nxt) else float(nxt),
            "event": notes.get(key),
        })
    return out


def build(px: dict[str, pd.Series], round_trip: dict[str, float]) -> dict:
    return {
        "n_days": int(len(next(iter(px.values())))),
        "from": str(next(iter(px.values())).index[0].date()),
        "to": str(next(iter(px.values())).index[-1].date()),
        "base_rates": base_rates(px),
        "edges": edge_table(px),
        "costs": cost_table(px, round_trip),
        "big_moves": big_moves(px),
    }
