"""朝昼晩に回すルーティン本体。

  1. 情報をかき集める (collect.py: 板・販売所参考値・Fear&Greed・ニュース等)
  2. 過去の日足に「今の値段」を継ぎ足してシグナルを計算し直す
  3. 戦略 × 銘柄ごとの仮想口座で実際に仮売買する (paper.py)
  4. 集めた情報・判断の根拠・約定・評価額を Supabase に丸ごと残す
  5. docs/data/routine.json にも同じ内容を書き出す(ページの予備 + git 履歴)

使い方:
    python src/routine.py                 # スロットは今の時刻から自動判定
    python src/routine.py --slot morning
    python src/routine.py --dry-run       # DB に書かずに動作だけ見る
    python src/routine.py --push          # 生成物を commit & push
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect as collector
import data as datamod
import db as dbmod
import rationale as rat
import strategies as st
from engine import Costs, run_signal
from paper import PaperBroker

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
SYMBOLS = ("BTC", "ETH", "BAT")
CAPITAL = 1_000_000

# 販売所しか無い銘柄の片道スプレッド想定(公表値が無いので仮定値)
HANBAIJO_SPREAD = 0.02
BOARD_FEE = 0.0015      # 板取引の手数料(最も不利なレート)
SLIPPAGE = 0.0005


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def slot_now() -> str:
    h = datetime.now(JST).hour
    if h < 10:
        return "morning"
    if h < 16:
        return "noon"
    return "evening"


SLOT_JA = {"morning": "朝", "noon": "昼", "evening": "晩", "manual": "手動"}


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def git_sha() -> str | None:
    code, out = git("rev-parse", "--short", "HEAD")
    return out if code == 0 else None


def commit_and_push(slot: str) -> None:
    code, _ = git("rev-parse", "--is-inside-work-tree")
    if code != 0:
        log("git リポジトリではないので push をスキップ")
        return
    git("add", "docs", "data")
    code, _ = git("diff", "--cached", "--quiet")
    if code == 0:
        log("変更なし。コミットしません")
        return
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    code, out = git("commit", "-m", f"chore: 仮売買ルーティン {SLOT_JA.get(slot, slot)} {stamp}")
    log(f"commit: {out.splitlines()[0] if out else 'ok'}")
    for attempt in range(3):
        git("pull", "--rebase", "--autostash")
        code, out = git("push")
        if code == 0:
            log("push: 成功")
            return
        log(f"push 失敗({attempt+1}/3): {out.splitlines()[-1] if out else ''}")
        time.sleep(4)
    log("push: 3回とも失敗した")


def cost_for(sym: str, snap: dict) -> tuple[float, str]:
    """片道の合計コスト率と取引場所を返す。"""
    if snap.get("hanbaijo_only") or snap.get("spread") is None:
        return HANBAIJO_SPREAD, "販売所"
    return BOARD_FEE + float(snap["spread"]) + SLIPPAGE, "板(Lightning)"


def build_series(sym: str, hist: pd.DataFrame, live_price: float) -> pd.Series:
    """日足の終値に「今この瞬間の値段」を今日の値として継ぎ足す。"""
    s = hist.set_index("date")["close"].astype(float)
    today = pd.Timestamp(datetime.now(JST).date())
    s.loc[today] = float(live_price)
    return s.sort_index()


# --------------------------------------------------------------------- 本体
def main() -> int:
    ap = argparse.ArgumentParser(description="朝昼晩の情報収集＆仮売買ルーティン")
    ap.add_argument("--slot", choices=["morning", "noon", "evening", "manual"],
                    default=None, help="省略時は現在時刻から判定")
    ap.add_argument("--dry-run", action="store_true", help="DB に書かない")
    ap.add_argument("--push", action="store_true", help="生成物を commit & push")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--no-backtest", action="store_true", help="参考バックテストを省く")
    args = ap.parse_args()

    started = time.time()
    slot = args.slot or slot_now()
    now = datetime.now(JST)
    log(f"ルーティン開始: {SLOT_JA.get(slot, slot)} ({slot}) {now:%Y-%m-%d %H:%M:%S}")

    sb = dbmod.client()
    use_db = sb.enabled and not args.dry_run
    if not use_db:
        why = "--dry-run 指定" if args.dry_run else "SUPABASE_SERVICE_ROLE_KEY が無い"
        log(f"DB 保存はスキップ（{why}）")

    # ---------------------------------------------------------------- 1. 収集
    ctx = collector.collect_all(SYMBOLS)
    market = ctx["market"]
    if not market:
        log("値段が1つも取れなかったので中断")
        return 1

    # ---------------------------------------------------------------- 2. 履歴
    log("日足の履歴を取得")
    hist = datamod.load_all(days=args.days, force=True)
    series = {s: build_series(s, hist[s], market[s]["price"]) for s in market}

    # ---------------------------------------------------------------- 3. run 作成
    run_id = None
    if use_db:
        rows = sb.insert("bf_runs", {
            "trade_date": now.strftime("%Y-%m-%d"), "slot": slot,
            "ran_at": now.isoformat(), "status": "running", "git_sha": git_sha(),
        }, upsert_on="trade_date,slot")
        run_id = int(rows[0]["id"])
        log(f"run_id = {run_id}")

    try:
        # ------------------------------------------------------------ 4. 相場保存
        if use_db:
            sb.insert("bf_market_snapshots", [{
                "run_id": run_id, "symbol": sym, "price": v["price"],
                "venue": v.get("venue") or "?", "spread": v.get("spread"),
                "best_bid": v.get("best_bid"), "best_ask": v.get("best_ask"),
                "volume_24h": v.get("volume_24h"), "change_24h": v.get("change_24h"),
                "raw": v.get("raw"), "ts": now.isoformat(),
            } for sym, v in market.items()], upsert_on="run_id,symbol", returning=False)

            sb.insert("bf_market_indicators", [{
                "run_id": run_id, "key": k, "label": v.get("label"),
                "value": v.get("value"), "text_value": v.get("text"),
                "source": v.get("source"), "ts": now.isoformat(),
            } for k, v in ctx["indicators"].items()],
                upsert_on="run_id,key", returning=False)

            arts, seen = [], set()
            for a in ctx["news"]["articles"]:
                link = a.get("link") or None
                if link and link in seen:
                    continue
                seen.add(link)
                arts.append({
                    "run_id": run_id, "source": a["source"], "lang": a.get("lang"),
                    "title": a["title"][:500], "link": link,
                    "summary": (a.get("summary") or "")[:1000],
                    "published_at": a.get("published"), "coins": a.get("coins") or [],
                    "score": a.get("score", 0)})
            if arts:
                sb.insert("bf_news_articles", arts,
                          upsert_on="run_id,link", returning=False)
            log(f"相場・指標・ニュース {len(arts)}件 を保存")

        # ------------------------------------------------------------ 5. 仮売買
        broker = PaperBroker(sb, CAPITAL) if use_db else None
        if broker:
            broker.load_all()

        strategies = [n for n in st.CATALOG]
        sig_rows, trades, decisions = [], [], []

        for sym in market:
            px = series[sym]
            cost_rate, venue = cost_for(sym, market[sym])
            price = float(market[sym]["price"])
            log(f"{sym}: {price:,.0f}円  {venue}  片道コスト {cost_rate*100:.3f}%")

            for name in strategies:
                sig = int(st.build(name, px)[-1])
                ind, why = rat.indicators_for(name, px)
                ind["cost_rate_pct"] = cost_rate * 100
                ind["venue"] = venue

                prev = None
                if broker:
                    acc = broker.ensure(sym, name)
                    prev = 1 if acc.holding else 0
                action = rat.action_label(prev, sig)

                tone = (ctx["news"]["by_coin"].get(sym) or {})
                full_why = why
                if tone:
                    full_why += (f" 参考: 直近のニュース{tone.get('count', 0)}件のトーンは"
                                 f"{tone.get('mood', '不明')}。")
                full_why += f" 往復コストは約{cost_rate*2*100:.2f}%（{venue}）。"

                sig_rows.append({
                    "run_id": run_id, "symbol": sym, "strategy": name,
                    "signal": sig, "prev_signal": prev, "action": action,
                    "rationale": full_why, "indicators": ind, "ts": now.isoformat()})
                decisions.append({"symbol": sym, "strategy": name, "signal": sig,
                                  "action": action, "rationale": full_why,
                                  "indicators": ind})

                if broker:
                    t = broker.step(run_id=run_id, symbol=sym, strategy=name,
                                    signal=sig, price=price, cost_rate=cost_rate,
                                    venue=venue, reason=full_why)
                    if t:
                        trades.append(t)
                        log(f"  約定 {sym} / {name}: {t['side']} "
                            f"{t['qty']:.6f} @ {price:,.0f}")

        if use_db and sig_rows:
            sb.insert("bf_signals", sig_rows,
                      upsert_on="run_id,symbol,strategy", returning=False)
        log(f"判断 {len(sig_rows)}件、約定 {len(trades)}件")

        # ------------------------------------------------------------ 6. 参考バックテスト
        bt_rows = []
        if not args.no_backtest:
            for sym in market:
                cost_rate, _ = cost_for(sym, market[sym])
                if market[sym].get("hanbaijo_only"):
                    costs = Costs(fee=0.0, spread=HANBAIJO_SPREAD, slippage=0.0)
                else:
                    costs = Costs(fee=BOARD_FEE, spread=float(market[sym]["spread"]),
                                  slippage=SLIPPAGE)
                for name in strategies:
                    r = run_signal(series[sym], st.build(name, series[sym]), costs,
                                   initial=CAPITAL, name=name, symbol=sym)
                    bt_rows.append({
                        "run_id": run_id, "symbol": sym, "strategy": name,
                        "days": len(series[sym]), "total_return": float(r.total_return),
                        "max_drawdown": float(r.max_drawdown), "sharpe": float(r.sharpe),
                        "n_trades": int(r.n_trades), "cost_paid": float(r.cost_paid)})
            if use_db and bt_rows:
                sb.insert("bf_backtests", bt_rows,
                          upsert_on="run_id,symbol,strategy", returning=False)
            log(f"参考バックテスト {len(bt_rows)}件")

        # ------------------------------------------------------------ 7. まとめ
        prices = {s: float(v["price"]) for s, v in market.items()}
        standings = broker.snapshot(prices) if broker else []
        summary = (f"{SLOT_JA.get(slot, slot)}のルーティン: "
                   f"{len(market)}銘柄 × {len(strategies)}戦略を判定、"
                   f"{len(trades)}件約定、ニュース{len(ctx['news']['articles'])}件")
        if standings:
            top = standings[0]
            summary += (f"。首位は {top['symbol']} {top['strategy']} "
                        f"({top['total_return']*100:+.2f}%)")
        log(summary)

        out = {
            "run_id": run_id, "slot": slot, "slot_ja": SLOT_JA.get(slot, slot),
            "ran_at": now.isoformat(), "summary": summary,
            "market": {s: {k: v for k, v in m.items() if k != "raw"}
                       for s, m in market.items()},
            "indicators": ctx["indicators"],
            "news": ctx["news"]["articles"][:25],
            "news_tone": ctx["news"]["by_coin"],
            "decisions": decisions,
            "trades": [{k: v for k, v in t.items() if k != "reason"} for t in trades],
            "standings": standings,
            "backtests": bt_rows,
        }
        ddir = ROOT / "docs" / "data"
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "routine.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        hist_path = ddir / "routine_history.json"
        rh = []
        if hist_path.exists():
            try:
                rh = json.loads(hist_path.read_text(encoding="utf-8"))
            except Exception:
                rh = []
        rh = [x for x in rh
              if not (x.get("ran_at", "")[:10] == now.strftime("%Y-%m-%d")
                      and x.get("slot") == slot)]
        rh.append({"ran_at": now.isoformat(), "slot": slot, "run_id": run_id,
                   "prices": prices, "n_trades": len(trades), "summary": summary,
                   "standings": standings[:5]})
        rh = rh[-300:]
        hist_path.write_text(json.dumps(rh, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")

        if use_db:
            sb.update("bf_runs",
                      {"status": "ok", "summary": summary,
                       "duration_ms": int((time.time() - started) * 1000)},
                      id=f"eq.{run_id}")

    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if use_db and run_id:
            sb.update("bf_runs", {"status": "error", "error": str(e)[:2000],
                                  "duration_ms": int((time.time() - started) * 1000)},
                      id=f"eq.{run_id}")
        return 1

    if args.push:
        commit_and_push(slot)
    log(f"完了 ({time.time() - started:.1f}秒)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
