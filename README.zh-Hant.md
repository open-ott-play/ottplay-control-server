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

[文件 / Wiki](docs/wiki/Home.zh-Hant.md)

這是一個可自行託管的小型伺服器，用於向 OTT-play 播放器傳送遠端命令。播放器主動發出 HTTP 請求，因此電視不必開放監聽連接埠。每個播放器都有自己的佇列和存取權杖；提交命令使用獨立的管理員權杖。

可選的[可靠控制](docs/reliable-control.md)提供限定權限範圍的整合認證資訊、容量受限的持久命令結果、可恢復執行且精確驗證設定的 Local/Home 套用設定，以及明確的直播/回看播放交接。這些功能要求控制器和 CLI 使用包含本指南的版本。精確設定驗證和播放交接還要求更新播放器。下方固定的舊安裝版本不包含這些功能。[架構圖](docs/assets/reliable-control.svg)說明設定、認證資訊和操作狀態分別保留在哪裡。

伺服器以單一可移植 Go 執行檔執行，並為可選的服務發現使用固定版本的 DNS 函式庫。發行包覆蓋 Linux amd64、arm64 和 ARMv7，macOS Intel 和 Apple Silicon，以及 Windows amd64 和 arm64。容器映像支援 Linux amd64 和 arm64。Kubernetes 和 k3s 使用附帶的 Helm chart。

## 啟動伺服器

