# セットアップでやってほしいこと

朝昼晩の仮売買ルーティンを動かすために、手元での作業が3つ残っています。
所要 5分ほど。終わったらこのファイルは消して構いません。

---

## 1. push する

コミットは3つ作ってあります。push だけ資格情報の都合でこちらからできませんでした。

```bash
cd C:\Users\akats\Documents\GitHub\bitflyer
git log --oneline -3      # 下の3件が出るか確認
git push
```

| コミット | 内容 |
|---|---|
| `2830acf` | 朝昼晩の仮売買ルーティンと Supabase 記録・閲覧ページ |
| `8c8ebc6` | 仮想口座の残高が初期化される不具合の修正 + index.html にリンク追加 |
| （最新） | このファイル |

---

## 2. GitHub Secrets を2つ登録する

`https://github.com/Akatsuki1910/bitflyer/settings/secrets/actions`
→ **New repository secret**

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://edqwvckcywbmakkvwiuc.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | 下記からコピー |

`service_role` キーの場所:
Supabase ダッシュボード → プロジェクト `Akatsuki1910's Project`
→ Project Settings → API Keys → **service_role**（`Reveal` を押すと出ます）

> `service_role` は RLS を無視できる強いキーです。Secrets 以外の場所
> （コード・Issue・チャット）に貼らないでください。
> 逆に `docs/db.html` に埋まっている `anon` キーは読み取り専用なので公開して問題ありません。

**未登録でも Actions は落ちません。** その場合 DB 保存だけ飛ばして
`docs/data/routine.json` の更新にとどまります。

---

## 3. 手動で1回動かして確認する

`https://github.com/Akatsuki1910/bitflyer/actions`
→ **朝昼晩の仮売買ルーティン** → **Run workflow** → slot は `manual` のまま実行

ログで見るところ:

- `bitFlyer 板: 2銘柄` … 板の値段が取れている
- `ニュース: NN件` … RSS が取れている
- `run_id = 1` … Supabase に書けている（ここが出なければ Secrets 未反映）
- `判断 27件、約定 NN件` … 27口座ぶんの判断が出ている

うまくいったら **`https://akatsuki1910.github.io/bitflyer/db.html`** を開く。
「いま」「口座の成績」「約定履歴」「判断と根拠」タブに中身が入っていれば完了です。

> GitHub Pages が未設定なら Settings → Pages → Source: `main` / `/docs` にしてください。

---

## 動き出したあと

cron は登録済みなので、以降は放置で動きます。

| 時刻 (JST) | 動くもの |
|---|---|
| 06:30 | バックテストのダッシュボード更新 (`daily.yml`) |
| 07:00 / 12:00 / 20:00 | 情報収集 → 仮売買 → Supabase 記録 (`routine.yml`) |

GitHub Actions の cron は数分〜十数分遅れることがあります（混雑時の仕様）。

---

## 気に留めておいてほしいこと

**Supabase を別アプリと共有しています。**
同じプロジェクトに Prisma で作った `User` / `Post` / `Tag`（いずれも0行）が同居しています。
そちらで `prisma migrate reset` を実行すると **`bf_*` テーブルも一緒に消えます**。
分けたくなったら言ってください。別プロジェクトに移せます。

**実データでの通信は未検証です。**
作業環境から bitFlyer / CoinGecko への通信がポリシーで塞がれていたため、
ロジックは擬似データで検証しました（2回連続実行で口座の引き継ぎ・売り約定・
残高が初期化されないこと・評価額の整合を確認済み）。
実 API との噛み合わせは上の手順3が初回テストになります。

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
