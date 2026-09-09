"""仮売買(ペーパートレード)の口座管理。

戦略 × 銘柄ごとに独立した仮想口座を持ち、朝昼晩のシグナルで実際に
売買を積み上げていく。バックテストと違って「その時点の値段でしか
約定できない」ので、後から都合よく直せない記録になる。

口座の状態は Supabase (bf_paper_accounts) が正。
約定は bf_paper_trades、各回の評価額は bf_equity_snapshots に残る。

売買はすべて全力(現金全部で買い / 全数量を売り)。
サイズ調整の巧拙を混ぜるとシグナルの良し悪しが見えなくなるため。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


@dataclass
class Account:
    id: int
    symbol: str
    strategy: str
    initial_capital: float
    cash: float
    qty: float
    avg_price: float | None
    realized_pnl: float
    cost_paid: float
    n_trades: int

    @classmethod
    def from_row(cls, r: dict) -> "Account":
        return cls(
            id=int(r["id"]), symbol=r["symbol"], strategy=r["strategy"],
            initial_capital=float(r["initial_capital"]), cash=float(r["cash"]),
            qty=float(r["qty"]), avg_price=float(r["avg_price"]) if r.get("avg_price") else None,
            realized_pnl=float(r.get("realized_pnl") or 0),
            cost_paid=float(r.get("cost_paid") or 0),
            n_trades=int(r.get("n_trades") or 0),
        )

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def total_return(self, price: float) -> float:
        return self.equity(price) / self.initial_capital - 1

    @property
    def holding(self) -> bool:
        return self.qty > 0


class PaperBroker:
    """Supabase を口座台帳として使う仮想ブローカー。"""

    def __init__(self, sb, initial_capital: float = 1_000_000):
        self.sb = sb
        self.initial_capital = initial_capital
        self._cache: dict[tuple[str, str], Account] = {}

    # -------------------------------------------------------------- 口座
    def load_all(self) -> dict[tuple[str, str], Account]:
        rows = self.sb.select("bf_paper_accounts", select="*", limit="1000")
        self._cache = {(r["symbol"], r["strategy"]): Account.from_row(r) for r in rows}
        return self._cache

    def ensure(self, symbol: str, strategy: str) -> Account:
        """口座を取り出す。無ければ元手を入れて新規に開く。

        既存口座を upsert で作り直すと残高が初期化されてしまうので、
        必ず「探してから、無ければ作る」順で扱う。
        """
        key = (symbol, strategy)
        if key in self._cache:
            return self._cache[key]

        rows = self.sb.select("bf_paper_accounts", select="*",
                              symbol=f"eq.{symbol}", strategy=f"eq.{strategy}",
                              limit="1")
        if not rows:
            try:
                rows = self.sb.insert(
                    "bf_paper_accounts",
                    {"symbol": symbol, "strategy": strategy,
                     "initial_capital": self.initial_capital,
                     "cash": self.initial_capital, "qty": 0, "realized_pnl": 0,
                     "cost_paid": 0, "n_trades": 0})
            except Exception:
                # 同時実行でかち合った場合は、相手が作ったものを読み直す
                rows = self.sb.select("bf_paper_accounts", select="*",
                                      symbol=f"eq.{symbol}",
                                      strategy=f"eq.{strategy}", limit="1")
                if not rows:
                    raise
        acc = Account.from_row(rows[0])
        self._cache[key] = acc
        return acc

    # -------------------------------------------------------------- 売買
    def step(self, *, run_id: int, symbol: str, strategy: str, signal: int,
             price: float, cost_rate: float, venue: str, reason: str) -> dict | None:
        """シグナルに従って必要なら約定させ、口座を更新する。

        返り値は約定した場合だけ約定内容の dict。何もしなければ None。
        """
        acc = self.ensure(symbol, strategy)
        trade: dict | None = None

        if signal == 1 and not acc.holding and acc.cash > 0:
            unit = price * (1 + cost_rate)          # 手数料込みの実効単価
            qty = acc.cash / unit
            gross = qty * price
            cost = acc.cash - gross
            trade = {
                "run_id": run_id, "account_id": acc.id, "symbol": symbol,
                "strategy": strategy, "side": "buy", "price": price, "qty": qty,
                "gross": gross, "cost": cost, "cash_after": 0.0, "qty_after": qty,
                "realized_pnl": None, "venue": venue, "reason": reason,
                "ts": datetime.now(JST).isoformat(),
            }
            acc.cash = 0.0
            acc.qty = qty
            acc.avg_price = unit
            acc.cost_paid += cost
            acc.n_trades += 1

        elif signal == 0 and acc.holding:
            gross = acc.qty * price
            cost = gross * cost_rate
            proceeds = gross - cost
            basis = acc.qty * (acc.avg_price or price)
            realized = proceeds - basis
            trade = {
                "run_id": run_id, "account_id": acc.id, "symbol": symbol,
                "strategy": strategy, "side": "sell", "price": price, "qty": acc.qty,
                "gross": gross, "cost": cost, "cash_after": acc.cash + proceeds,
                "qty_after": 0.0, "realized_pnl": realized, "venue": venue,
                "reason": reason, "ts": datetime.now(JST).isoformat(),
            }
            acc.cash += proceeds
            acc.qty = 0.0
            acc.avg_price = None
            acc.realized_pnl += realized
            acc.cost_paid += cost
            acc.n_trades += 1

        if trade:
            self.sb.insert("bf_paper_trades", trade, returning=False)

        self.sb.update(
            "bf_paper_accounts",
            {"cash": acc.cash, "qty": acc.qty, "avg_price": acc.avg_price,
             "last_price": price, "equity": acc.equity(price),
             "total_return": acc.total_return(price),
             "realized_pnl": acc.realized_pnl, "cost_paid": acc.cost_paid,
             "n_trades": acc.n_trades,
             "updated_at": datetime.now(JST).isoformat()},
            id=f"eq.{acc.id}")

        self.sb.insert(
            "bf_equity_snapshots",
            {"run_id": run_id, "account_id": acc.id, "symbol": symbol,
             "strategy": strategy, "price": price, "cash": acc.cash, "qty": acc.qty,
             "equity": acc.equity(price), "total_return": acc.total_return(price),
             "ts": datetime.now(JST).isoformat()},
            upsert_on="run_id,account_id", returning=False)

        return trade

    # -------------------------------------------------------------- まとめ
    def snapshot(self, prices: dict[str, float]) -> list[dict]:
        out = []
        for (sym, strat), acc in sorted(self._cache.items()):
            px = prices.get(sym)
            if px is None:
                continue
            out.append({
                "symbol": sym, "strategy": strat, "holding": acc.holding,
                "cash": acc.cash, "qty": acc.qty, "equity": acc.equity(px),
                "total_return": acc.total_return(px), "n_trades": acc.n_trades,
                "realized_pnl": acc.realized_pnl, "cost_paid": acc.cost_paid,
            })
        out.sort(key=lambda r: -r["total_return"])
        return out
