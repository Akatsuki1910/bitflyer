"""毎朝のルーティン本体。

  1. 価格を取り直す (CoinGecko)
  2. bitFlyer の板からスプレッドを実測
  3. 全戦略をバックテストし直す
  4. 各ルールの「今日のシグナル」を出す
  5. 直近1年で「判断の材料が翌日の方向を当てられたか」を検定し直す (study.py)
  6. ニュースを集める
  7. docs/index.html と docs/data/*.json を書き出す
  8. --push を付ければ git commit & push まで行う

GitHub Actions から `python src/daily.py --push` で呼ばれる。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard
import data as datamod
import news as newsmod
import strategies as st
import study as studymod
from engine import Costs, run_dca, run_signal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
CAPITAL = 1_000_000

COSTS = {
    "BTC": Costs(fee=0.0015, spread=0.00016, slippage=0.0005),
    "ETH": Costs(fee=0.0015, spread=0.00031, slippage=0.0005),
    "BAT": Costs(fee=0.0, spread=0.02, slippage=0.0),   # 販売所のみ
}


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def load_research() -> dict | None:
    """src/research.py が手動で書き出した長期の検証結果。無ければページに出さない。"""
    path = ROOT / "docs" / "data" / "research.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def commit_and_push() -> None:
    code, _ = git("rev-parse", "--is-inside-work-tree")
    if code != 0:
        log("git リポジトリではないので push をスキップ")
        return
    git("add", "docs")
    code, out = git("diff", "--cached", "--quiet")
    if code == 0:
        log("変更なし。コミットしません")
        return
    stamp = datetime.now(JST).strftime("%Y-%m-%d")
    code, out = git("commit", "-m", f"chore: 日次更新 {stamp}")
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


def main() -> None:
    ap = argparse.ArgumentParser(description="毎朝のシミュレーション＆ダッシュボード更新")
    ap.add_argument("--push", action="store_true", help="更新後に git commit & push する")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    log("価格を取得")
    prices_raw = datamod.load_all(days=args.days)
    px = {s: df.set_index("date")["close"] for s, df in prices_raw.items()}

    log("bitFlyer の板スプレッドを実測")
    spreads = datamod.measured_spreads()
    for s, v in spreads.items():
        log(f"  {s}: {v*100:.4f}%")
        COSTS[s] = Costs(fee=0.0015, spread=v, slippage=0.0005)

    log("バックテスト")
    rows, signals = [], {}
    for sym in ("BTC", "ETH", "BAT"):
        signals[sym] = {}
        for name in st.CATALOG:
            sig = st.build(name, px[sym])
            signals[sym][name] = int(sig[-1])
            r = run_signal(px[sym], sig, COSTS[sym], initial=CAPITAL, name=name, symbol=sym)
            rows.append({"sym": sym, "name": name, "ret": r.total_return,
                         "mdd": r.max_drawdown, "sharpe": r.sharpe,
                         "trades": r.n_trades, "cost": r.cost_paid})
        d = run_dca(px[sym], COSTS[sym], every_days=7, initial=CAPITAL, symbol=sym)
        rows.append({"sym": sym, "name": "毎週積立(DCA)", "ret": d.total_return,
                     "mdd": d.max_drawdown, "sharpe": d.sharpe,
                     "trades": 0, "cost": d.cost_paid})
        signals[sym]["毎週積立(DCA)"] = 1

    log("1年ぶんの答え合わせ(材料が翌日の方向を当てられたか)")
    round_trip = {s: 2 * (c.fee + c.spread + c.slippage) for s, c in COSTS.items()}
    study = studymod.build(px, round_trip)
    for b in study["base_rates"]:
        log(f"  {b['sym']}: 前日と同じ向き {b['follow']*100:.1f}% / 平均値幅 {b['abs_mean']:.2f}%")

    log("ニュースを収集")
    nws = newsmod.collect()
    log(f"  {len(nws['articles'])}件")

    n_days = len(px["BTC"])
    ctx = {
        "prices": px, "spreads": spreads, "news": nws, "signals": signals,
        "rows": sorted(rows, key=lambda r: (r["sym"], -r["ret"])),
        "n_days": n_days, "study": study,
        "research": load_research(),
    }

    out = dashboard.write(ctx)
    log(f"生成: {out.relative_to(ROOT)}")

    # JSON も残す(履歴として積み上げる)
    ddir = ROOT / "docs" / "data"
    ddir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).strftime("%Y-%m-%d")
    snapshot = {
        "date": today,
        "generated_at": datetime.now(JST).isoformat(),
        "prices": {s: float(px[s].iloc[-1]) for s in px},
        "spreads": {s: float(v) for s, v in spreads.items()},
        "signals": signals,
        "results": [{k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                     for k, v in r.items()} for r in rows],
        "news_tone": nws["by_coin"],
    }
    (ddir / "latest.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (ddir / "study.json").write_text(
        json.dumps(study, ensure_ascii=False, indent=2), encoding="utf-8")

    hist_path = ddir / "history.json"
    hist = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            hist = []
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "prices": snapshot["prices"],
                 "signals": signals, "news_tone": nws["by_coin"]})
    hist = hist[-400:]
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"履歴 {len(hist)}日分")

    if args.push:
        commit_and_push()
    log("完了")


if __name__ == "__main__":
    main()
