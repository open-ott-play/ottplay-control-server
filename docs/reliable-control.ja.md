<div lang="ja" dir="ltr">

# 信頼性のある制御と再生の引き継ぎ

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ドキュメント索引](wiki/Home.md)

これらの機能には、このガイドを含むリビジョンのコントローラーと CLI が必要です。設定の厳密な比較と引き継ぎには、プレイヤーの更新も必要です。古いリリースアーカイブやインストールガイドにある過去の CLI リビジョンに、これらの機能が自動的に追加されるわけではありません。`preset_session.py` を含む**すべての `cli/*.py` ファイルをまとめて**インストールし、非公開の設定は保持してください。

![制御、チェックポイント、引き継ぎの流れ](assets/reliable-control.svg)

## 上限付きの永続的な結果保存を有効にする

この任意のブロックを非公開のサーバー設定に追加し、検証してからサーバーを再起動します。サービスアカウントが書き込めるローカル永続ファイルシステム上の絶対パスを選んでください。Unix ではディレクトリを非公開（0700）にし、既存のジャーナルとロックファイルを通常の非公開ファイル（0600）にする必要があります。Windows ではディレクトリの ACL をサービスアカウントに制限します。1 つのジャーナルを所有するのは 1 つのプロセスです。

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

サーバーは存在しないディレクトリをモード 0700 で作成します。上限は 1–4096 レコード、60–86400 秒、ディスク上で 4 MiB です。保存期間は `command_ttl_seconds` をカバーする必要があります。ジャーナルが満杯になると、有効なレコードを追い出すのではなく、新しいキー付き操作を拒否します。ファイル破損、安全でない Unix ディレクトリ、別の書き込みプロセス、永続書き込みの失敗がある場合は、安全側に倒して拒否します。既定では引き続き既存のメモリ内コマンド処理を使用します。

同梱の systemd ユニットは `/var/lib/ottplay-control` をモード 0700 で作成します。Compose では UID 65532 が所有するホストディレクトリを用意し、`CONTROL_SERVER_STATE` にその絶対パスを設定して、`deploy/compose.yml` と `deploy/compose.journal.yml` を組み合わせます。後者が追加するのは書き込み可能な状態マウントだけで、ルートファイルシステムは読み取り専用のままです。Helm では永続ボリューム要求を別途用意し、`journalExistingClaim=CLAIM_NAME` を設定します。要求は `/var/lib/ottplay-control` にマウントされるので、上記の非公開サブディレクトリを使ってください。チャートは単一レプリカ、`Recreate`、`fsGroupChangePolicy: OnRootMismatch` を維持し、再マウントで既存の非公開ディレクトリの権限が再帰的に広がらないようにします。選択したストレージドライバーがこの所有権の動作をサポートすることを確認してください。チャートは要求を作成・削除せず、マウントを有効にしてもジャーナルは有効になりません。引き続き非公開のサーバー設定が制御します。

`POST /api/requests` では `action` と `params` の隣に `idempotency_key` を追加します。従来のコマンド POST は代わりに `Idempotency-Key` を受け付けます。キーは 16–80 文字の ASCII 英字、数字、アンダースコア、ハイフンで構成し、実行意図ごとに新しいランダムキーを生成してください。同じ所有者とキーで、デバイス、操作、正規化 JSON ペイロードも同じなら、再度キューに入れず元の ID を返します。ペイロードの変更は 409 を返します。キーの範囲はデバイスではなくオペレーター ID（または管理者）です。保存期間後にはキーが新しい意図として受理される場合があるため、クライアントはこの期間外で古い意図を再試行してはいけません。

`GET /api/operations?device_id=DEVICE` はジャーナルの有効状態、制限、コマンド TTL を返します。操作を読むには `&key=KEY` を追加します。

