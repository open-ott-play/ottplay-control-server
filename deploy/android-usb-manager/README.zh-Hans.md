# Android USB 传输镜像

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

本目录包含共享管理器传输容器的部署模板和 [`Dockerfile`](Dockerfile)。Python 管理器运行在 USB 主机上。镜像提供 ADB、Python 和原子维护适配器所用的工具；不包含第二套设备注册表或恢复守护进程。配置、命令、迁移、恢复限制及回滚见[操作指南](../../docs/android-usb-manager.md)。

没有 Git 工作副本时，从同一[发布版本](https://github.com/open-ott-play/ottplay-control-server/releases)下载 `ottplay-control-server-deployment.tar.gz` 和 `release-manifest.json`。验证归档摘要，按[获取部署包](../../docs/android-usb-manager.md#get-the-deployment-package)将其解压到新目录。包中包含此构建配方以及 `tools/` 下的六个主机端/远端脚本；请保留目录结构。包括 `v0.1.5-beta.6` 在内的旧包缺少这些脚本，需改用对应的源代码归档。包中不含构建好的 USB 传输镜像。控制服务器的 OCI 发布资产另有用途。

## 源代码与产物来源

基础镜像为 `python:3.12.14-slim-trixie`，固定到 `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`。基础镜像、APT 指令及通过哈希锁定的 `websocket-client==1.9.0` 安装已在 [PR 128 的 b4181bf 修订](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager)中审查。保留原指令文本以复用已有构建缓存。可选的 WebSocket 包支持现有的通用维护客户端；迁移后的 CDP 适配器也可仅使用 Python 标准库。

此配方在安装锁定的 wheel 后删除已安装的 `pip` 包，不包含 PR 128 的管理器和恢复代码，在前台启动 ADB，并禁用继承的健康检查。移除运行时安装器会减少可用攻击面，但并不证明漏洞扫描没有发现问题。

PR 128 审查过的产物为 `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`。它具有不同的入口点和不兼容的心跳健康检查。它是参考产物，不是此 Dockerfile 的构建结果。请勿将其镜像 ID、源代码出处或扫描结果归属于此配方。

APT 仓库未固定到快照。因此，即使基础摘要和 Python wheel 哈希相同，未来的干净重建仍可能选择更新的系统软件包。认定构建时，记录确切的源代码修订、构建器/平台、生成镜像 ID、软件包版本和扫描结果。生产配置必须使用该已认定的不可变镜像 ID 或摘要。

## 构建时不干扰忙碌的 USB 主机

优先在独立构建器上构建和扫描。目标主机只需要认定过的镜像，无须下载软件包或配备构建工具链。

复用准备好的本地构建器前，先验证确切的基础镜像可用，且 APT 和 wheel 安装层已缓存。仅加载 PR 128 的最终镜像无法证明任一条件。从仓库根目录或解压后的部署目录执行以下明确的离线尝试：

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` 适用于构建步骤，并不笼统保证构建器在解析基础镜像时绝不访问仓库服务。受限主机使用本流程前，请确认构建器会从本地解析基础镜像。若缺少 APT 或 wheel 层，请停止尝试，不要悄悄允许在 USB 主机上通过网络重建。

BuildKit 将构建步骤的网络模式纳入缓存键。因此，默认网络模式下创建的缓存可能无法命中上述离线尝试。确认确切的基础镜像和指令链后才能复用；如果任一软件包安装步骤实际执行而非显示 `CACHED`，立即停止。最后删除 `pip` 的步骤显式使用 `RUN --network=none`；即便先前缓存步骤使用原网络模式，该步骤也不需要网络。

修改生产配置前，检查 Python 和 ADB 版本，扫描确切的生成镜像，验证二进制文件传输和 APK 暂存，然后执行指南中的设备身份、密钥授权、休眠及启动验收检查。必须测试 ADB 34 与旧 Android 设备的通信；仅凭版本号无法证明与 KitKat 或 Fire 兼容。在不再需要回滚之前，保留当前镜像和私有部署备份。

## 运行时资源预算

由主机管理器而非此 Dockerfile 设置 256 MiB 内存、内存加交换空间共 256 MiB、CPU shares 128，不设 CPU 或 PID 硬配额。它强制 `--no-healthcheck`、`--network none`、只读根文件系统、移除 capabilities 及精确的 USB 节点映射。`/tmp` 是有界 tmpfs，消耗相同的内存预算。暂存适配器每次写入 1 MiB，单个载荷上限为 128 MiB；操作共享管理器锁。

Docker 文档说明了[内存、交换空间及 CPU shares 控制](https://docs.docker.com/engine/containers/resource_constraints/)和 [`--no-healthcheck` 选项](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck)的区别。
