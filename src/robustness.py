"""「勝った戦略」が実力かマグレかを調べる。

1) ランダム売買との比較
   同じ売買回数・同じ建玉期間の長さを、日付だけランダムにばらまいた
   売買を何千回も試す。本物の戦略がその分布のどこに位置するかを見る。
   上位5%に入らなければ「サイコロと区別がつかない」。

2) パラメータ感応度
   移動平均の日数を少し変えただけで結果がひっくり返るなら、
   その戦略は過去データに合わせ込んだだけ(カーブフィット)の可能性が高い。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import strategies as st
from engine import Costs, run_signal


def _runs(signal: np.ndarray) -> list[int]:
    """建玉していた連続日数のリスト"""
    out, cur = [], 0
    for v in signal:
        if v:
            cur += 1
        elif cur:
            out.append(cur)
            cur = 0
    if cur:
        out.append(cur)
    return out


def random_signal(n: int, hold_lengths: list[int], rng: np.random.Generator) -> np.ndarray:
    """同じ長さの建玉ブロックを、日付だけランダムに置き直す。"""
    sig = np.zeros(n, dtype=int)
    for length in sorted(hold_lengths, reverse=True):
        for _ in range(60):  # 空きが見つかるまで試す
            start = int(rng.integers(0, max(1, n - length)))
            if sig[start:start + length].sum() == 0:
                sig[start:start + length] = 1
                break
    return sig


def monte_carlo(px: pd.Series, name: str, costs: Costs, capital: float,
                n_iter: int = 3000, seed: int = 42) -> dict:
    sig = st.build(name, px)
    actual = run_signal(px, sig, costs, initial=capital, name=name)
    holds = _runs(sig)
    if not holds:
        return {"name": name, "actual": actual.total_return, "p": float("nan"),
                "median": float("nan"), "n_trades": 0}

    rng = np.random.default_rng(seed)
    n = len(px)
    rets = np.empty(n_iter)
    for i in range(n_iter):
        rs = random_signal(n, holds, rng)
        rets[i] = run_signal(px, rs, costs, initial=capital).total_return

    p = float((rets >= actual.total_return).mean())
    return {
        "name": name,
        "actual": actual.total_return,
        "p": p,                              # ランダムがこの戦略以上になる確率
        "median": float(np.median(rets)),
        "q95": float(np.quantile(rets, 0.95)),
        "n_trades": actual.n_trades,
    }


def sma_grid(px: pd.Series, costs: Costs, capital: float,
             shorts=(5, 10, 15, 20, 30, 40, 50),
             longs=(20, 30, 50, 75, 100, 150, 200)) -> pd.DataFrame:
    """短期/長期の組み合わせ総当たり。数字がバラバラなら不安定な戦略。"""
    rows = {}
    for s in shorts:
        rows[s] = {}
        for l in longs:
            if s >= l:
                rows[s][l] = np.nan
                continue
            sig = st.sma_cross(px, short=s, long=l)
            r = run_signal(px, sig, costs, initial=capital)
            # 一度も売買が起きなかった組み合わせは 0% ではなく「該当なし」
            rows[s][l] = r.total_return * 100 if r.n_trades else np.nan
    return pd.DataFrame(rows).T
