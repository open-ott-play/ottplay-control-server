# OTT-playコントローラーのドキュメント

<!-- language-navigation:start -->
<p dir="ltr">
<a href="Home.md" lang="en" dir="ltr">English</a> · <a href="Home.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="Home.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="Home.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="Home.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="Home.es.md" lang="es" dir="ltr">Español</a> · <a href="Home.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="Home.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="Home.he.md" lang="he" dir="rtl">עברית</a> · <a href="Home.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="Home.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="Home.it.md" lang="it" dir="ltr">Italiano</a> · <a href="Home.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="Home.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="Home.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="Home.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="Home.pt.md" lang="pt" dir="ltr">Português</a> · <a href="Home.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="Home.th.md" lang="th" dir="ltr">ไทย</a> · <a href="Home.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="Home.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="Home.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="Home.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="Home.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

このバージョン管理されたドキュメント索引は、Wiki形式のナビゲーションの元データです。編集してもGitHub Wikiのページが自動公開されることはありません。

- [プレーヤーの起動と接続](../../README.ja.md)
- [ターミナルCLI：インストールとコマンドリファレンス](../cli.md)
- [信頼性の高い制御：ジャーナル、Local/Homeの再開、厳密な検証と引き継ぎ](../reliable-control.ja.md)
- [通信プロトコル/APIの契約](../api.md)
- [認証、オペレーターの権限範囲と通信の安全性](../security-design.md)
- [実行時診断](../remote-diagnostics.md)
- [共有Android USBマネージャー：インストール、復旧と移行](../android-usb-manager.ja.md)
- [ワークベンチと保存済みの証跡](../workbench.md)
- [デプロイと永続ストレージ](../deployment.md)
- [開発とテスト](../development.md)
- [リリース手順](../release-workflow.md)

コントローラーは制限の定められたコマンドを配信します。プロバイダーの認証情報、カタログの識別情報、再生ポリシーはプレーヤー側が管理します。リクエスト成功の受領通知は、実機の映像と音声の確認に代わるものではありません。
