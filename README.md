# bitFlyer 売買シミュレーション

bitFlyer で **BTC / ETH / BAT** を売買していたら儲かったのかを、毎朝自動で検証してダッシュボードに出すツールです。

ダッシュボードは GitHub Pages で公開され、GitHub Actions が毎朝 06:30 JST に更新します。
さらに **朝・昼・晩の3回**、情報を集めて仮売買を行い、根拠ごと Supabase に記録しています（→ [仮売買の記録](docs/db.html)）。

---

## 検証してわかったこと

直近365日（2025-09-10 〜 2026-09-09、BTC -25.7% / ETH -39.4% / BAT -49.3% の下落局面）での結果:

| 調べたこと | 結果 |
|---|---|
| 8戦略 × 3銘柄 = 24通り（買い持ち除く）のうち黒字 | **3通り（12%）** |
| ランダム売買に有意に勝った（p<0.05） | **1通り**（何の実力が無くても偶然 1.2通り は出る水準） |
| 多重検定を補正しても残った戦略 | **0通り** |

**「実力で勝った」と言える売買ルールは1つも見つかりませんでした。**

黒字だった3つも、パラメータを少し動かすと壊れます。例えば BAT の SMA クロスは
20/50 だと +16% ですが、隣の 15/50 は -6%、20/75 は -20%。
過去データに合わせ込んだだけ（カーブフィット）の典型です。

### 効いていたのは戦略よりコスト

| 銘柄 | 取引場所 | 片道コスト | 往復 |
|---|---|---|---|
| BTC | 板（Lightning） | 約 0.22% | 約 0.43% |
| ETH | 板（Lightning） | 約 0.23% | 約 0.46% |
| BAT | **販売所のみ** | 約 2%（仮定） | **約 4%** |

**BAT は bitFlyer の板取引に無く、販売所でしか売買できません。**
（`GET /v1/getmarkets` に `BAT_JPY` は存在しない）
往復4%が最初から乗るので、14回売買しただけで元手100万円のうち約35万円がコストで消えます。

BTC で「片道コストをいくらまで払えるか」を調べると:

| 戦略 | 黒字でいられる上限 |
|---|---|
| RSI14 逆張り | 片道 2.0% まで |
| ドンチャン 20/10 | 片道 0.5% まで |
| MACD 12/26/9 | 片道 0.1% まで |

売買回数の多い戦略ほどコストに弱く、販売所で回すと確実に負けます。

---

## 使い方

```bash
pip install -r requirements.txt
```

### 一通り検証する

```bash
python src/run.py
```

戦略ごとの成績表、3銘柄ポートフォリオ、コスト感応度を表示します。

```bash
python src/run.py --capital 500000        # 元手50万円
python src/run.py --hanbaijo-spread 3     # 販売所スプレッドを片道3%と仮定
python src/run.py --venue hanbaijo        # 全部を販売所で売買した場合
python src/run.py --days 180              # 直近180日だけ
```

### マグレかどうかを調べる

```bash
python src/verdict.py
```

同じ売買回数・同じ保有日数でデタラメに売買した場合と比較して、
その戦略が偶然の産物かどうかを判定します（多重検定の補正込み）。

### 毎朝のルーティンを手元で回す

```bash
python src/daily.py           # 生成のみ
python src/daily.py --push    # 生成して git commit & push まで
```

`docs/` は GitHub Actions が毎朝上書きする生成物です。
手元で `daily.py` を回すと `docs/` に差分が出ますが、コミットする必要はありません。
push 前に競合したら、生成物なので迷わず捨てて構いません。

```bash
git checkout -- docs    # ローカルの生成差分を捨てる
git pull --rebase
```

---

## 仕組み

```
src/
  data.py         価格取得（CoinGecko）とキャッシュ、bitFlyer 板スプレッドの実測
  engine.py       バックテストの本体。手数料・スプレッド・スリッページを引く
  strategies.py   売買ルール9種
  robustness.py   ランダム売買との比較、パラメータ総当たり
  run.py          コマンドラインの検証ツール
  verdict.py      多重検定まで含めた最終判定
  news.py         ニュース収集（CoinPost / Cointelegraph / CoinDesk の RSS）
  dashboard.py    docs/index.html を生成
  daily.py        毎朝のバックテスト更新（上記を全部つなげて commit & push）
  collect.py      朝昼晩の情報収集（板・参考値・Fear&Greed・市況・ニュース）
  rationale.py    「なぜその判断か」を数字つきの日本語にする
  paper.py        仮想口座。戦略×銘柄ごとに独立して仮売買を積み上げる
  db.py           Supabase(PostgREST)の最小クライアント
  sources.py      確認する情報ソースの原本。SOURCES.md と DB に書き出す
  routine.py      朝昼晩のルーティン本体
docs/             GitHub Pages が配信する成果物
  index.html      バックテストのダッシュボード
  db.html         仮売買の記録（Supabase を直接読む）
  data/latest.json, data/history.json, data/routine.json
SOURCES.md        毎回確認している情報ソース一覧（自動生成）
.github/workflows/daily.yml     毎朝 06:30 JST にバックテストを更新
.github/workflows/routine.yml   朝昼晩 07:00 / 12:00 / 20:00 JST に仮売買
```

### バックテストの前提

- **現物ロングのみ**（bitFlyer の現物と同じ制約。空売り・レバレッジなし）
- シグナルは t 日の終値までの情報で計算し、**約定は t+1 日の終値**
  （未来の値を使う lookahead バイアスを避けるため）
