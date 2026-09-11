"""いまの状況を1画面ぶんのテキストにまとめる。

Claude Code の見立てタスクがこれを読んで、判断と見立てを書く。
巨大な JSON をそのまま読ませると無駄が多いので、判断に要る分だけを絞って出す。

読み取りは Supabase の anon キー（公開・読み取り専用）。書き込みはしない。
時刻はすべて JST で出す（Supabase は UTC で返すので、そのまま読むと日付を取り違える）。

    python src/brief.py            # 直近の回
    python src/brief.py --runs 6   # 過去6回ぶんの推移も出す

先に `python src/score_calls.py` を回しておくと、Claude の判断の採点結果も最新になる。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import public_db as pdb
from public_db import jst, q

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
AI_STRATEGY = "Claudeの判断"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def pct(v, d=2) -> str:
    return "—" if v is None else f"{float(v)*100:+.{d}f}%"


def yen(v) -> str:
    """1,000円未満は小数2桁まで出す（BAT の数%の動きが丸めで消えないように）。"""
    if v is None:
        return "—"
    v = float(v)
    return f"{v:,.2f}円" if abs(v) < 1000 else f"{v:,.0f}円"


def held(a: dict) -> str:
    return "建玉あり" if float(a.get("qty") or 0) > 0 else "現金"


def load_json(name: str):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def lessons_index() -> list[tuple[str, str, str]]:
    """lessons.md から (ID, 状態, 起点の1行目) を抜く。"""
    p = ROOT / "knowledge" / "lessons.md"
    if not p.exists():
        return []
    # 冒頭の書式見本（コードブロック内の L-000）を拾わないように、コードブロックを除く
    text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
    out = []
    for block in re.split(r"\n(?=### L-\d+)", text):
        m = re.match(r"### (L-\d+)", block)
        if not m:
            continue
        state = re.search(r"\*\*状態:\*\*\s*(\S+)", block)
        origin = re.search(r"\*\*起点:\*\*\s*(.+)", block)
        out.append((m.group(1), state.group(1) if state else "?",
                    (origin.group(1).strip() if origin else "")[:90]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="さかのぼる実行回数")
    args = ap.parse_args()

    runs = q("bf_runs", select="*", order="ran_at.desc", limit=str(args.runs))
    if not runs:
        print("まだ1回も実行されていません。")
        return 1
    run = runs[0]
    rid = run["id"]

    print(f"# いまの状況（{datetime.now(pdb.JST):%Y-%m-%d %H:%M} JST 時点）\n")
    print(f"最新の回: run_id={rid} / {run.get('slot')} / {jst(run.get('ran_at'), '%Y-%m-%d %H:%M')}"
          f" / status={run.get('status')}")
    print(f"要約: {run.get('summary') or '—'}\n")

    # ---------------------------------------------------------------- 相場
    mkt = q("bf_market_snapshots", select="*", run_id=f"eq.{rid}", order="symbol")
    print("## 相場（この回の価格）")
    for m in mkt:
        sp = f" / 片道スプレッド {float(m['spread'])*100:.3f}%" if m.get("spread") else ""
        print(f"- {m['symbol']}: {yen(m['price'])}  24h {pct(m.get('change_24h'))}"
              f"  [{m.get('venue')}]{sp}")

    ind = q("bf_market_indicators", select="*", run_id=f"eq.{rid}", order="key")
    if ind:
        print("\n## 市況の記述（※ Fear & Greed とニュースのトーンは判断根拠にしない: playbook I-01/I-02）")
        for i in ind:
            v = i.get("value")
            v = f"{float(v):,.2f}" if v is not None else (i.get("text_value") or "—")
            extra = f" ({i['text_value']})" if i.get("text_value") and i.get("value") is not None else ""
            print(f"- {i.get('label') or i['key']}: {v}{extra}")

    # ---------------------------------------------------------------- Claude の判断
    card = load_json("ai_scorecard.json")
    calls = (load_json("ai_calls.json") or {}).get("calls", [])
    print(f"\n## {AI_STRATEGY}")
    if not calls:
        print("- まだ判断を1件も出していない")
    else:
        latest = {}
        for c in calls:
            if c.get("symbol") and (c["symbol"] not in latest
                                    or c["made_at"] > latest[c["symbol"]]["made_at"]):
                latest[c["symbol"]] = c
        print("### いま有効な判断（銘柄ごとの最新）")
        for sym in sorted(latest):
            c = latest[sym]
            print(f"- {sym} [{c.get('id')}] {c.get('made_at','')[:16]}: "
                  f"position={c.get('position')} outlook={c.get('outlook')} "
                  f"確信度={c.get('confidence')} 根拠={','.join(c.get('playbook_refs') or [])}")
            if c.get("invalidation"):
                print(f"    反証条件: {c['invalidation']}")

    if card:
        s = card.get("summary") or {}
        print(f"\n### 採点（{jst(card.get('generated_at'))} 時点・score_calls.py が機械的に判定）")
        print(f"- 判断 {s.get('n_calls', 0)}件 / 採点済み {s.get('n_scored', 0)}件 / "
              f"判定なし(±0.5%以内) {s.get('n_void', 0)}件 / 見送り(flat) {s.get('n_abstain', 0)}件 / "
              f"採点待ち {s.get('n_pending', 0)}件")
        if s.get("n_scored"):
            b = s.get("baseline") or {}
            f = lambda v: "—" if v is None else f"{v*100:.0f}%"
            print(f"- 的中率 {f(s.get('hit_rate'))} / ブライア {s.get('brier'):.3f}"
                  f"（コイン投げなら0.250。これより大きければ確信度の付け方が悪い）")
            print(f"- 素朴な基準: コイン投げ 50% / いつも上 {f(b.get('always_up_hit_rate'))} / "
                  f"直前と同じ向き {f(b.get('momentum_hit_rate'))}（{b.get('momentum_n', 0)}件）")
            for c in s.get("calibration") or []:
                print(f"- 確信度 {c['confidence']}: {c['n']}件で的中 {f(c['hit_rate'])}"
                      f"（平均確信度 {c['mean_confidence']:.2f}）")

        scored = [r for r in card.get("calls") or [] if r.get("status") == "scored"]
        if scored:
            print("\n### 直近の答え合わせ（新しい順）")
            for r in scored[:12]:
                mark = "的中" if r.get("hit") else "外れ"
                print(f"- [{mark}] {r['id']} {r['symbol']}: outlook={r.get('outlook')} "
                      f"確信度={r.get('confidence')} → 実際 {r.get('realized')} "
                      f"({pct(r.get('change'))}, {r.get('elapsed_h', 0):.0f}時間, "
                      f"{jst(r.get('ref_at'))}→{jst(r.get('outcome_at'))})")
        pending = [r for r in card.get("calls") or [] if r.get("status") == "pending"]
        if pending:
            print(f"\n### 採点待ち {len(pending)}件")
            for r in pending[:6]:
                print(f"- {r['id']} {r['symbol']}: {r.get('waiting_for')}")

        for sym, a in (card.get("accounts") or {}).items():
            print(f"\n### 口座 {sym}（{jst(a.get('started_at'))} から）")
            print(f"- {AI_STRATEGY}: {pct(a.get('return'))} / 売買{a.get('n_trades')}回 / "
                  f"コスト {yen(a.get('cost_paid'))} / 同じ銘柄 {a.get('of')}口座中 {a.get('rank')}位")
            print(f"- 同期間の買い持ち {pct(a.get('buy_and_hold_same_period'))} / "
                  f"ルールの中央値 {pct(a.get('rules_median_same_period'))}")

    # ---------------------------------------------------------------- 口座
    accs = q("bf_paper_accounts", select="*", order="total_return.desc")
    rules = [a for a in accs if a["strategy"] != AI_STRATEGY]
    if rules:
        tot_eq = sum(float(a.get("equity") or 0) for a in rules)
        tot_init = sum(float(a.get("initial_capital") or 0) for a in rules)
        tot_cost = sum(float(a.get("cost_paid") or 0) for a in rules)
        print(f"\n## ルール口座 {len(rules)}件（※ 何口座が買いかを数えて根拠にしない: playbook I-09）")
        print(f"- 合計評価額 {yen(tot_eq)} / 元手 {yen(tot_init)} "
              f"({pct(tot_eq/tot_init-1 if tot_init else None)})")
        print(f"- 建玉あり {sum(1 for a in rules if float(a.get('qty') or 0) > 0)}件 / "
              f"支払コスト累計 {yen(tot_cost)}")
        for label, rows in (("上位5件", rules[:5]), ("下位5件", rules[-5:])):
            print(f"\n### {label}")
            for a in rows:
                print(f"- {a['symbol']} {a['strategy']}: {pct(a.get('total_return'))} "
                      f"({yen(a.get('equity'))}, {a.get('n_trades')}回, {held(a)})")

    # ---------------------------------------------------------------- 直近の約定
    trades = q("bf_paper_trades", select="*", order="ts.desc", limit="15")
    print(f"\n## 直近の約定 {len(trades)}件")
    if not trades:
        print("- まだ約定なし")
    for t in trades:
        pnl = f" 実現損益 {yen(t.get('realized_pnl'))}" if t.get("realized_pnl") is not None else ""
        print(f"- {jst(t.get('ts'))} {t['symbol']} {t['strategy']}: "
              f"{t['side']} @ {yen(t.get('price'))}{pnl}")

    # ---------------------------------------------------------------- ルールの判断
    sigs = q("bf_signals", select="symbol,strategy,action,signal", run_id=f"eq.{rid}")
    if sigs:
        agg: dict[str, list[str]] = {}
        for s in sigs:
            agg.setdefault(s["action"], []).append(f"{s['symbol']} {s['strategy']}")
        print("\n## この回の判断の内訳")
        for act, items in sorted(agg.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(items[:4]) + ("…" if len(items) > 4 else "")
            print(f"- {act}: {len(items)}件（{shown}）")

    # ---------------------------------------------------------------- ニュース
    news = q("bf_news_articles", select="*", run_id=f"eq.{rid}", order="published_at.desc",
             limit="20")
    print(f"\n## 集めたニュース {len(news)}件（見出しの事実だけを材料にする。トーンの数値は使わない）")
    for n in news:
        coins = ",".join(n.get("coins") or []) or "-"
        print(f"- [{jst(n.get('published_at'))} {n.get('source')}/{coins}] {n.get('title')}")
        if n.get("link"):
            print(f"    {n['link']}")

    # ---------------------------------------------------------------- 推移
    if len(runs) > 1:
        print("\n## 直近の実行")
        for r in runs:
            print(f"- run_id={r['id']} {jst(r.get('ran_at'))} {r.get('slot')}: {r.get('summary') or '—'}")

    # ---------------------------------------------------------------- 教訓と前回の見立て
    idx = lessons_index()
    if idx:
        print("\n## 教訓ノート（knowledge/lessons.md）")
        for lid, state, origin in idx:
            print(f"- {lid} [{state}] {origin}")

    prev = load_json("commentary.json")
    if prev:
        print(f"\n## 前回の見立て（{jst(prev.get('generated_at'))}）")
        print(f"- 見出し: {prev.get('headline')}")
        for w in prev.get("watchlist") or []:
            print(f"- watchlist: {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