- `pending`: 受理済み。保存された応答はまだありません。
- `responded`: 検証済みのプレイヤー応答が届きました。`result_status` は `ok`、`rejected`、`unsupported` のいずれかです。元の結果本文は通常どおり期限切れになります。
- `acknowledged`: 従来のコンシューマーがコマンドを確認しました。目に見える再生や設定変更の保存を証明するものではありません。
- `unknown`: 期限が過ぎたか、応答の保存前にサーバーが再起動しました。サーバーは再起動後、このコマンドを**決して再実行しません**。

同じキーの POST を繰り返すと、`status: retained`、元の ID、状態が返ります。通常の新しい POST は `status: queued` のままです。ジャーナルレコードは所有者、デバイス、操作、ランダム ID、ペイロードのダイジェスト、状態、期限だけを含みます。コマンドペイロード、プロバイダー URL、トークン、スクリーンショット、応答本文は含みません。保留中のコマンドは永続化されません。これは上限付きの重複排除と結果追跡であり、厳密に 1 回の実行、永続的な作業キュー、複数サーバーの高可用性ではありません。

## Local/Home の計画・適用・再開・検証

既存の `ott PLAYER load PRESET` の動作は維持されます。新しい手順は次のとおりです。

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` はオフラインで動作し、ローカルプリセットを検証して、設定スロットと希望するアクティブプロファイルを内容を表示せず一覧にします。プレイヤーは調べません。`verify` は読み取り専用で厳密に比較し、選択された M3U プロファイルを確認します。差異がある場合や比較が未対応の場合は、ゼロ以外の終了コードを返します。

`apply` にはサーバージャーナルが必要です。非公開のチェックポイントを作成し、最初の変更前に構成済みのすべてのプロバイダーを比較します。各保存は `settings_compare` を通して保存済みバイトを期待値と比較し、一致すれば書き込みを省略します。最後の検証は Plex、すべての M3U スロット、任意の Stalker スロット、選択した M3U プロファイルを対象とします。比較が返すのは真偽値で、保存された秘密情報は返しません。

既定のチェックポイントは `~/.config/ottplay-control/checkpoints/` 配下です。モードの後に `--checkpoint /absolute/private/path/session.json` を指定すると別ファイルを選べます。チェックポイントにはデバイス、プリセット名、サーバー/プリセットのリビジョンのハッシュ、完了したステップ名、保留中のランダム操作キー 1 つと期限を保存します。設定値は含めません。ファイルは fsync 後にアトミックに置換され、同時に動く CLI の書き込みに対してロックされます。対応するローカルプリセットを利用可能にしておいてください。Unix ではチェックポイントのディレクトリを非公開（0700）、通常ファイルを 0600 にする必要があります。Windows では継承 ACL を CLI 実行ユーザーに制限してください。Unix のモードビットは Windows のアクセス権を強制しません。

`resume` は同じサーバー、デバイス、プリセットのリビジョンを検証してから、保留中の結果を読みます。保存された応答があれば即座に再確認できます。結果が不確かな場合、前のコマンド期限、リクエストタイムアウト、わずかな余裕時間が経過するまで変更送信を止めます。その期間後に `resume` を再実行してください。完了済みステップも保存設定を再確認するため、ユーザーの変更を過去の書き込み成功と取り違えません。相対コマンドを無条件に再実行することはありません。絶対指定のプロバイダー/プロファイル選択は保護待機後に繰り返せます。

新しい `apply` は既存のチェックポイントを上書きしません。`resume` で再利用するか、変更したプリセットには新しいファイルを選んでください。デバイスごとにチェックポイントは別です。すべてのプレイヤーにまたがるトランザクションやロールバックはなく、後のステップが失敗しても先に成功した書き込みは残ります。`verified` は比較時点のプレイヤー保存値を確認するもので、ネットワーク読み込み成功や物理的な映像・音声出力を確認するものではありません。

## 連携には必要な権限だけを付与する

HA、RF ブリッジ、エージェントには `admin_token` を配布せず、[通常のコマンドオペレーター](security-design.md#scoped-command-operators)を使います。正確なデバイス ID と `input.channel_up`、`input.channel_down` などの操作を許可してください。このプリセット手順の設定操作には、`settings_compare`、`provider`、`profiles`、`status`、`profile`、`profile_settings`、`provider_settings` の明示的な権限が必要です。引き継ぎには送信元の `playback_handoff_export` と受信先の `playback_handoff_import` が必要です。オペレーター項目は列挙されたデバイスと操作を組み合わせます。同じ操作を全デバイスに適用してはいけない場合は、別のオペレーター ID を使ってください。トークンと診断への同意は別の権限のままです。

オペレーターはプレイヤーのキューのポーリング、プレイヤー応答の提出、全デバイスの一覧取得、管理者デバッグ出力の読み取り、他のオペレーターの結果の読み取りを行えません。操作範囲を狭めると、その保存済み操作の読み取りもできなくなります。オペレーターを削除して再起動すると認証情報が失効します。古いジャーナルレコードが残っている間、その ID を別の主体に再利用しないでください。

## 別のプレイヤーで続ける

```sh
ott handoff living-room bedroom
```

送信元はバージョン 1 のライブ/アーカイブ参照をエクスポートします。内容はプロバイダー、不透明なソース識別情報、不透明な項目フィンガープリント `channel-v1:<8 hex>.<8 hex>`、アーカイブの場合は現在の番組時刻です。生のプロバイダー ID とストリーム URL はローカルに残ります。受信先には**同じソース/アカウントの設定**が必要で、自分のカタログから一意に一致する項目を解決し、自分のローカル認証情報で新しい URL を取得します。コントローラーは再生可能な URL をコピーせず、受信先の認証情報も変えません。送信元は再生を続けます。CLI の成功は受信先がリクエストを送信したことを意味し、物理画面やスピーカーの確認ではありません。

バージョン 1 はプレイヤーが必要な識別情報を提供できる場合、`m3u`、`xtream`、`stalker` のライブ/アーカイブカタログをサポートします。不明または曖昧な項目、ソースの不一致、ローカルポリシーによる拒否は、名前による代替検索をせず失敗します。VOD/シリーズ、異なるアカウント間のマッピング、送信元の一時停止、履歴/お気に入りの継続同期は、この初版の契約に含みません。

## 検証と復旧の演習

`go test -race ./...`、`go vet ./...`、`python3 -m unittest discover -s tests` を実行します。テストは、書き込み成功後の応答喪失、再開前の厳密な比較、設定のずれ、不明な操作の期限、2 つの書き込みプロセス、破損ジャーナル、オペレーター間の読み取り、不正な引き継ぎペイロードを網羅します。ステージング用プレイヤーではさらに、設定保存後に CLI を中断し、コントローラーを再起動して再開・検証し、一致するソースと不一致のソース間の引き継ぎを試してください。映像・音声は別途確認します。

現在の FOSS 作業コピーと開発依存関係をインストール済みの環境で、リポジトリをまたぐローカル検証を行うには、次を実行します。

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

スモークテストはこの Go サーバーをビルドし、FOSS の TypeScript プロジェクト全体をコンパイルして、実際のトランスポート、コマンドハンドラー、ソース識別、同梱プレイリストコーデックを Python CLI に対して実行します。対応する 3 プロバイダーすべての厳密な設定、不一致、異なるローカル行 ID でのライブ引き継ぎ、アーカイブのシークオフセット、URL 解決失敗、厳密な通信形式検証を確認します。テスト用代替物はストレージ、プロバイダーポリシー、再生ホストのインターフェースだけで、ブラウザー、物理メディア出力、実アカウントへのアクセスは検証しません。ループバックにバインドし、非公開の一時ファイルを使い、ストリーム URL には接続せず、終了時にファイルとサーバーを片付けます。

</div>
