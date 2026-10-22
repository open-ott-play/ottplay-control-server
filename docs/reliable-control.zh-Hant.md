<div lang="zh-Hant" dir="ltr">

# 可靠控制與播放移交

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[文件索引](wiki/Home.md)

這些功能要求使用包含本指南之修訂版中的控制器與 CLI。精確比較設定及播放移交還要求更新播放器。舊版發行封存檔和安裝指南中的歷史 CLI 修訂版不會自動取得這些功能。請**一同安裝所有 `cli/*.py` 檔案**，包括 `preset_session.py`；保留私有設定。

![控制、檢查點與移交流程](assets/reliable-control.svg)

## 啟用有界的持續儲存結果

將這個選用區塊加入私有伺服器設定，接著驗證設定並重新啟動伺服器。選擇本機持續性檔案系統上的絕對路徑，並確保服務帳戶可寫入。在 Unix 上，目錄必須私有（0700）；現有的日誌與鎖定檔必須是一般私有檔案（0600）。在 Windows 上，將目錄 ACL 限制為服務帳戶。一個日誌只能由一個程序持有。

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

伺服器以 0700 模式建立缺少的目錄。限制為 1–4096 筆記錄、60–86400 秒，以及磁碟上的 4 MiB。保留期限必須涵蓋 `command_ttl_seconds`。日誌已滿時會拒絕新的帶鍵操作，而不會移除仍有效的記錄。檔案損壞、Unix 目錄不安全、存在其他寫入者或持續儲存寫入失敗時，均以拒絕方式安全失敗。預設仍使用原有的記憶體命令處理路徑。

隨附的 systemd 單元以 0700 模式建立 `/var/lib/ottplay-control`。對於 Compose，準備一個屬於 UID 65532 的主機目錄，將 `CONTROL_SERVER_STATE` 設為其絕對路徑，並搭配使用 `deploy/compose.yml` 和 `deploy/compose.journal.yml`。後者僅增加可寫入的狀態掛載；根檔案系統仍為唯讀。對於 Helm，單獨準備持續性磁碟區宣告並設定 `journalExistingClaim=CLAIM_NAME`。宣告掛載於 `/var/lib/ottplay-control`；請使用上面所示的私有子目錄。Chart 保留單一複本、`Recreate` 和 `fsGroupChangePolicy: OnRootMismatch`，避免重新掛載時遞迴放寬已建立之私有目錄的權限。確認所選儲存驅動程式支援這些擁有權語意。Chart 不會建立或刪除該宣告，啟用掛載也不會啟用日誌：日誌仍由私有伺服器設定控制。

對於 `POST /api/requests`，在 `action` 和 `params` 旁加入 `idempotency_key`。舊版命令 POST 改為接受 `Idempotency-Key`。鍵由 16–80 個 ASCII 字母、數字、底線或連字號組成；為每個意圖產生新的隨機鍵。同一擁有者/鍵搭配同一裝置、操作及正規化 JSON 載荷，會傳回原始 ID，不會再次排入佇列。更改載荷會傳回 409。鍵依操作員 ID（或管理員）劃分範圍，而非依裝置。保留期限結束後，該鍵可能被當作新意圖接受：用戶端不得在此時間窗口之外重試舊意圖。

`GET /api/operations?device_id=DEVICE` 回報日誌是否啟用、限制與命令 TTL。加入 `&key=KEY` 可讀取某項操作：

- `pending`：已接受；尚無保留的回應。
- `responded`：已收到通過驗證的播放器回應；`result_status` 為 `ok`、`rejected` 或 `unsupported`。原始結果本文仍依正常規則到期。
- `acknowledged`：舊版消費端已確認命令。這不證明可見播放或設定變更已儲存。
- `unknown`：期限已過，或伺服器在保留回應前重新啟動。伺服器重新啟動後**絕不重播**此命令。

重複的帶鍵 POST 傳回 `status: retained`、原始 ID 與狀態。一般的新 POST 仍傳回 `status: queued`。日誌記錄僅包含擁有者、裝置、操作、隨機 ID、載荷摘要、狀態及期限。不包含命令載荷、供應商 URL、權杖、螢幕擷取畫面或回應本文。等待中的命令不會持續儲存。這是有界去重與結果追蹤，不是恰好一次執行、持續性工作佇列或多伺服器高可用性。

## 規劃、套用、恢復與驗證 Local/Home

