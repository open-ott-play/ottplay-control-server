<div lang="zh-Hans" dir="ltr">

# Android OTA：普通播放器与独立的 root 代理

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

对于 **k8a/fire8 和其他 Capacitor 播放器**，使用普通播放器的 Wi-Fi 更新器，而非 root 代理命令：

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

不指定 `--version` 时选择最新发布的 beta。Android 7+ 使用 `full` 而非 `api22`。CLI 确定官方版本、检查清单、准备 APK 并轮询状态，然后只请求一次安装。Android 可能要求在平板上确认。维护前应明确关闭信息亭并解锁受保护设置；命令不会更改这些策略。详见[完整 CLI 流程](cli.md#capacitor-apk-updates-over-wi-fi)。

这需要预先安装支持 `AppUpdate` 的 APK、Wi-Fi，以及运行中且已连接控制器的播放器。正常的后续更新不需要 USB，但无法连接离线平板或已停止的 WebView。首次安装、更新器不可用、应用损坏或网络丢失仍可能需要本地恢复。这里不意味着提供自动发布调度器或通用静默安装器。

本指南其余部分描述的是**另一个用于 a1 的 root 维护代理**，仅限已配置的 Matricom/API19 `play.ott.kitkat` 安装。它并未安装在普通 Fire/Capacitor 设备上，也不能直接用于这些设备。

完成一次性 root USB 初始化后，独立 Android 代理可通过主动建立的 HTTPS 连接接收签名更新。自我更新不依赖正常工作的 WebView。0.1.2 代理还支持协调更新 APK 与系统触控辅助程序的组合包，保留恢复文件并在提交前验证。

本指南解释完整流程。精确的清单字段、限制及硬件要求见[原生代理参考](../native/android-agent/README.md#signed-updates-over-wi-fi)。

## 更新哪些内容

![OTA 架构：审查过的发布字节被签名，通过直接 HTTPS 发布，由独立代理验证，并在安装后检查。Web 部署独立进行。](images/ota-architecture.svg)

- **原生代理：**`kind: agent` 替换其自身 ARMv7 二进制。即便 OTT 被禁用并使用其他 HOME 应用，此途径仍可工作。
- **仅 APK：**`kind: apk` 替换封装应用并保留数据。OTT 必须启用且位于前台。候选包内置的辅助程序必须与已安装系统辅助程序完全一致。
- **APK 与触控辅助程序：**`kind: bundle` 成对更新两个组件。旧代理必须先接收支持组合包的代理单独更新。
- **托管播放器：**在现有源站部署已验证的 Web 产物，然后重载并检查运行中的播放器。APK 更新不会部署托管的 JavaScript。见[发布工作流](release-workflow.md)。
- **控制器与 CLI：**分别部署控制器并安装完整 CLI 模块。更新任一部分都不会替换平板上的代码。

固件、内核、系统 WebView、root 代理中间层和启动脚本不属于此 OTA 格式。[原生参考](../native/android-agent/README.md#bootstrap)说明初始化与恢复边界。受支持的 OTA 操作不需要网络 ADB 或任意远程 shell。

## 从已审查版本到确认安装

1. **选择已验证的字节。** CI 通过后合并已审查的更改。下载目标发布产物，验证源代码修订、不可变发布证据和校验和。从原生归档提取 `agent` 二进制；归档本身不是可安装的代理载荷。
2. **准备并签名。** 使用初始化时已固定其公钥的现有 Ed25519 私钥。保密该密钥及设备凭据。
3. **发布并检查传输。** 仅将载荷和 `signed.json` 发布到不可变 HTTPS 路径。两者必须以 HTTP 200 直接返回字节；更新器拒绝重定向。运行打包工具的 `verify` 命令。
4. **发送一次更新。** 记录请求 ID，在收到回执后检查状态。响应丢失或不确定不是盲目重复操作的理由。
5. **确认效果。** 将新读取的安装哈希及版本与准备的发布版本比较。对于运行中的播放器，还要检查信息亭目标、队列和持续推进的播放。实际图像与声音需要观察。

代理发布示例；替换为选定版本和真实路径：

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

`ENVELOPE_SHA256` 是打包工具的 `envelope_sha256`，而非载荷哈希。`accepted` 表示请求已被接纳，并不表示安装成功。自我替换可能使旧操作回执为 `unknown`；新的运行时、预期代理版本及匹配的安装哈希提供安装证据。[CLI 参考](cli.md#independent-android-agent)说明独立原生路由和回执处理。

## APK/辅助程序事务如何恢复

![APK 与辅助程序事务：预检、保存旧组件对、应用、验证、提交；失败时恢复旧组件对，恢复未完成时继续阻止应用变更。](images/ota-transaction.svg)

组合包恰好包含 `app.apk` 和 `ott-touch-guard`。打包工具可通过 `--kind bundle --apk` 直接从已签名封装 APK 创建它。工具提取内置辅助程序，确保交付的两份副本匹配。见[组合包命令和要求](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)。

修改文件前，代理要求已安装组件对匹配、空间充足、前台 OTT 可响应、系统分区只读，并声明支持 APK 降级。对于锁定的信息亭，它验证相同的电视频道 ID 或有序 VPortal ID；仅队列长度相等不够。不支持的锁定队列会被拒绝。有意禁用 OTT 的情况也会拒绝，保留用户当前 HOME 应用。

停止应用前会持久保存恢复文件和待完成事务。代理等待应用及旧辅助程序停止，原子替换辅助程序，将 `/system` 恢复只读，安装 APK 并检查结果。应用或验证失败会触发旧组件对的恢复。启动恢复只恢复文件，不启动 OTT 或接管 HOME。

若恢复无法完成，`updates.recovery_failed` 为 true。诊断及签名的代理单独修复仍可用；应用变更与播放看门狗被阻止。不得删除保存的事务来强行安装。二进制恢复不会撤销应用数据迁移，固件仍可能拒绝降级。见[恢复限制](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)。

## 结果与验收记录

![验证图表：488 个 CLI 测试、576 个发布工具测试和 6 个 OTA 打包器测试；参考平板运行已验证的新代理，APK/辅助程序的实机验收仍未完成。](images/ota-validation.svg)

2026 年 10 月 14 日实施审计通过了 488 个 CLI 测试（跳过一个）、576 个发布工具测试、六个 OTA 打包器测试、原生 Go 竞态/静态检查及 ARMv7 构建。这些数字描述测试套件规模，不是覆盖率或性能。故障注入测试涵盖无效归档、中断事务、部分恢复、目标变化和恢复隔离。独立审查还发现并修正了不足的队列身份检查，以及主机/设备 APK 与辅助程序验证不一致的问题。

截至 2026 年 10 月 15 日的检查结果：

- OTA 实现已合并到[控制器 PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104)，并包含在 [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4) 中。
- 当前和上一版签名代理包已发布到 [OTA 交付主机](https://android-updates.ottplay.here.now/)。载荷和签名封装字节已通过直接 HTTPS 下载并成功比较。
- 参考平板通过独立 OTA 通道从 `0.1.1-beta.21` 更新至 `0.1.2-beta.4`。运行代理的版本和已安装 SHA-256 与选定版本一致；运行时改变但 OS 启动 ID 未变。OTT 仍没有运行中的应用进程，也未发送应用启动或 HOME 选择操作。
- 原生状态报告了系统触控辅助程序哈希、无待处理组合包、无恢复失败。APK 元数据不可用，因此该观察不能确定已安装 APK 的版本或哈希。
- APK/辅助程序协调事务已通过主机侧验证；在参考平板上的实际安装、降级恢复、图像和音频仍未验收。

每次后续发布应保留发布源代码/校验和、准备的签名封装哈希、请求 ID、前后原生状态和独立的实机验收结果。下载成功或命令回执不能替代这些观察。

</div>
