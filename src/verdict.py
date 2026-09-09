"""全戦略x全銘柄を「ランダム売買」と突き合わせ、多重検定の補正まで行う最終判定。

27通り(9戦略x3銘柄)も試せば、中身が無くても p<0.05 は平均1.35個出る。
「1個勝った」では意味がないので、期待値と実際の個数を比べる。
"""
import sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")

import data as datamod, strategies as st, robustness as rb
from engine import Costs

CAP = 1_000_000
N_ITER = int(sys.argv[1]) if len(sys.argv) > 1 else 800  # 試行回数は引数で変えられる
COSTS = {"BTC": Costs(0.0015, 0.00016, 0.0005),
         "ETH": Costs(0.0015, 0.00031, 0.0005),
         "BAT": Costs(0.0, 0.02, 0.0)}

px = {s: df.set_index("date")["close"] for s, df in datamod.load_all().items()}
names = [n for n in st.CATALOG if n != "買い持ち(Buy&Hold)"]

rows = []
for sym in ("BTC", "ETH", "BAT"):
    for name in names:
        r = rb.monte_carlo(px[sym], name, COSTS[sym], CAP, n_iter=N_ITER)
        r["sym"] = sym
        rows.append(r)
        print(f"  {sym} {name} ... p={r['p']:.3f}", flush=True)

n = len(rows)
alpha = 0.05
hits = [r for r in rows if r["p"] < alpha]
profitable = [r for r in rows if r["actual"] > 0]

print("\n" + "=" * 76)
print("■ 最終判定")
print("=" * 76)
print(f"検証した組み合わせ           : {n} 通り (9戦略 x 3銘柄、買い持ちを除く)")
print(f"黒字だった組み合わせ         : {len(profitable)} 通り "
      f"({len(profitable)/n*100:.0f}%)")
print(f"ランダム売買に有意に勝った数 : {len(hits)} 通り (p<{alpha})")
print(f"中身がゼロでも偶然出る期待数 : {n*alpha:.1f} 通り")
bonf = alpha / n
survivors = [r for r in rows if r["p"] < bonf]
print(f"多重検定の補正後(p<{bonf:.4f})に残った戦略 : {len(survivors)} 通り")
if survivors:
    for r in survivors:
        print(f"    {r['sym']} {r['name']} {r['actual']*100:+.2f}% p={r['p']:.4f}")
else:
    print("    → 該当なし。統計的に「実力で勝った」と言える戦略は1つも無い。")

print("\n黒字だった組み合わせの内訳:")
for r in sorted(profitable, key=lambda r: -r["actual"]):
    print(f"  {r['sym']:4s} {r['name']:26s} {r['actual']*100:+7.2f}%  "
          f"売買{r['n_trades']:3d}回  p={r['p']:.3f}")

print(chr(10) + "=" * 76)
print("■ パラメータ感応度 (SMAクロスの日数を総当たり、リターン%)")
print("  少し動かしただけで結果がひっくり返るなら、それは過去データへの合わせ込み")
print("=" * 76)
for sym in ("BTC", "ETH", "BAT"):
    g = rb.sma_grid(px[sym], COSTS[sym], CAP)
    print(f"{chr(10)}[{sym}] 行=短期, 列=長期")
    print(g.round(1).to_string(na_rep="-"))
    v = g.to_numpy()
    v = v[~np.isnan(v)]
    print(f"  → 黒字だった組み合わせ: {(v > 0).sum()}/{len(v)}  "
          f"最良{v.max():+.1f}%  最悪{v.min():+.1f}%  ばらつき{v.std():.1f}pt")

print(chr(10) + "※ 過去の値動きに対する検証であり、将来の損益を保証するものではありません。")
