"""docs/index.html を生成する。GitHub Pages がそのまま配信する静的ページ。

外部 CDN に依存しない(チャートは自前のインライン SVG)。
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
JST = timezone(timedelta(hours=9))

SYM_NAME = {"BTC": "ビットコイン", "ETH": "イーサリアム", "BAT": "ベーシックアテンション"}


# ----------------------------------------------------------------- SVG チャート
def sparkline(values, w=560, h=140, pad=6) -> str:
    v = np.asarray([x for x in values if x == x], dtype=float)
    if len(v) < 2:
        return ""
    lo, hi = float(v.min()), float(v.max())
    rng = (hi - lo) or 1.0
    xs = np.linspace(pad, w - pad, len(v))
    ys = h - pad - (v - lo) / rng * (h - 2 * pad)
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    up = v[-1] >= v[0]
    color = "var(--up)" if up else "var(--down)"
    area = f"{pad},{h-pad} " + pts + f" {w-pad},{h-pad}"
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spark" role="img">'
        f'<polygon points="{area}" fill="{color}" opacity="0.10"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def multi_line(series: dict, w=880, h=320, pad=34) -> str:
    """複数の資産推移を1枚に重ねる(全部を初日=100 に正規化)。"""
    colors = ["#f7931a", "#627eea", "#e34a3c", "#3fb950", "#a371f7", "#58a6ff", "#d29922"]
    norm = {}
    for k, s in series.items():
        a = np.asarray(s, dtype=float)
        if len(a) < 2 or a[0] == 0:
            continue
        norm[k] = a / a[0] * 100
    if not norm:
        return ""
    lo = min(float(a.min()) for a in norm.values())
    hi = max(float(a.max()) for a in norm.values())
    rng = (hi - lo) or 1.0

    def y_of(val):
        return h - pad - (val - lo) / rng * (h - 2 * pad)

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    # 横の目盛り
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        val = lo + rng * frac
        y = y_of(val)
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{w-pad}" y2="{y:.1f}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="2" y="{y+4:.1f}" class="ax">{val:.0f}</text>')
    y100 = y_of(100)
    parts.append(f'<line x1="{pad}" y1="{y100:.1f}" x2="{w-pad}" y2="{y100:.1f}" '
                 f'stroke="var(--fg-dim)" stroke-width="1" stroke-dasharray="4 4"/>')

    legend = []
    for i, (k, a) in enumerate(norm.items()):
        c = colors[i % len(colors)]
        xs = np.linspace(pad, w - pad, len(a))
        pts = " ".join(f"{x:.1f},{y_of(val):.1f}" for x, val in zip(xs, a))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2.2" '
                     f'stroke-linejoin="round"/>')
        legend.append(f'<span class="lg"><i style="background:{c}"></i>{html.escape(k)} '
                      f'<b>{a[-1]:.0f}</b></span>')
    parts.append("</svg>")
    return "".join(parts) + f'<div class="legend">{"".join(legend)}</div>'


# ----------------------------------------------------------------- 部品
def card(sym: str, px: pd.Series, spread: float | None, news_mood: dict) -> str:
    last = float(px.iloc[-1])
    def chg(days):
        if len(px) <= days:
            return None
        return last / float(px.iloc[-1 - days]) - 1
    d1, d7, d30 = chg(1), chg(7), chg(30)
    dec = 2 if last < 1000 else 0

    def badge(v, label):
        if v is None:
            return ""
        cls = "up" if v >= 0 else "down"
        return f'<div class="chg"><span class="k">{label}</span>' \
               f'<span class="{cls}">{v*100:+.2f}%</span></div>'

    venue = (f'板 スプレッド {spread*100:.3f}%' if spread is not None
             else '<span class="warn">販売所のみ</span>')
    mood = news_mood.get(sym, {})
    mood_txt = f'ニュース {mood.get("count",0)}件 / {mood.get("mood","中立")}'

    return f"""
    <article class="card">
      <header><h3>{sym} <small>{SYM_NAME[sym]}</small></h3><span class="venue">{venue}</span></header>
      <p class="price">{last:,.{dec}f} <span class="unit">JPY</span></p>
      <div class="chgs">{badge(d1,"24時間")}{badge(d7,"7日")}{badge(d30,"30日")}</div>
      {sparkline(px.to_numpy()[-120:])}
      <p class="axlabel">直近120日</p>
      <p class="mood">{html.escape(mood_txt)}</p>
    </article>"""


def signal_table(signals: dict) -> str:
    """今日のシグナル一覧。行=戦略, 列=銘柄"""
    strategies = list(next(iter(signals.values())).keys())
    head = "".join(f"<th>{s}</th>" for s in signals)
    rows = []
    for st_name in strategies:
        cells = []
        for sym in signals:
            v = signals[sym][st_name]
            cls = "buy" if v == 1 else "flat"
            txt = "買い" if v == 1 else "様子見"
            cells.append(f'<td><span class="sig {cls}">{txt}</span></td>')
        rows.append(f"<tr><th scope=row>{html.escape(st_name)}</th>{''.join(cells)}</tr>")
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>戦略</th>{head}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def result_table(rows: list[dict]) -> str:
    def fmt(r):
        cls = "up" if r["ret"] >= 0 else "down"
        return (f'<tr><td>{html.escape(r["sym"])}</td><td>{html.escape(r["name"])}</td>'
                f'<td class="num {cls}">{r["ret"]*100:+.2f}%</td>'
                f'<td class="num">{r["mdd"]*100:.1f}%</td>'
                f'<td class="num">{r["sharpe"]:.2f}</td>'
                f'<td class="num">{r["trades"]}</td>'
                f'<td class="num">{r["cost"]:,.0f}</td></tr>')
    body = "".join(fmt(r) for r in rows)
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th>銘柄</th><th>戦略</th><th class="num">リターン</th>
      <th class="num">最大DD</th><th class="num">シャープ</th>
      <th class="num">売買</th><th class="num">コスト(円)</th></tr></thead>
      <tbody>{body}</tbody></table></div>"""


