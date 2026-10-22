<div lang="zh-Hant" dir="ltr">

# Android OTA：一般播放器與獨立的 root 代理程式

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

對於 **k8a/fire8 和其他 Capacitor 播放器**，請使用一般播放器的 Wi-Fi 更新器，而非 root 代理程式命令：

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

不指定 `--version` 時選取最新發佈的 beta。Android 7+ 使用 `full` 而非 `api22`。CLI 確定官方版本、檢查資訊清單、準備 APK 並輪詢狀態，然後只要求安裝一次。Android 可能要求在平板確認。維護前應明確關閉資訊站並解鎖受保護設定；命令不會改變這些原則。詳見[完整 CLI 流程](cli.md#capacitor-apk-updates-over-wi-fi)。

這需要預先安裝支援 `AppUpdate` 的 APK、Wi-Fi，以及執行中且已連接控制器的播放器。正常後續更新不需要 USB，但無法連線離線平板或停止的 WebView。首次安裝、更新器不可用、應用程式損壞或網路遺失，仍可能需要本機復原。這不表示提供自動發佈排程器或通用無訊息安裝程式。

本指南其餘部分描述的是**另一個供 a1 使用的 root 維護代理程式**，僅限已佈建的 Matricom/API19 `play.ott.kitkat` 安裝。它並未安裝在一般 Fire/Capacitor 裝置，也不能直接用於這些裝置。

完成一次性的 root USB 初始化後，獨立 Android 代理程式可透過主動建立的 HTTPS 連線接收簽章更新。自我更新不依賴正常運作的 WebView。0.1.2 代理程式亦支援協調更新 APK 與系統觸控輔助程式的組合套件，保存復原檔案並在提交前驗證。

本指南解釋完整流程。精確的資訊清單欄位、限制及硬體需求見[原生代理程式參考](../native/android-agent/README.md#signed-updates-over-wi-fi)。

## 更新哪些內容

![OTA 架構：經審查的發佈位元組經過簽章，以直接 HTTPS 發佈，由獨立代理程式驗證，安裝後再檢查。Web 部署分開進行。](images/ota-architecture.svg)

- **原生代理程式：**`kind: agent` 取代自身 ARMv7 二進位檔。即使 OTT 停用且使用另一個 HOME 應用程式，此路徑仍可運作。
- **僅 APK：**`kind: apk` 取代包裝應用程式並保留資料。OTT 必須啟用且位於前景。候選套件內嵌的輔助程式必須與已安裝的系統輔助程式完全一致。
- **APK 與觸控輔助程式：**`kind: bundle` 成對更新兩個元件。舊代理程式必須先收到支援組合套件的代理程式單獨更新。
- **託管播放器：**在既有來源部署已驗證的 Web 產物，重新載入並檢查執行中的播放器。APK 更新不會部署託管的 JavaScript。見[發佈工作流程](release-workflow.md)。
- **控制器與 CLI：**分別部署控制器並安裝完整 CLI 模組。更新其中任一部分，都不會取代平板上的程式碼。

韌體、核心、系統 WebView、root 中介程式與啟動指令碼不屬於此 OTA 格式。[原生參考](../native/android-agent/README.md#bootstrap)說明初始化與復原邊界。支援的 OTA 操作不需要網路 ADB 或任意遠端 shell。

## 從已審查版本到確認安裝

1. **選擇已驗證的位元組。** CI 通過後合併經審查的變更。下載目標發佈產物，驗證原始碼修訂、不可變發佈證據及校驗和。從原生封存擷取 `agent` 二進位檔；封存本身不是可安裝的代理程式承載。
2. **準備與簽章。** 使用初始化時已固定公鑰的既有 Ed25519 私密金鑰。保密該金鑰和裝置憑證。
3. **發佈並檢查傳輸。** 僅將承載和 `signed.json` 發佈至不可變 HTTPS 路徑。兩者必須以 HTTP 200 直接傳回位元組；更新器拒絕重新導向。執行封裝工具的 `verify` 命令。
4. **傳送一次更新。** 記錄要求 ID，在收到回條後檢查狀態。回應遺失或不確定，不是盲目重複操作的理由。
5. **確認效果。** 將新讀取的安裝雜湊與版本和準備的發佈版本比較。執行中的播放器還應檢查資訊站目標、佇列和持續推進的播放。實際影像與聲音需要觀察。

代理程式發佈範例；請替換為選定版本與真實路徑：

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

`ENVELOPE_SHA256` 是封裝工具的 `envelope_sha256`，不是承載雜湊。`accepted` 表示要求已接納，不代表安裝成功。自我取代可能使舊操作回條變為 `unknown`；新的執行階段、預期代理程式版本和相符的安裝雜湊提供安裝證據。[CLI 參考](cli.md#independent-android-agent)說明獨立原生路由與回條處理。

## APK/輔助程式交易如何復原

![APK 與輔助程式交易：預檢、保存舊元件對、套用、驗證、提交；失敗時還原舊元件對，復原未完成時繼續阻止應用程式變更。](images/ota-transaction.svg)

組合套件恰好包含 `app.apk` 和 `ott-touch-guard`。封裝工具可用 `--kind bundle --apk` 直接從已簽章的包裝 APK 建立它。工具擷取內嵌輔助程式，確保交付的兩份複本相符。見[組合套件命令與需求](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)。

修改檔案前，代理程式要求已安裝的元件對相符、空間足夠、前景 OTT 可回應、系統分割區唯讀，並宣告支援 APK 降級。對於鎖定的資訊站，它驗證相同的電視頻道 ID 或有序 VPortal ID；只有佇列長度相同並不足夠。不支援的鎖定佇列會被拒絕。有意停用 OTT 的情況也會被拒絕，以保留使用者目前的 HOME 應用程式。

停止應用程式前會持久保存復原檔案及待完成交易。代理程式等待應用程式和舊輔助程式停止，以不可分割方式取代輔助程式，將 `/system` 恢復唯讀，安裝 APK 並檢查結果。套用或驗證失敗會觸發舊元件對的還原。啟動復原只還原檔案，不啟動 OTT 或接管 HOME。

若復原無法完成，`updates.recovery_failed` 為 true。診斷和已簽章的代理程式單獨修復仍可使用；應用程式變更與播放看門狗被封鎖。不得刪除已保存的交易來強行安裝。二進位復原不會復原應用程式資料移轉，韌體仍可能拒絕降級。見[復原限制](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)。

## 結果與驗收記錄

![驗證圖表：488 個 CLI 測試、576 個發佈工具測試及 6 個 OTA 封裝器測試；參考平板執行已驗證的新代理程式，APK/輔助程式的實機驗收仍待完成。](images/ota-validation.svg)

2026 年 10 月 14 日實作稽核通過了 488 個 CLI 測試（略過一個）、576 個發佈工具測試、六個 OTA 封裝器測試、原生 Go 競爭/靜態檢查及 ARMv7 建置。這些數字描述測試套件大小，不是涵蓋率或效能。故障注入測試涵蓋無效封存、中斷交易、部分還原、目標變更和復原隔離。獨立審查還找出並修正不足的佇列身分檢查，以及主機/裝置 APK 與輔助程式驗證不一致的問題。

截至 2026 年 10 月 15 日的檢查結果：

- OTA 實作已合併至[控制器 PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104)，並包含於 [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4)。
- 目前及上一版簽章代理程式套件已發佈至 [OTA 交付主機](https://android-updates.ottplay.here.now/)。承載及簽章封裝位元組已透過直接 HTTPS 下載並成功比較。
- 參考平板經獨立 OTA 通道從 `0.1.1-beta.21` 更新至 `0.1.2-beta.4`。執行中代理程式版本和已安裝 SHA-256 與選定版本一致；執行階段改變，但 OS 啟動 ID 未變。OTT 仍無執行中的應用程式行程，也未傳送應用程式啟動或 HOME 選擇操作。
- 原生狀態回報系統觸控輔助程式雜湊、無待處理組合套件且無復原失敗。APK 中繼資料不可用，因此此觀察不能確定已安裝 APK 的版本或雜湊。
- APK/輔助程式協調交易已通過主機端驗證；參考平板上的實際安裝、降級復原、影像和音訊仍未驗收。

每次後續推出更新時，保留發佈原始碼/校驗和、準備的簽章封裝雜湊、要求 ID、前後原生狀態及獨立實機驗收結果。下載成功或命令回條不能取代這些觀察。

</div>
