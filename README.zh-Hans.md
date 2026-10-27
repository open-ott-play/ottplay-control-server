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

[文档 / Wiki](docs/wiki/Home.zh-Hans.md)

这是一个可自行托管的小型服务器，用于向 OTT-play 播放器发送远程命令。播放器主动发出 HTTP 请求，因此电视不必开放监听端口。每个播放器都有自己的队列和访问令牌；提交命令使用独立的管理员令牌。

可选的[可靠控制](docs/reliable-control.md)提供限定权限范围的集成凭据、容量受限的持久命令结果、可恢复执行且精确验证设置的 Local/Home 配置应用，以及明确的直播/回看播放交接。这些功能要求控制器和 CLI 使用包含本指南的版本。精确设置验证和播放交接还要求更新播放器。下方固定的旧安装版本不包含这些功能。[架构图](docs/assets/reliable-control.svg)说明设置、凭据和操作状态分别保留在哪里。

服务器以单个可移植 Go 可执行文件运行，并为可选的服务发现使用固定版本的 DNS 库。发布包覆盖 Linux amd64、arm64 和 ARMv7，macOS Intel 和 Apple Silicon，以及 Windows amd64 和 arm64。容器镜像支持 Linux amd64 和 arm64。Kubernetes 和 k3s 使用附带的 Helm chart。

## 启动服务器

