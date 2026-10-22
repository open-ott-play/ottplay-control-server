# Android USB転送イメージ

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

このディレクトリには、共有マネージャーの転送コンテナー用のデプロイテンプレートと[`Dockerfile`](Dockerfile)があります。PythonマネージャーはUSBホストで動作します。イメージはADB、Python、アトミック保守アダプターのツールを提供し、2つ目のデバイス登録簿や復旧デーモンは含みません。準備、コマンド、移行、復旧の制限、ロールバックは[運用ガイド](../../docs/android-usb-manager.md)を参照してください。

Gitチェックアウトがない場合は、同じ[リリース](https://github.com/open-ott-play/ottplay-control-server/releases)から`ottplay-control-server-deployment.tar.gz`と`release-manifest.json`をダウンロードします。アーカイブのダイジェストを検証し、[デプロイパッケージの取得](../../docs/android-usb-manager.md#get-the-deployment-package)に従って新しいディレクトリに展開します。パッケージにはこのレシピと`tools/`内のホスト側・リモート側スクリプト6本が含まれます。ディレクトリ構造を維持してください。`v0.1.5-beta.6`を含む古いパッケージにはこれらのスクリプトがなく、対応するソースアーカイブが必要です。ビルド済みUSB転送イメージは含まれません。コントロールサーバーのOCIリリースアセットは別用途です。

## ソースとアーティファクトの由来

ベースは`python:3.12.14-slim-trixie`で、`sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`に固定されています。ベース、APT命令、ハッシュ固定の`websocket-client==1.9.0`インストールは[PR 128のリビジョンb4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager)でレビュー済みです。既存のビルドキャッシュを再利用できるよう命令文を維持しています。任意のWebSocketパッケージは既存の汎用保守クライアントを支援します。移行済みのCDPアダプターはPython標準ライブラリだけでも動作します。

このレシピは固定されたwheelのインストール後に`pip`パッケージを削除し、PR 128のマネージャーと復旧コードを含めず、ADBをフォアグラウンドで起動し、継承したヘルスチェックを無効化します。実行環境のインストーラーを削除すると攻撃対象領域は減りますが、脆弱性スキャンで問題がなかったことを証明するものではありません。

PR 128のレビュー済みアーティファクトは`sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`でした。エントリーポイントが異なり、互換性のないハートビートのヘルスチェックを含みます。これは参照用であり、このDockerfileのビルドではありません。そのイメージID、ソースの来歴、スキャン結果をこのレシピのものとして扱わないでください。

APTリポジトリはスナップショット固定されていません。そのため、同じベースダイジェストとPython wheelのハッシュでも、将来のクリーンビルドでは新しいOSパッケージが選ばれる可能性があります。ビルドを認定するときは、正確なソースリビジョン、ビルダー/プラットフォーム、生成イメージID、パッケージのバージョン、スキャンを記録します。本番設定には、その認定済みの不変なイメージIDまたはダイジェストを使う必要があります。

## 稼働中のUSBホストを妨げずにビルドする

ビルドとスキャンは別のビルダーで行うことを推奨します。対象ホストには認定済みイメージだけが必要で、パッケージのダウンロードやビルドツール一式は不要です。

準備済みのローカルビルダーを再利用する場合は、正確なベースイメージが存在し、APTとwheelのインストール層がキャッシュされていることを先に確認します。PR 128の最終イメージをロードしただけでは、どちらも確認できません。リポジトリのルートまたは展開したデプロイディレクトリから、意図的にオフラインで試すには次を使います。

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none`はビルド手順に適用されます。ベースの解決時にビルダーがレジストリに一切接続しないという一般的な保証ではありません。制約のあるホストで使う前に、ベースがローカルで解決されることを確認してください。APTまたはwheelの層がなければ中止し、USBホスト上で暗黙にネットワーク再ビルドを許可しないでください。

BuildKitはビルド手順のネットワークモードをキャッシュキーに含めます。そのため、既定のモードで作ったキャッシュは上記オフライン試行でヒットしない場合があります。正確なベースと命令列を確認してから再利用し、いずれかのパッケージインストール手順が`CACHED`と表示されず実行されたら中止します。最後の`pip`削除は明示的に`RUN --network=none`を使い、それ以前のキャッシュ手順が元のモードでもネットワークを必要としません。

本番設定を変える前にPythonとADBのバージョンを確認し、生成された正確なイメージをスキャンし、バイナリファイルの転送とAPKのステージングを検証します。その後、ガイドのデバイス識別、鍵の認可、スリープ、起動の受け入れ確認を実施します。ADB 34と古いAndroidデバイスの通信はテストが必要です。バージョン番号だけではKitKatやFireとの互換性を証明できません。ロールバックが不要になるまで、現行イメージと非公開のデプロイバックアップを保持してください。

## 実行時のリソース枠

このDockerfileではなくホストマネージャーが、メモリ256 MiB、メモリとスワップの合計256 MiB、CPU shares 128を設定します。CPUまたはPIDのハードクォータはありません。`--no-healthcheck`、`--network none`、読み取り専用ルートファイルシステム、capabilityの削除、厳密なUSBノード割り当てを強制します。`/tmp`は上限付きtmpfsで、同じメモリ枠を消費します。ステージングアダプターは1 MiB単位で書き込み、単一ペイロードを128 MiBに制限します。操作はマネージャーのロックを共有します。

Dockerの文書では、[メモリ、スワップ、CPU sharesの制御](https://docs.docker.com/engine/containers/resource_constraints/)と[`--no-healthcheck`オプション](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck)をそれぞれ説明しています。
