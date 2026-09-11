"""Claude の判断を docs/data/ai_calls.json に追記する。

判断の記録は採点の前提そのものなので、手で JSON を編集させず、ここを必ず通す。
機械的に守らせること:

  - 追記だけ。過去の判断は書き換えない・消さない
  - made_at（判断した時刻）は実行した瞬間の JST を機械が付ける。前後にずらせない
  - id と based_on_run_id（どの回のデータを見て判断したか）も機械が付ける
  - 形式・値の範囲を検査し、1件でもおかしければ何も書かない
  - outlook=flat は「方向の見通しを持たない（見送り）」。確信度は付けず、採点もされない

入力は判断の中身だけを書いた JSON:

    {"calls": [
      {"symbol": "BTC", "position": "long", "outlook": "down", "confidence": 0.55,
       "reasoning": "...", "counterpoint": "...", "invalidation": "...",
       "playbook_refs": ["P-01", "P-05"], "lessons_applied": ["L-001"]},
      {"symbol": "BAT", "position": "flat", "outlook": "flat",
       "reasoning": "...", "counterpoint": "...", "invalidation": "...",
       "playbook_refs": ["P-01", "B-01"]}
    ]}

    python src/add_calls.py data/_calls_input.json
    python src/add_calls.py data/_calls_input.json --check   # 検査だけして書かない
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

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "docs" / "data" / "ai_calls.json"

SYMBOLS = {"BTC", "ETH", "BAT"}
POSITIONS = {"long", "flat"}
OUTLOOKS = {"up", "down", "flat"}
# playbook I-01（Fear & Greed）と I-02（ニュースのトーン）は判断根拠にしない決まり
FORBIDDEN_REFS = {"I-01", "I-02"}
REQUIRED_TEXT = ("reasoning", "counterpoint", "invalidation")


def validate(c: dict, n: int) -> list[str]:
    e = []
    tag = f"{n+1}件目({c.get('symbol', '?')})"
    if c.get("symbol") not in SYMBOLS:
        e.append(f"{tag}: symbol は {sorted(SYMBOLS)} のどれか")
    if c.get("position") not in POSITIONS:
        e.append(f"{tag}: position は long / flat")
    if c.get("outlook") not in OUTLOOKS:
        e.append(f"{tag}: outlook は up / down / flat")
    conf = None
    if c.get("outlook") == "flat":
        # 見送り（方向の見通しを持たない）。採点しないので確信度は持たせない
        if c.get("confidence") is not None:
            e.append(f"{tag}: outlook=flat（見送り）には confidence を書かない")
    else:
        try:
            conf = float(c.get("confidence"))
            if not 0.5 <= conf <= 0.95:
                e.append(f"{tag}: confidence は 0.50〜0.95（0.5未満なら outlook を逆にする。"
                         f"見通しが無いなら outlook=flat で見送る）")
        except (TypeError, ValueError):
            e.append(f"{tag}: up/down の判断には confidence（数値）が要る")
    for k in REQUIRED_TEXT:
        if not str(c.get(k) or "").strip():
            e.append(f"{tag}: {k} が空（playbook 0章: 反対側の材料と反証条件は必須）")
    refs = c.get("playbook_refs") or []
    if not refs:
        e.append(f"{tag}: playbook_refs が空（根拠にした playbook の ID を書く）")
    bad = sorted(set(refs) & FORBIDDEN_REFS)
    if bad:
        e.append(f"{tag}: {bad} は判断根拠にしない決まり（playbook I-01/I-02, lessons L-001）")
    for r in refs:
        if not re.fullmatch(r"[A-Z]-\d{2}", str(r)):
            e.append(f"{tag}: playbook_refs の {r!r} は ID の形ではない（例 P-01）")
    if conf is not None and conf >= 0.70 and len(refs) < 2:
        e.append(f"{tag}: 確信度0.70以上は独立した根拠が2つ以上いる（playbook 0章）")
    if c.get("made_at") or c.get("id"):
        e.append(f"{tag}: made_at と id は書かない（機械が付ける）")
    return e


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="判断を書いた JSON ファイル（- なら標準入力）")
    ap.add_argument("--check", action="store_true", help="検査だけして書かない")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    new = json.loads(raw)
    new = new.get("calls", []) if isinstance(new, dict) else new
    if not new:
        print("判断が0件")
        return 1

    errors = [msg for i, c in enumerate(new) for msg in validate(c, i)]
    syms = [c.get("symbol") for c in new]
    if len(set(syms)) != len(syms):
        errors.append("同じ銘柄が1回の入力に2件ある")
    if errors:
        print("書き込まなかった。直すところ:")
        for msg in errors:
            print(f"  - {msg}")
        return 1

    doc = json.loads(CALLS.read_text(encoding="utf-8")) if CALLS.exists() else {"calls": []}
    existing = doc.setdefault("calls", [])
    before = json.dumps(existing, ensure_ascii=False, sort_keys=True)

    runs = pdb.q("bf_runs", select="id,ran_at", status="eq.ok", order="ran_at.desc", limit="1")
    run_id = runs[0]["id"] if runs else None
    now = datetime.now(pdb.JST).replace(microsecond=0)
    ids = {c.get("id") for c in existing}
    last_pos = {}
    for c in existing:
        last_pos[c.get("symbol")] = c.get("position")

    added = []
    for c in new:
        base = f"{now:%Y-%m-%d}-{c['symbol']}"
        cid, k = base, 2
        while cid in ids:
            cid, k = f"{base}-{k}", k + 1
        ids.add(cid)
        rec = {"id": cid, "made_at": now.isoformat(), "based_on_run_id": run_id,
               "symbol": c["symbol"], "position": c["position"], "outlook": c["outlook"],
               "confidence": (round(float(c["confidence"]), 2)
                              if c["outlook"] != "flat" else None),
               "horizon_h": int(c.get("horizon_h") or 24),
               "reasoning": c["reasoning"].strip(), "counterpoint": c["counterpoint"].strip(),
               "invalidation": c["invalidation"].strip(),
               "playbook_refs": list(c["playbook_refs"]),
               "lessons_applied": list(c.get("lessons_applied") or [])}
        added.append(rec)
        change = (f"  ※ position を {last_pos[c['symbol']]} → {c['position']} に変える"
                  f"（往復コストを上回る見込みを reasoning に書いたか）"
                  if c["symbol"] in last_pos and last_pos[c["symbol"]] != c["position"] else "")
        print(f"{cid}: position={rec['position']} outlook={rec['outlook']} "
              f"確信度={rec['confidence']} 根拠={','.join(rec['playbook_refs'])}{change}")

    if args.check:
        print(f"検査のみ。問題なし（{len(added)}件）。書き込んでいない")
        return 0

    existing.extend(added)
    # 過去の判断に触れていないことを書き込む直前にもう一度確かめる
    assert json.dumps(existing[:len(existing) - len(added)], ensure_ascii=False,
                      sort_keys=True) == before, "過去の判断が変わっている"
    CALLS.parent.mkdir(parents=True, exist_ok=True)
    tmp = CALLS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CALLS)
    print(f"追記: {len(added)}件（全{len(existing)}件） based_on_run_id={run_id} made_at={now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