从[发布页面](https://github.com/open-ott-play/ottplay-control-server/releases)下载匹配的压缩包，按照发布清单验证后解压。在 Windows 上，将以下命令中的程序名改为 `ottplay-control-server.exe`。

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` 在新文件中生成随机且互不相同的凭据；在 Unix 上，该文件仅允许所有者访问。它拒绝覆盖现有文件或符号链接，且从不打印凭据。请妥善保护此文件；在 Windows 上，将 ACL 限制为运行服务器的账户。`config.example.json` 说明各字段，并有意使用无效的占位令牌。

默认地址是 `0.0.0.0:8081`，即所有 IPv4 接口，包括局域网。使用服务器的局域网地址和端口连接电视。现有配置会保留其显式指定的 `listen` 值；将其改为 `0.0.0.0:8081`，或使用 `--listen 0.0.0.0:8081`，即可开放原先仅监听回环地址的安装。将播放器**页面的准确 origin** 添加到 `allowed_origins`，例如 `http://192.168.1.20:8443`。协议、主机名和端口必须一致，不要附加路径。origin 列表为空时，只允许非浏览器客户端。配置修改在重启后生效。设备和管理员凭据仍是必需的。

对于可信局域网中的旧电视浏览器，支持 HTTP。连接经过不可信网络时，请使用 HTTPS：`serve --config config.json --tls-cert server.crt --tls-key server.key`，或在反向代理处终止 TLS。HTTPS 播放器页面不能在浏览器中连接纯 HTTP 服务器。原生应用仍遵循操作系统的传输策略；此设置不会放宽该策略。

直接提供 HTTPS 时，服务器会在开放监听前验证所提供的证书链：RSA 密钥至少为 2048 位，ECDSA 密钥至少为 224 位，也支持 Ed25519。升级前请更换密钥长度不足的证书。参见 [TLS 证书策略及其范围](docs/security-design.md#direct-https-certificate-policy)。

## 连接播放器

使用包含命令服务器连接功能的播放器版本。打开 **设置 → 远程控制** 并输入：

1. **服务器地址：** 服务器 IP 或主机名，可附带端口，也可以是完整的 `http://` / `https://` 地址。仅填写主机时，默认使用 HTTP 端口 8081。
2. **访问代码：** `devices` 中该播放器条目的 `token`，绝不能使用 `admin_token`。
3. **连接：** 启用轮询，并检查同一对话框中显示的状态。

播放器只在当前安装中保存此连接，不会将其包含在设置备份或云端迁移中。无需启用独立的本地 HTTP 监听器。Web 浏览器需要获准的 CORS origin；带有 `Origin: null` 的打包电视页面可显式启用 `allow_null_origin`，用于指定的设备/初始化路由。这不会开放管理员或诊断操作员路由。

## 终端遥控

[带图示的 Android OTA 指南](docs/android-ota.md)区分普通 Capacitor Wi-Fi 更新（`ott DEVICE update --variant api22|full`，默认最新 beta；`--version` 选择发布版本）与独立的 root 代理。它说明签名代理和 APK/helper 更新、恢复，以及已安装结果的验证。

[Android ADB 指南](docs/android-adb.md)介绍通过工作站或 Synology 访问 USB、屏幕和键盘控制、截屏、日志、APK 安装及正常重启，也涵盖旧平板电脑的限制。

可选的 [DNS-SD 发现与配对](docs/discovery.md)让播放器通过其网络域查找控制器，并在执行 `ott approve NAME CODE` 后获得独立的设备令牌。DNS 只保存服务元数据。不启用发现功能，现有手动连接也能继续工作。

使用 Python CLI 管理播放器别名、实时音量、频道搜索、当前 EPG、提供商选择及受支持的提供商配置：参见 [CLI 指南](docs/cli.md)。服务器和播放器都必须包含请求/响应扩展。

Python CLI 从源代码工作副本安装，与原生服务器压缩包分开。[安装和配置步骤](docs/cli.md#installation-and-connection)涵盖 macOS/Linux 与 Windows、Python/PATH 设置以及必需的 CLI 模块。它们固定到经过审查的 0.1.1 开发提交，包含 workbench 和离线验证；稳定版 CLI v0.1.0 和原生服务器压缩包不包含这些命令。[更新或回退 CLI](docs/cli.md#update-select-or-roll-back-the-cli)时，无需替换现有工作副本、启动器或私有配置。本地启动器可使用只读的[播放列表解析器](docs/cli.md#local-playlist-resolver)，在没有控制器时搜索频道、当前节目和可播放的回看内容。安装并配置后：

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

`a1` 是已注册播放器的示例别名，不是计算机主机名。参见[注册与配对](docs/cli.md#register-and-connect-a-player)、[所有命令及示例](docs/cli.md#commands)、[断开连接和撤销访问](docs/cli.md#disconnect-deregister-or-revoke-a-player)以及[故障排查](docs/cli.md#troubleshooting)。

对于始终开启的播放器，使用 [standby 和 go](docs/cli.md#stop-playback-while-keeping-the-player-online) 停止播放并在稍后唤醒，而不退出应用。`stop` 仍是 `standby` 的别名；`start` 和 `wake` 仍是 `go` 的别名。远程控制必须保持连接，因此待机不意味着设备完全没有网络流量。`ott a1` 显示播放器声明的操作。命令被接受并不确认关闭或开始播放已完成。

[画面比例命令](docs/cli.md#aspect-ratio-fit-and-fill)要求更新 CLI、控制器，并使用声明 `aspect` 能力的播放器。收到确认后才应用更改；再次读取 `aspect`，确认模式及是否已保存。Fill 按比例裁剪，而不是拉伸画面。

远程[截屏](docs/cli.md#remote-screenshots)要求更新 CLI、控制器，并使用具有受支持捕获适配器的播放器。启用播放器的远程控制连接，即授权该控制器截屏和诊断；原生截屏不需要单独的权限开关。浏览器仍要求通过本地来源选择器选择来源。图片可能包含播放器设置、PIN 页面和可见凭据，因此只应连接可信控制器。`ott a1` 报告可用性及需要选择的来源。截屏与结构化遥测采集彼此独立。

要将所有符合某个标题的 VPortal 视频随机排序并循环播放：

```sh
ott l vpr "wedding"
```

`vpr` 将完整选择随机排序一次，然后循环该队列。再次运行可重新随机排序。`vp` 按目录顺序播放匹配项；`vp --list` 列出它们而不改变播放。参见 [VPortal 命令](docs/cli.md#commands)。

使用播放器已保存的 Plex 配置，按明确顺序播放 Plex 内容：

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

CLI 默认最多等待 45 秒。写成 `ott -t 45 l plex shuffle "Tom and Jerry"` 只是显式指定同样的响应超时，并不限制视频播放时长。收集大型 Plex 媒体库可能超过 10–15 秒的短超时。请将 `-t SECONDS`（或 `--timeout SECONDS`）放在播放器别名前。参见 [Plex 媒体库超时](docs/cli.md#plex-libraries-by-name)，了解预览示例以及播放请求超时后的处理方式。

Plex 队列要求 CLI、控制器和播放器实现 `plex_queue`。`plex play --shuffle ID...` 将给定 ID 随机排序一次。除非现有 kiosk 策略让其循环，否则队列在最后一项后停止。队列存在时，普通的 `next` 和 `prev` 操作该队列；`plex stop` 清空它。预览绝不会切换提供商或替换当前播放。参见 [Plex 队列命令](docs/cli.md#ordered-plex-queues)，了解限制和就绪要求。媒体库查找还要求更新后的播放器和控制器声明 `plex_library`；它使用播放器已保存的 Plex 连接，并拒绝不明确或不完整的选择。参见[按名称查找 Plex 媒体库](docs/cli.md#plex-libraries-by-name)。

### 保存本地视频配置档

在当前版本的播放器和控制器中，本地文件/文件夹使用 `offline` 提供商：

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

请先在播放器上连接文件/文件夹。保存会记录其配置和选中的视频，不会开始播放。导出只传输描述；媒体及操作系统/浏览器的访问授权仍留在设备上。其他安装需要在本地绑定，加载也不意味着已确认播放。参见[本地视频配置档](docs/cli.md#local-videos-profiles)，了解设置、能力、可移植格式限制和故障排查。

### 跨配置档保存当前项目

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

收藏项目保存在目标播放器上，正常重启不会丢失。选择收藏会加载保存的提供商/配置档并解析新链接；若来源已变或项目缺失，操作会失败，不会替换成其他频道或影片。CLI、控制器和播放器必须支持 `favorites`；`ott l` 显示可用操作。参见[跨配置档收藏](docs/cli.md#favorites-across-profiles)，了解别名、限制、确认语义和故障排查。

### 加载已保存的设置组合

将命名设置组合保存在 `~/.config/ottplay-control/cli.json` 的私有 `presets` 部分。每组包含编号 M3U 配置档（播放列表和回看小时数）、Plex 凭据，以及加载完成后要选择的 M3U 配置档。参见[配置示例](docs/cli.md#named-setups)。

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI 先验证整组配置，再等待播放器确认后才发送下一项更改。它处理提供商加载、保存 Plex 和 M3U 槽位，并选择指定的 M3U 配置档。错误会停止序列并报告已确认的步骤；结果不确定的写入绝不会自动重试。不需要额外的配置档服务或重启控制器。请保护此配置：其中包含提供商凭据。

## 发送命令

本例从本地私有配置读取管理员令牌，不将其放入 URL 或命令行参数：

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

其他命令可按编号或名称选频道、随机选频道、切换提供商、显示消息、在支持时修改 M3U 播放列表，或退出播放器。可用性取决于播放器平台和当前提供商。参见 [API 契约](docs/api.md)。

在支持独立 VPortal 提供商的播放器上，`ott a1 provider
vportal` 选择其专属的 15 个配置档。使用 `profile N vportal LINK`、`profile N
name NAME`，或包含 `name` 与 `vportal` 的私有 `profile-config N FILE`，然后通过 `profile N` 选择已配置的槽位。现有 M3U 配置档保持独立。`profiles` 报告元数据，不暴露账户页面链接或密钥。

开始播放 VPortal 影片或剧集队列后，`ott a1 kiosk on` 锁定该选择。播放器在重新加载后保留队列和位置，并重新解析媒体 URL 以便恢复。使用 `kiosk off` 选择其他标题或更改配置档，再用 `kiosk on` 锁定。kiosk 回执显示标题和剧集数量；提供商请求和流 URL 留在播放器上。

面向儿童播放时使用 `ott a1 kiosk on --strict`：本地点击会显示视频底栏。更新的 core 播放器允许点击或拖动时间轴，在当前 VPortal/Plex 剧集中跳转；其他本地控制仍锁定。旧播放器、频道 kiosk 和拦截时间轴手势的封装保留只读底栏。现有锁定保持其目标。通过控制器执行 `ott a1 kiosk off` 解锁。在 Android 网站中，防止通过系统退出还需要使用 PIN 单独固定应用；参见 [kiosk 模式](docs/cli.md#kiosk-mode)。

## 远程诊断

先参阅[只读 workbench 快速入门](docs/workbench.md#quick-start)，了解 `doctor`、界面/媒体 `inspect`、操作回执查询、证据包、`health`、Web/原生 `media-progress` 和离线 `report verify`。它使用已有的管理员 CLI 配置与播放器别名。原生通道用于另行部署的 Android 维护代理；Tauri 播放器使用 Web 检查。报告区分解码器进度与实际屏幕/声音验证。遇到能力缺失、通道不可用或证据不完整时，参见 [workbench 故障排查](docs/workbench.md#troubleshooting)。

可选的[协议 2 诊断](docs/remote-diagnostics.md)为每个运行时提供显式启用的会话，使用独立且权限受限的操作员凭据，并限制结构化遥测的范围。已启用的播放器连接提供本地授权，无需再次询问信任；每次采集仍有受限的会话有效期。诊断控制轮询独立于现有命令队列。

[诊断 CLI 与 MCP 指南](docs/diagnostics-cli.md)涵盖安装、操作员凭据、连接设置、每条诊断命令/工具、采集和修复示例、运行时撤销及故障排查。`ott diagnostics --help` 列出这一独立命令族；普通播放器别名和管理员凭据不授予诊断访问权。

## 部署包

- [Android 平板：Plex 严格 kiosk 与启动](docs/android-kiosk-setup.md)
- [共享 Android USB 管理与启动恢复](docs/android-usb-manager.md)
- [Docker / Compose、Kubernetes、k3s、systemd、macOS 和 Windows](docs/deployment.md)
- [配置、交付语义与限制](docs/api.md)
- [发布与验证流程](docs/development.md)

队列有容量限制，默认 60 秒后过期，存储在内存中。重启服务器会丢弃待处理命令。请运行一个副本；独立副本不共享队列。播放器确认的是一次发送尝试，并非实际播放或硬件音量。网络重试在正在运行的播放器会话内去重，不跨应用重启去重。

服务器遵循 [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) 中可选 `local_proxy.py` 示例的命令 API，并使用分离的管理员/设备凭据和确认交付。旧代理仍是独立组件。本项目不代理媒体，也不改变播放器的 HLS、EPG 或提供商网络通信。

## 许可证

MIT。附带发布工具的署名记录在 `NOTICE` 和 `LICENSES/` 中。

## 项目维护

参见[贡献和测试要求](CONTRIBUTING.md)、[安全报告策略](SECURITY.md)、[安全设计](docs/security-design.md)，以及 [OpenSSF 证据和剩余标准](docs/openssf-evidence.md)。

当前播放器的 Plex 队列最多接受 500 个 ID。使用 `ott f10 plex play --shuffle ID...` 随机排序一次，再在已选 Plex 提供商时使用 `ott f10 kiosk on --strict` 循环并锁定队列。`ott f10 kiosk off` 解除锁定。旧播放器仍采用其声明的队列上限。

## 发布候选版本的模糊测试

公开的发布候选版本对[模糊测试指南](docs/fuzzing.md)所述输入边界执行有界原生模糊测试。变异测试任务不在 PR、beta 或夜间构建中运行。指南涵盖范围、预算、证据和回归问题复现。

## 社区与安全证据

参见[参与贡献](CONTRIBUTING.md)、[治理](GOVERNANCE.md)、[行为准则](CODE_OF_CONDUCT.md)、[路线图](ROADMAP.md)和 [OpenSSF 证据](docs/openssf-evidence.md)。证据索引区分受支持的安全配置、实际测试结果和剩余的 Silver 要求。
