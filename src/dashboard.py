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


# ----------------------------------------------------------------- 1年ぶんの答え合わせ
def edge_table(study: dict) -> str:
    """材料ごとの「翌日の方向」。当たっていないことを見せるための表。"""
    syms = [b["sym"] for b in study["base_rates"]]
    head = "".join(f'<th class="num">{s}</th>' for s in syms)
    rows = []
    for row in study["edges"]:
        cells = []
        for s in syms:
            c = row["cells"].get(s)
            if not c:
                cells.append('<td class="num">—</td>')
                continue
            # 五分五分から離れていると言えるのは p<0.05 のときだけ
            strong = ' class="warn"' if c["p"] < 0.05 else ""
            cells.append(
                f'<td class="num"><span{strong}>{c["side"]}{c["rate"]*100:.0f}%</span>'
                f'<br><small>{c["judged"]}日 p={c["p"]:.2f}</small></td>')
        rows.append(f'<tr><th scope=row>{html.escape(row["label"])}</th>{"".join(cells)}</tr>')
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>判断に使ってきた材料</th>{head}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def cost_table(study: dict) -> str:
    """保有期間ごとに、往復コストを取り返すのに必要な的中率。"""
    days = [h["days"] for h in study["costs"][0]["holds"]]
    head = "".join(f'<th class="num">{d}日</th>' for d in days)
    rows = []
    for c in study["costs"]:
        cells = []
        for h in c["holds"]:
            need = "取り返せない" if h["need"] is None else f'{h["need"]*100:.0f}%'
            cls = ' class="warn"' if h["need"] is None or h["need"] > 0.6 else ""
            cells.append(f'<td class="num"><span{cls}>{need}</span>'
                         f'<br><small>値幅 {h["move"]:.1f}%</small></td>')
        rows.append(f'<tr><th scope=row>{c["sym"]}<br><small>往復 {c["cost"]:.2f}%</small></th>'
                    f'{"".join(cells)}</tr>')
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>銘柄</th>{head}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def moves_table(study: dict) -> str:
    """大きく動いた日と、その日の材料(手書きメモ)。"""
    rows = []
    for m in study["big_moves"]:
        mv = "".join(
            f'<td class="num {"up" if v >= 0 else "down"}">{v:+.1f}%</td>'
            for v in m["moves"].values())
        nxt = ("—" if m["next"] is None else
               f'<span class="{"up" if m["next"] >= 0 else "down"}">{m["next"]:+.1f}%</span>')
        ev = m.get("event")
        if ev:
            note = html.escape(ev["note"])
            if ev.get("url"):
                note += (f' <a href="{html.escape(ev["url"], quote=True)}" target="_blank" '
                         f'rel="noopener noreferrer">{html.escape(ev.get("source", "出典"))}</a>')
        else:
            note = '<span class="dim">—</span>'
        rows.append(f'<tr><th scope=row>{m["date"]}</th>{mv}'
                    f'<td class="num">{nxt}</td><td class="why">{note}</td></tr>')
    heads = "".join(f'<th class="num">{s}</th>' for s in study["big_moves"][0]["moves"])
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>日付</th>{heads}<th class="num">翌日のBTC</th>
      <th>その日の材料（動いた後に報じられたもの）</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


# ----------------------------------------------------------------- 8年ぶんで探した傾向
VERDICT_CLS = {"再現した": "up", "効かない": "down", "再現しなかった": "down"}


