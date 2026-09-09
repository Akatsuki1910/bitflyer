"""売買ルール集。

どの関数も「t日の終値までの情報だけ」を使って
0/1 のシグナル配列(1=買い持ち, 0=現金)を返す。
約定を1日ずらすのは engine 側の仕事。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def buy_and_hold(px: pd.Series) -> np.ndarray:
    return np.ones(len(px), dtype=int)


def sma_cross(px: pd.Series, short: int = 20, long: int = 50) -> np.ndarray:
    s = px.rolling(short).mean()
    l = px.rolling(long).mean()
    sig = (s > l).astype(int)
    sig[l.isna() | s.isna()] = 0
    return sig.to_numpy()


def above_sma(px: pd.Series, period: int = 100) -> np.ndarray:
    ma = px.rolling(period).mean()
    sig = (px > ma).astype(int)
    sig[ma.isna()] = 0
    return sig.to_numpy()


def _rsi(px: pd.Series, period: int = 14) -> pd.Series:
    d = px.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def rsi_reversion(px: pd.Series, period: int = 14, buy: int = 30, sell: int = 70) -> np.ndarray:
    """売られすぎで買い、買われすぎで売る(逆張り)。"""
    r = _rsi(px, period).to_numpy()
    sig = np.zeros(len(px), dtype=int)
    holding = 0
    for t in range(len(px)):
        if t < period:
            sig[t] = 0
            continue
        if holding == 0 and r[t] < buy:
            holding = 1
        elif holding == 1 and r[t] > sell:
            holding = 0
        sig[t] = holding
    return sig


def donchian(px: pd.Series, entry: int = 20, exit_: int = 10) -> np.ndarray:
    """直近高値ブレイクで買い、直近安値割れで売る(順張り)。"""
    hi = px.rolling(entry).max().shift(1)
    lo = px.rolling(exit_).min().shift(1)
    p = px.to_numpy()
    hi_a, lo_a = hi.to_numpy(), lo.to_numpy()
    sig = np.zeros(len(px), dtype=int)
    holding = 0
    for t in range(len(px)):
        if np.isnan(hi_a[t]) or np.isnan(lo_a[t]):
            sig[t] = 0
            continue
        if holding == 0 and p[t] > hi_a[t]:
            holding = 1
        elif holding == 1 and p[t] < lo_a[t]:
            holding = 0
        sig[t] = holding
    return sig


def momentum(px: pd.Series, lookback: int = 90) -> np.ndarray:
    """Nか月前より高ければ買い持ち(タイムシリーズ・モメンタム)。"""
    sig = (px > px.shift(lookback)).astype(int)
    sig[px.shift(lookback).isna()] = 0
    return sig.to_numpy()


def macd(px: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> np.ndarray:
    ema_f = px.ewm(span=fast, adjust=False).mean()
    ema_s = px.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig_line = line.ewm(span=signal, adjust=False).mean()
    out = (line > sig_line).astype(int)
    out.iloc[:slow] = 0
    return out.to_numpy()


# ルール名 -> (関数, 引数)
CATALOG = {
    "買い持ち(Buy&Hold)":       (buy_and_hold, {}),
    "SMA 5/20 クロス":          (sma_cross, {"short": 5, "long": 20}),
    "SMA 20/50 クロス":         (sma_cross, {"short": 20, "long": 50}),
    "SMA 50/100 クロス":        (sma_cross, {"short": 50, "long": 100}),
    "100日線より上で保有":       (above_sma, {"period": 100}),
    "RSI14 逆張り(30/70)":      (rsi_reversion, {}),
    "ドンチャン 20/10 ブレイク": (donchian, {}),
    "3か月モメンタム":           (momentum, {"lookback": 90}),
    "MACD 12/26/9":             (macd, {}),
}


def build(name: str, px: pd.Series) -> np.ndarray:
    fn, kw = CATALOG[name]
    return fn(px, **kw)