def news_list(articles: list[dict]) -> str:
    out = []
    for a in articles[:18]:
        when = ""
        if a.get("published"):
            try:
                when = datetime.fromisoformat(a["published"]).strftime("%m/%d %H:%M")
            except Exception:
                pass
        coins = "".join(f'<span class="tag">{c}</span>' for c in a.get("coins", []))
        tone = a.get("score", 0)
        tcls = "up" if tone > 0 else ("down" if tone < 0 else "")
        link = html.escape(a.get("link", "") or "#", quote=True)
        out.append(
            f'<li><a href="{link}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(a["title"])}</a>'
            f'<div class="meta"><span class="src">{html.escape(a["source"])}</span>'
            f'<span>{when}</span>{coins}'
            f'<span class="{tcls}">{tone:+d}</span></div></li>')
    return f'<ul class="news">{"".join(out)}</ul>'


# ----------------------------------------------------------------- ページ全体
CSS = """
*{box-sizing:border-box}
:root{
  --bg:#ffffff; --panel:#f6f8fa; --line:#d7dde3; --grid:#e6ebf0;
  --fg:#1f2328; --fg-dim:#6b7684; --accent:#0969da;
  --up:#1a7f37; --down:#cf222e; --warnbg:#fff4e5; --warnfg:#9a5b00;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --grid:#21262d;
  --fg:#e6edf3; --fg-dim:#8b949e; --accent:#58a6ff;
  --up:#3fb950; --down:#f85149; --warnbg:#2b2007; --warnfg:#e3b341;
}}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Sans",
  "Noto Sans JP","Yu Gothic UI",Meiryo,sans-serif;}
.wrap{max-width:960px;margin:0 auto;padding:28px 18px 72px}
h1{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:38px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
h3{margin:0;font-size:1.05rem}
h3 small{font-weight:400;color:var(--fg-dim);font-size:.72rem;margin-left:5px}
.sub{color:var(--fg-dim);font-size:.83rem;margin:0 0 6px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));gap:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:15px}
.card header{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.card header h3{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.venue{font-size:.7rem;color:var(--fg-dim);text-align:right;white-space:nowrap}
.price{font-size:1.6rem;font-weight:650;margin:9px 0 7px;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.unit{font-size:.72rem;color:var(--fg-dim);font-weight:400}
.chgs{display:flex;gap:14px;margin-bottom:9px;flex-wrap:wrap}
.chg{display:flex;flex-direction:column;font-size:.82rem;font-variant-numeric:tabular-nums}
.chg .k{font-size:.66rem;color:var(--fg-dim)}
.up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--warnfg);font-weight:600}
.spark{width:100%;height:52px;display:block;margin:3px 0 6px}
.mood{font-size:.74rem;color:var(--fg-dim);margin:2px 0 0}
.axlabel{font-size:.64rem;color:var(--fg-dim);margin:-4px 0 5px;text-align:right}
.chart{width:100%;height:auto;display:block;background:var(--panel);
  border:1px solid var(--line);border-radius:11px;padding:6px}
.ax{fill:var(--fg-dim);font-size:10px}
.legend{display:flex;gap:15px;flex-wrap:wrap;margin-top:9px;font-size:.79rem;color:var(--fg-dim)}
.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.lg b{color:var(--fg);font-variant-numeric:tabular-nums;margin-left:3px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--line);border-radius:11px}
table.grid{border-collapse:collapse;width:100%;font-size:.85rem;min-width:520px}
table.grid th,table.grid td{padding:8px 11px;border-bottom:1px solid var(--grid);text-align:left}
table.grid thead th{background:var(--panel);position:sticky;top:0;font-size:.76rem;
  color:var(--fg-dim);font-weight:600;white-space:nowrap}
table.grid tbody tr:last-child td{border-bottom:0}
table.grid tbody th{font-weight:500;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sig{display:inline-block;padding:2px 9px;border-radius:99px;font-size:.74rem;font-weight:600}
.sig.buy{background:color-mix(in srgb,var(--up) 17%,transparent);color:var(--up)}
.sig.flat{background:var(--grid);color:var(--fg-dim)}
ul.news{list-style:none;padding:0;margin:0;display:grid;gap:2px}
ul.news li{padding:10px 12px;border-bottom:1px solid var(--grid)}
ul.news li:last-child{border-bottom:0}
ul.news a{color:var(--fg);text-decoration:none;font-size:.9rem;line-height:1.5}
ul.news a:hover{color:var(--accent);text-decoration:underline}
.meta{display:flex;gap:9px;align-items:center;flex-wrap:wrap;
  font-size:.7rem;color:var(--fg-dim);margin-top:4px;font-variant-numeric:tabular-nums}
.src{font-weight:600}
.tag{background:var(--grid);border-radius:4px;padding:1px 6px;font-size:.66rem}
.note{background:var(--warnbg);color:var(--warnfg);border-radius:10px;
  padding:13px 15px;font-size:.83rem;margin:16px 0}
.note b{display:block;margin-bottom:3px}
.verdict{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:15px 17px;font-size:.88rem}
.verdict ul{margin:8px 0 0;padding-left:20px} .verdict li{margin:5px 0}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--fg-dim);font-size:.76rem}
footer a{color:var(--accent)}
@media(max-width:560px){.wrap{padding:20px 13px 56px}.price{font-size:1.4rem}}
"""