保留既有的 `ott PLAYER load PRESET` 行為。新流程如下：

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` 離線執行：驗證本機預設組態，列出設定槽位與期望的作用中設定檔，但不印出其內容。它不檢查播放器。`verify` 執行精確的唯讀比較，並檢查所選 M3U 設定檔。如有差異或不支援比較，就傳回非零結束代碼。

`apply` 需要伺服器日誌。它建立私有檢查點，並在首次修改之前比較每個已設定的供應商。每次儲存都透過 `settings_compare` 將已儲存位元組與期望值比較；相符時略過寫入。最終驗證涵蓋 Plex、所有 M3U 槽位、選用的 Stalker 槽位及所選 M3U 設定檔。比較傳回布林值，絕不傳回已儲存的秘密。

預設檢查點位於 `~/.config/ottplay-control/checkpoints/` 下。在模式後使用 `--checkpoint /absolute/private/path/session.json` 可選擇其他檔案。檢查點包含裝置、預設組態名稱、伺服器/預設組態修訂版雜湊、已完成步驟名稱，以及一個等待中操作的隨機鍵與期限。不包含設定值。檔案在 fsync 後以不可分割方式取代，並透過鎖定防止同時 CLI 寫入。請保留相符的本機預設組態。在 Unix 上，檢查點目錄必須私有（0700），一般檔案權限為 0600。在 Windows 上，將檢查點目錄繼承的 ACL 限制為執行 CLI 的使用者；Unix 模式位元無法實施 Windows 存取權限。

`resume` 驗證同一伺服器、裝置及預設組態修訂版，然後讀取等待中的結果。保留的回應允許立即重新檢查。不確定的結果會阻止傳送修改，直到前一命令期限、要求逾時與一小段餘裕均已過去。過了該窗口後再次執行 `resume`。它甚至會重新檢查已完成步驟的已儲存設定，避免把使用者變更誤認為先前成功寫入。它絕不會盲目重播相對命令。在保護等待期之後，可以重複絕對的供應商/設定檔選擇。

新的 `apply` 絕不會覆寫現有檢查點。以 `resume` 重用它，或為變更後的預設組態選擇新檔案。不同裝置有各自的檢查點。不存在跨所有播放器的交易或復原；後續步驟失敗時，先前成功的寫入仍然保留。`verified` 只確認比較時播放器儲存的值，不代表網路載入成功或實際視訊/音訊輸出正常。

## 僅向整合授予必要權限

為 HA、RF 橋接或代理程式使用[一般命令操作員](security-design.md#scoped-command-operators)，而不是散發 `admin_token`。允許精確的裝置 ID 及 `input.channel_up`、`input.channel_down` 等操作。此預設組態流程中的設定操作需要明確授予 `settings_compare`、`provider`、`profiles`、`status`、`profile`、`profile_settings` 和 `provider_settings` 權限。移交需要來源上的 `playback_handoff_export` 及目標上的 `playback_handoff_import`。操作員項目組合其列出的裝置與操作；如果同一操作不應適用於每個列出裝置，請使用獨立操作員身分。權杖與診斷同意仍是獨立權限。

操作員不能輪詢播放器佇列、提交播放器回應、列出全部裝置、讀取管理員偵錯輸出或其他操作員的結果。縮小某操作的權限範圍也會阻止讀取其保留記錄。刪除操作員並重新啟動會撤銷其認證。舊日誌記錄仍存在時，不要將操作員 ID 重用於另一主體。

## 在另一播放器上繼續

```sh
ott handoff living-room bedroom
```

來源匯出版本 1 的直播/封存參照：供應商、不透明的來源身分、不透明的 `channel-v1:<8 hex>.<8 hex>` 項目指紋，以及封存中的目前節目時間。原始供應商 ID 和串流 URL 留在本機。目標必須擁有**同一已設定的來源/帳戶**，在自己的目錄中解析出唯一相符項目，並使用自己的本機認證取得新 URL。控制器既不複製可播放 URL，也不修改目標認證。來源繼續播放。CLI 成功表示目標已派送要求，不代表其實體螢幕或喇叭已驗證。

當播放器能提供所需身分時，版本 1 支援 `m3u`、`xtream` 和 `stalker` 的直播/封存目錄。未知或不明確的項目、來源不相符及本機原則拒絕都會失敗，不會退回按名稱比對。VOD/影集、不同帳戶間對應、暫停來源，以及持續同步歷程記錄/收藏，不屬於此初版約定。

## 驗證與復原演練

執行 `go test -race ./...`、`go vet ./...` 和 `python3 -m unittest discover -s tests`。測試涵蓋成功寫入後回應遺失、恢復前精確比較、設定漂移、未知操作期限、兩個寫入者、日誌損壞、跨操作員讀取及格式錯誤的移交載荷。在預備環境播放器上，還應在儲存設定後中斷 CLI、重新啟動控制器、恢復並驗證；測試相符和不相符來源間的移交。單獨確認視訊/音訊。

進行本機跨儲存庫驗證時，使用目前 FOSS 工作複本並安裝其開發相依套件，然後執行：

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

煙霧測試建置此 Go 伺服器、編譯完整 FOSS TypeScript 專案，並將其真實傳輸層、命令處理常式、來源身分及隨附的播放清單編解碼器與 Python CLI 聯合執行。它檢查三個受支援供應商的精確設定、差異、使用不同本機列 ID 的直播移交、封存定位偏移、URL 解析失敗及嚴格的傳輸格式驗證。只有儲存、供應商原則及播放宿主介面是測試替身；它不驗證瀏覽器、實際媒體輸出或真實帳戶存取。它繫結回送介面，使用私有暫存檔，不存取串流 URL，並在結束時清理自己的檔案與伺服器。

</div>
