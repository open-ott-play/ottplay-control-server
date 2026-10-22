# Android 平板：Plex 嚴格資訊站與啟動

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB 指南](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB 管理器](android-usb-manager.md)

專用平板需要分別驗證三項設定：內容佇列、OTT 嚴格資訊站原則和 Android 啟動。安裝 APK 或驗證螢幕關閉與恢復，不能證明作業系統重新啟動後會啟動應用程式。

## 識別與安裝

1. 記錄實際平板、OTT 別名/UUID、USB 管理器別名、序號、型號、Android ID 和 SDK。OTT 與 USB 別名可能不同；不要僅憑別名推斷身分，也不要重用其他平板的權杖。
2. 使用相容該 SDK 的官方 APK 變體，驗證發行 SHA-256 及套件/簽章身分，以 `install -r` 安裝並保留播放器資料。若 USB 由[共用管理器](android-usb-manager.md)管理，請使用它，不要啟動競爭的 ADB 伺服器或執行未明確指定裝置的 `adb` 命令。
3. 使用播放器自己的控制器憑證配對，並驗證 `ott TABLET`。載入所需的具名預設集，執行 `ott TABLET load PRESET verify`。家用網路預設集不能與開發電腦的 localhost 預設集互換。

## 從平板驗證 Plex

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

驗證媒體庫 ID、標題和項目數。管理員電腦要求成功，不能證明平板連通。失敗時，檢查平板儲存的位址、路由、TLS 支援及憑證。優先使用伺服器有效的 HTTPS 端點。若舊韌體必須使用私人 LAN HTTP，僅限此安裝使用，記錄權杖和媒體未加密傳輸的事實，不要改寫共用預設集或全域停用憑證驗證。切勿在診斷或文件公開權杖或私人媒體 URL。

變更 Plex 伺服器位址會清除所選媒體庫根目錄。選擇 **Plex** 供應商，開啟 **Plex settings → Folders**，勾選目標媒體庫列（不只是 **Browse folders**），選擇 **Ok**，再選 **Save and open library**。播放器 `1.1.58-beta.19` 中，僅有伺服器憑證可能開啟資料夾選擇器，而 `ready` 仍為 false；執行佇列命令前完成資料夾選擇。不要只為取得 `ready: true` 而改用 M3U：Plex 佇列雖可能在那裡播放，但 `kiosk on --strict` 可能儲存沒有繫結媒體的等待中 M3U 原則，它無法鎖定或還原 Plex 佇列。

## 啟動並鎖定佇列

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

有序佇列使用 `plex library 'LIBRARY NAME'`，而非 `plex shuffle`。Shuffle 只決定一次順序，資訊站重複該順序。確認目標項目、`enabled: true`、`strict: true`，以及原則繫結的是目標 Plex 媒體佇列。沒有頻道或媒體的 `waiting` 原則不算驗收通過的資訊站。至少觀察兩次媒體取樣，確認播放位置/影格數增加，並檢查畫面。只有回條或 `playing` 標籤而無進展並不足夠。測試項目結束後的自然切換；若無法實際測試完整循環，明確記錄該限制。

嚴格資訊站控制播放器介面。它並非 Android 裝置擁有者註冊，也無法證明 Home、Recents、通知和 Settings 已遭封鎖。保留現有系統啟動器和有人監督的維護途徑。

## 單獨啟用啟動功能

建置並驗證 [OTT Home 0.2.0](../native/android-home/README.md)，保留現有更新簽章金鑰。在 `play.ott.foss` 旁安裝 `play.ott.home`。開啟 `play.ott.home/.StartupActivity`，啟用 **Start OTT after reboot**。此選項預設關閉，不改變官方播放器二進位檔、OTA 簽章、設定檔資料或 Home 按鈕指派。

韌體提供 Home 選擇器時，也可選擇 **OTT Home → Always**。沒有選擇器時（包括已測試的舊 Fire 韌體），開機啟動許可獨立提供啟動功能。已設定預設 Home 的安裝維持原行為。完整回復需停用兩種機制。

共用 USB 管理器的 `recover_on_boot` 是第三種獨立機制：需要 NAS 和 USB 線，只處理新啟動，不會持續重啟當機應用程式。它不能取代自主啟動。測試自主啟動時保持復原原則關閉，以免掩蓋伴隨應用程式未啟動的問題。遵守安全鎖定畫面要求，提示時正常解鎖。

## 驗收與交接

私下儲存證據，不在公開回條包含憑證：

- APK 版本、雜湊、實際裝置/OTT/USB 身分對應及所選預設集。
- Plex 媒體庫、項目數/順序、嚴格原則及最初持續推進的播放。
- 實際重新啟動作業系統前後的啟動 ID；之後為 `sys.boot_completed=1`。
- 從重新啟動到觀察期間，沒有手動啟動播放器或 USB 復原。
- OTT 前景活動、控制器重新連接、佇列/嚴格原則還原及播放進展。啟動接收器的 `requested` 結果只表示已發出要求。
- 單獨測試電源按鈕：螢幕關閉、音訊停止、正常解鎖和恢復。
- 準確限制：需要安全解鎖、USB 輔助復原、未測試的實體離開途徑、串流錯誤或記憶體耗盡。不要將這些情況稱為完全驗收通過。

交付下一台平板前，重複相同檢查清單，不要複製上一台裝置的成功結論。舊 Fire 平板可能因廠商原生 HLS 當機而需要用 `hls.js` 播放 M3U；這不能證明 Plex 播放穩定。

## 維護與回復

```sh
ott TABLET kiosk off
```

開啟 **OTT Startup**，停用 **Start OTT after reboot**，若變更過 Home 選擇則還原原設定。必要時透過已驗證的 USB 傳輸只解除安裝 `play.ott.home`，保留 `play.ott.foss` 及資料。停用啟動不會變更佇列，也不會自動關閉資訊站。