- 最終日に必ず全部売却。買い持ちも含め全戦略が往復コストを払うので比較がフェア
- 板取引の手数料は最も不利な **0.15%**（直近30日の約定金額が少ない場合のレート）
- スプレッドは bitFlyer の板 API から毎回実測

### データの出どころ

| 用途 | ソース |
|---|---|
| JPY建ての過去価格 | CoinGecko 公開 API（無料枠は直近365日・日足まで） |
| 板の現在値・スプレッド | bitFlyer Lightning API（`getticker`） |
| ニュース | CoinPost / Cointelegraph / CoinDesk の RSS |

bitFlyer 自身は過去のローソク足を返す公開 API を持たないため、過去価格は CoinGecko を使っています。
実際の bitFlyer の約定値とは多少ずれます。

---

## 朝昼晩の仮売買ルーティン

過去データの検証とは別に、**朝(07:00) / 昼(12:00) / 晩(20:00) JST** に情報を集めて
その場の値段で仮売買を行い、判断の根拠ごと Supabase に記録しています。

- 見るページ: **[docs/db.html](docs/db.html)**（GitHub Pages の `/db.html`）
- 情報ソースの一覧: **[SOURCES.md](SOURCES.md)**（原本は `src/sources.py`）
- 実行: `.github/workflows/routine.yml`

### バックテストとの違い

| | バックテスト（`src/run.py`） | 仮売買（`src/routine.py`） |
|---|---|---|
| 対象 | 過去365日をまとめて再現 | 今この瞬間の値段で1回ずつ |
| やり直し | 何度でもできる | できない。記録が残るだけ |
| 目的 | ルールに実力があるか | 実際に回したらどうなるか |

過去データに合わせ込んだ戦略は、前へ進み始めた瞬間に壊れます。
その様子をそのまま残すのがこのルーティンです。

### 仮売買のルール

- **戦略 × 銘柄 = 27口座**。1口座あたり元手100万円で完全に独立
- シグナルが 0→1 で全額買い、1→0 で全数量売り。サイズ調整はしない
- 約定価格は板がある銘柄は bitFlyer の mid、無い銘柄は CoinGecko の参考値
- コストは片道で「手数料 0.15% + 実測スプレッド + スリッページ 0.05%」。
  販売所しか無い BAT は片道2%と仮定
- **実際の注文は一切出していません**

### 手元で回す

```bash
python src/routine.py --dry-run     # DB に書かずに動作だけ見る
python src/routine.py --slot noon   # 昼の回として記録する
python src/sources.py               # SOURCES.md を書き直して DB に同期
```

### Claudeの見立て（AIが関わる唯一の場所）

記録と判断はすべて決定論的な計算式で、**AIは一切関与していません**。
ニュースの「トーン」も強気/弱気ワードの単純カウントで、感情分析ではありません。

AIが担当するのは解釈だけです。Claude Code の定期タスクが毎晩20:30に

1. `python src/brief.py` で状況のダイジェストを読み
2. `docs/data/commentary.json` に見立てを書き
3. commit & push

を行い、`db.html` の「Claudeの見立て」タブに出ます。

**なぜ GitHub Actions ではなく Claude Code 側なのか:**

| | 記録（価格・仮売買・DB） | 見立て |
|---|---|---|
| 実行 | GitHub Actions | Claude Code の定期タスク |
| 落ちたとき | 記録に穴が空く＝致命的 | 1日飛んでも無害 |
| 必要なもの | クラウドで確実に発火すること | 思考 |

Claude Code の定期タスクは**アプリが開いている間しか動きません**（閉じていれば次回起動時に遅れて実行）。
記録をこちらに寄せると、PCが落ちていた日に穴が空くうえ、後から誤った時刻の価格で約定が入ります。
そのため記録は Actions に残し、見立てだけを Claude 側に置いています。
`ANTHROPIC_API_KEY` が不要になる副次的な利点もあります。

見立ての文章は売買判断に一切影響しません。バックテストと実運用で判断ロジックが
完全に一致していることが、この仕組みの検証を成立させているためです。

### Supabase

テーブルは `bf_` で始まるものが一式（`bf_runs` / `bf_signals` / `bf_paper_trades` /
`bf_paper_accounts` / `bf_equity_snapshots` / `bf_market_snapshots` /
`bf_market_indicators` / `bf_news_articles` / `bf_backtests` / `bf_sources`）。

RLS は **読み取りだけ anon に開放**、書き込みは `service_role` のみです。
`docs/db.html` は anon キーで直接読んでいるので、キーがページに埋まっていますが
これで書き換えはできません。

GitHub Actions 側に次の Secrets が必要です:

| Secret | 値 |
|---|---|
| `SUPABASE_URL` | `https://edqwvckcywbmakkvwiuc.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase ダッシュボード → Project Settings → API Keys の `service_role` |

未設定でもルーティンは動きますが、DB 保存を飛ばして `docs/data/routine.json` だけ更新します。

> このプロジェクトには Prisma で作った別アプリのテーブル（`User` / `Post` 等）も同居しています。
> そちらで `prisma migrate reset` を実行すると `bf_*` が消えるので注意してください。

---

## 注意

- 過去の値動きに対する検証結果であり、**将来の損益を保証するものではありません**
- 検証期間は下落局面の365日だけです。上昇局面では結論が変わり得ます
- 販売所のスプレッドは公表値が無いため片道2%と仮定しています。実際の値は
  `--hanbaijo-spread` で変えて試してください
- ニュースの「トーン」は強気・弱気ワードの単純カウントで、相場予測ではありません
- 投資判断はご自身の責任で行ってください
