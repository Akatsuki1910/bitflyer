"""いまの状況を1画面ぶんのテキストにまとめる。

Claude Code の定期タスクがこれを読んで「見立て」を書く。
巨大な JSON をそのまま読ませると無駄が多いので、判断に要る分だけを絞って出す。

読み取りは Supabase の anon キー(公開・読み取り専用。docs/db.html と同じもの)。
書き込みはしないので service_role は要らない。

    python src/brief.py            # 直近の回
    python src/brief.py --runs 5   # 過去5回ぶんの推移も出す
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _config() -> tuple[str, str]:
    """URL と anon キーは docs/db.html を唯一の出どころにする(二重管理を避ける)。"""
    html = (ROOT / "docs" / "db.html").read_text(encoding="utf-8")
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', html).group(1)
    key = re.search(r'SUPABASE_ANON_KEY\s*=\s*"([^"]+)"', html).group(1)
    return url, key


URL, KEY = _config()


def q(table: str, **params) -> list[dict]:
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='*.,()')}"
                  for k, v in params.items())
    req = urllib.request.Request(
        f"{URL}/rest/v1/{table}?{qs}",
        headers={"apikey": KEY, "Authorization": "Bearer " + KEY,
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pct(v, d=2) -> str:
    return "—" if v is None else f"{float(v)*100:+.{d}f}%"


def yen(v) -> str:
    return "—" if v is None else f"{float(v):,.0f}円"


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

    print(f"# いまの状況（{datetime.now(JST):%Y-%m-%d %H:%M} JST 時点）\n")
    print(f"最新の回: {run.get('slot')} / {run.get('ran_at')} / status={run.get('status')}")
    print(f"要約: {run.get('summary') or '—'}\n")

    # ---------------------------------------------------------------- 相場
    mkt = q("bf_market_snapshots", select="*", run_id=f"eq.{rid}", order="symbol")
    print("## 相場")
    for m in mkt:
        sp = f" / 片道スプレッド {float(m['spread'])*100:.3f}%" if m.get("spread") else ""
        print(f"- {m['symbol']}: {yen(m['price'])}  24h {pct(m.get('change_24h'))}"
              f"  [{m.get('venue')}]{sp}")

    ind = q("bf_market_indicators", select="*", run_id=f"eq.{rid}", order="key")
    if ind:
        print("\n## 市況・センチメント")
        for i in ind:
            v = i.get("value")
            v = f"{float(v):,.2f}" if v is not None else (i.get("text_value") or "—")
            print(f"- {i.get('label') or i['key']}: {v}"
                  + (f" ({i['text_value']})" if i.get("text_value") and i.get("value") is not None else ""))

    # ---------------------------------------------------------------- 口座
    accs = q("bf_paper_accounts", select="*", order="total_return.desc")
    if accs:
        held = [a for a in accs if float(a.get("qty") or 0) > 0]
        traded = [a for a in accs if int(a.get("n_trades") or 0) > 0]
        tot_eq = sum(float(a.get("equity") or 0) for a in accs)
        tot_init = sum(float(a.get("initial_capital") or 0) for a in accs)
        tot_cost = sum(float(a.get("cost_paid") or 0) for a in accs)
        print(f"\n## 仮想口座 {len(accs)}件")
        print(f"- 合計評価額 {yen(tot_eq)} / 元手 {yen(tot_init)} "
              f"({pct(tot_eq/tot_init-1 if tot_init else None)})")
        print(f"- 建玉あり {len(held)}件 / 売買経験あり {len(traded)}件 "
              f"/ 支払コスト累計 {yen(tot_cost)}")
        print("\n### 上位5件")
        for a in accs[:5]:
            print(f"- {a['symbol']} {a['strategy']}: {pct(a.get('total_return'))} "
                  f"({yen(a.get('equity'))}, {a.get('n_trades')}回, "
                  f"{'建玉あり' if float(a.get('qty') or 0) > 0 else '現金'})")
        print("\n### 下位5件")
        for a in accs[-5:]:
            print(f"- {a['symbol']} {a['strategy']}: {pct(a.get('total_return'))} "
                  f"({yen(a.get('equity'))}, {a.get('n_trades')}回, "
                  f"{'建玉あり' if float(a.get('qty') or 0) > 0 else '現金'})")

    # ---------------------------------------------------------------- 直近の約定
    trades = q("bf_paper_trades", select="*", order="ts.desc", limit="15")
    print(f"\n## 直近の約定 {len(trades)}件")
    if not trades:
        print("- まだ約定なし")
    for t in trades:
        print(f"- {t.get('ts','')[:16]} {t['symbol']} {t['strategy']}: "
              f"{t['side']} @ {yen(t.get('price'))}"
              + (f" 実現損益 {yen(t.get('realized_pnl'))}" if t.get("realized_pnl") is not None else ""))

    # ---------------------------------------------------------------- 判断
    sigs = q("bf_signals", select="symbol,strategy,action,signal", run_id=f"eq.{rid}")
    if sigs:
        agg = {}
        for s in sigs:
            agg.setdefault(s["action"], []).append(f"{s['symbol']} {s['strategy']}")
        print("\n## 今回の判断の内訳")
        for act, items in sorted(agg.items(), key=lambda kv: -len(kv[1])):
            print(f"- {act}: {len(items)}件" + (f"（{', '.join(items[:4])}…）" if len(items) > 4
                                                else f"（{', '.join(items)}）" if items else ""))

    # ---------------------------------------------------------------- ニュース
    news = q("bf_news_articles", select="*", run_id=f"eq.{rid}", order="score.desc", limit="20")
    print(f"\n## 集めたニュース {len(news)}件")
    for n in news:
        coins = ",".join(n.get("coins") or []) or "-"
        print(f"- [{n.get('source')}/{coins}/{n.get('score'):+d}] {n.get('title')}")
        if n.get("link"):
            print(f"    {n['link']}")

    # ---------------------------------------------------------------- 推移
    if len(runs) > 1:
        print("\n## 直近の実行")
        for r in runs:
            print(f"- {r.get('ran_at','')[:16]} {r.get('slot')}: {r.get('summary') or '—'}")

    # ---------------------------------------------------------------- 前回の見立て
    prev = ROOT / "docs" / "data" / "commentary.json"
    if prev.exists():
        try:
            c = json.loads(prev.read_text(encoding="utf-8"))
            print(f"\n## 前回の見立て（{c.get('generated_at','')[:16]}）")
            print(f"- 見出し: {c.get('headline')}")
            for s in (c.get("sections") or [])[:5]:
                print(f"- {s.get('title')}: {str(s.get('body'))[:120]}")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
