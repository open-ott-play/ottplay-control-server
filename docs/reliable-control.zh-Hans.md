<div lang="zh-Hans" dir="ltr">

# 可靠控制与播放移交

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[文档索引](wiki/Home.md)

这些功能要求使用包含本指南的修订版中的控制器和 CLI。精确比较设置及播放移交还要求更新播放器。旧版发布归档和安装指南中的历史 CLI 修订版不会自动获得这些功能。请**一同安装所有 `cli/*.py` 文件**，包括 `preset_session.py`；保留私有配置。

![控制、检查点与移交流程](assets/reliable-control.svg)

## 启用有界的持久化结果

将这个可选块添加到私有服务器配置中，然后验证配置并重启服务器。选择本地持久化文件系统上的绝对路径，并确保服务账户可写。在 Unix 上，目录必须私有（0700）；已有的日志和锁文件必须是普通私有文件（0600）。在 Windows 上，将目录 ACL 限制为服务账户。一个日志只能由一个进程持有。

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

服务器以 0700 模式创建缺失目录。限制为 1–4096 条记录、60–86400 秒以及磁盘上的 4 MiB。保留期限必须覆盖 `command_ttl_seconds`。日志满时拒绝新的带键操作，而不是移除仍有效的记录。文件损坏、Unix 目录不安全、存在其他写入者或持久化写入失败时，均以拒绝方式安全失败。默认仍使用原有的内存命令处理路径。

随附的 systemd 单元以 0700 模式创建 `/var/lib/ottplay-control`。对于 Compose，准备一个归 UID 65532 所有的宿主目录，将 `CONTROL_SERVER_STATE` 设为其绝对路径，并组合使用 `deploy/compose.yml` 和 `deploy/compose.journal.yml`。后者仅增加可写状态挂载；根文件系统仍为只读。对于 Helm，单独准备持久卷声明并设置 `journalExistingClaim=CLAIM_NAME`。声明挂载在 `/var/lib/ottplay-control`；请使用上面所示的私有子目录。Chart 保留单副本、`Recreate` 和 `fsGroupChangePolicy: OnRootMismatch`，避免重新挂载时递归放宽已创建私有目录的权限。确认所选存储驱动支持这些所有权语义。Chart 不会创建或删除该声明，启用挂载也不会启用日志：日志仍由私有服务器配置控制。

对于 `POST /api/requests`，在 `action` 和 `params` 旁添加 `idempotency_key`。旧版命令 POST 改为接受 `Idempotency-Key`。键由 16–80 个 ASCII 字母、数字、下划线或连字符组成；为每个意图生成新的随机键。同一所有者/键配合同一设备、操作及规范化 JSON 载荷，会返回原始 ID，而不会再次入队。更改载荷会返回 409。键按操作员 ID（或管理员）划分作用域，而非按设备。保留期限结束后，该键可能被当作新意图接受：客户端不得在此时间窗口之外重试旧意图。

`GET /api/operations?device_id=DEVICE` 报告日志是否启用、限制以及命令 TTL。添加 `&key=KEY` 可读取某项操作：

- `pending`：已接受；尚无保留的响应。
- `responded`：已收到通过验证的播放器响应；`result_status` 为 `ok`、`rejected` 或 `unsupported`。原始结果正文仍按正常规则过期。
- `acknowledged`：旧版消费者已确认命令。这并不证明可见播放或设置变更已保存。
- `unknown`：期限已过，或服务器在保留响应前重启。服务器重启后**绝不重放**此命令。

重复的带键 POST 返回 `status: retained`、原始 ID 和状态。普通的新 POST 仍返回 `status: queued`。日志记录仅包含所有者、设备、操作、随机 ID、载荷摘要、状态及期限。不包含命令载荷、提供商 URL、令牌、截图或响应正文。等待中的命令不会持久化。这是有界去重和结果跟踪，不是恰好一次执行、持久任务队列或多服务器高可用。

## 规划、应用、恢复与验证 Local/Home

