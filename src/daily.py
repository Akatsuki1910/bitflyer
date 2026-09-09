"""毎朝のルーティン本体。

  1. 価格を取り直す (CoinGecko)
  2. bitFlyer の板からスプレッドを実測
  3. 全戦略をバックテストし直す
  4. 各ルールの「今日のシグナル」を出す
  5. ニュースを集める
  6. docs/index.html と docs/data/*.json を書き出す
  7. --push を付ければ git commit & push まで行う

GitHub Actions から `python src/daily.py --push` で呼ばれる。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard
import data as datamod
import news as newsmod
import robustness as rb
import strategies as st
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


def build_verdict(rows: list[dict], mc: list[dict], n_days: int) -> str:
    tested = [r for r in rows if r["name"] != "買い持ち(Buy&Hold)"]
    profitable = [r for r in tested if r["ret"] > 0]
    bh = {r["sym"]: r["ret"] for r in rows if r["name"] == "買い持ち(Buy&Hold)"}
    beat = [r for r in tested if r["ret"] > bh.get(r["sym"], -9)]
    n = len(tested)
    sig = [m for m in mc if m["p"] < 0.05]
    bonf = 0.05 / max(n, 1)
    survivors = [m for m in mc if m["p"] < bonf]

    n_strategies = len({r["name"] for r in tested})
    n_symbols = len({r["sym"] for r in tested})
    items = [
        f"検証した組み合わせ <b>{n}通り</b>"
        f"（{n_strategies}戦略 × {n_symbols}銘柄、買い持ちを除く）",
        f"売買コストを払った後で黒字だったのは <b>{len(profitable)}通り（{len(profitable)/max(n,1)*100:.0f}%）</b>",
        f"買い持ちに勝ったのは <b>{len(beat)}通り</b>",
    ]
    if mc:
        items.append(
            f"「同じ回数・同じ保有日数でデタラメに売買した場合」と比べて有意に良かったのは "
            f"<b>{len(sig)}通り</b>（何の実力も無くても偶然 {n*0.05:.1f}通り は出る水準）")
        if survivors:
            names = "、".join(f"{m['sym']} {m['name']}" for m in survivors)
            items.append(f"多重検定を補正しても残ったのは <b>{names}</b>")
        else:
            items.append("多重検定を補正すると <b>実力で勝ったと言える戦略は残りません</b>")

    if profitable:
        best = max(profitable, key=lambda r: r["ret"])
        items.append(f"最も成績が良かったのは {best['sym']} の「{best['name']}」"
                     f"（{best['ret']*100:+.2f}%、売買{best['trades']}回）")

    if len(profitable) <= n * 0.25:
        head = (f"<p>直近{n_days}日を振り返ると、"
                "<b>まともに儲かった売買ルールはほぼ見つかりません</b>。"
                "多くのルールは買い持ちより傷が浅いだけで、絶対値ではマイナスです。</p>")
    else:
        head = (f"<p>直近{n_days}日では {len(profitable)} 通りのルールが黒字になりました。"
                "ただし試行回数が多いほど偶然の当たりも増えるため、"
                "下の統計と合わせて見てください。</p>")

    return head + "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


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
    code, out = git("push")
    log("push: " + ("成功" if code == 0 else f"失敗 {out}"))


def main() -> None:
    ap = argparse.ArgumentParser(description="毎朝のシミュレーション＆ダッシュボード更新")
    ap.add_argument("--push", action="store_true", help="更新後に git commit & push する")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--mc-iter", type=int, default=400,
                    help="ランダム売買比較の試行回数(0で省略)")
    args = ap.parse_args()

    log("価格を取得")
    prices_raw = datamod.load_all(days=args.days, force=True)
    px = {s: df.set_index("date")["close"] for s, df in prices_raw.items()}

    log("bitFlyer の板スプレッドを実測")
    spreads = datamod.measured_spreads()
    for s, v in spreads.items():
        log(f"  {s}: {v*100:.4f}%")
        COSTS[s] = Costs(fee=0.0015, spread=v, slippage=0.0005)

    log("バックテスト")
    rows, curves, signals = [], {}, {}
    for sym in ("BTC", "ETH", "BAT"):
        signals[sym] = {}
        for name in st.CATALOG:
            sig = st.build(name, px[sym])
            signals[sym][name] = int(sig[-1])
            r = run_signal(px[sym], sig, COSTS[sym], initial=CAPITAL, name=name, symbol=sym)
            rows.append({"sym": sym, "name": name, "ret": r.total_return,
                         "mdd": r.max_drawdown, "sharpe": r.sharpe,
                         "trades": r.n_trades, "cost": r.cost_paid})
            if name == "買い持ち(Buy&Hold)":
                curves[f"{sym} 買い持ち"] = r.equity.to_numpy()
        d = run_dca(px[sym], COSTS[sym], every_days=7, initial=CAPITAL, symbol=sym)
        rows.append({"sym": sym, "name": "毎週積立(DCA)", "ret": d.total_return,
                     "mdd": d.max_drawdown, "sharpe": d.sharpe,
                     "trades": 0, "cost": d.cost_paid})
        signals[sym]["毎週積立(DCA)"] = 1

    # 一番マシだった戦略の推移も重ねる
    best = max((r for r in rows if r["name"] != "買い持ち(Buy&Hold)"), key=lambda r: r["ret"])
    if best["name"] != "毎週積立(DCA)":
        bsig = st.build(best["name"], px[best["sym"]])
        br = run_signal(px[best["sym"]], bsig, COSTS[best["sym"]], initial=CAPITAL)
        curves[f"{best['sym']} {best['name']}"] = br.equity.to_numpy()

    log("ランダム売買との比較")
    mc = []
    if args.mc_iter > 0:
        for r in rows:
            if r["ret"] <= 0 or r["name"] in ("買い持ち(Buy&Hold)", "毎週積立(DCA)"):
                continue
            m = rb.monte_carlo(px[r["sym"]], r["name"], COSTS[r["sym"]], CAPITAL,
                               n_iter=args.mc_iter)
            m["sym"] = r["sym"]
            mc.append(m)
            log(f"  {r['sym']} {r['name']}: p={m['p']:.3f}")

    log("ニュースを収集")
    nws = newsmod.collect()
    log(f"  {len(nws['articles'])}件")

    n_days = len(px["BTC"])
    ctx = {
        "prices": px, "spreads": spreads, "news": nws, "signals": signals,
        "rows": sorted(rows, key=lambda r: (r["sym"], -r["ret"])),
        "curves": curves, "n_days": n_days,
        "verdict_html": build_verdict(rows, mc, n_days),
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
