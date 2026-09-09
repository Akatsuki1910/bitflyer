# 毎回確認している情報ソース

朝昼晩のルーティン（`src/routine.py`）がどこから何を取っているかの一覧。
`src/sources.py` が原本で、このファイルは自動生成されます。

| 印 | 意味 |
|---|---|
| 自動 | ルーティンが毎回叩いている |
| 目視 | 自動化していない。前提が変わっていないか人が定期的に見る |

## 価格・板

| ソース | 取得 | 何のために見るか |
|---|---|---|
| [bitFlyer getticker BTC_JPY](https://api.bitflyer.com/v1/getticker?product_code=BTC_JPY) | 自動 | BTC の板の現在値・最良気配。仮売買の約定価格とスプレッド実測に使う |
| [bitFlyer getticker ETH_JPY](https://api.bitflyer.com/v1/getticker?product_code=ETH_JPY) | 自動 | ETH の板の現在値・最良気配 |
| [bitFlyer getticker FX_BTC_JPY](https://api.bitflyer.com/v1/getticker?product_code=FX_BTC_JPY) | 自動 | 証拠金取引の値段。現物との乖離をセンチメント指標として見る |
| [bitFlyer getmarkets](https://api.bitflyer.com/v1/getmarkets) | 自動 | 板取引できる銘柄の一覧。ここに無い銘柄は販売所のみ＝往復コストが重い |
| [CoinGecko simple/price](https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,basic-attention-token&vs_currencies=jpy&include_24hr_change=true) | 自動 | JPY 建ての現在値と24時間変化率。板の無い BAT の実勢値はこれを使う |
| [CoinGecko market_chart(日足365日)](https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=jpy&days=365&interval=daily) | 自動 | シグナル計算のもとになる過去の日足。bitFlyer に公開ローソク足 API が無いため代用<br>※ 無料枠は直近365日・日足まで |

## 市況

| ソース | 取得 | 何のために見るか |
|---|---|---|
| [CoinGecko /global](https://api.coingecko.com/api/v3/global) | 自動 | 暗号資産全体の時価総額と BTC/ETH ドミナンス |
| [Crypto Fear & Greed Index](https://api.alternative.me/fng/?limit=1) | 自動 | 市場心理の指標(0=極度の恐怖 〜 100=極度の強欲) |

## ニュース

| ソース | 取得 | 何のために見るか |
|---|---|---|
| [CoinPost RSS](https://coinpost.jp/?feed=rss2) | 自動 | 国内の暗号資産ニュース(日本語) |
| [Cointelegraph RSS](https://cointelegraph.com/rss) | 自動 | 海外の暗号資産ニュース |
| [CoinDesk RSS](https://www.coindesk.com/arc/outboundfeeds/rss/) | 自動 | 海外の暗号資産ニュース |

## 目視確認

| ソース | 取得 | 何のために見るか |
|---|---|---|
| [bitFlyer お知らせ](https://bitflyer.com/ja-jp/news) | 目視 | 取扱銘柄の追加/廃止、システムメンテ、仕様変更。前提が変わったらコードを直す |
| [bitFlyer 手数料一覧](https://bitflyer.com/ja-jp/commission) | 目視 | 板取引の手数料レート。コード内の 0.15% 前提が正しいか確認する |
| [bitFlyer 販売所の価格](https://bitflyer.com/ja-jp/) | 目視 | 販売所スプレッドの実測。BAT の往復4%という仮定を検算する |
| [bitFlyer 稼働状況](https://status.bitflyer.com/) | 目視 | 障害・メンテで値段が取れなかった回の原因確認 |
| [金融庁 暗号資産関連](https://www.fsa.go.jp/policy/virtual_currency/index.html) | 目視 | 規制の変更。取扱銘柄や税制の前提が変わる |
| [JVCEA 統計情報](https://jvcea.or.jp/about-crypto/statistics/) | 目視 | 国内取引所全体の出来高・預託金。板の薄さの目安 |
| [Coinglass 清算マップ](https://www.coinglass.com/ja/LiquidationData) | 目視 | レバレッジの偏り。急変の前後で確認する |

## 前提として置いていること

| 項目 | 値 | 出どころ |
|---|---|---|
| 板取引の手数料(片道) | 0.15% | bitFlyer 手数料一覧の最も不利なレート |
| 板のスプレッド(片道) | 毎回実測 | `getticker` の best_bid / best_ask |
| スリッページ(片道) | 0.05% | 仮定値 |
| 販売所のスプレッド(片道) | 2% | 公表値が無いための仮定。BAT はこちら |
| 元手 | 1,000,000円 / 口座 | 戦略 × 銘柄ごとに独立 |

販売所スプレッドは実測して直す価値があります（BAT は往復4%の仮定が結論を左右する）。