def build(ctx: dict) -> str:
    now = datetime.now(JST)
    cards = "".join(card(s, ctx["prices"][s], ctx["spreads"].get(s), ctx["news"]["by_coin"])
                    for s in ("BTC", "ETH", "BAT"))
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>bitFlyer 売買シミュレーション</title>
<meta name="description" content="BTC/ETH/BAT を bitFlyer で売買したら儲かったのかを毎朝検証する自動ダッシュボード">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='13' font-size='13'>📈</text></svg>">
<style>{CSS}</style>
</head>
<body><div class="wrap">

<h1>bitFlyer 売買シミュレーション</h1>
<p class="sub">BTC / ETH / BAT を実際に売買していたら儲かったのかを、毎朝自動で検証します。
最終更新 {now.strftime('%Y-%m-%d %H:%M')} JST</p>
<p class="sub">朝昼晩に実際に判断して積み上げている仮売買の記録は
<a href="./db.html"><b>仮売買の記録（Supabase）</b></a> にあります。</p>

<h2>相場</h2>
<div class="cards">{cards}</div>

<h2>今日のシグナル</h2>
<p class="sub">各ルールが「今日の終値時点でポジションを持つべきか」を示します。
売買の推奨ではなく、下の成績表を出しているルールが現在どう判断しているかの表示です。</p>
{signal_table(ctx['signals'])}

<h2>資産推移（元手100として)</h2>
<p class="sub">直近{ctx['n_days']}日。手数料・スプレッド・スリッページを引いた後の金額です。</p>
{multi_line(ctx['curves'])}

<h2>戦略成績（直近{ctx['n_days']}日・売買コスト込み)</h2>
{result_table(ctx['rows'])}

<h2>検証結果</h2>
<div class="verdict">
{ctx['verdict_html']}
</div>

<div class="note">
<b>売買コストの前提</b>
板取引(Lightning)＝手数料0.15%＋実測スプレッド＋スリッページ0.05%。
販売所＝片道2%と仮定。<b style="display:inline">BAT は bitFlyer の板取引に無いため販売所のみ</b>で、
往復4%のコストが最初から乗ります。
</div>

<h2>ニュース</h2>
<p class="sub">BTC / ETH / BAT に言及した直近の記事。数値は強気・弱気ワードの単純カウントで、相場予測ではありません。</p>
{news_list(ctx['news']['articles'])}

<footer>
価格データ: CoinGecko ／ 板情報: bitFlyer Lightning API ／ ニュース: CoinPost・Cointelegraph・CoinDesk<br>
過去の値動きに対する検証結果であり、将来の損益を保証するものではありません。投資判断はご自身の責任で。<br>
GitHub Actions により毎朝 07:00 JST に自動更新。
</footer>
</div></body></html>"""


def write(ctx: dict) -> Path:
    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    out = DOCS / "index.html"
    out.write_text(build(ctx), encoding="utf-8")
    return out
