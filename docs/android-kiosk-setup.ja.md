# Androidタブレット：Plexの厳格なキオスクと起動

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADBガイド](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USBマネージャー](android-usb-manager.md)

専用タブレットでは、コンテンツのキュー、OTTの厳格なキオスクポリシー、Androidの起動を別々に設定・検証する必要があります。APKのインストールや画面消灯からの復帰確認だけでは、OS再起動後の起動を確認できません。

## 識別とインストール

1. 物理タブレット、OTTのエイリアス/UUID、USBマネージャーのエイリアス、シリアル番号、モデル、Android ID、SDKを記録します。OTTとUSBの名前は異なる場合があります。名前だけで同一性を判断したり、別のタブレットのトークンを使ったりしないでください。
2. SDKと互換性のある公式APKを使い、リリースのSHA-256とパッケージ/署名の識別情報を確認して、`install -r`でインストールします。プレーヤーデータは保持します。USBを[共有マネージャー](android-usb-manager.md)が所有している場合はそれを使い、競合するADBサーバーや対象を指定しない`adb`コマンドを実行しないでください。
3. プレーヤー固有のコントローラー認証情報でペアリングし、`ott TABLET`を確認します。目的の名前付きプリセットを読み込み、`ott TABLET load PRESET verify`を実行します。家庭内ネットワーク用と開発機のlocalhost用プリセットは相互に代用できません。

## タブレットからPlexを確認

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

ライブラリID、タイトル、項目数を確認します。管理者のPCからのリクエスト成功は、タブレットの接続を証明しません。失敗時はタブレットの保存アドレス、経路、TLS対応、認証情報を確認してください。サーバーの有効なHTTPSエンドポイントを優先します。古いファームウェアがプライベートLAN上のHTTPを必要とする場合は、このインストールだけに限定し、トークンとメディアが暗号化されずに転送されることを記録します。共有プリセットを書き換えたり、証明書検証を全体で無効化したりしないでください。診断や文書にトークンや非公開メディアURLを載せないでください。

Plexサーバーのアドレス変更は選択済みライブラリルートを消去します。プロバイダーを**Plex**にし、**Plex settings → Folders**を開き、目的のライブラリ行を選択します（**Browse folders**だけでは不十分です）。**Ok**、続いて**Save and open library**を選びます。プレーヤー`1.1.58-beta.19`では、サーバー認証情報だけでフォルダー選択が開いても`ready`はfalseのままの場合があります。キュー操作の前に選択を完了してください。`ready: true`を得るためだけにM3Uへ切り替えないでください。Plexキューはそこで再生できても、`kiosk on --strict`はメディア未接続の待機中M3Uポリシーを保存する場合があり、Plexキューのロックや復元にはなりません。

## キューの開始とロック

```sh
ott TABLET kiosk off
ott TABLET plex shuffle 'LIBRARY NAME'
ott TABLET plex status
ott TABLET debug --engine
# Continue only after the intended queue is playing.
ott TABLET kiosk on --strict
ott TABLET plex status
ott TABLET kiosk status
ott TABLET debug --engine
```

順序付きキューには`plex shuffle`の代わりに`plex library 'LIBRARY NAME'`を使います。Shuffleは一度順序を決め、キオスクがそれを繰り返します。目的の項目、`enabled: true`、`strict: true`、目的のPlexメディアキューへのポリシーの関連付けを確認します。チャンネルもメディアもない`waiting`ポリシーは受け入れ済みキオスクではありません。位置/フレーム数が進むサンプルを2回以上観測し、画面を確認します。確認応答や`playing`表示だけで進行がなければ不十分です。項目末尾の自然な遷移を試し、一周すべての検証が現実的でなければ、その制限を明記します。

厳格なキオスクはプレーヤーUIを制御します。Androidのデバイス所有者登録ではなく、Home、Recents、通知、Settingsの遮断を証明するものでもありません。既存のシステムランチャーと、立ち会いで保守できる経路を維持してください。

## 起動を別途有効にする

更新用の既存署名鍵を保持し、[OTT Home 0.2.0](../native/android-home/README.md)をビルドして検証します。`play.ott.foss`と並べて`play.ott.home`をインストールします。`play.ott.home/.StartupActivity`を開き、**Start OTT after reboot**を有効にします。初期状態では無効です。公式プレーヤーのバイナリ、OTA署名、プロファイルデータ、Homeボタン割り当ては変更しません。

Home選択があるファームウェアでは、任意で**OTT Home → Always**も選びます。選択機能がない場合（検証した古いFireを含む）、起動許可は独立して機能します。既に既定のHomeとして設定された環境は動作を維持します。完全に戻すには両方の仕組みを無効にします。

共有USBマネージャーの`recover_on_boot`は3つ目の独立した仕組みです。NASとUSBケーブルが必要で、新しいブートだけを処理し、クラッシュしたアプリを継続的に再起動しません。自律起動の代用ではありません。自律起動の試験ではこの復旧を無効にし、補助アプリの起動不良を隠さないようにします。安全なロック画面を尊重し、要求されたら通常の方法で解除してください。

## 受け入れと引き渡し

証跡は非公開で保存し、公開する記録に認証情報を含めないでください。

- APKバージョン、ハッシュ、実機/OTT/USBの対応、選択したプリセット。
- Plexライブラリ、項目数/順序、厳格なポリシー、最初の再生進行。
- 実際のOS再起動前後のブートIDと、再起動後の`sys.boot_completed=1`。
- 再起動から観測まで手動プレーヤー起動やUSB復旧がないこと。
- OTTがフォアグラウンドにあること、コントローラー再接続、キュー/厳格なポリシーの復元、再生の進行。ブート受信側の`requested`は要求を送ったことだけを意味します。
- 電源ボタンの別試験：画面消灯、音声停止、通常のロック解除と復帰。
- 正確な制限：安全なロック解除が必要、USB支援復旧、未試験の物理的な離脱経路、ストリーム障害、メモリ不足。これらを完全な受け入れ済みと呼ばないでください。

次のタブレットを渡す前にも、前の成功結果をコピーせず同じチェックリストを使います。古いFireではベンダー標準HLSがクラッシュするため、M3Uに`hls.js`が必要な場合があります。それはPlexの再生安定性の証明ではありません。

## 保守とロールバック

```sh
ott TABLET kiosk off
```

**OTT Startup**で**Start OTT after reboot**を無効にし、変更した場合は元のHome選択に戻します。必要なら検証済みUSB転送経由で`play.ott.home`だけを削除し、`play.ott.foss`とそのデータは保持します。起動の無効化はキューを変えず、キオスクも自動で無効にしません。