def research_rows(res: dict) -> str:
    rows = []
    for r in res["rows"]:
        cls = VERDICT_CLS.get(r["verdict"], "dim")
        note = f'<br><small>{html.escape(r["note"])}</small>' if r.get("note") else ""
        rows.append(
            f'<tr><th scope=row>{html.escape(r["name"])}{note}</th>'
            f'<td>{html.escape(r["kind"])}</td>'
            f'<td class="num">{html.escape(r["is"])}</td><td class="num">{html.escape(r["oos"])}</td>'
            f'<td><span class="{cls}"><b>{html.escape(r["verdict"])}</b></span></td></tr>')
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>傾向</th><th>種類</th><th class="num">前半 {res['split']['is']}</th>
      <th class="num">後半 {res['split']['oos']}</th><th>判定</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def magnitude_table(res: dict) -> str:
    head = "".join(f'<th class="num">{lab}</th>' for lab in
                   ("最も小さい", "小さい", "中くらい", "大きい", "最も大きい"))
    rows = []
    for sym, m in res["magnitude"].items():
        cells = "".join(f'<td class="num">{q["move"]:.2f}%<br><small>±0.5%以内 {q["flat"]*100:.0f}%</small></td>'
                        for q in m["quintiles"])
        rows.append(f"<tr><th scope=row>{sym}</th>{cells}</tr>")
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>予測した値幅</th>{head}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def trend_table(res: dict) -> str:
    periods = list(dict.fromkeys(t["period"] for t in res["trend"]))
    head = "".join(f'<th class="num">{p}</th>' for p in periods)
    rows = []
    for sym in dict.fromkeys(t["sym"] for t in res["trend"]):
        for key, label in (("bh", "買い持ち"), ("rule", "100日線の上で保有")):
            cells = []
            for p in periods:
                t = next(x for x in res["trend"] if x["sym"] == sym and x["period"] == p)[key]
                cls = "up" if t["annual"] >= 0 else "down"
                cells.append(f'<td class="num"><span class="{cls}">{t["annual"]*100:+.0f}%</span>'
                             f'<br><small>最大下落 {t["maxdd"]*100:.0f}%</small></td>')
            rows.append(f'<tr><th scope=row>{sym} {label}</th>{"".join(cells)}</tr>')
    return f"""<div class="scroll"><table class="grid">
      <thead><tr><th scope=col>年率リターン（往復コスト込み）</th>{head}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


def research_section(res: dict | None) -> str:
    if not res:
        return ""
    m = res["models"]
    model_txt = "／".join(
        f'{s} 前半 {v["is"]["hit"]*100:.1f}% → <b>後半 {v["oos"]["hit"]*100:.1f}%</b>'
        f'（いつも上 {v["oos"]["always_up"]*100:.1f}%）' for s, v in m.items())
    now = "／".join(f'{s} {"上" if v["above"] else "下"}（{v["vs_ma100"]*100:+.0f}%、{v["since"]} から）'
                    for s, v in res["now"].items())
    return f"""
<h2>8年ぶんで探した傾向</h2>
<p class="sub">1年では標本が足りないので、{res['from']} 〜 {res['to']} の Binance の日足・1時間足、資金調達率、
Fear & Greed、米国株、FOMC の日程（{res['fomc_count']}回）で調べ直しました。
<b>前半（{res['split']['is']}）で見つけた傾向が、後半（{res['split']['oos']}）でも同じ向きに出るか</b>で判定しています。
更新は手動（<code>python src/research.py</code>）、{res['generated_at'][:10]} 時点。</p>
{research_rows(res)}
<p class="sub">これらの材料を全部入れて翌日の方向を予測するモデルを前半で作ると、的中率は {model_txt}。
<b>前半で当たって見えた分は、後半ではほぼ消えました。</b></p>

<h2>予測できたのは「方向」より「大きさ」</h2>
<p class="sub">直近の値幅・週末・FOMC の日程から翌日の値幅を予測し（前半で推定）、後半の日を予測の小さい順に5つに分けた実際の平均値幅です。</p>
{magnitude_table(res)}