保留现有的 `ott PLAYER load PRESET` 行为。新流程如下：

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` 离线运行：验证本地预设，列出设置槽位和期望的活动配置文件，但不打印其内容。它不检查播放器。`verify` 执行精确的只读比较，并检查所选 M3U 配置文件。如有差异或不支持比较，则返回非零退出码。

`apply` 需要服务器日志。它创建私有检查点，并在首次修改之前比较每个已配置的提供商。每次保存都通过 `settings_compare` 将已存储字节与期望值进行比较；匹配时跳过写入。最终验证覆盖 Plex、所有 M3U 槽位、可选的 Stalker 槽位及所选 M3U 配置文件。比较返回布尔值，绝不返回已保存的秘密。

默认检查点位于 `~/.config/ottplay-control/checkpoints/` 下。在模式后使用 `--checkpoint /absolute/private/path/session.json` 可选择其他文件。检查点包含设备、预设名称、服务器/预设修订版的哈希、已完成步骤名称，以及一个等待中操作的随机键和期限。不包含设置值。文件在 fsync 后原子替换，并通过锁防止并发 CLI 写入。请保留匹配的本地预设。在 Unix 上，检查点目录必须私有（0700），普通文件权限为 0600。在 Windows 上，将检查点目录继承的 ACL 限制为运行 CLI 的用户；Unix 模式位无法实施 Windows 访问权限。

`resume` 验证同一服务器、设备及预设修订版，然后读取等待中的结果。保留的响应允许立即重新检查。不确定的结果会阻止发送修改，直到前一命令期限、请求超时及一小段余量均已过去。过了该窗口后再次运行 `resume`。它甚至会重新检查已完成步骤的已存储设置，以免把用户更改误认为先前成功写入。它绝不会盲目重放相对命令。在保护等待期之后，可以重复绝对的提供商/配置文件选择。

新的 `apply` 绝不会覆盖已有检查点。用 `resume` 复用它，或为变更后的预设选择新文件。不同设备有各自的检查点。不存在跨所有播放器的事务或回滚；后续步骤失败时，先前成功的写入仍然保留。`verified` 只确认比较时播放器保存的值，不代表网络加载成功或实际视频/音频输出正常。

## 仅向集成授予必要权限

为 HA、RF 桥接或代理使用[普通命令操作员](security-design.md#scoped-command-operators)，而不是分发 `admin_token`。允许精确的设备 ID 及 `input.channel_up`、`input.channel_down` 等操作。此预设流程中的设置操作需要显式授予 `settings_compare`、`provider`、`profiles`、`status`、`profile`、`profile_settings` 和 `provider_settings` 权限。移交需要来源上的 `playback_handoff_export` 及目标上的 `playback_handoff_import`。操作员条目组合其列出的设备与操作；如果同一操作不应适用于每个列出设备，请使用独立操作员身份。令牌与诊断同意仍是独立权限。

操作员不能轮询播放器队列、提交播放器响应、列出全部设备、读取管理员调试输出或其他操作员的结果。缩小某操作的权限范围也会阻止读取其保留记录。删除操作员并重启会撤销其凭据。旧日志记录仍存在时，不要把操作员 ID 复用于另一主体。

## 在另一播放器上继续

```sh
ott handoff living-room bedroom
```

来源导出版本 1 的直播/归档引用：提供商、不透明的来源身份、不透明的 `channel-v1:<8 hex>.<8 hex>` 项目指纹，以及归档中的当前节目时间。原始提供商 ID 和流 URL 留在本地。目标必须拥有**同一已配置的来源/账户**，在自己的目录中解析出唯一匹配项目，并使用自己的本地凭据获取新 URL。控制器既不复制可播放 URL，也不修改目标凭据。来源继续播放。CLI 成功意味着目标已分派请求，并不代表其物理屏幕或扬声器已验证。

当播放器能提供所需身份时，版本 1 支持 `m3u`、`xtream` 和 `stalker` 的直播/归档目录。未知或有歧义的项目、来源不匹配及本地策略拒绝都会失败，不会退回按名称匹配。VOD/剧集、不同账户间映射、暂停来源以及持续同步历史/收藏，不属于此初版约定。

## 验证与恢复演练

运行 `go test -race ./...`、`go vet ./...` 和 `python3 -m unittest discover -s tests`。测试覆盖成功写入后响应丢失、恢复前精确比较、配置漂移、未知操作期限、两个写入者、日志损坏、跨操作员读取及格式错误的移交载荷。在预发布播放器上，还应在保存设置后中断 CLI、重启控制器、恢复并验证；测试匹配和不匹配来源间的移交。单独确认视频/音频。

进行本地跨仓库验证时，使用当前 FOSS 工作副本并安装其开发依赖，然后运行：

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

冒烟测试构建此 Go 服务器、编译完整 FOSS TypeScript 项目，并将其真实传输层、命令处理器、来源身份及随附的播放列表编解码器与 Python CLI 联合运行。它检查三个受支持提供商的精确设置、差异、使用不同本地行 ID 的直播移交、归档定位偏移、URL 解析失败及严格的传输格式验证。只有存储、提供商策略和播放宿主接口是测试替身；它不验证浏览器、实际媒体输出或真实账户访问。它绑定回环接口，使用私有临时文件，不访问流 URL，并在退出时清理自己的文件和服务器。

</div>
