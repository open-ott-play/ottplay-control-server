# Android 平板：Plex 严格信息亭与启动

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB 指南](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB 管理器](android-usb-manager.md)

专用平板需要分别验证三项设置：内容队列、OTT 严格信息亭策略和 Android 启动。安装 APK 或验证屏幕关闭与恢复，不能证明操作系统重启后会启动应用。

## 识别与安装

1. 记录实际平板、OTT 别名/UUID、USB 管理器别名、序列号、型号、Android ID 和 SDK。OTT 与 USB 别名可能不同；不要仅凭别名推断身份，也不要复用其他平板的令牌。
2. 使用兼容该 SDK 的官方 APK 变体，验证发布 SHA-256 及包名/签名身份，以 `install -r` 安装并保留播放器数据。若 USB 由[共享管理器](android-usb-manager.md)管理，请使用它，不要启动竞争的 ADB 服务器或执行未明确指定设备的 `adb` 命令。
3. 使用播放器自己的控制器凭据配对，并验证 `ott TABLET`。加载所需的命名预设，运行 `ott TABLET load PRESET verify`。家庭网络预设不能与开发机器的 localhost 预设互换。

## 从平板验证 Plex

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

验证库 ID、标题和项目数。管理员电脑请求成功，不能证明平板连通。请求失败时，检查平板保存的地址、路由、TLS 支持和凭据。优先使用服务器有效的 HTTPS 端点。若旧固件必须使用私有 LAN HTTP，仅在此安装中使用，记录令牌和媒体未经加密传输的事实，不要重写共享预设或全局禁用证书验证。切勿在诊断或文档中公开令牌或私有媒体 URL。

较新的播放器如果在 `caps` 中提供 `categories`，Plex 会直接打开所有视频，无需先选择文件夹。使用 `ott TABLET categories` 和 `ott TABLET category all`；参见[媒体分类](cli.md#media-categories-and-stable-video-selection)。下面的文件夹选择步骤适用于明确标注的旧版播放器。

更改 Plex 服务器地址会清除所选库根目录。选择 **Plex** 提供商，打开 **Plex settings → Folders**，勾选目标库行（而不只是 **Browse folders**），选择 **Ok**，再选 **Save and open library**。播放器 `1.1.58-beta.19` 中，只有服务器凭据可能打开文件夹选择器，而 `ready` 仍为 false；执行队列命令前完成文件夹选择。不要只为获得 `ready: true` 而改用 M3U：Plex 队列虽可能在那里播放，但 `kiosk on --strict` 可能保存没有绑定媒体的等待中 M3U 策略，它不能锁定或恢复 Plex 队列。

## 启动并锁定队列

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

有序队列使用 `plex library 'LIBRARY NAME'`，而非 `plex shuffle`。Shuffle 只确定一次顺序，信息亭重复该顺序。确认目标项目、`enabled: true`、`strict: true`，以及策略绑定的是目标 Plex 媒体队列。没有频道或媒体的 `waiting` 策略不算验收通过的信息亭。至少观察两次媒体采样，确认播放位置/帧数增加，并检查屏幕。只有回执或 `playing` 标签而无进展并不足够。测试项目结束后的自然切换；若无法实际测试完整循环，明确记录该限制。

严格信息亭控制播放器界面。它并非 Android 设备所有者注册，也不能证明 Home、Recents、通知和 Settings 已被阻止。保留现有系统启动器和有人值守的维护途径。

## 单独启用启动功能

构建并验证 [OTT Home 0.2.0](../native/android-home/README.md)，保留现有更新签名密钥。在 `play.ott.foss` 旁安装 `play.ott.home`。打开 `play.ott.home/.StartupActivity`，启用 **Start OTT after reboot**。该选项默认关闭，不改变官方播放器二进制、OTA 签名、配置文件数据或 Home 按钮分配。

固件提供 Home 选择器时，还可选择 **OTT Home → Always**。没有选择器时（包括已测试的旧 Fire 固件），开机启动许可独立提供启动功能。已配置默认 Home 的安装保持原行为。完整回滚需禁用两种机制。

共享 USB 管理器的 `recover_on_boot` 是第三种独立机制：需要 NAS 和 USB 线，只处理新启动，不会持续重启崩溃应用。它不能代替自主启动。测试自主启动时保持该恢复策略关闭，以免掩盖伴随应用未启动的问题。遵守安全锁屏要求，提示时正常解锁。

## 验收与交接

私下保存证据，不在公开回执中包含凭据：

- APK 版本、哈希、实际设备/OTT/USB 身份映射及所选预设。
- Plex 库、项目数/顺序、严格策略及最初持续推进的播放。
- 实际重启操作系统前后的启动 ID；重启后为 `sys.boot_completed=1`。
- 从重启到观察期间，没有手动启动播放器或 USB 恢复。
- OTT 前台活动、控制器重新连接、队列/严格策略恢复及播放进展。启动接收器的 `requested` 结果仅表示已发出请求。
- 单独测试电源按钮：屏幕关闭、音频停止、正常解锁和恢复。
- 准确限制：需要安全解锁、USB 辅助恢复、未经测试的物理退出途径、流错误或内存耗尽。不要把这些情况称为完全验收通过。

交付下一台平板前，重复同一检查清单，不要复制上一台设备的成功结论。旧 Fire 平板可能因厂商原生 HLS 崩溃而需要用 `hls.js` 播放 M3U；这不能证明 Plex 播放稳定。

## 维护与回滚

```sh
ott TABLET kiosk off
```

打开 **OTT Startup**，禁用 **Start OTT after reboot**，若更改过 Home 选择则恢复原设置。必要时通过已验证的 USB 传输仅卸载 `play.ott.home`，保留 `play.ott.foss` 及其数据。禁用启动不会改变队列，也不会自动关闭信息亭。