從[發行頁面](https://github.com/open-ott-play/ottplay-control-server/releases)下載匹配的壓縮檔，按照發行清單驗證後解壓縮。在 Windows 上，將以下命令中的程式名改為 `ottplay-control-server.exe`。

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` 在新檔案中生成隨機且互不相同的認證資訊；在 Unix 上，該檔案僅允許擁有者存取。它拒絕覆蓋現有檔案或符號連結，且從不列印認證資訊。請妥善保護此檔案；在 Windows 上，將 ACL 限制為執行伺服器的帳戶。`config.example.json` 說明各欄位，並有意使用無效的預留權杖。

預設位址是 `0.0.0.0:8081`，即所有 IPv4 介面，包括區域網路。使用伺服器的區域網路位址和連接埠連線電視。現有設定會保留其明確指定的 `listen` 值；將其改為 `0.0.0.0:8081`，或使用 `--listen 0.0.0.0:8081`，即可開放原先僅監聽環回位址的安裝。將播放器**頁面的準確 origin** 新增到 `allowed_origins`，例如 `http://192.168.1.20:8443`。通訊協定、主機名稱和連接埠必須一致，不要附加路徑。origin 列表為空時，只允許非瀏覽器用戶端。設定修改在重新啟動後生效。裝置和管理員認證資訊仍是必需的。

對於可信區域網路中的舊電視瀏覽器，支援 HTTP。連線經過不可信網路時，請使用 HTTPS：`serve --config config.json --tls-cert server.crt --tls-key server.key`，或在反向代理處終止 TLS。HTTPS 播放器頁面不能在瀏覽器中連線純 HTTP 伺服器。原生應用程式仍遵循作業系統的傳輸策略；此設定不會放寬該策略。

直接提供 HTTPS 時，伺服器會在開放監聽前驗證所提供的憑證鏈：RSA 金鑰至少為 2048 位，ECDSA 金鑰至少為 224 位，也支援 Ed25519。升級前請更換金鑰長度不足的憑證。參見 [TLS 憑證策略及其範圍](docs/security-design.md#direct-https-certificate-policy)。

## 連線播放器

使用包含命令伺服器連線功能的播放器版本。開啟 **設定 → 遠端控制** 並輸入：

1. **伺服器位址：** 伺服器 IP 或主機名稱，可附帶連接埠，也可以是完整的 `http://` / `https://` 位址。僅填寫主機時，預設使用 HTTP 連接埠 8081。
2. **存取碼：** `devices` 中該播放器條目的 `token`，絕不能使用 `admin_token`。
3. **連線：** 啟用輪詢，並檢查同一對話方塊中顯示的狀態。

播放器只在目前安裝中儲存此連線，不會將其包含在設定備份或雲端移轉中。無需啟用獨立的本機 HTTP 監聽器。Web 瀏覽器需要獲准的 CORS origin；帶有 `Origin: null` 的封裝電視頁面可明確啟用 `allow_null_origin`，用於指定的裝置/初始化路由。這不會開放管理員或診斷操作員路由。

## 終端遙控

[帶圖示的 Android OTA 指南](docs/android-ota.md)區分普通 Capacitor Wi-Fi 更新（`ott DEVICE update --variant api22|full`，預設最新 beta；`--version` 選擇發行版本）與獨立的 root 代理程式。它說明已簽署的代理和 APK/helper 更新、恢復，以及已安裝結果的驗證。

[Android ADB 指南](docs/android-adb.md)介紹透過工作站或 Synology 存取 USB、螢幕和鍵盤控制、截圖、紀錄、APK 安裝及正常重新啟動，也涵蓋舊平板電腦的限制。

可選的 [DNS-SD 發現與配對](docs/discovery.md)讓播放器透過其網路域查詢控制器，並在執行 `ott approve NAME CODE` 後獲得獨立的裝置權杖。DNS 只儲存服務後設資料。不啟用發現功能，現有手動連線也能繼續工作。

使用 Python CLI 管理播放器別名、即時音量、頻道搜尋、目前 EPG、供應商選擇及受支援的供應商設定：參見 [CLI 指南](docs/cli.md)。伺服器和播放器都必須包含請求/回應擴充功能。

Python CLI 從原始碼工作副本安裝，與原生伺服器壓縮檔分開。[安裝和設定步驟](docs/cli.md#installation-and-connection)涵蓋 macOS/Linux 與 Windows、Python/PATH 設定以及必需的 CLI 模組。它們固定到經過審查的 0.1.1 開發提交，包含 workbench 和離線驗證；穩定版 CLI v0.1.0 和原生伺服器壓縮檔不包含這些命令。[更新或回復舊版 CLI](docs/cli.md#update-select-or-roll-back-the-cli)時，無需替換現有工作副本、啟動器或私有設定。本機啟動器可使用唯讀的[播放清單解析器](docs/cli.md#local-playlist-resolver)，在沒有控制器時搜尋頻道、目前節目和可播放的回看內容。安裝並設定後：

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

`a1` 是已註冊播放器的範例別名，不是計算機主機名稱。參見[註冊與配對](docs/cli.md#register-and-connect-a-player)、[所有命令及範例](docs/cli.md#commands)、[斷開連線和撤銷存取](docs/cli.md#disconnect-deregister-or-revoke-a-player)以及[疑難排解](docs/cli.md#troubleshooting)。

對於始終開啟的播放器，使用 [standby 和 go](docs/cli.md#stop-playback-while-keeping-the-player-online) 停止播放並在稍後喚醒，而不退出應用。`stop` 仍是 `standby` 的別名；`start` 和 `wake` 仍是 `go` 的別名。遠端控制必須保持連線，因此待機不意味著裝置完全沒有網路流量。`ott a1` 顯示播放器宣告的操作。命令被接受並不確認關閉或開始播放已完成。

[畫面比例命令](docs/cli.md#aspect-ratio-fit-and-fill)要求更新 CLI、控制器，並使用宣告 `aspect` 能力的播放器。收到確認後才應用更改；再次讀取 `aspect`，確認模式及是否已儲存。Fill 按比例裁剪，而不是拉伸畫面。

遠端[截圖](docs/cli.md#remote-screenshots)要求更新 CLI、控制器，並使用具有受支援捕獲介面卡的播放器。啟用播放器的遠端控制連線，即授權該控制器截圖和診斷；原生截圖不需要單獨的權限開關。瀏覽器仍要求透過本機來源選擇器選擇來源。圖片可能包含播放器設定、PIN 頁面和可見認證資訊，因此只應連線可信控制器。`ott a1` 報告可用性及需要選擇的來源。截圖與結構化遙測擷取彼此獨立。

要將所有符合某個標題的 VPortal 影片隨機排序並迴圈播放：

```sh
ott l vpr "wedding"
```

`vpr` 將完整選擇隨機排序一次，然後迴圈該佇列。再次執行可重新隨機排序。`vp` 按目錄順序播放匹配項；`vp --list` 列出它們而不改變播放。參見 [VPortal 命令](docs/cli.md#commands)。

使用播放器已儲存的 Plex 設定，按明確順序播放 Plex 內容：

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

CLI 預設最多等待 45 秒。寫成 `ott -t 45 l plex shuffle "Tom and Jerry"` 只是明確指定同樣的回應超時，並不限制影片播放時長。收集大型 Plex 媒體庫可能超過 10–15 秒的短超時。請將 `-t SECONDS`（或 `--timeout SECONDS`）放在播放器別名前。參見 [Plex 媒體庫超時](docs/cli.md#plex-libraries-by-name)，瞭解預覽範例以及播放請求超時後的處理方式。

Plex 佇列要求 CLI、控制器和播放器實現 `plex_queue`。`plex play --shuffle ID...` 將給定 ID 隨機排序一次。除非現有 kiosk 策略讓其迴圈，否則佇列在最後一項後停止。佇列存在時，普通的 `next` 和 `prev` 操作該佇列；`plex stop` 清空它。預覽絕不會切換供應商或替換目前播放。參見 [Plex 佇列命令](docs/cli.md#ordered-plex-queues)，瞭解限制和就緒要求。媒體庫查詢還要求更新後的播放器和控制器宣告 `plex_library`；它使用播放器已儲存的 Plex 連線，並拒絕不明確或不完整的選擇。參見[按名稱查詢 Plex 媒體庫](docs/cli.md#plex-libraries-by-name)。

### 儲存本機影片設定檔

在目前版本的播放器和控制器中，本機檔案/資料夾使用 `offline` 供應商：

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

請先在播放器上連線檔案/資料夾。儲存會記錄其設定和選中的影片，不會開始播放。匯出只傳輸描述；媒體及作業系統/瀏覽器的存取授權仍留在裝置上。其他安裝需要在本機繫結，載入也不意味著已確認播放。參見[本機影片設定檔](docs/cli.md#local-videos-profiles)，瞭解設定、能力、可移植格式限制和疑難排解。

### 跨設定檔儲存目前專案

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

收藏專案儲存在目標播放器上，正常重新啟動不會丟失。選擇收藏會載入儲存的供應商/設定檔並解析新連結；若來源已變或專案缺失，操作會失敗，不會替換成其他頻道或影片。CLI、控制器和播放器必須支援 `favorites`；`ott l` 顯示可用操作。參見[跨設定檔收藏](docs/cli.md#favorites-across-profiles)，瞭解別名、限制、確認語義和疑難排解。

### 載入已儲存的設定組合

將命名設定組合儲存在 `~/.config/ottplay-control/cli.json` 的私有 `presets` 部分。每組包含編號 M3U 設定檔（播放清單和回看小時數）、Plex 認證資訊，以及載入完成後要選擇的 M3U 設定檔。參見[設定範例](docs/cli.md#named-setups)。

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI 先驗證整組設定，再等待播放器確認後才傳送下一項更改。它處理供應商載入、儲存 Plex 和 M3U 槽位，並選擇指定的 M3U 設定檔。錯誤會停止序列並報告已確認的步驟；結果不確定的寫入絕不會自動重試。不需要額外的設定檔服務或重新啟動控制器。請保護此設定：其中包含供應商認證資訊。

## 傳送命令

本例從本機私有設定讀取管理員權杖，不將其放入 URL 或命令列引數：

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

其他命令可按編號或名稱選頻道、隨機選頻道、切換供應商、顯示訊息、在支援時修改 M3U 播放清單，或退出播放器。可用性取決於播放器平臺和目前供應商。參見 [API 契約](docs/api.md)。

在支援獨立 VPortal 供應商的播放器上，`ott a1 provider
vportal` 選擇其專屬的 15 個設定檔。使用 `profile N vportal LINK`、`profile N
name NAME`，或包含 `name` 與 `vportal` 的私有 `profile-config N FILE`，然後透過 `profile N` 選擇已設定的槽位。現有 M3U 設定檔保持獨立。`profiles` 報告後設資料，不暴露帳戶頁面連結或金鑰。

開始播放 VPortal 影片或劇集佇列後，`ott a1 kiosk on` 鎖定該選擇。播放器在重新載入後保留佇列和位置，並重新解析媒體 URL 以便恢復。使用 `kiosk off` 選擇其他標題或更改設定檔，再用 `kiosk on` 鎖定。kiosk 回條顯示標題和劇集數量；供應商請求和串流 URL 留在播放器上。

面向兒童播放時使用 `ott a1 kiosk on --strict`：本機點選會顯示影片底欄。更新的 core 播放器允許點選或拖動時間軸，在目前 VPortal/Plex 劇集中跳轉；其他本機控制仍鎖定。舊播放器、頻道 kiosk 和攔截時間軸手勢的封裝保留唯讀底欄。現有鎖定保持其目標。透過控制器執行 `ott a1 kiosk off` 解鎖。在 Android 網站中，防止透過系統退出還需要使用 PIN 單獨釘選應用程式；參見 [kiosk 模式](docs/cli.md#kiosk-mode)。

## 遠端診斷

先參閱[唯讀 workbench 快速入門](docs/workbench.md#quick-start)，瞭解 `doctor`、介面/媒體 `inspect`、操作回條查詢、證據包、`health`、Web/原生 `media-progress` 和離線 `report verify`。它使用已有的管理員 CLI 設定與播放器別名。原生通道用於另行部署的 Android 維護代理程式；Tauri 播放器使用 Web 檢查。報告區分解碼器進度與實際螢幕/聲音驗證。遇到能力缺失、通道不可用或證據不完整時，參見 [workbench 疑難排解](docs/workbench.md#troubleshooting)。

可選的[通訊協定 2 診斷](docs/remote-diagnostics.md)為每個執行環境提供明確啟用的工作階段，使用獨立且權限受限的操作員認證資訊，並限制結構化遙測的範圍。已啟用的播放器連線提供本機授權，無需再次詢問信任；每次擷取仍有受限的工作階段有效期。診斷控制輪詢獨立於現有命令佇列。

[診斷 CLI 與 MCP 指南](docs/diagnostics-cli.md)涵蓋安裝、操作員認證資訊、連線設定、每條診斷命令/工具、擷取和修復範例、執行環境撤銷及疑難排解。`ott diagnostics --help` 列出這一獨立命令族；普通播放器別名和管理員認證資訊不授予診斷存取權。

## 部署套件

- [Android 平板：Plex 嚴格 kiosk 與啟動](docs/android-kiosk-setup.md)
- [共享 Android USB 管理與啟動恢復](docs/android-usb-manager.md)
- [Docker / Compose、Kubernetes、k3s、systemd、macOS 和 Windows](docs/deployment.md)
- [設定、交付語義與限制](docs/api.md)
- [發行與驗證流程](docs/development.md)

佇列有容量限制，預設 60 秒後過期，儲存在記憶體中。重新啟動伺服器會丟棄待處理命令。請執行一個副本；獨立副本不共享佇列。播放器確認的是一次傳送嘗試，並非實際播放或硬體音量。網路重試在正在執行的播放器工作階段內去重，不跨應用程式重新啟動去重。

伺服器遵循 [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) 中可選 `local_proxy.py` 範例的命令 API，並使用分離的管理員/裝置認證資訊和確認交付。舊代理仍是獨立元件。本專案不代理媒體，也不改變播放器的 HLS、EPG 或供應商網路通訊。

## 授權條款

MIT。附帶發行工具的署名記錄在 `NOTICE` 和 `LICENSES/` 中。

## 專案維護

參見[貢獻和測試要求](CONTRIBUTING.md)、[安全報告策略](SECURITY.md)、[安全設計](docs/security-design.md)，以及 [OpenSSF 證據和剩餘標準](docs/openssf-evidence.md)。

目前播放器的 Plex 佇列最多接受 500 個 ID。使用 `ott f10 plex play --shuffle ID...` 隨機排序一次，再在已選 Plex 供應商時使用 `ott f10 kiosk on --strict` 迴圈並鎖定佇列。`ott f10 kiosk off` 解除鎖定。舊播放器仍採用其宣告的佇列上限。

## 發行候選版本的模糊測試

公開的發行候選版本對[模糊測試指南](docs/fuzzing.md)所述輸入邊界執行有限度的原生模糊測試。變異測試任務不在 PR、beta 或夜間建置中執行。指南涵蓋範圍、預算、證據和回歸問題重現。

## 社群與安全證據

參見[參與貢獻](CONTRIBUTING.md)、[治理](GOVERNANCE.md)、[行為準則](CODE_OF_CONDUCT.md)、[藍圖](ROADMAP.md)和 [OpenSSF 證據](docs/openssf-evidence.md)。證據索引區分受支援的安全設定、實際測試結果和剩餘的 Silver 要求。
