"""長期データで「予測できる傾向」を探す研究用スクリプト(手動で実行する)。

直近1年(study.py)では標本が小さいので、2018年以降の日足・2020年以降の1時間足・
資金調達率・Fear & Greed・米国市場の指標・FOMC の日程を集めて検定し直す。

見つけた傾向が過去データへの当てはめでないことを確かめるため、
**2018〜2022年(前半)で見つけたものが、2023年以降(後半)でも同じ向きに出るか**を必ず見る。

    python src/research.py            # 取得(キャッシュがあれば使う)して docs/data/research.json を書く
    python src/research.py --refresh  # 取り直す

価格は Binance の USDT 建て(bitFlyer の円建てとはドル円の分だけずれる)。
Binance の先物 API は地域によって弾かれるので、GitHub Actions では回さない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data"
OUT = ROOT / "docs" / "data" / "research.json"
JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "bitflyer-research/1.0"}

IS_END, OOS_START = "2022-12-31", "2023-01-01"
COST = {"BTC": 0.0045, "ETH": 0.0049, "BAT": 0.04}      # 往復
BAND = 0.5


# ----------------------------------------------------------------- 取得
def _get(url: str, raw: bool = False):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                body = r.read()
                return body.decode("utf-8", "ignore") if raw else json.loads(body)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"取得失敗: {url} ({last})")


def _cached(name: str, refresh: bool, fetch) -> pd.DataFrame:
    path = CACHE / f"lt_{name}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path)
    print(f"  取得: {name}")
    df = fetch()
    df.to_csv(path, index=False)
    return df


def klines(sym: str, interval: str, start: str, refresh: bool) -> pd.DataFrame:
    def fetch():
        rows, t = [], int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
        while True:
            d = _get("https://data-api.binance.vision/api/v3/klines"
                     f"?symbol={sym}USDT&interval={interval}&startTime={t}&limit=1000")
            if not d:
                break
            rows += d
            t = d[-1][0] + 1
            if len(d) < 1000:
                break
        df = pd.DataFrame(rows).iloc[:, [0, 4, 7]]
        df.columns = ["t", "c", "qv"]
        return df
    df = _cached(f"{sym}_{interval}", refresh, fetch)
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    df = df.set_index("t").astype(float)
    if interval == "1d":
        df.index = df.index.normalize()
    return df


def funding(sym: str, refresh: bool) -> pd.Series:
    def fetch():
        rows, t = [], int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000)
        while True:
            d = _get("https://fapi.binance.com/fapi/v1/fundingRate"
                     f"?symbol={sym}USDT&startTime={t}&limit=1000")
            if not d:
                break
            rows += d
            t = d[-1]["fundingTime"] + 1
            if len(d) < 1000:
                break
        return pd.DataFrame({"t": [r["fundingTime"] for r in rows],
                             "rate": [float(r["fundingRate"]) for r in rows]})
    df = _cached(f"{sym}_funding", refresh, fetch)
    s = df.set_index(pd.to_datetime(df["t"], unit="ms"))["rate"]
    return (s.resample("D").mean() * 3 * 365 * 100).rolling(7).mean()   # 7日平均・年率%


def fear_greed(refresh: bool) -> pd.Series:
    def fetch():
        d = _get("https://api.alternative.me/fng/?limit=0")["data"]
        return pd.DataFrame({"t": [int(x["timestamp"]) for x in d],
                             "fng": [int(x["value"]) for x in d]})
    df = _cached("fng", refresh, fetch)
    return df.set_index(pd.to_datetime(df["t"], unit="s").dt.normalize())["fng"].sort_index()


def us_market(name: str, ticker: str, refresh: bool) -> pd.Series:
    """米国の日足の変化率(米東部の日付)。

    米国の終値は UTC 20〜21時なので、同じ日付の暗号資産の日足(UTC 0〜24時)の終わる前に出る。
    features() の y は翌日のリターンなので、同じ日付で並べれば「米国の終値 → 翌日」の比較になる。
    """
    def fetch():
        d = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=10y&interval=1d")
        r = d["chart"]["result"][0]
        return pd.DataFrame({"t": r["timestamp"], "c": r["indicators"]["quote"][0]["close"]}).dropna()
    df = _cached(name, refresh, fetch)
    idx = pd.to_datetime(df["t"], unit="s", utc=True).dt.tz_convert("America/New_York")
    return pd.Series(df["c"].values, index=idx.dt.tz_localize(None).dt.normalize()).pct_change() * 100


def fomc_dates(refresh: bool) -> pd.DatetimeIndex:
    def fetch():
        pages = ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"] + [
            f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm" for y in (2018, 2019, 2020)]
        ds = set()
        for p in pages:
            ds |= set(re.findall(r"monetary(20\d{6})a\.htm", _get(p, raw=True)))
        return pd.DataFrame({"d": sorted(d for d in ds if d >= "20180101")})
    df = _cached("fomc", refresh, fetch)
    return pd.to_datetime(df["d"].astype(str), format="%Y%m%d")


# ----------------------------------------------------------------- 統計
def tstat(x) -> float:
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 4 and x.std() > 0 else float("nan")


def pval(t: float) -> float:
    return 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2)))) if t == t else float("nan")


def halves(s):
    return s[:IS_END], s[OOS_START:]


def backtest(c: pd.Series, sig: pd.Series, rt: float) -> dict:
    """sig は t日終値で持ちたいか(0/1)。約定は t+1 日、片道 rt/2 のコスト。"""
    r = c.pct_change().fillna(0)
    pos = sig.reindex(c.index).shift(1).fillna(0)
    trade = pos.diff().abs().fillna(pos.iloc[0])
    eq = (1 + pos * r - trade * rt / 2).cumprod()
    return {"annual": float(eq.iloc[-1] ** (365 / len(c)) - 1),
            "maxdd": float((eq / eq.cummax() - 1).min()),
            "trades": int(trade.sum())}


# ----------------------------------------------------------------- 検定
def features(sym, px, fund, fng, spx, fomc):
    c = px["c"]
    r = c.pct_change() * 100
    X = pd.DataFrame(index=c.index)
    X["r1"] = r
    for k in (3, 7, 30, 90):
        X[f"r{k}"] = (c / c.shift(k) - 1) * 100
    X["ma20"] = (c / c.rolling(20).mean() - 1) * 100
    X["ma100"] = (c / c.rolling(100).mean() - 1) * 100
    X["vol7"] = r.rolling(7).std()
    X["vol30"] = r.rolling(30).std()
    X["dd90"] = (c / c.rolling(90).max() - 1) * 100
    X["fund7"] = fund.reindex(X.index)
    X["fng"] = fng.reindex(X.index)
    X["spx"] = spx.reindex(X.index).fillna(0)
    X["fomc_next"] = X.index.map(lambda t: (t + pd.Timedelta(days=1)) in set(fomc)).astype(float)
    for i in range(6):
        X[f"d{i}"] = (X.index.dayofweek == i).astype(float)
    X["sat"] = (X.index.dayofweek == 4).astype(float)    # 翌日が土曜
    X["sun"] = (X.index.dayofweek == 5).astype(float)    # 翌日が日曜
    X["y"] = r.shift(-1)
    return X


DIR_COLS = ["r1", "r3", "r7", "r30", "r90", "ma20", "ma100", "vol7", "dd90",
            "fund7", "fng", "spx", "fomc_next", "d0", "d1", "d2", "d3", "d4", "d5"]


def direction_model(X: pd.DataFrame) -> dict:
    """全部の材料を入れたリッジ回帰。前半で推定して、前半と後半の的中率を比べる。"""
    X = X.dropna(subset=DIR_COLS + ["y"])
    tr, te = halves(X)
    mu, sd = tr[DIR_COLS].mean(), tr[DIR_COLS].std().replace(0, 1)
    z = lambda D: np.c_[np.ones(len(D)), ((D[DIR_COLS] - mu) / sd).values]  # noqa: E731
    Z = z(tr)
    w = np.linalg.solve(Z.T @ Z + 50 * np.eye(Z.shape[1]), Z.T @ tr["y"].values)
    out = {}
    for lab, D in (("is", tr), ("oos", te)):
        p, y = z(D) @ w, D["y"].values
        m = np.abs(y) > BAND
        strong = m & (np.abs(p) >= np.quantile(np.abs(p), 0.8))
        out[lab] = {"hit": float((np.sign(p[m]) == np.sign(y[m])).mean()), "days": int(m.sum()),
                    "hit_strong": float((np.sign(p[strong]) == np.sign(y[strong])).mean()),
                    "always_up": float((y[m] > 0).mean())}
    return out


def magnitude(X: pd.DataFrame) -> dict:
    """翌日の値幅の大きさ。直近の実現ボラ・週末・FOMC から前半で推定し、後半を5分位に分ける。"""
    cols = ["vol7", "vol30", "sat", "sun", "fomc_next"]
    X = X.dropna(subset=cols + ["y"])
    tr, te = halves(X)
    A = np.c_[np.ones(len(tr)), tr[cols].values]
    w = np.linalg.lstsq(A, np.log(tr["y"].abs().values + 0.1), rcond=None)[0]
    p = pd.Series(np.c_[np.ones(len(te)), te[cols].values] @ w, index=te.index)
    q = pd.qcut(p, 5, labels=False)
    ay = te["y"].abs()
    return {"quintiles": [{"move": float(ay[q == k].mean()),
                           "flat": float((ay[q == k] <= BAND).mean())} for k in range(5)]}


def run(refresh: bool) -> dict:
    print("データを集める")
    px = {s: klines(s, "1d", "2018-01-01", refresh) for s in ("BTC", "ETH", "BAT")}
    hr = {s: klines(s, "1h", "2020-01-01", refresh) for s in ("BTC", "ETH")}
    fund = {s: funding(s, refresh) for s in ("BTC", "ETH")}
    fng = fear_greed(refresh)
    spx = us_market("SPX", "%5EGSPC", refresh)
    fomc = fomc_dates(refresh)

    rows = []     # 材料ごとの前半/後半の結果

    def add(name, kind, is_txt, oos_txt, verdict, note=""):
        rows.append({"name": name, "kind": kind, "is": is_txt, "oos": oos_txt,
                     "verdict": verdict, "note": note})

    print("検定")
    r = {s: px[s]["c"].pct_change() * 100 for s in px}
    noise = 2 / np.sqrt(len(r["BTC"].dropna()) / 2)

    # 1. 値幅の持続(ボラティリティ・クラスタリング)
    a_is = [halves(r[s])[0].abs().autocorr(1) for s in ("BTC", "ETH")]
    a_oos = [halves(r[s])[1].abs().autocorr(1) for s in ("BTC", "ETH")]
    add("値動きの大きさは翌日も続く", "大きさ",
        f"BTC {a_is[0]:+.2f} / ETH {a_is[1]:+.2f}", f"BTC {a_oos[0]:+.2f} / ETH {a_oos[1]:+.2f}",
        "再現した", f"値幅の自己相関。偶然の目安は ±{noise:.2f}")

    # 2. 週末の値幅
    def sat_ratio(s):
        x = s.abs()
        return x[x.index.dayofweek == 5].mean() / x[x.index.dayofweek < 5].mean()
    add("土曜は値幅が小さい", "大きさ",
        f"BTC 平日の{sat_ratio(halves(r['BTC'])[0]):.0%}", f"BTC 平日の{sat_ratio(halves(r['BTC'])[1]):.0%}",
        "再現した", "UTC の土曜日。±0.5%以内に収まりやすい")

    # 3. FOMC の値幅と方向
    h = hr["BTC"]["c"]
    def fomc_move(ds):
        mv = [abs(h[t + pd.Timedelta(hours=42)] / h[t + pd.Timedelta(hours=18)] - 1) * 100
              for t in ds if t + pd.Timedelta(hours=42) in h.index and t + pd.Timedelta(hours=18) in h.index]
        return float(np.median(mv)), len(mv)
    base24 = float((h.pct_change(24).abs() * 100).median())
    f_is, f_oos = fomc_move(fomc[fomc <= IS_END]), fomc_move(fomc[fomc >= OOS_START])
    add("FOMC の発表から24時間は値幅が広がる", "大きさ",
        f"中央値 {f_is[0]:.1f}%（普段 {base24:.1f}%）", f"中央値 {f_oos[0]:.1f}%（普段 {base24:.1f}%）",
        "再現した", "日程は事前に分かる")
    fd_is = [r["BTC"].get(t, np.nan) for t in fomc if t <= pd.Timestamp(IS_END)]
    fd_oos = [r["BTC"].get(t, np.nan) for t in fomc if t >= pd.Timestamp(OOS_START)]
    add("FOMC の日は上がりやすい", "方向",
        f"BTC {np.nanmean(fd_is):+.2f}%（t={tstat(fd_is):+.1f}）", f"BTC {np.nanmean(fd_oos):+.2f}%（t={tstat(fd_oos):+.1f}）",
        "弱い", "向きは同じだが、後半は偶然と区別できない")

    # 4. 翌日の反転
    for s in ("BTC", "ETH"):
        a, b = halves(r[s])
        add(f"{s}: 前日の逆に動く", "方向", f"自己相関 {a.autocorr(1):+.3f}", f"自己相関 {b.autocorr(1):+.3f}",
            "弱まった" if abs(b.autocorr(1)) < abs(a.autocorr(1)) / 2 else "弱い",
            "毎日売買するとコストで大きく負ける")

    # 5. 曜日
    for s in ("BTC",):
        a, b = halves(r[s])
        wed = lambda x: x[x.index.dayofweek == 2]  # noqa: E731
        thu = lambda x: x[x.index.dayofweek == 3]  # noqa: E731
        add("水曜は上げ、木曜は下げやすい", "方向",
            f"水 {wed(a).mean():+.2f}% / 木 {thu(a).mean():+.2f}%", f"水 {wed(b).mean():+.2f}% / 木 {thu(b).mean():+.2f}%",
            "弱い", "向きは同じ。ただし1日0.3%前後で、往復コストに届かない")

    # 6. 資金調達率
    c = px["BTC"]["c"]
    f7 = fund["BTC"].reindex(c.index)
    fwd7 = (c.shift(-7) / c - 1) * 100
    def fund_gap(a, b):
        ff, ww = f7[a:b], fwd7[a:b]
        return ww[ff < 0].mean() - ww[ff >= 0].mean(), int((ff < 0).sum())
    g_is, g_oos = fund_gap(None, IS_END), fund_gap(OOS_START, None)
    add("資金調達率がマイナスだと、その後の1週間は上げやすい", "方向",
        f"差 {g_is[0]:+.1f}pt（{g_is[1]}日）", f"差 {g_oos[0]:+.1f}pt（{g_oos[1]}日）",
        "弱い", "売り方が偏った後の反発。後半は小さく、日数も少ない")

    # 7. 数週間のモメンタム
    for s in ("BTC", "ETH", "BAT"):
        cc = px[s]["c"]
        past, fut = cc / cc.shift(30) - 1, (cc.shift(-30) / cc - 1) * 100
        def gap(a, b):
            p_, f_ = past[a:b], fut[a:b]
            return f_[p_ > 0].mean() - f_[p_ <= 0].mean()
        gi, go = gap(None, IS_END), gap(OOS_START, None)
        add(f"{s}: 過去30日が上げなら、次の30日も上げやすい", "方向",
            f"差 {gi:+.1f}pt", f"差 {go:+.1f}pt",
            "再現した" if gi > 0 and go > 0 else "再現しなかった",
            "数週間単位の順張り" if s != "BAT" else "小型銘柄は後半で逆向き")

    # 8. 効かなかったもの
    X = {s: features(s, px[s], fund[s], fng, spx, fomc) for s in ("BTC", "ETH")}
    for col, name in (("fng", "Fear & Greed の水準"), ("spx", "米株（S&P500）の前日の動き")):
        d = X["BTC"][[col, "y"]].dropna()
        if col == "spx":
            d = d[d[col] != 0]          # 米国の休場日を除く
        a, b = halves(d)
        ci, co = a[col].corr(a["y"]), b[col].corr(b["y"])
        nz = 2 / np.sqrt(len(b))
        add(name, "方向", f"翌日との相関 {ci:+.3f}", f"翌日との相関 {co:+.3f}",
            "効かない" if abs(co) < nz or np.sign(ci) != np.sign(co) else "弱い",
            f"後半の偶然の目安は ±{nz:.3f}")
    rb, re_ = r["BTC"], r["ETH"]
    ci, co = [x["BTC"].corr(x["ETH"].shift(-1)) for x in
              (pd.DataFrame({"BTC": halves(rb)[0], "ETH": halves(re_)[0]}),
               pd.DataFrame({"BTC": halves(rb)[1], "ETH": halves(re_)[1]}))]
    add("BTC の動きを ETH が1日遅れで追う", "方向", f"相関 {ci:+.3f}", f"相関 {co:+.3f}", "効かない")

    # 9. 全部入りのモデル
    models = {s: direction_model(X[s]) for s in ("BTC", "ETH")}
    mags = {s: magnitude(X[s]) for s in ("BTC", "ETH")}

    # 10. 数週間のトレンドで持つ/持たない(週1判定)
    periods = [("2018-20", "2018", "2020"), ("2021-22", "2021", "2022"),
               ("2023-24", "2023", "2024"), ("2025-26", "2025", "2026")]
    trend = []
    for s in ("BTC", "ETH", "BAT"):
        cc = px[s]["c"]
        sig = (cc > cc.rolling(100).mean()).astype(int).where(cc.index.dayofweek == 0).ffill().fillna(0)
        for lab, a, b in periods:
            bh = backtest(cc[a:b], pd.Series(1, index=cc[a:b].index), COST[s])
            tr_ = backtest(cc[a:b], sig[a:b], COST[s])
            trend.append({"sym": s, "period": lab, "bh": bh, "rule": tr_})
    sens = []
    for s in ("BTC", "ETH"):
        cc = px[s]["c"]
        for n in (30, 50, 100, 150, 200):
            sig = (cc > cc.rolling(n).mean()).astype(int).where(cc.index.dayofweek == 0).ffill().fillna(0)
            wins = 0
            for lab, a, b in periods:
                bh = backtest(cc[a:b], pd.Series(1, index=cc[a:b].index), COST[s])
                tr_ = backtest(cc[a:b], sig[a:b], COST[s])
                wins += tr_["maxdd"] > bh["maxdd"]
            sens.append({"sym": s, "n": n, "shallower": wins, "periods": len(periods)})

    # いまの状態
    now = {}
    for s in ("BTC", "ETH", "BAT"):
        cc = px[s]["c"]
        ma = cc.rolling(100).mean()
        above = cc > ma
        since = above[above.ne(above.shift())].index[-1]
        now[s] = {"vs_ma100": float(cc.iloc[-1] / ma.iloc[-1] - 1), "above": bool(above.iloc[-1]),
                  "since": str(since.date())}

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "from": str(px["BTC"].index[0].date()), "to": str(px["BTC"].index[-1].date()),
        "split": {"is": f"2018〜{IS_END[:4]}", "oos": f"{OOS_START[:4]}〜"},
        "rows": rows, "models": models, "magnitude": mags,
        "trend": trend, "trend_sensitivity": sens, "now": now,
        "fomc_count": int(len(fomc)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="長期データで予測できる傾向を探す")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず取り直す")
    args = ap.parse_args()
    res = run(args.refresh)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"書き出し: {OUT.relative_to(ROOT)}")
    for row in res["rows"]:
        print(f"  [{row['verdict']}] {row['name']}: 前半 {row['is']} / 後半 {row['oos']}")
    for s, m in res["models"].items():
        print(f"  {s} 全部入りモデル: 前半 {m['is']['hit']:.1%} → 後半 {m['oos']['hit']:.1%}"
              f"（強い日だけ {m['is']['hit_strong']:.1%} → {m['oos']['hit_strong']:.1%}、いつも上 {m['oos']['always_up']:.1%}）")


if __name__ == "__main__":
    main()
