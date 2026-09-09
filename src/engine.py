"""バックテストエンジン: 現物ロング/ノーポジのみ(bitFlyer 現物と同じ制約)。

重要な前提:
  * シグナルは t 日の終値までの情報で計算し、約定は t+1 日の終値。
    (未来の値を使う lookahead バイアスを避けるため)
  * 売買コストは「片道コスト c」に集約する:
        買値 = 仲値 * (1 + c)   売値 = 仲値 * (1 - c)
    c = 取引手数料 + スプレッド片道 + スリッページ
  * 最終日に必ず全部売却する。買い持ちも含め全戦略が往復コストを払うので
    比較がフェアになる。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Costs:
    """片道の売買コスト内訳(すべて小数。0.0015 = 0.15%)"""

    fee: float = 0.0015          # 取引手数料
    spread: float = 0.0002       # 板の半値幅 or 販売所スプレッド(片道)
    slippage: float = 0.0005     # 成行の滑り

    @property
    def one_way(self) -> float:
        return self.fee + self.spread + self.slippage

    @property
    def round_trip(self) -> float:
        return 2 * self.one_way

    def label(self) -> str:
        return f"片道{self.one_way*100:.3f}% / 往復{self.round_trip*100:.3f}%"


# bitFlyer の代表的なコスト設定
# Lightning(板): 手数料は直近30日の約定金額で 0.01〜0.15%。最も不利な 0.15% を採用。
#                スプレッドは data.py の実測値(BTC 約0.016%, ETH 約0.031%)。
LIGHTNING_BTC = Costs(fee=0.0015, spread=0.00016, slippage=0.0005)
LIGHTNING_ETH = Costs(fee=0.0015, spread=0.00031, slippage=0.0005)
# 販売所: 取引手数料は無料だが売値と買値の差(スプレッド)が広い。
#         公表値が無いため片道2%と仮定。BATなど板の無い銘柄はここでしか買えない。
HANBAIJO = Costs(fee=0.0, spread=0.02, slippage=0.0)


@dataclass
class Result:
    name: str
    symbol: str
    equity: pd.Series          # 資産推移(JPY)
    trades: list = field(default_factory=list)
    cost_paid: float = 0.0     # 支払った売買コスト合計(JPY)
    initial: float = 1_000_000.0
    pos_hist: np.ndarray = None

    @property
    def final(self) -> float:
        return float(self.equity.iloc[-1])

    @property
    def total_return(self) -> float:
        return self.final / self.initial - 1

    @property
    def years(self) -> float:
        d = (self.equity.index[-1] - self.equity.index[0]).days
        return max(d, 1) / 365.25

    @property
    def cagr(self) -> float:
        if self.final <= 0:
            return -1.0
        return (self.final / self.initial) ** (1 / self.years) - 1

    @property
    def max_drawdown(self) -> float:
        peak = self.equity.cummax()
        return float((self.equity / peak - 1).min())

    @property
    def sharpe(self) -> float:
        r = self.equity.pct_change().dropna()
        r = r[np.isfinite(r)]
        if len(r) < 2 or r.std() == 0:
            return 0.0
        return float(r.mean() / r.std() * np.sqrt(365))

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return float("nan")
        return sum(1 for t in self.trades if t["pnl"] > 0) / len(self.trades)

    @property
    def exposure(self) -> float:
        """相場に資金を晒していた期間の割合"""
        if self.pos_hist is None or len(self.pos_hist) == 0:
            return 1.0
        return float(np.mean(self.pos_hist))


def run_signal(
    prices: pd.Series,
    signal: np.ndarray,
    costs: Costs,
    initial: float = 1_000_000.0,
    name: str = "",
    symbol: str = "",
) -> Result:
    """signal: 各日の「その日の終値時点で持ちたいポジション」(1=フル買い, 0=現金)。

    エンジン側で1日ずらして約定させるので、戦略側は当日終値までの情報で
    自由にシグナルを組んでよい。
    """
    px = prices.to_numpy(dtype=float)
    n = len(px)

    # t 日終値のシグナル -> t+1 日終値で約定
    target = np.zeros(n, dtype=int)
    target[1:] = np.asarray(signal, dtype=int)[:-1]

    c = costs.one_way
    cash, qty = initial, 0.0
    equity = np.zeros(n)
    pos_hist = np.zeros(n)
    cost_paid = 0.0
    trades: list = []
    entry = None

    for t in range(n):
        want = target[t]
        if t == n - 1:
            want = 0  # 最終日は必ず手仕舞い(往復コストを全戦略に負担させる)

        if want == 1 and qty == 0.0 and cash > 0:
            buy = px[t] * (1 + c)
            qty = cash / buy
            cost_paid += qty * px[t] * c
            cash = 0.0
            entry = {"entry_date": prices.index[t], "entry_px": buy, "qty": qty}
        elif want == 0 and qty > 0.0:
            sell = px[t] * (1 - c)
            proceeds = qty * sell
            cost_paid += qty * px[t] * c
            if entry:
                pnl = proceeds - entry["qty"] * entry["entry_px"]
                trades.append({
                    **entry,
                    "exit_date": prices.index[t],
                    "exit_px": sell,
                    "pnl": pnl,
                    "ret": pnl / (entry["qty"] * entry["entry_px"]),
                    "days": (prices.index[t] - entry["entry_date"]).days,
                })
            entry = None
            cash, qty = proceeds, 0.0

        equity[t] = cash + qty * px[t]
        pos_hist[t] = 1.0 if qty > 0 else 0.0

    return Result(
        name=name,
        symbol=symbol,
        equity=pd.Series(equity, index=prices.index),
        trades=trades,
        cost_paid=cost_paid,
        initial=initial,
        pos_hist=pos_hist,
    )


def run_dca(
    prices: pd.Series,
    costs: Costs,
    every_days: int = 7,
    initial: float = 1_000_000.0,
    name: str = "積立(DCA)",
    symbol: str = "",
) -> Result:
    """定期定額積立。initial を期間中に均等に投入しきる。"""
    px = prices.to_numpy(dtype=float)
    n = len(px)
    buy_days = set(range(0, n - 1, every_days))
    amount = initial / len(buy_days)

    c = costs.one_way
    cash, qty = initial, 0.0
    cost_paid = 0.0
    equity = np.zeros(n)

    for t in range(n):
        if t in buy_days and cash >= amount - 1e-9:
            buy = px[t] * (1 + c)
            q = amount / buy
            qty += q
            cost_paid += q * px[t] * c
            cash -= amount
        if t == n - 1 and qty > 0:  # 最終日に全売却
            sell = px[t] * (1 - c)
            cost_paid += qty * px[t] * c
            cash += qty * sell
            qty = 0.0
        equity[t] = cash + qty * px[t]

    return Result(
        name=name,
        symbol=symbol,
        equity=pd.Series(equity, index=prices.index),
        trades=[],
        cost_paid=cost_paid,
        initial=initial,
        pos_hist=np.ones(n),
    )


def run_rebalance(
    price_df: pd.DataFrame,
    costs_by_symbol: dict,
    every_days: int = 30,
    initial: float = 1_000_000.0,
    name: str = "3銘柄等分+月次リバランス",
) -> Result:
    """BTC/ETH/BAT を等金額で保有し、定期的に比率を戻す。"""
    syms = list(price_df.columns)
    px = price_df.to_numpy(dtype=float)
    n, m = px.shape
    target_w = np.full(m, 1.0 / m)

    qty = np.zeros(m)
    cash = initial
    cost_paid = 0.0
    equity = np.zeros(n)

    for t in range(n):
        value = cash + float(np.dot(qty, px[t]))
        if (t % every_days == 0) or t == n - 1:
            liquidate = t == n - 1
            target_val = np.zeros(m) if liquidate else value * target_w
            cur_val = qty * px[t]
            for i, s in enumerate(syms):
                c = costs_by_symbol[s].one_way
                diff = target_val[i] - cur_val[i]
                if abs(diff) < value * 0.005 and not liquidate:
                    continue  # 0.5%未満のズレは動かさない
                if diff > 0:                    # 買い増し
                    q = diff / (px[t][i] * (1 + c))
                    qty[i] += q
                    cost_paid += q * px[t][i] * c
                    cash -= diff
                else:                           # 売り
                    q = min(-diff / px[t][i], qty[i])
                    proceeds = q * px[t][i] * (1 - c)
                    qty[i] -= q
                    cost_paid += q * px[t][i] * c
                    cash += proceeds
        equity[t] = cash + float(np.dot(qty, px[t]))

    return Result(
        name=name,
        symbol="PORTFOLIO",
        equity=pd.Series(equity, index=price_df.index),
        trades=[],
        cost_paid=cost_paid,
        initial=initial,
        pos_hist=np.ones(n),
    )