<h2>数週間のトレンドで持つ／持たないを決めると</h2>
<p class="sub">「100日線の上なら持つ」を週1回（月曜）だけ判定した場合と、買い持ちの比較です。
移動平均の日数を30〜200日で変えても、BTC・ETH ではほとんどの期間で最大下落が浅くなりました。
強い上昇相場（2023-24）では取り逃がします。いまの100日線との位置: {now}</p>
{trend_table(res)}
"""


def study_findings(study: dict) -> str:
    br = {b["sym"]: b for b in study["base_rates"]}
    follow = "／".join(f'{s} {br[s]["follow"]*100:.0f}%' for s in br)
    n_tests = sum(1 for row in study["edges"] for c in row["cells"].values() if c)
    n_strong = sum(1 for row in study["edges"] for c in row["cells"].values()
                   if c and c["p"] < 0.05)
    need1 = {c["sym"]: c["holds"][0]["need"] for c in study["costs"]}
    need_txt = "／".join(
        f'{s} {"どんな的中率でも不可能" if v is None else f"{v*100:.0f}%"}'
        for s, v in need1.items())
    kinds = list(dict.fromkeys(
        k for m in study["big_moves"] if m.get("event")
        for k in m["event"]["kind"].split("・")))
    items = [
        f"翌日が前日と同じ向きに動いた割合は {follow}。"
        "<b>「直前の動きに乗る／逆らう」は、それだけでは根拠にならない</b>",
        f"11の材料 × 3銘柄 = {n_tests}通りを試して、五分五分と言えないほど偏ったのは "
        f"<b>{n_strong}通り</b>（実力が無くても偶然 {n_tests*0.05:.1f}通り は出る水準）",
        f"24時間の方向を当てて出入りするとき、往復コストを取り返すのに必要な的中率は {need_txt}。"
        "<b>BAT は1日の平均値幅より往復コストのほうが大きい</b>",
        "保有を長くするほど必要な的中率は下がる（BTC は60日保有なら51%）。"
        "<b>方向を当てる勝負より、持つ期間を延ばすほうがコスト構造に合う</b>",
        f"大きく動いた日の材料は {('／'.join(kinds) or '—')} で、"
        "いずれも<b>動いた後に記事になったもの</b>。前日に読めた材料ではない",
    ]
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


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
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--line);border-radius:11px}
table.grid{border-collapse:collapse;width:100%;font-size:.85rem;min-width:520px}
table.grid th,table.grid td{padding:8px 11px;border-bottom:1px solid var(--grid);text-align:left}
table.grid thead th{background:var(--panel);position:sticky;top:0;font-size:.76rem;
  color:var(--fg-dim);font-weight:600;white-space:nowrap}
table.grid tbody tr:last-child td{border-bottom:0}
table.grid tbody th{font-weight:500;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.grid small{color:var(--fg-dim);font-size:.7rem;font-weight:400}
table.grid td.why{white-space:normal;min-width:260px;font-size:.8rem;line-height:1.5}
table.grid td.why a{color:var(--accent)}
.dim{color:var(--fg-dim)}
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

<h2>1年ぶんの答え合わせ</h2>
<p class="sub">{ctx['study']['from']} 〜 {ctx['study']['to']}（{ctx['study']['n_days']}日）の日足で、
このサイトが判断の根拠にしてきた材料が「翌日どちらに動くか」を当てられていたかを調べた結果です。
翌日の変化が ±0.5% 以内の日は判定なしとして数えません。</p>
{edge_table(ctx['study'])}
<p class="sub">セルは「多かった側とその割合／判定できた日数／p値」。p値は「実力が無くてもこれくらい偏るか」の目安で、
0.05 未満だけが五分五分と言いにくい数字です。<b>どの材料も、翌日の方向をほとんど当てられていません。</b></p>

<h2>コストの壁（往復コストを取り返すのに必要な的中率)</h2>
<p class="sub">保有期間ごとの平均的な値幅に対して、売買コストを差し引いても黒字が残る的中率です。</p>
{cost_table(ctx['study'])}

<h2>大きく動いた日に何があったか</h2>
<p class="sub">直近{ctx['study']['n_days']}日で BTC の値動きが大きかった10日。材料は
<a href="https://github.com/Akatsuki1910/bitflyer/blob/main/knowledge/events.json">knowledge/events.json</a>
に手で書き写したものです。</p>
{moves_table(ctx['study'])}

<h2>わかったこと</h2>
<div class="verdict">
{study_findings(ctx['study'])}
</div>
{research_section(ctx.get('research'))}

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
