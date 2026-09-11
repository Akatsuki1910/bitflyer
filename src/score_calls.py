"""Claude の判断を、価格だけで機械的に採点する。

Claude が自分の判断を自己採点すると甘くなるので、採点はここでやる。
決まりは knowledge/playbook.md の D-02 と同じ。

  基準の時点  判断(made_at)より後に来た最初のルーティンの価格
              （判断した時点の価格を後から見て有利にならないように）
  評価の時点  基準から (horizon_h - 3) 時間以上たった最初のルーティンの価格
              （GitHub Actions は数時間遅れることがあるので幅を持たせ、実際の経過時間を記録する）
  採点する    見通しが up / down の判断だけ。値動きが +0.5%超なら上、-0.5%未満なら下
  判定なし    値動きが ±0.5% 以内（void）。当たりにも外れにもしない
  見送り      見通しが flat（abstain）。方向の見通しを持たなかったので採点しない
  ブライアス  (確信度 - 的中なら1・外れなら0)^2 の平均。コイン投げ（いつも0.5）なら0.25

素朴な基準として「いつも上」「直前の同じ長さの値動きと同じ向き」も同じ窓で採点し、並べて出す。

※ 当初は上/下/横ばいの3択で採点していたが、1日の値幅（BTC ±1.5%、BAT ±2.7%）に対して
  ±0.5%の横ばいは15〜26%しか起きず、確信度0.5以上を求めると実力の無い正直な予測でも過信になるため改めた。

    python src/score_calls.py            # 採点して docs/data/ai_scorecard.json を書く
    python src/score_calls.py --quiet
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import public_db as pdb

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "docs" / "data" / "ai_calls.json"
OUT = ROOT / "docs" / "data" / "ai_scorecard.json"

AI_STRATEGY = "Claudeの判断"
FLAT_BAND = 0.005
TOLERANCE_H = 3.0


def direction(change: float) -> str:
    if change > FLAT_BAND:
        return "up"
    if change < -FLAT_BAND:
        return "down"
    return "flat"


def load_calls() -> list[dict]:
    if not CALLS.exists():
        return []
    d = json.loads(CALLS.read_text(encoding="utf-8"))
    return d.get("calls", []) if isinstance(d, dict) else []


def made_at(call: dict) -> datetime:
    t = datetime.fromisoformat(call["made_at"])
    return t if t.tzinfo else t.replace(tzinfo=pdb.JST)


def rate(xs: list[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ 判断ごとの採点
def score_calls(calls: list[dict]) -> list[dict]:
    runs = [r for r in pdb.q_all("bf_runs", select="id,ran_at,slot,status", order="ran_at")
            if r.get("status") == "ok"]
    for r in runs:
        r["t"] = pdb.ts(r["ran_at"])
    snaps = pdb.q_all("bf_market_snapshots", select="run_id,symbol,price", order="run_id")
    price = {(s["run_id"], s["symbol"]): float(s["price"]) for s in snaps if s.get("price")}

    def first_after(t: datetime, sym: str, inclusive: bool = False):
        for r in runs:
            if (r["t"] >= t if inclusive else r["t"] > t) and (r["id"], sym) in price:
                return r
        return None

    def last_before(t: datetime, sym: str):
        best = None
        for r in runs:
            if r["t"] <= t and (r["id"], sym) in price:
                best = r
        return best

    out = []
    for c in calls:
        sym = c.get("symbol")
        horizon = float(c.get("horizon_h") or 24)
        conf = float(c.get("confidence") or 0.5)
        row = {k: c.get(k) for k in ("id", "symbol", "made_at", "position", "outlook",
                                      "confidence", "horizon_h", "playbook_refs")}
        row["status"] = "pending"

        ref = first_after(made_at(c), sym)
        if not ref:
            row["waiting_for"] = "判断の後の最初のルーティン"
            out.append(row)
            continue
        end = first_after(ref["t"] + timedelta(hours=horizon - TOLERANCE_H), sym, inclusive=True)
        ref_px = price[(ref["id"], sym)]
        row.update(ref_run_id=ref["id"], ref_at=ref["ran_at"], ref_price=ref_px)
        if not end:
            row["waiting_for"] = f"基準から約{horizon:.0f}時間後のルーティン"
            out.append(row)
            continue

        end_px = price[(end["id"], sym)]
        change = end_px / ref_px - 1
        realized = direction(change)
        row.update(outcome_run_id=end["id"], outcome_at=end["ran_at"], outcome_price=end_px,
                   change=change, realized=realized,
                   elapsed_h=(end["t"] - ref["t"]).total_seconds() / 3600)

        if c.get("outlook") not in ("up", "down"):
            # 方向の見通しを持たなかった。24時間の方向はほぼコイン投げなので、見送りは正当な選択
            row["status"] = "abstain"
        elif realized == "flat":
            # ±0.5%以内しか動かず、上か下かの判定がつかない。当たりにも外れにもしない
            row["status"] = "void"
        else:
            hit = c.get("outlook") == realized
            row.update(status="scored", hit=hit, brier=(conf - (1.0 if hit else 0.0)) ** 2,
                       baseline_up_hit=(realized == "up"))
            prev = last_before(ref["t"] - timedelta(hours=horizon - TOLERANCE_H), sym)
            if prev and prev["id"] != ref["id"]:
                momentum = direction(ref_px / price[(prev["id"], sym)] - 1)
                if momentum in ("up", "down"):
                    row["baseline_momentum_outlook"] = momentum
                    row["baseline_momentum_hit"] = (momentum == realized)
        out.append(row)

    out.sort(key=lambda r: r.get("made_at") or "", reverse=True)
    return out


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["status"] == "scored"]
    mom = [r["baseline_momentum_hit"] for r in scored if "baseline_momentum_hit" in r]

    def block(rs: list[dict]) -> dict:
        return {"n": len(rs), "hit_rate": rate([r["hit"] for r in rs]),
                "brier": statistics.fmean(r["brier"] for r in rs) if rs else None}

    calib = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        rs = [r for r in scored if lo <= float(r.get("confidence") or 0.5) < hi]
        if rs:
            calib.append({"confidence": f"{lo:.1f}〜{min(hi, 1.0):.1f}", "n": len(rs),
                          "hit_rate": rate([r["hit"] for r in rs]),
                          "mean_confidence": statistics.fmean(
                              float(r.get("confidence") or 0.5) for r in rs)})

    count = lambda st: sum(1 for r in rows if r["status"] == st)
    return {
        "n_calls": len(rows), "n_scored": len(scored),
        "n_void": count("void"), "n_abstain": count("abstain"), "n_pending": count("pending"),
        **block(scored),
        "baseline": {
            "always_up_hit_rate": rate([r["baseline_up_hit"] for r in scored]),
            "momentum_hit_rate": rate(mom), "momentum_n": len(mom),
            "coin_flip_hit_rate": 0.5, "coin_flip_brier": 0.25,
        },
        "by_symbol": {s: block([r for r in scored if r["symbol"] == s])
                      for s in sorted({r["symbol"] for r in scored})},
        "by_outlook": {o: block([r for r in scored if r.get("outlook") == o])
                       for o in ("up", "down")},
        "calibration": calib,
    }


# ------------------------------------------------------------------ 口座どうしの比較
def compare_accounts() -> dict:
    """Claude の口座を、同じ銘柄のルール口座と「Claude が始めてから」の同じ期間で比べる。"""
    accounts = pdb.q_all("bf_paper_accounts",
                         select="id,symbol,strategy,equity,initial_capital,n_trades,cost_paid",
                         order="id")
    out = {}
    for ai in (a for a in accounts if a["strategy"] == AI_STRATEGY):
        sym = ai["symbol"]
        first = pdb.q("bf_equity_snapshots", select="run_id,ts",
                      account_id=f"eq.{ai['id']}", order="run_id", limit="1")
        if not first:
            continue
        r0 = first[0]["run_id"]
        at_start = {s["account_id"]: float(s["equity"]) for s in pdb.q(
            "bf_equity_snapshots", select="account_id,equity",
            run_id=f"eq.{r0}", symbol=f"eq.{sym}")}

        rows = []
        for a in accounts:
            if a["symbol"] != sym or a.get("equity") is None:
                continue
            if a["strategy"] == AI_STRATEGY:
                # Claude は r0 で入った売買のコストも自分の成績として負う
                base = float(a["initial_capital"])
            elif a["id"] in at_start:
                base = at_start[a["id"]]
            else:
                continue
            rows.append({"strategy": a["strategy"],
                         "return": float(a["equity"]) / base - 1 if base else None})
        rows.sort(key=lambda r: -(r["return"] if r["return"] is not None else -9))
        ai_row = next(r for r in rows if r["strategy"] == AI_STRATEGY)
        others = [r["return"] for r in rows if r["strategy"] != AI_STRATEGY
                  and r["return"] is not None]
        bh = next((r["return"] for r in rows if r["strategy"] == "買い持ち(Buy&Hold)"), None)
        out[sym] = {
            "started_run_id": r0, "started_at": first[0]["ts"],
            "return": ai_row["return"], "n_trades": ai["n_trades"],
            "cost_paid": float(ai.get("cost_paid") or 0),
            "buy_and_hold_same_period": bh,
            "rules_median_same_period": statistics.median(others) if others else None,
            "rank": rows.index(ai_row) + 1, "of": len(rows),
            "table": rows,
        }
    return out


def build_and_write() -> dict:
    calls = load_calls()
    rows = score_calls(calls) if calls else []
    card = {
        "generated_at": datetime.now(pdb.JST).isoformat(),
        "rules": {"flat_band": FLAT_BAND, "horizon_tolerance_h": TOLERANCE_H,
                  "reference": "判断の後に来た最初のルーティンの価格"},
        "summary": summarize(rows),
        "accounts": compare_accounts(),
        "calls": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(card, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return card


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    card = build_and_write()

    if not args.quiet:
        s = card["summary"]
        pct = lambda v: "—" if v is None else f"{v*100:.0f}%"
        print(f"判断 {s['n_calls']}件 / 採点済み {s['n_scored']}件 / 判定なし {s['n_void']}件 / "
              f"見送り {s['n_abstain']}件 / 採点待ち {s['n_pending']}件")
        if s["n_scored"]:
            print(f"  的中率 {pct(s['hit_rate'])}  ブライア {s['brier']:.3f}（コイン投げなら0.250）")
            b = s["baseline"]
            print(f"  基準: いつも上 {pct(b['always_up_hit_rate'])} / "
                  f"直前と同じ向き {pct(b['momentum_hit_rate'])}（{b['momentum_n']}件）")
        for sym, a in card["accounts"].items():
            print(f"  口座 {sym}: {a['return']*100:+.2f}%（{a['rank']}位/{a['of']}） "
                  f"同期間の買い持ち {pct(a['buy_and_hold_same_period'])}")
        print(f"書き出し: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
