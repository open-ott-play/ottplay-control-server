# OTT-play Control Server

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15562/badge)](https://www.bestpractices.dev/projects/15562)

[![Fuzzing: RC only](https://img.shields.io/badge/Fuzzing-RC%20only-blue)](docs/fuzzing.md)

[ドキュメント / Wiki](docs/wiki/Home.ja.md)

OTT-play プレーヤーにリモートコマンドを届ける、小規模なセルフホスト用サーバーです。プレーヤーが外向きの HTTP リクエストを送るため、テレビ側で待ち受けポートを公開する必要はありません。各プレーヤーは専用のキューとアクセストークンを持ち、コマンドの送信には別の管理者トークンを使います。

オプションの[信頼性を高めた制御](docs/reliable-control.md)では、権限範囲を限定した統合用認証情報、保存量を制限した永続的なコマンド結果、設定を厳密に検証しながら再開できる Local/Home の適用、ライブ再生とアーカイブ再生の明示的な引き継ぎを追加します。これらには、本ガイドを含むリビジョンのコントローラーと CLI が必要です。厳密な設定検証と再生の引き継ぎにはプレーヤーの更新も必要です。下記の古い固定インストールリビジョンには、これらの機能はありません。[アーキテクチャ図](docs/assets/reliable-control.svg)で、設定、認証情報、操作の状態を保持する場所を確認できます。

サーバーは移植可能な単一の Go 実行ファイルとして動作し、オプションのサービス検出にはバージョンを固定した DNS ライブラリを使います。配布パッケージは Linux amd64、arm64、ARMv7、macOS Intel と Apple Silicon、Windows amd64 と arm64 に対応します。コンテナイメージは Linux amd64 と arm64 に対応します。Kubernetes と k3s では付属の Helm chart を使います。

## サーバーを起動する

[リリース](https://github.com/open-ott-play/ottplay-control-server/releases)から対応するアーカイブをダウンロードし、リリースマニフェストと照合してから展開します。Windows では、以下のコマンドで `ottplay-control-server.exe` を使ってください。

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` は、新しいファイルにランダムで互いに異なる認証情報を作成します。Unix では所有者だけがアクセスできます。既存のファイルやシンボリックリンクの上書きを拒否し、認証情報を画面に出力することはありません。ファイルを非公開に保ち、Windows では ACL をサーバー実行アカウントのみに制限してください。`config.example.json` は各フィールドを説明し、意図的に無効なプレースホルダートークンを含んでいます。

既定のアドレスは `0.0.0.0:8081`（LAN を含むすべての IPv4 インターフェイス）です。テレビはサーバーの LAN アドレスとポートに接続します。既存の設定は明示的な `listen` 値を保持します。以前はループバックだけで待ち受けていた環境を公開するには、これを `0.0.0.0:8081` に変えるか、`--listen 0.0.0.0:8081` を使います。プレーヤーの**ページの正確なオリジン**を `allowed_origins` に追加してください。例は `http://192.168.1.20:8443` です。スキーム、ホスト名、ポートが一致する必要があり、パスは付けません。オリジン一覧が空の場合、ブラウザー以外のクライアントだけが許可されます。設定変更は再起動後に反映されます。デバイスと管理者の認証情報は引き続き必要です。

信頼できる LAN 上の古いテレビブラウザー向けに HTTP をサポートします。信頼できないネットワークを通る接続では HTTPS を使います。`serve --config config.json --tls-cert server.crt --tls-key server.key` を指定するか、リバースプロキシで TLS を終端してください。HTTPS で開いたプレーヤーページからは、ブラウザー内で平文 HTTP サーバーに接続できません。ネイティブアプリにも OS の通信ポリシーが適用され、この設定で緩和されることはありません。

直接 HTTPS を提供する場合、待ち受けを開始する前に指定された証明書チェーンを検証します。RSA 鍵は 2048 ビット以上、ECDSA 鍵は 224 ビット以上が必要で、Ed25519 も対応しています。短すぎる鍵の証明書はアップグレード前に交換してください。[TLS 証明書のポリシーと適用範囲](docs/security-design.md#direct-https-certificate-policy)を参照してください。

## プレーヤーを接続する

コマンドサーバーへの接続機能を含むプレーヤーリリースを使います。**設定 → リモート制御**を開き、次を入力します。

1. **サーバーアドレス:** サーバーの IP またはホスト名。ポートを付けても、完全な `http://` / `https://` アドレスでも構いません。ホスト名だけの場合は HTTP ポート 8081 が既定です。
2. **アクセスコード:** `devices` にある、そのプレーヤーのエントリーの `token`。`admin_token` は使いません。
3. **接続:** ポーリングを有効にし、同じダイアログに表示される状態を確認します。

プレーヤーはこの接続を現在のインストール内だけに保存し、設定のバックアップやクラウド転送には含めません。別のローカル HTTP リスナーは不要です。Web ブラウザーには許可された CORS オリジンが必要です。`Origin: null` を送るパッケージ化されたテレビページは、指定のデバイス／初期接続ルートに対して明示的な `allow_null_origin` 設定を使えます。管理者や診断オペレーターのルートが有効になるわけではありません。

## ターミナルから操作する

[図解付き Android OTA ガイド](docs/android-ota.md)は、通常の Capacitor Wi-Fi 更新（`ott DEVICE update --variant api22|full`、既定は最新ベータ、`--version` でリリースを選択）と、別途導入する root 権限のエージェントを区別しています。署名付きのエージェントおよび APK/helper の更新、復旧、インストール結果の検証を説明します。

[Android ADB ガイド](docs/android-adb.md)では、ワークステーションまたは Synology を介した USB アクセス、画面とキーボードの操作、スクリーンショット、ログ、APK のインストール、通常の再起動を扱い、古いタブレットの制約も説明します。

オプションの [DNS-SD 検出とペアリング](docs/discovery.md)により、プレーヤーは自分のネットワークドメインを通じてコントローラーを見つけ、`ott approve NAME CODE` の後に個別のデバイストークンを受け取れます。DNS が保持するのはサービスメタデータだけです。検出を有効にしなくても、既存の手動接続はそのまま動作します。

Python CLI は、プレーヤーのエイリアス、現在の音量、チャンネル検索、現在の EPG、プロバイダーの選択、および対応するプロバイダーの設定に使います。[CLI ガイド](docs/cli.md)を参照してください。サーバーとプレーヤーの両方にリクエスト／レスポンス拡張が必要です。

Python CLI はソースの作業コピーからインストールし、ネイティブサーバーのアーカイブとは別です。[インストールと設定の手順](docs/cli.md#installation-and-connection)は macOS/Linux と Windows、Python/PATH の設定、必要な CLI モジュールを扱います。Workbench とオフライン検証を含む、レビュー済みの 0.1.1 開発コミットに固定しています。安定版 CLI v0.1.0 とネイティブサーバーのアーカイブには、これらのコマンドは含まれません。既存の作業コピー、ランチャー、非公開設定を置き換えずに [CLI を更新またはロールバック](docs/cli.md#update-select-or-roll-back-the-cli)できます。ローカルランチャーは、読み取り専用の[プレイリストリゾルバー](docs/cli.md#local-playlist-resolver)を使い、コントローラーなしでチャンネル、現在の番組、再生可能なアーカイブを検索できます。インストールと設定が済んだら、次を実行します。

```sh
ott --help                 # all CLI command families
ott devices                # controller registrations and last contact
ott a1                     # this player's status and available controls
ott a1 prev                # previous channel in the active category (previous also works)
ott a1 next                # next channel, wrapping at the end of the category
ott a1 channels "РЕН"       # list channel-name matches without playing
ott a1 "РЕН ТВ HD"          # search and play a matching channel
ott a1 volume 35            # vol/v are equivalent command aliases
ott a1 aspect               # current Fit/Fill mode and saved-setting readback
ott a1 aspect fill          # fill the screen proportionally, cropping the edges
ott a1 aspect "Fit to screen" # full mode name; preserve the whole picture
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 standby             # stop playback and enter player standby; stay connected
ott a1 go                  # leave standby and start configured playback (start/wake also work)
ott a1 screenshot          # save a PNG; browser builds need a selected capture source
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
ott a1 test run media-progress --report ./a1-progress
ott report verify ./a1-progress --json  # re-evaluate saved evidence offline
```

`a1` は登録済みプレーヤーのエイリアスの例で、コンピューターのホスト名ではありません。[登録とペアリング](docs/cli.md#register-and-connect-a-player)、[全コマンドと例](docs/cli.md#commands)、[切断とアクセスの取り消し](docs/cli.md#disconnect-deregister-or-revoke-a-player)、[トラブルシューティング](docs/cli.md#troubleshooting)を参照してください。

常時稼働するプレーヤーでは [standby と go](docs/cli.md#stop-playback-while-keeping-the-player-online) を使い、アプリを終了せずに再生を止め、後で復帰させます。`stop` は引き続き `standby` のエイリアスで、`start` と `wake` は `go` のエイリアスです。リモート制御の接続は維持する必要があるため、スタンバイがネットワーク通信ゼロを保証するわけではありません。`ott a1` はプレーヤーが提供する操作を表示します。コマンドの受理は、停止処理や再生開始が完了した確認にはなりません。

[アスペクト比のコマンド](docs/cli.md#aspect-ratio-fit-and-fill)には、更新済みの CLI とコントローラー、および `aspect` 機能を通知するプレーヤーが必要です。変更は応答確認後に適用されます。`aspect` をもう一度読み、モードと保存状況を確認してください。Fill は画像を引き伸ばさず、比率を保って切り抜きます。

リモートの[スクリーンショット](docs/cli.md#remote-screenshots)には、更新済みの CLI とコントローラー、および対応するキャプチャアダプターを持つプレーヤーが必要です。プレーヤーのリモート制御接続を有効にすると、そのコントローラーにスクリーンショットと診断を許可します。ネイティブのスクリーンショットには別の権限スイッチは不要です。ブラウザーでは引き続きローカルのソース選択が必要です。画像には設定、PIN 画面、表示中の認証情報が含まれる可能性があるため、信頼できるコントローラーにだけ接続してください。`ott a1` が利用可否と必要なソース選択を報告します。スクリーンショットは構造化テレメトリーの収集とは別です。

タイトルに一致する VPortal 動画をすべてシャッフルし、繰り返し再生するには次を使います。

```sh
ott l vpr "wedding"
```

`vpr` は選択全体を一度シャッフルして、そのキューを繰り返します。別の順序にするにはもう一度実行します。`vp` はカタログ順に一致項目を再生し、`vp --list` は再生を変えずに一覧を表示します。[VPortal コマンド](docs/cli.md#commands)を参照してください。

プレーヤーに保存された Plex 設定を使い、明示した順序で再生するには次を使います。

```sh
ott l plex preview 78777 78776 78775  # check availability without starting playback
ott l plex play 78777 78776 78775     # starts the first item from zero, in this order
ott l plex queue                     # current item and preparation/playback state
ott l plex next                      # advance once; no wrap at the last item
ott l plex stop                      # stop and clear this queue
ott l plex libraries                 # list libraries (requires plex_library support)
ott l plex shuffle "Tom and Jerry"   # shuffle one complete library, up to 500 items
ott l plex library --preview id:16    # read-only check of an explicit library
```

CLI は既定で最大 45 秒待ちます。`ott -t 45 l plex shuffle "Tom and Jerry"` は同じ応答タイムアウトを明示する書き方であり、動画の再生時間を制限しません。大きな Plex ライブラリの取得は、10～15 秒という短いタイムアウトを超えることがあります。`-t SECONDS`（または `--timeout SECONDS`）はプレーヤーエイリアスの前に置きます。[Plex ライブラリのタイムアウト](docs/cli.md#plex-libraries-by-name)で、プレビューの例と再生要求がタイムアウトした場合の対処を確認してください。

Plex キューには `plex_queue` を実装した CLI、コントローラー、プレーヤーが必要です。`plex play --shuffle ID...` は渡された ID を一度シャッフルします。既存のキオスクポリシーで繰り返す場合を除き、最後の項目で停止します。キューが存在する間は通常の `next` と `prev` がキューに作用し、`plex stop` がキューを消去します。プレビューはプロバイダーを切り替えず、現在の再生も置き換えません。制約と準備状態は [Plex キューコマンド](docs/cli.md#ordered-plex-queues)を参照してください。ライブラリ検索にはさらに、`plex_library` を通知する更新済みのプレーヤーとコントローラーが必要です。プレーヤーに保存された Plex 接続を使い、曖昧または不完全な選択を拒否します。[名前による Plex ライブラリの検索](docs/cli.md#plex-libraries-by-name)を参照してください。

### ローカル動画のプロファイルを保存する

最新のプレーヤーとコントローラーでは、ローカルファイル／フォルダーは `offline` プロバイダーを使います。

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

まずプレーヤーでファイル／フォルダーを接続します。保存はその設定と選択した動画を記録し、再生は始めません。エクスポートで移すのは記述情報だけで、メディアと OS／ブラウザーのアクセス許可はデバイスに残ります。別のインストールではローカルでの関連付けが必要であり、読み込みは再生が確認されたことを意味しません。設定、機能、移植形式の制約、問題解決は[ローカル動画プロファイル](docs/cli.md#local-videos-profiles)を参照してください。

### 現在の項目をプロファイルをまたいで保存する

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

お気に入りは通常の再起動後も対象プレーヤーに残ります。選択すると保存されたプロバイダー／プロファイルを読み込み、新しいリンクを解決します。ソースが変わった場合や項目がない場合はエラーになり、別のチャンネルや映画で代用しません。CLI、コントローラー、プレーヤーは `favorites` に対応している必要があります。`ott l` が利用できる操作を表示します。エイリアス、制約、確認応答の意味、問題解決は[プロファイルをまたぐお気に入り](docs/cli.md#favorites-across-profiles)を参照してください。

### 保存した設定セットを読み込む

名前付き設定セットは `~/.config/ottplay-control/cli.json` の非公開の `presets` セクションに保存します。各セットには番号付き M3U プロファイル（プレイリストとアーカイブ時間）、Plex 認証情報、読み込み完了時に選ぶ M3U プロファイルが含まれます。[設定例](docs/cli.md#named-setups)を参照してください。

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI は最初にセット全体を検証し、次の変更を送る前にプレーヤーの確認応答を待ちます。プロバイダーの読み込みを扱い、Plex と M3U スロットを保存して、設定された M3U プロファイルを選びます。エラーが起きるとシーケンスを止め、確認済みの手順を報告します。結果が不確かな書き込みは自動で繰り返しません。追加のプロファイルサービスやコントローラー再起動は不要です。この設定にはプロバイダーの認証情報が含まれるため、非公開に保ってください。

## コマンドを送る

次の例はローカルの非公開設定から管理者トークンを読み、URL やコマンドライン引数には含めません。

```python
import json
import urllib.request

with open("config.json", encoding="utf-8") as stream:
    config = json.load(stream)
request = urllib.request.Request(
    "http://127.0.0.1:8081/api/webhook/commands?device_id=living-room",
    data=json.dumps({"command": "set_volume", "volume": 35}).encode(),
    headers={
        "Authorization": "Bearer " + config["admin_token"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status)
```

ほかのコマンドでは、番号または名前でチャンネルを選ぶ、ランダムにチャンネルを選ぶ、プロバイダーを切り替える、メッセージを表示する、対応時に M3U プレイリストを変更する、プレーヤーを終了する、といった操作ができます。利用可否はプレーヤーのプラットフォームと現在のプロバイダーによります。[API 契約](docs/api.md)を参照してください。

独立した VPortal プロバイダーに対応するプレーヤーでは、`ott a1 provider
vportal` がその専用の 15 個のプロファイルを選びます。`profile N vportal LINK`、`profile N
name NAME`、または `name` と `vportal` を含む非公開ファイル `profile-config N FILE` を使い、その後 `profile N` で設定済みスロットを選択します。既存の M3U プロファイルは別に保たれます。`profiles` はアカウントページへのリンクや鍵を公開せず、メタデータを報告します。

VPortal の映画またはエピソードキューを開始したら、`ott a1 kiosk on` でその選択を固定します。プレーヤーは再読み込み後もキューと位置を保持し、復旧のためにメディア URL を再解決します。別のタイトルを選ぶ、またはプロファイルを変更するには `kiosk off` を使い、その後 `kiosk on` で固定します。キオスクの受領情報にはタイトルとエピソード数が表示され、プロバイダーへのリクエストとストリーム URL はプレーヤーに残ります。

子ども向けの再生には `ott a1 kiosk on --strict` を使います。ローカルでタップすると動画のフッターが表示されます。更新済みの core プレーヤーではタイムラインをタップまたはドラッグして現在の VPortal／Plex エピソード内をシークできますが、ほかのローカル操作はロックされたままです。古いプレーヤー、チャンネルキオスク、タイムラインのジェスチャーを横取りするラッパーでは、フッターは読み取り専用です。既存のロックは対象を維持します。コントローラーから `ott a1 kiosk off` で解除してください。Android の Web サイトでは、システム操作による終了を防ぐために PIN 付きのアプリ固定を別途設定する必要があります。[キオスクモード](docs/cli.md#kiosk-mode)を参照してください。

## リモート診断

まず[読み取り専用 Workbench のクイックスタート](docs/workbench.md#quick-start)で、`doctor`、UI／メディアの `inspect`、操作の受領情報の検索、証拠バンドル、`health`、Web／ネイティブの `media-progress`、オフラインの `report verify` を確認してください。既存の管理者 CLI 設定とプレーヤーエイリアスを使います。ネイティブ経路は別途導入した Android メンテナンスエージェント向けで、Tauri プレーヤーは Web 検査を使います。レポートでは、デコーダーの進捗と、実際の画面／音声の確認を区別します。機能の欠落、使えない経路、不完全な証拠については [Workbench のトラブルシューティング](docs/workbench.md#troubleshooting)を参照してください。

オプションの[プロトコル 2 診断](docs/remote-diagnostics.md)は、ランタイムごとに明示的に有効化するセッションを提供します。権限を限定した独立のオペレーター認証情報と、範囲を制限した構造化テレメトリーを使います。有効なプレーヤー接続が追加の信頼確認なしにローカル認可を与えますが、各キャプチャのセッション有効期間は引き続き制限されます。診断制御のポーリングは、既存のコマンドキューとは独立しています。

[診断 CLI と MCP のガイド](docs/diagnostics-cli.md)は、インストール、オペレーター認証情報、接続設定、各診断コマンド／ツール、キャプチャと修復の例、ランタイムのアクセス取り消し、問題解決を扱います。`ott diagnostics --help` はこの独立したコマンド群を表示します。通常のプレーヤーエイリアスと管理者認証情報では診断アクセスは得られません。

## 配置パッケージ

- [Android タブレット: Plex の厳格なキオスクと起動](docs/android-kiosk-setup.md)
- [共有 Android USB 管理と起動時の復旧](docs/android-usb-manager.md)
- [Docker / Compose、Kubernetes、k3s、systemd、macOS、Windows](docs/deployment.md)
- [設定、配送の意味、制約](docs/api.md)
- [リリースと検証の手順](docs/development.md)

キューは容量が制限され、既定では 60 秒で期限切れになり、メモリ内に保存されます。サーバーの再起動で未処理のコマンドは破棄されます。レプリカは 1 つで運用してください。独立したレプリカ間でキューは共有されません。プレーヤーが確認するのは送信の試行であり、再生やハードウェア音量の確認ではありません。ネットワーク再試行の重複排除は動作中のプレーヤーセッション内だけで行い、アプリの再起動をまたいでは行いません。

本サーバーは [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) のオプションの `local_proxy.py` 例のコマンド API に従い、管理者とデバイスの認証情報を分離し、確認応答付きで届けます。従来のプロキシは独立したコンポーネントのままです。本プロジェクトはメディアのプロキシにはならず、プレーヤーの HLS、EPG、プロバイダー通信も変更しません。

## ライセンス

MIT。取り込んだリリースツールの帰属情報は `NOTICE` と `LICENSES/` にあります。

## プロジェクトの保守

[貢献とテストの要件](CONTRIBUTING.md)、[セキュリティ報告ポリシー](SECURITY.md)、[セキュリティ設計](docs/security-design.md)、[OpenSSF の証拠と未達の基準](docs/openssf-evidence.md)を参照してください。

現在のプレーヤーでは Plex キューは最大 500 個の ID を受け付けます。`ott f10 plex play --shuffle ID...` で一度シャッフルし、Plex プロバイダーを選んだ状態で `ott f10 kiosk on --strict` を使ってキューを繰り返し、固定します。`ott f10 kiosk off` で解除できます。古いプレーヤーは、そのプレーヤーが通知するキュー上限のままです。

## リリース候補のファジング

公開リリース候補では、[ファジングガイド](docs/fuzzing.md)に記載された入力境界に対し、制限付きのネイティブファジングを実行します。変異キャンペーンは PR、ベータ、ナイトリービルドでは実行しません。ガイドには対象範囲、予算、証拠、リグレッションの再現手順を記載しています。

## コミュニティーとセキュリティの証拠

[貢献](CONTRIBUTING.md)、[ガバナンス](GOVERNANCE.md)、[行動規範](CODE_OF_CONDUCT.md)、[ロードマップ](ROADMAP.md)、[OpenSSF の証拠](docs/openssf-evidence.md)を参照してください。証拠の索引は、対応するセキュリティプロファイル、実際のテスト結果、残る Silver 要件を区別しています。
