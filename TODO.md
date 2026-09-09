# セットアップでやってほしいこと

残っているのは **1つだけ** です（`SUPABASE_SERVICE_ROLE_KEY` の登録）。
それ以外はこちらで済ませたか、実データで検証済みです。

---

## 残り: `SUPABASE_SERVICE_ROLE_KEY` を登録する

`https://github.com/Akatsuki1910/bitflyer/settings/secrets/actions`
→ **New repository secret**

| Name | Value |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase ダッシュボード → プロジェクト `Akatsuki1910's Project` → Project Settings → API Keys → **service_role**（`Reveal` で表示） |

**これはご自身で登録してください。** service_role は RLS を無視できる書き込みキーなので、
こちらでは値を取得も転記もしません。Secrets 以外（コード・Issue・チャット）に貼らないでください。

未登録でも Actions は落ちません。その場合 DB 保存だけ飛ばして
`docs/data/routine.json` の更新にとどまります。

> `SUPABASE_URL` は登録済みです（`https://edqwvckcywbmakkvwiuc.supabase.co`）。
> これは公開情報で、`docs/db.html` に埋まっている `anon` キーと同じく秘密ではありません。

---

## 済ませたこと

### push は保留中

コミットは4つ手元にあります。指示どおり push していません。

```bash
git log --oneline -4     # 内容を確認
git push                 # 出すときはこれだけ
```

| コミット | 内容 |
|---|---|
| `2830acf` | 朝昼晩の仮売買ルーティンと Supabase 記録・閲覧ページ |
| `8c8ebc6` | 仮想口座の残高が初期化される不具合の修正 |
| `8f26206` | このファイル（初版） |
| （最新） | CoinGecko レート制限対策 + このファイルの更新 |

### 実データでの通信を検証した（旧・手順3のかわり）

push していないので Actions では動かせません。手元で実 API を叩いて確認しました。

| 見たところ | 結果 |
|---|---|
| bitFlyer 板 (`getticker` BTC/ETH/FX) | 2銘柄 + FX乖離 -0.03% 取得 |
| bitFlyer `getmarkets` | BAT_JPY は無し = 販売所のみ を再確認 |
| CoinGecko `simple/price`・`global` | 3銘柄 + BTCドミナンス58.5% 取得 |
| Fear & Greed | 66 (Greed) 取得 |
| ニュース RSS 3媒体 | 23件取得 |
| ルーティン全体 (`--dry-run`) | 27件の判断・27件の参考バックテストまで完走 |

### Supabase 側を確認した

| 見たところ | 結果 |
|---|---|
| `bf_*` テーブル | 10個すべて存在 |
| upsert が使う一意制約 | すべて存在（`bf_runs(trade_date,slot)` 等） |
| RLS | 全テーブル `anon` に SELECT 許可 → `db.html` から読める |
| 括弧つき戦略名でのフィルタ | `RSI14 逆張り(30/70)` 等も PostgREST が正しく解釈 |
| 既存データ | 取引系は全テーブル 0行。擬似データの残骸なし＝初回は元手100万円から始まる |

**残る未検証は DB への書き込み経路だけです。** service_role キーが要るため、
上のキー登録後の初回実行が実質のテストになります。

### CoinGecko のレート制限対策を入れた

初回の実データ検証で、履歴取得が毎回 `force=True` だったため
**レート制限に当たって72秒リトライし、失敗すればルーティンごと落ちる**状態でした。

- 日足は日中変わらないので12時間キャッシュを使うようにした（85秒 → 7.3秒）
- 取得に失敗しても古いキャッシュがあればそれで続行するようにした
- Actions にも `data/` のキャッシュを追加し、朝の取得を昼・晩で使い回すようにした

---

## 動き出したあと

| 時刻 (JST) | 動くもの |
|---|---|
| 06:30 | バックテストのダッシュボード更新 (`daily.yml`) |
| 07:00 / 12:00 / 20:00 | 情報収集 → 仮売買 → Supabase 記録 (`routine.yml`) |

両方とも `concurrency: bitflyer-main-push` を共有しているので、push がかち合いません。
GitHub Actions の cron は数分〜十数分遅れることがあります（混雑時の仕様）。

push したあとの確認先:
**`https://akatsuki1910.github.io/bitflyer/db.html`**
「いま」「口座の成績」「約定履歴」「判断と根拠」タブに中身が入っていれば完了です。

---

## 気に留めておいてほしいこと

**Supabase を別アプリと共有しています。**
同じプロジェクトに Prisma で作った `User` / `Post` / `Tag`（いずれも0行）が同居しています。
そちらで `prisma migrate reset` を実行すると **`bf_*` テーブルも一緒に消えます**。
分けたくなったら言ってください。別プロジェクトに移せます。

**販売所スプレッド2%は仮定値です。**
BAT は板取引が無く販売所のみなので、往復4%という仮定が結果を大きく左右します。
実測して直す価値があります（`src/routine.py` の `HANBAIJO_SPREAD`）。

---

## 直したくなったときの場所

| やりたいこと | 触るファイル |
|---|---|
| 見に行くサイトを足す/消す | `src/sources.py`（`SOURCES.md` と DB は自動更新） |
| 売買ルールを足す | `src/strategies.py` の `CATALOG` |
| 判断の根拠の書き方を変える | `src/rationale.py` |
| 実行する時刻を変える | `.github/workflows/routine.yml` の `cron`（UTC 表記） |
| コスト前提を変える | `src/routine.py` の `HANBAIJO_SPREAD` / `BOARD_FEE` / `SLIPPAGE` |
| 元手を変える | `src/routine.py` の `CAPITAL`（※既存口座には遡及しません） |
| 閲覧ページの見た目 | `docs/db.html` |
| 価格キャッシュの有効時間 | `src/data.py` の `fetch_prices(max_age_h=...)` |
