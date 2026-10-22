<div lang="ja" dir="ltr">

# Android OTA：通常のプレーヤーと独立したrootエージェント

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

**k8a/fire8などのCapacitorプレーヤー**では、rootエージェントのコマンドではなく、通常のプレーヤーのWi-Fiアップデーターを使います。

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

`--version`を省略すると最新の公開betaが選ばれます。Android 7以降では`api22`の代わりに`full`を使います。CLIは公式リリースを解決し、マニフェストを確認し、APKを準備して状態をポーリングしてから、一度だけインストールを要求します。Androidがタブレット上で確認を求める場合があります。保守前にキオスクを明示的に無効にし、保護された設定を解除してください。このコマンドがポリシーを変更することはありません。[CLIの完全な手順](cli.md#capacitor-apk-updates-over-wi-fi)を参照してください。

あらかじめ`AppUpdate`対応APKがインストールされ、Wi-Fiが利用でき、プレーヤーが稼働してコントローラーに接続している必要があります。通常の2回目以降の更新にUSBは不要ですが、オフラインのタブレットや停止したWebViewには届きません。初回インストール、アップデーターの不在、アプリ破損、ネットワーク喪失にはローカル復旧が必要な場合があります。自動リリーススケジューラーや汎用の無人インストーラーを意味しません。

以降は、準備済みのMatricom/API19 `play.ott.kitkat`環境に限定された、**別のroot権限を持つa1保守エージェント**を説明します。通常のFire/Capacitor機器に既にインストールされていたり、そのまま使えたりするものではありません。

一度だけ行うroot USB初期設定後、独立Androidエージェントは外向きHTTPS接続で署名済み更新を受け取れます。自己更新に動作中のWebViewは不要です。0.1.2エージェントは、復旧ファイルを保存して確定前に検証する、APKとシステムタッチヘルパーの協調バンドル更新にも対応します。

このガイドは全体の経路を説明します。正確なマニフェスト項目、制限、ハードウェア要件は[ネイティブエージェントのリファレンス](../native/android-agent/README.md#signed-updates-over-wi-fi)を参照してください。

## 更新対象

![OTA構成：レビュー済みリリースのバイト列を署名し、直接HTTPSで公開し、独立エージェントが検証してインストール後に確認します。Webデプロイは別です。](images/ota-architecture.svg)

- **ネイティブエージェント：**`kind: agent`は自身のARMv7バイナリを置き換えます。OTTを無効にして別のHOMEアプリを使っている場合も機能します。
- **APKのみ：**`kind: apk`はデータを維持してラッパーを置き換えます。OTTが有効でフォアグラウンドにある必要があります。候補APKの内蔵ヘルパーはインストール済みシステムヘルパーと完全一致する必要があります。
- **APKとタッチヘルパー：**`kind: bundle`は2つのコンポーネントを対で更新します。古いエージェントには先にバンドル対応のエージェント単独更新が必要です。
- **ホスト型プレーヤー：**検証済みWebアーティファクトを既存オリジンにデプロイし、稼働中プレーヤーを再読み込みして確認します。APK更新はホストされたJavaScriptをデプロイしません。[リリース手順](release-workflow.md)を参照してください。
- **コントローラーとCLI：**コントローラーのデプロイと完全なCLIモジュール一式のインストールは別々に行います。どちらの更新もタブレットのコードを置き換えません。

ファームウェア、カーネル、システムWebView、rootブローカー、起動スクリプトはこのOTA形式の対象外です。[ネイティブのリファレンス](../native/android-agent/README.md#bootstrap)に初期設定と復旧の境界を記載しています。対応するOTA操作にネットワークADBや任意のリモートシェルは不要です。

## レビュー済みリリースからインストール確認まで

1. **検証済みのバイト列を選ぶ。** CI成功後にレビュー済み変更をマージします。対象リリースをダウンロードし、ソースリビジョン、不変のリリース証跡、チェックサムを確認します。ネイティブアーカイブから`agent`バイナリを取り出してください。アーカイブ自体はインストール可能なエージェントペイロードではありません。
2. **準備して署名する。** 初期設定時に公開鍵を固定した既存のEd25519秘密鍵を使います。鍵とデバイス認証情報を非公開に保ちます。
3. **公開して転送を確認する。** ペイロードと`signed.json`だけを不変のHTTPSパスで公開します。両方がHTTP 200でバイト列を直接返す必要があり、リダイレクトは拒否されます。パッケージツールの`verify`を実行します。
4. **更新を1回送る。** リクエストIDを記録し、受領確認後に状態を確認します。応答の喪失や不確実さを理由に操作を盲目的に繰り返さないでください。
5. **効果を確認する。** 新しく読み取ったインストール済みハッシュとバージョンを準備したリリースと比較します。稼働中プレーヤーではキオスク対象、キュー、再生の進行も確認します。実際の映像と音は観察が必要です。

エージェントのリリース例です。選択したバージョンと実際のパスに置き換えてください。

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256`はパッケージツールの`envelope_sha256`であり、ペイロードのハッシュではありません。`accepted`は要求を受け付けた意味で、インストール成功ではありません。自己置換によって旧操作の受領記録が`unknown`になる場合があります。新しいランタイム、期待したエージェントバージョン、一致するインストール済みハッシュが証拠となります。[CLIリファレンス](cli.md#independent-android-agent)は独立したネイティブルーティングと記録処理を説明します。

## APK/ヘルパートランザクションの復旧

![APKとヘルパーのトランザクション：事前確認、旧ペア保存、適用、検証、確定。失敗時は旧ペアを復元し、復旧未完了ならアプリ変更を遮断し続けます。](images/ota-transaction.svg)

バンドルには`app.apk`と`ott-touch-guard`だけが含まれます。パッケージツールは`--kind bundle --apk`で署名済みラッパーAPKから直接作成できます。内蔵ヘルパーを抽出するため、配布する2つのコピーは一致します。[バンドルコマンドと要件](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)を参照してください。

ファイル変更前に、インストール済みペアの一致、十分な空き容量、応答するフォアグラウンドのOTT、読み取り専用システムパーティション、APKダウングレード対応の表明が必要です。ロック済みキオスクでは同じTVチャンネルIDまたは順序付きVPortal IDを検証します。キューの長さが同じだけでは不十分です。未対応のロック済みキューは拒否します。意図的にOTTを無効化している場合も拒否し、ユーザーの現在のHOMEアプリを維持します。

アプリを停止する前に復旧ファイルと未完了トランザクションを永続化します。エージェントはアプリと旧ヘルパーの停止を待ち、ヘルパーをアトミックに置き換え、`/system`を読み取り専用に戻し、APKをインストールして結果を確認します。適用または検証に失敗すると旧ペアを復元します。起動時復旧ではOTTを起動したりHOMEを奪ったりせず、ファイルを復元します。

復旧が完了できなければ`updates.recovery_failed`がtrueになります。診断と署名済みエージェント単独修復は引き続き利用でき、アプリ変更と再生ウォッチドッグは遮断されます。強制インストールのために保存トランザクションを削除してはいけません。バイナリ復旧はアプリデータ移行を元に戻さず、ファームウェアがダウングレードを拒否する可能性もあります。[復旧の制限](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)を参照してください。

## 結果と受け入れ記録

![検証図：CLIテスト488件、リリースツールテスト576件、OTAパッケージャーテスト6件。基準タブレットでは検証済み新エージェントが動作していますが、APK/ヘルパーの実機受け入れは未完了です。](images/ota-validation.svg)

2026年10月14日の実装監査で、CLIテスト488件（1件スキップ）、リリースツールテスト576件、OTAパッケージャーテスト6件、ネイティブGoの競合/静的検査、ARMv7ビルドが成功しました。件数はテスト規模であり、カバレッジや性能ではありません。障害注入テストは不正アーカイブ、中断トランザクション、部分復元、対象変更、復旧時の隔離を確認します。独立レビューでも不十分なキュー識別確認と、ホスト/デバイス間でのAPK・ヘルパー検証の不一致を発見し修正しました。

2026年10月15日の確認結果：

- OTA実装は[コントローラーPR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104)でマージされ、[v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4)に含まれています。
- 現在と直前の署名済みエージェントパッケージは[OTA配布ホスト](https://android-updates.ottplay.here.now/)で公開されています。ペイロードとエンベロープのバイト列は直接HTTPSでダウンロードし、一致を確認済みです。
- 基準タブレットは独立OTA経路で`0.1.1-beta.21`から`0.1.2-beta.4`へ更新しました。動作中エージェントのバージョンとインストール済みSHA-256は対象リリースと一致し、ランタイムは変わりましたがOSブートIDは不変でした。OTTのアプリプロセスは動作しておらず、アプリ起動やHOME選択操作も送っていません。
- ネイティブ状態にはシステムタッチヘルパーのハッシュがあり、保留バンドルも復旧失敗もありませんでした。APKメタデータは取得できなかったため、APKのインストール済みバージョンやハッシュを示す証拠ではありません。
- APK/ヘルパーの協調トランザクションはホスト側で検証済みですが、基準タブレットでの実際のインストール、ダウングレード復旧、映像と音声の受け入れは未完了です。

以後の各展開では、リリースのソース/チェックサム、準備したエンベロープハッシュ、リクエストID、前後のネイティブ状態、別途行う実機受け入れ結果を残してください。ダウンロード成功やコマンド受領通知は、これらの観測の代わりにはなりません。

</div>
