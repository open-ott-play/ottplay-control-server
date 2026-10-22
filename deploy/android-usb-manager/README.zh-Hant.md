# Android USB 傳輸映像

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

本目錄包含共用管理器傳輸容器的部署範本和 [`Dockerfile`](Dockerfile)。Python 管理器在 USB 主機上執行。映像提供 ADB、Python 及原子維護配接器使用的工具；不包含第二套裝置登錄表或復原常駐程式。佈建、命令、移轉、復原限制及回復見[操作指南](../../docs/android-usb-manager.md)。

沒有 Git 工作複本時，從同一[發行版本](https://github.com/open-ott-play/ottplay-control-server/releases)下載 `ottplay-control-server-deployment.tar.gz` 和 `release-manifest.json`。驗證封存摘要，依照[取得部署套件](../../docs/android-usb-manager.md#get-the-deployment-package)解壓至新目錄。套件包含此建置配方及 `tools/` 下的六個主機端/遠端指令碼；請保留目錄結構。包括 `v0.1.5-beta.6` 在內的舊套件缺少這些指令碼，需改用對應的原始碼封存。套件不含已建置的 USB 傳輸映像。控制伺服器的 OCI 發行資產另有用途。

## 原始碼與產物來源

基礎映像為 `python:3.12.14-slim-trixie`，固定至 `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`。基礎映像、APT 指令及以雜湊鎖定的 `websocket-client==1.9.0` 安裝已在 [PR 128 的 b4181bf 修訂](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager)中審查。保留原指令文字以重用現有建置快取。選用的 WebSocket 套件支援既有的通用維護用戶端；移轉後的 CDP 配接器也可只使用 Python 標準函式庫。

此配方在安裝鎖定的 wheel 後刪除已安裝的 `pip` 套件，排除 PR 128 的管理器和復原程式碼，在前景啟動 ADB，並停用繼承的健康檢查。移除執行階段安裝器可縮小可用攻擊面，但不能證明弱點掃描沒有發現問題。

PR 128 審查過的產物為 `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`。它含有不同的進入點和不相容的心跳健康檢查。這是參考產物，而非此 Dockerfile 的建置結果。請勿將其映像 ID、原始碼出處或掃描結果歸屬於此配方。

APT 套件庫未固定至快照。因此，即使基礎摘要及 Python wheel 雜湊相同，未來的全新重建仍可能選用較新的系統套件。驗證建置資格時，記錄確切的原始碼修訂、建置器/平台、產生的映像 ID、套件版本及掃描結果。正式環境設定必須使用該已通過驗證的不可變映像 ID 或摘要。

## 建置時不干擾忙碌的 USB 主機

優先在獨立建置器上建置及掃描。目標主機只需經過驗證的映像，不必下載套件或具備建置工具鏈。

重用準備好的本機建置器前，先確認確切的基礎映像可用，且 APT 與 wheel 安裝層已快取。僅載入 PR 128 的最終映像無法證明任一條件。在儲存庫根目錄或解壓後的部署目錄執行以下刻意離線的嘗試：

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` 適用於建置步驟，並非概括保證建置器解析基礎映像時絕不連線至登錄服務。受限主機使用本流程前，確認建置器會在本機解析基礎映像。若缺少 APT 或 wheel 層，請停止嘗試，不要默默允許在 USB 主機上透過網路重建。

BuildKit 將建置步驟的網路模式納入快取鍵。因此，以預設網路模式建立的快取可能無法命中上述離線嘗試。確認確切的基礎映像及指令鏈後才能重用；若任一套件安裝步驟實際執行而非顯示 `CACHED`，請停止。最後刪除 `pip` 的步驟明確使用 `RUN --network=none`；即使先前的快取步驟使用原網路模式，也不需要網路。

變更正式環境設定前，檢查 Python 與 ADB 版本，掃描確切的產生映像，驗證二進位檔案傳輸及 APK 暫存，再執行指南中的裝置身分、金鑰授權、睡眠及開機驗收檢查。必須測試 ADB 34 與舊 Android 裝置的通訊；僅憑版本號無法證明與 KitKat 或 Fire 相容。在不再需要回復之前，保留目前映像及私有部署備份。

## 執行階段資源預算

由主機管理器而非此 Dockerfile 設定 256 MiB 記憶體、記憶體加交換空間共 256 MiB、CPU shares 128，不設 CPU 或 PID 硬性配額。它強制 `--no-healthcheck`、`--network none`、唯讀根檔案系統、移除 capabilities 及精確 USB 節點對應。`/tmp` 是有限制的 tmpfs，消耗相同記憶體預算。暫存配接器每次寫入 1 MiB，單一承載上限為 128 MiB；各操作共用管理器鎖定。

Docker 文件說明了[記憶體、交換空間及 CPU shares 控制](https://docs.docker.com/engine/containers/resource_constraints/)與 [`--no-healthcheck` 選項](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck)的區別。
