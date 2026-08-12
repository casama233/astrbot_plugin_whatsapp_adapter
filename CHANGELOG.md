# Changelog

## [0.2.37] - 2026-08-12

- 统一 WhatsApp UMO 为 QQ 风格公开数字 ID：私聊使用用户 ID，群聊使用群 ID，开启 unique_session 时使用 用户ID_群ID，不再把 @lid、@s.whatsapp.net、@hosted 或 @g.us 写入新会话。
- Gateway 额外提供私聊 canonical PN，适配器在第一条未知 LID 消息进入管道前主动解析并持久化 PN/LID 映射，避免同一联系人因地址模式或设备后缀变化分裂为多个 UMO。
- 主动发送继续接受旧版 PN、LID、群 JID 与 用户ID_群JID session，并在运输边界统一恢复正确 JID；raw_message 与 target_jid 继续保留完整运输身份。
- 新增 PN/LID 同会话、手机端发出消息、首次 LID 解析、群聊隔离以及新旧 session 主动发送回归测试，并补充 UMO 公开规范文档。

## [0.2.36] - 2026-08-12

- 重构内置更新器为 release-pinned Updater v2：检查阶段生成与 Release、asset、digest 绑定的 candidateToken，安装阶段只接受同一候选与 expected version，不再重新查询最新版。
- 正式更新只接受 astrbot_plugin_whatsapp_adapter-vX.Y.Z.zip，并要求 GitHub Release asset SHA-256 digest；移除 source zipball fallback，下载后再次校验摘要。
- 发布包会同时验证 metadata.yaml、main.py、package.json 与 package-lock.json 版本一致性，并继续执行 ZIP 路径、文件类型、体积与插件身份安全检查。
- 更新 transaction 使用持久 lock，并将 staging/backup 移出 AstrBot 插件扫描目录；切换前停止 WhatsApp runtime，reload 后通过插件、adapter 与 Gateway health gate 才删除 rollback 数据。
- Linux 支持时使用 renameat2(RENAME_EXCHANGE) 降低切换窗口；Windows 与不支持该能力的文件系统使用带回滚保护的双 rename，并明确保留极端断电窗口这一平台边界。
- 内置 updater 不再修改 AstrBot 全局 Python 环境；若新版本改变 requirements.txt 将 fail closed，并要求通过 AstrBot 插件管理器更新。
- 管理页的更新与登出确认改为纯两次点击状态机，彻底移除 window.confirm monkey-patch 与 destructive-action UI 的动态 HTML sink。
- CI 扩展为 Ubuntu 与 Windows 双平台，并修复 release identity parser、Gateway source patcher 的 CRLF 兼容问题及跨平台 path 测试。
- 重要过渡说明：从 v0.2.35 升级到 v0.2.36 时，建议优先使用 AstrBot 插件管理器或手动安装本 Release 的正式 ZIP，因为发起这一次升级的仍是 v0.2.35 旧 updater；完成升级后后续版本将使用 Updater v2 的完整 transaction 与 health-gate 保证。

## [0.2.35] - 2026-08-12

- 修复 AstrBot Plugin Page 沙箱环境中「再次点击确认更新」或「再次点击确认登出」仍可能没有反应的问题。
- 将临时确认 shim 的恢复时机从 microtask 延后到下一事件任务，确保同一次点击的 capture 与 bubble 监听器都能正确消费二次确认。
- 新增浏览器式 listener 间 microtask checkpoint 回归测试，防止真实浏览器时序与 Node 假 DOM 测试不一致再次漏测。

## [0.2.34] - 2026-08-12

- 支持在同一个 AstrBot 进程中运行多个 WhatsApp 平台实例，并为每个账号分配稳定且隔离的 Gateway endpoint 与认证目录。
- 默认 whatsapp 实例保留基准端口，secondary 实例从下一可用端口开始分配，支持并发启动、重连、热重载与端口绑定竞争恢复，避免 session 串号和端口漂移。
- 强化多实例安全边界：instance ID 使用安全且抗碰撞的目录标识，自定义 auth_dir 自动按实例隔离，外部 Gateway endpoint 禁止被多个账号 runtime 静默共用。
- 新增多实例端口、认证目录、external Gateway、bind-race 等回归测试及 docs/multi-instance.md 使用文档。
- 感谢 @cdxiaodong 提交并推动最初的多实例方案，维护者整合版保留了原始提交与贡献归属。

## [0.2.33] - 2026-08-12

- 修复 AstrBot Plugin Page 受限 iframe 中原生 confirm 无法弹出，导致「立即更新」点击后没有反应的问题。
- 将插件更新改为 10 秒内二次点击确认，保留误触保护且不依赖 allow-modals。
- 同步修复「登出并重新扫码」的确认流程，并增加沙箱环境回归测试。

## [0.2.32] - 2026-08-11

- 修复消失讯息计时器冲突，避免历史或延迟的 ephemeral metadata 覆盖当前聊天室设置。
- 优化私聊短时间连续图片合并，保留每张图片的说明文字、@提及、来源顺序与重放边界。
- 改善并发流式回复的输入状态管理，避免一个回复结束时提前清除仍在进行中的 typing presence。
- 强化 AstrBot 合规发布流程，统一版本与制品校验、16 MiB 市场限制、SHA-256 校验和及可恢复发布。

## [0.2.31] - 2026-08-11

- 全面对齐 QQ 适配器的数字身份、提及顺序、引用、群资料及可信 OneBot raw 事件语义，同时兼容 PN、LID、Hosted 与多设备 JID。
- 修复 AstrBot 4.x 流式回复未触发发送后钩子的问题，使天使之心能在真实投递后及时取消安抚任务并释放会话锁；同时修复主动消息旧聚合延迟、缺少消息 ID 时重复发送及流式 Markdown 尾部残留。
- Gateway 支持过期媒体重新上传、失败文件清理及重连 generation 隔离，避免贴图下载失败、磁盘残留、迟到事件重复路由或污染新连接。
- 新增只绑定当前 WhatsApp 会话的原生投票、联系人名片与活动 AI 工具，并完整解析入站原生活动；模型不能指定其他收件人。
- 登录管理页新增安全的手机号配对码方式，仅允许未注册登录 session，并提供号码校验、串行化、冷却及敏感信息日志保护；感谢 @cdxiaodong 的 #6 贡献。
- Gateway 新增 HTTPS_PROXY、HTTP_PROXY 与 NO_PROXY 支持，WebSocket／媒体可分别绕过代理，依赖与 lockfile 完整纳入且日志不会泄露代理凭证；感谢 @cdxiaodong 的 #7 贡献。
- 补齐包装消息、编辑缓存、原生 mention-all、媒体 mentions、文件名、链接预览、Location 与标准组件降级等通用兼容行为。

## [0.2.30] - 2026-08-08

- 入站 @ 提及會使用聯絡人／群成員暱稱，並以 QQ 相容的 message_str 呈現。
- 修正 LID 與手機號身分跳變，補齊引用訊息與常用事件中繼資料。
- 修正流式 Markdown 尾端殘留格式符，同時保留後續可閉合的格式標記，並支援 Location 原生傳送。
- 相容 AstrBot 4.27+ 的指令處理器匯入位置。

## [0.2.29] - 2026-08-01

- 管理頁新增獨立 GitHub Release 更新卡片，可自動或手動檢查新版本並查看更新說明，不再依賴官方插件市場的同步與快取。
- 手動安裝固定使用本倉庫穩定 Release，限制可信 HTTPS 來源與包體大小，拒絕路徑穿越、重複路徑、符號連結及特殊檔案，並校驗插件名稱、版本與 AstrBot 相容範圍。
- 更新會先在暫存目錄預裝 Python／Node 生產依賴並執行語法檢查，通過後才原子切換插件目錄；AstrBot 熱重載失敗時自動恢復舊版本，不觸碰 WhatsApp 認證資料。
- 更新工作改為背景單任務執行，頁面立即取得任務狀態並輪詢進度；重複點擊會被阻擋，GitHub 檢查結果保留五分鐘且不加入 5 秒連線輪詢，避免過度請求。
- 新增 Release 選擇、可信 URL、ZIP 安全解壓、原子切換／回滾及並發更新的回歸測試，並以 AstrBot 4.26.8 真實 API 驗證。

## [0.2.28] - 2026-08-01

- 修復啟用「應用聊天室的消失訊息設定」後，收件端仍顯示「此訊息不會自動刪除／傳送者可能正在使用版本較舊的 WhatsApp」：針對精確鎖定的 Baileys `7.0.0-rc14` 在安裝期恢復 `ephemeralSettingTimestamp`，並只使用聊天室真實的消失訊息設定時間。
- 消失訊息期限與設定時間不完整或不相符時改發普通訊息，不再以發送時間偽造 metadata；支援從聊天同步、聊天更新與入站 ephemeral message 回填設定，並正確處理 `0`／`null` 清除計時器。
- 修復其他 AstrBot 插件只能取得 WhatsApp 群 ID、無法取得群名稱：Gateway 讀取 Baileys `GroupMetadata.subject`，填入 AstrBot 標準 `message.group.group_name`，並實作 `event.get_group()`。
- 入站事件同步提供 `groupName`、`group_name`、`groupSubject` 兼容欄位；新增群資料快取與 `groups.update` 更新處理，避免統計、權限及群管理插件把 JID 當作群名稱。
- 新增消失訊息與群名稱回歸測試，以及第一方 GitHub Actions Python／Node 完整測試工作流。

## [0.2.27] - 2026-07-30

- 出站引用行为与 AstrBot Telegram adapter 对齐：仅当出站 MessageChain 包含 `Reply` 时引用，分段回复只在首段引用，串流回复不再强制反复引用触发消息。
- `Reply` 组件现在只作为传输控制信息处理，不会再递归发送其内嵌 chain，避免把被引用的旧问题误当成新回复内容。
- Gateway 引用快取改为严格按聊天室与 message ID 复合键查找，移除跨聊天室全局 ID fallback，并缓存成功发出的消息供后续同聊天室引用。
- 引用当前入站消息时固定使用该消息发送者作为 participant，不再误用入站消息本身所引用的上一条消息发送者。

## [0.2.26] - 2026-07-30

- Baileys 升级并精确锁定到 `7.0.0-rc14`，带入最新 WhatsApp Web 协议版本、连接兼容性及 profile picture token 修正。
- Gateway 改用已发布 Baileys 套件内建的协议版本，不再在每次 socket 启动或重连时从 `master` 动态取值，减少外部请求并保证安装结果可复现。
- 更新 `protobufjs` 至 `7.6.5`、`sharp` 至 `0.35.3`，修复当前生产依赖树中的已知 DoS 与 libvips 高危漏洞。
- Gateway 启动前会核对 lockfile 中的直接依赖及安全关键传递依赖版本；插件升级后即使旧 `node_modules` 仍存在，也会串行执行一次必要更新，不再因“目录存在”而长期停留在旧版。

## [0.2.25] - 2026-07-30

- 管理页刷新改为 single-flight：前一次状态/二维码请求结束前不再叠加新一轮轮询，避免 Gateway 启动缓慢时形成请求堆积。
- Gateway 管理页启动流程加入异步互斥锁，状态、二维码、重启、登出和 session 重建不会并发争抢同一进程。
- Node 依赖安装加入进程内互斥与二次检查，平台实例和插件 Page 同时启动时也只会执行一次 `npm install --omit=dev`。

## [0.2.24] - 2026-07-30

- 适配 AstrBot 4.26.8 新插件平台：插件最低版本调整为 `>=4.24.2,<5`，补充作者主页、检索标签及「三方集成」分类，并统一注册作者身份。
- 插件 Page API 从 Quart `jsonify` 迁移到 AstrBot 官方 `json_response`，保留 503 状态码并让 Gateway 启动错误始终返回结构化 JSON。
- 新增 Node.js 20+、npm 与 Baileys 依赖预检；登录页在 Gateway 无法启动时直接显示具体缺失项，首次启动仍只执行一次必要的生产依赖安装。
- 发版归档排除测试及开发配置，图标标准化为 256×256，确保 AstrBot Cloud 安装包精简且符合平台规范。
- 修正文档中的在线状态语义：默认关闭时仅在回复期间短暂在线并刷新最后在线时间，回复完成后恢复离线。

## [0.2.23] - 2026-07-27

### Fixed
- 关闭 `default_mark_online` 时，机器人回复期间仍会短暂显示在线并刷新「最后在线」：发送顺序为全局 `available` → 当前会话 `composing` → `paused` → 全局 `unavailable`。
- 回复结束后仍会立即恢复离线，不会重新引入长期显示在线的问题。

## [0.2.22] - 2026-07-27

### Fixed
- 修复关闭 `default_mark_online` 后仍会长期显示在线的问题：连接建立和配置热更新都会显式发送全局 `unavailable`。
- 回复结束时不再发送全局 `available`；现在仅对当前会话发送 `paused` 以停止输入状态，因此「发送打字状态」与「常驻在线」完全分离。

## [0.2.21] - 2026-07-26

### Fixed
- 發布版本同步登入生命週期修復：掃碼後的 `515 restart required` 會保留同一認證 session，等待憑證落盤後重建連線，不再因誤判憑證失效而丟失剛完成的手機配對。
- QR 過期、明確認證失效與暫時性網路中斷分流處理，並以退避重連降低重複請求及風控風險。

## [0.2.20] - 2026-07-26

### Fixed
- 登录凭证经 WhatsApp 明确判定失效时，Gateway 自动切换到隔离的新认证 epoch 并生成二维码；旧 socket 的延迟写入不再能污染新登录。
- 修复扫码成功后的 515 mandatory restart 被误判为损坏凭证、导致手机端一直「正在登录」后失败的问题；现在会先等待新凭证完整落盘，再使用同一 session 快速重建 socket。
- 管理页「重试／刷新二维码」改为重建登录 session，不再重复加载已经失效的凭证。
- 串行化 socket 启动、重连、登出与 session reset，避免并发操作同时读写同一份认证状态。
- 登录失效时立即清除旧账号 JID/LID，并区分 Gateway 存活与 WhatsApp 登录状态。

### Changed
- 恢复 `media_caption_mode` 等真正按 WhatsApp 帐号变化的实例选项。
- 所有有限枚举配置统一改为下拉选项：Gateway 日志级别、私聊/群聊策略、媒体文字模式与群聊预回应模式。
- 链接预览、输入/已读/在线状态、入站格式解析、相簿去抖与流式节流改为插件级 `default_*` 全局默认。
- 文字和媒体大小限制改为内部固定值，不再允许配置覆盖。
- 移除用户可配置的第二套指令前缀；正常流程统一沿用 AstrBot 的 `wake_prefix` 和 CommandFilter，仅为旧版非 `/` 前缀保留一个版本的隐藏兼容扫描。
- 配置优先级调整为“运行时默认 < 插件全局默认 < 平台实例配置”，并忽略旧平台配置中已移除的键。
- Gateway 连接改为插件全局单一来源，避免登录页与平台实例连接不同端口或认证目录；旧实例的显式非默认连接值会在运行时自动接管并提示保存。
- 旧平台实例中显式修改过的通用消息行为会以隐藏兼容覆盖保留一个版本；历史模板默认值不会阻挡新的插件全局默认。
- 旧版自定义 WhatsApp 指令前缀提供隐藏兼容迁移；新配置统一使用 AstrBot 全局 `wake_prefix`。

## [0.2.19] - 2026-07-25

### Changed
- WhatsApp 原生 Markdown 转换：`**bold**` → `*bold*`、`*italic*` → `_italic_`、`~~strike~~` → `~strike~`，标题/表格/链接/水平线降级为可读文本
- 流式输出改为累积原始 Markdown，每次 publish 只渲染一次完整缓冲区，不再逐 token 重扫全文
- 格式感知切片器跨消息边界自动关闭/重开 `*`、`_`、`~`、code 标记，grapheme 单位切片避免拆散 ZWJ emoji 与组合字符
- mention 绑定可见 `@文字`，每个 chunk 只附带实际出现在该段的 JID

### Fixed
- 流式回复中 `**...**` 被拆分到不同 chunk 时不再残留未闭合的双星号
- edit 不可用或 Gateway 未回传 message id 时，停止中途更新，结束只补发一次完整结果
- realtime fallback 以 raw offset 记录进度，不再以不稳定的 rendered offset 截取
- fallback 分句不再把 `~` 当作标点，避免切断删除线
- 正确 handle generator cancellation、`break`、文字与媒体交错场景
- 更精确的反引号 run 长度保护 inline code、巢状 backtick、多行及未闭合 code
- 保护 escaped Markdown，避免 `\\*`、`\\_` 被误转
- `.pre-commit-config.yaml` 目录替换为有效 YAML 文件，pre-commit.ci 检查已通过

## [0.2.16] - 2026-07-13

### Fixed
- 修复部分 WhatsApp 客户端或图片 caption 中 `@` 机器人时 Baileys 未提供 `mentionedJid`，导致 adapter 误判 `self_mentioned=False`、群聊唤醒不触发的问题
- 新增保守文本兜底：当消息文本中出现 `@机器人 PN/LID 数字` 时，也判定为提及机器人自身，仅影响自身 @ 唤醒判断，不会把 @ 其他人误判为唤醒

## [0.2.15] - 2026-07-13

### Fixed
- 修复 WhatsApp 登录失效后点击「登出并重新扫码」仍继续使用旧 auth 凭据的问题：logout 时立即让旧 socket 事件失效，并阻止旧 `creds.update` 在 auth 删除后写回旧凭据
- 修复重新扫码流程中 Gateway 遇到 `loggedOut` / `Connection Failure` 后直接停在 `logged_out`、无法生成 QR 的问题：手动重启/登出后的显式启动会进行有限重试，并在状态中暴露 `lastError`
- 修复 Gateway 健康监控把 `logged_out` / `starting` 当作异常反复重启和刷 WARN 日志的问题
- 插件管理页在长时间无 QR 时显示连接失败原因和重试按钮，不再无限停留在「正在连接 WhatsApp Web...」

## [0.2.14] - 2026-06-26

### Changed
- 降低正常 SSE 空闲超时重连日志噪音，空闲重连链路改为 debug 级别，保留首次启动、手动重启与异常中断的可见日志

### Fixed
- 兼容 AstrBot 4.26.1 `Image` / `Record` / `Video` / `File` 组件的异步媒体解析接口，改善本地文件、URL、base64/data URI 的发送体验
- 优先使用 AstrBot 媒体组件的真实 `path` 字段，并正确解码 `file://` URI，修复含空格或中文路径的媒体发送问题
- `File` 组件发送改用 `get_file(allow_return_url=True)`，避免异步上下文读取 `File.file` 导致文件路径为空或警告
- Gateway SSE 增加 keepalive，减少无事件时客户端读超时导致的周期性重连

## [0.2.13] - 2026-06-23

### Security
- 插件管理页移除 `innerHTML` 渲染外部数据，改用 DOM API 与 `textContent`，避免日志与二维码数据触发 XSS 风险

### Fixed
- 容器重建后 `node_modules` 缺失导致 WhatsApp Gateway 无法监听 `127.0.0.1:18789`：Gateway 启动前会检查 Node 依赖，缺失时自动执行 `npm install --omit=dev`
- `npm` 不存在或依赖安装失败时直接输出明确错误与安装日志，不再只表现为 Gateway 健康检查超时

## [0.2.12] - 2026-06-11

### Fixed
- 流式编辑间隔 `streaming_edit_throttle` 配置项未汉化：补充 WHATSAPP_I18N_RESOURCES 中 zh-CN 区域的缺失条目

## [0.2.11] - 2026-06-11

### Added
- 新增流式回复编辑间隔配置项 `streaming_edit_throttle`（预设 `1.0` 秒，最小可调节至 `0.1`），取代原本硬编码的 `2.0` 秒，让流式打字输出速度可自主控制

## [0.2.10] - 2026-06-06

### Fixed
- 收件端持續顯示「此訊息不會自動刪除 / 傳送者可能正在使用版本較舊的WhatsApp」警告：根因為 Baileys 7.0.0-rc13 在 `messages.js:600` 把 `ephemeralSettingTimestamp` 欄位註解掉，導致收件端把發送端當作「舊版 WhatsApp」。新增 `apply_ephemeral` 配置（預設關閉）徹底不帶 `ephemeralExpiration`，警告即消失；同時把 `chats.update` / `chats.upsert` 對 ephemeral 快取的更新也一併略過

## [0.2.9] - 2026-06-06

### Fixed
- WhatsApp 流式回覆进入分段轰炸：`send_streaming(use_fallback=True)` 改走 `_send_streaming_edit()` 编辑同一条消息，不再每段发新消息
- 流式编辑 `throttle_seconds` 从 0.8s 提升至 2.0s，降低 WhatsApp 风控风险
- 流式编辑失败后无限补发：新增 `edit_failed` 状态机 + `unsupported_streaming_strategy` 受控降级（按自然句子边界 fallback 或仅补发最终文本）
- 外部插件（Conversa）绕过 `send_streaming()` 每 1.5s `context.send_message()` 分段刷屏：`send_by_session()` 新增 2.2s 纯文字聚合器，仅对 `segmented_reply.enable=true` 启用
- 热替换（hot-swap）后旧 adapter 实例缺失新属性崩溃：新增 `_ensure_send_buffer_state()` 惰性初始化，`main.py` 热替换时同步 `_platform_settings`
- 编辑适配器配置保存后掉线无响应：`reload()` 不再直接停 Gateway/client，改设 `_reconnect_event` 让事件循环自动重建；`_ensure_gateway_running()` 对外部 Gateway 重新下发配置
- 适配器配置保存后 `pre_ack_public` 旧值 `true`（bool）未迁移至 `"mentions"` 字符串，`sanitize_whatsapp_platform_config()` 增加 bool→str 强制转换
- `_group_pre_ack_mode()` 对 bool 的 fallback 错误（`True→"always"` 修正为 `True→"mentions"`，对齐原 `ack_reaction_group=true` 语义）

### Changed
- `RUNTIME_DEFAULT_CONFIG`（运行期全量默认）与 `DEFAULT_CONFIG`（UI 可见配置）分离
- `CONFIG_METADATA` / `WHATSAPP_I18N_RESOURCES` 仅保留 `UI_CONFIG_KEYS` 中的项，适配器 UI 不再暴露媒体切片/typing/健康检查等进阶配置
- `pre_ack_emojis` 默认值从 `✍️` 改为 `👀`（对齐用户实际使用值），配置文案从「预回应表情列表」改为「预回应表情」
- `_pre_ack()` 改为取单个有效 emoji，不再 `random.choice`
- 移除 `import random`（已无使用）
- 适配器配置保存后 clean 逻辑改为 `sanitize_whatsapp_platform_config()`，覆盖热替换与 `patch_platform_manager_hot_reload()`

### Deprecated
- 新增 `DEPRECATED_CONFIG_KEYS`：`reaction_level`、`remove_ack_after_reply`、`inbound_reaction_events`、`ack_reaction_emoji`、`ack_reaction_direct`、`ack_reaction_group`、`私聊启用手动回应`、`群组回应模式`
- 保留 `CONFIG_KEY_ALIASES` 映射 `ack_reaction_*` → `pre_ack_*`，兼容老用户现有配置

### Added
- `sanitize_whatsapp_platform_config()` / `_coerce_pre_ack_public()`：统一清洗与迁移运行时/持久化的 WhatsApp 平台配置
- `_ensure_send_buffer_state()`：惰性初始化适配器 buffer 相关新属性（`_send_text_buffers` / `_send_text_sessions` / `_send_text_tasks`）
- 适配器配置 CONFIG_METADATA 补全：`pre_ack_emoji`、`pre_ack_private`、`pre_ack_public`、`pre_ack_emojis`、`pre_ack_done_emoji`、`media_max_mb`、`gateway_health_check_interval`
- 插件页 i18n 补全：`gateway_port` / `log_level` hint（zh-CN / zh-TW / en-US）

## [0.2.8] - 2026-05-27

### Fixed
- 使用真实 `auth_dir` 持久化 lid→PN 映射，避免自定义登录态目录重启后丢失映射
- Gateway 与 adapter 的 allowlist 匹配规则对齐，支持 phone、PN JID、LID JID 与 local-part 形式
- 出站 PN→lid 还原兼容 PN device suffix，避免回复进入错误会话
- Gateway 连接成功时规范化 `selfLid` 为完整 `@lid` JID，提升自身消息与 @ 提及判断稳定性
- fallback 数据目录改为 `data/plugin_data/{plugin_name}`，符合 AstrBot 官方插件存储规范

### Changed
- Gateway 在 allowlist 模式下无法解析 LID→PN 时明确拒绝并广播 `lid_unresolved`，不再作为 accepted 消息透传

## [0.2.7] - 2026-05-24

### Fixed
- `flush_pending_text` 被误删的 `for chunk` 循环导致所有消息发送崩溃
- 单字符粗体/斜体/删除线 Markdown 格式未转换
- `mention_jid_for_token` 返回 `""` 而非 `None`（与 `mention_jid_from_at` 不一致）
- 热重载后 `logo_token` 不刷新

### Changed
- `session_id` 统一使用原始 JID（lid/PN/group），不做格式转换
- 非唤醒群消息提交 `event`（`is_wake=False`），LLM 不响应但插件可处理
- 预回复表情系统对齐 OpenClaw 方案：`pre_ack_done_emoji`（✅）、`remove_ack_after_reply` 移除
- 移除冗余配置 `reaction_level`、`inbound_reaction_events`、`remove_ack_after_reply`
- Gateway `waitForLidPnMapping` 超时从 10s 改为 3s，超时后透传交由 adapter 最终判断
- 所有日志中文化

### Added
- Gateway `POST /lid/resolve` 端点，adapter 异步查询 lid→PN 映射
- `_LID_PN_CACHE` / `_PN_LID_CACHE` 双向缓存 + 磁盘持久化

## [0.2.6] - 2026-05-24

### Security
- `sender_allowed` 现在真正拦截未授权发送者的消息（之前仅控制预回复表情，事件仍会被提交到下游 LLM）

### Fixed
- `_PN_LID_CACHE` 正向映射被 `chat_jid` 静默覆盖导致出向路由错误
- `convert_message` 中死代码 `elif` 分支
- 冗余导入清理（`os`、`time`、重复的 `import json`）

### Changed
- Gateway 连接成功立即发送 `available` 在线状态
- `_send_presence` 区分 composing（受 `typing_indicator` 控制）与 available（始终可发）
- `waitForLidPnMapping` 超时从 10s 改为 3s，超时后透传给 adapter 最终判断

## [0.2.5] - 2026-05-24

### Fixed
- 群消息未 @ 提及/回复机器人时不再触发 LLM 回复
- `pre_ack_public=always` 仅控制预回复表情开关，不影响唤醒逻辑
- 表情回应（emoji reaction）永不触发 LLM
- 热重载时 SSE 连接卡死导致 Gateway 无法重启（`_events_response` 主动释放）
- 热重载无限循环（`_stop_health_monitor` 未 catch Exception，`_restarting` 未初始化）
- 群消息 lid→PN 映射缺失导致 allowlist 拒绝（Gateway 超时后透传 + adapter 缓存兜底）
- `session_id` / `nickname` 使用 lid JID 导致 LLM 上下文出现未解析的 lid

### Changed
- `/` 前缀指令（含未注册）统一唤醒机器人，看齐其他平台行为
- lid→PN 持久化缓存：启动时从 Gateway auth 目录加载，新映射自动写入磁盘
- 出向 PN→lid 正向映射，确保消息归流到正确会话
- Gateway 侧 `waitForLidPnMapping` 超时不再 reject，交由 adapter 最终判断

### Added
- `_LID_PN_CACHE` / `_PN_LID_CACHE` 双向映射缓存
- `_load_lid_mappings()` / `_save_lid_mapping()` 磁盘持久化
- `_is_sender_allowed` lid 缓存兜底匹配 allowlist
- `WhatsAppGatewayClient._events_response` 跟踪，主动中断 SSE 连接

## [0.2.4] - 2026-05-24

### Fixed
- 群消息未 @ 提及/回复机器人时不再触发 LLM 回复（增加 `is_group_wake` 独立唤醒判断）
- `pre_ack_public=always` 仅控制预回复表情开关，不再影响机器人唤醒逻辑
- 表情回应（emoji reaction）永不触发 LLM

### Changed
- `/` 前缀指令（含未注册）统一唤醒机器人，已注册指令走指令系统，未注册指令交 LLM 处理（看齐其他平台行为）

## [0.2.3] - 2026-05-24

### Changed
- 統一預回復表情系統：移除舊 `ack_reaction_*` 配置鍵（DEFAULT_CONFIG / CONFIG_METADATA / i18n），全面改用 `pre_ack_*` 新系統
- `pre_ack_public` 類型由 `bool` 改為 `string`，支援 `always` / `mentions` / `never` 三種模式，預設值改為 `mentions`
- 所有 WhatsApp 日誌改為中文提示，移除冗餘 JSON dict 輸出，改用簡潔鍵值格式
- Gateway 子進程管理支援跨平台（Windows 用 `taskkill /T`，Unix 用 `killpg` + `start_new_session`）

### Fixed
- 熱重載時 `_ACTIVE_ADAPTERS` 未正確維護（缺少 add/discard），導致重複實例
- 熱重載時未刷新已註冊指令列表
- `_normalize_phone` 空字串誤加 `+` 前綴
- `pre_ack_public` 預設回退值與 `DEFAULT_CONFIG` 不一致（`True` → `"mentions"`）
- `WhatsAppGatewayClient` session 建立缺少鎖保護，可能出現競態條件

### Added
- `_coerce_str_list()`：統一解析 `allow_from`/`groups` 等多種格式（JSON 陣列、逗號/換行分隔字串）
- `_normalize_config_value()`：按 key 自動歸一化配置值
- `_group_pre_ack_mode()`：集中解析群組預確認模式
- 全局執行時註冊表 `_runtime_owner_registry()`：自動終止衝突的舊適配器實例

## [0.2.2] - 2026-05-23

### Fixed
- `pre_ack_emoji` / `pre_ack_private` / `pre_ack_public` / `pre_ack_emojis` config keys were dead code — `handle_msg` now reads the new `pre_ack_*` system instead of the legacy `ack_reaction_*` keys
- Gateway process cleanup now uses `killpg` to ensure orphaned subprocesses are terminated
- `markOnline` fallback default corrected from `True` to `False` to match documented `DEFAULT_CONFIG`

### Changed
- `_pre_ack` now supports multiple emojis (`pre_ack_emojis` accepts comma/space-separated list) — picks one at random for each reaction
- Gateway restart on hot-swap now uses `_force_gateway_restart` flag instead of manually stopping/restarting the process
- Backward compat aliases added for legacy `ack_reaction_*` → `pre_ack_*` config keys via `CONFIG_KEY_ALIASES`

### Added
- `senderPhone` field in message events from Gateway
- Lock file mechanism in `run()` to prevent duplicate adapter event loops
- `start_new_session=True` for Gateway subprocess to isolate process group

## [0.2.1] - 2026-05-23

### Added
- `loadLidMappingsFromDisk()` — loads Baileys persisted `lid-mapping-*_reverse.json` on Gateway startup
- `waitForLidPnMapping()` — waits for Lid→PN mapping via `contacts.upsert` / `lid-mapping.update` / `chats.phoneNumberShare` events with `presenceSubscribe` trigger
- Lid JID stored to `knownContacts` from `senderPn`/`participantPn` in incoming messages
- `rememberGroupParticipants()` now resolves Lid→PN for group members
- DM fallback: when no phone resolved, waits up to 10s for mapping, then rejects with `dm_allowlist_unresolved_lid`

### Fixed
- Plugin reload creates duplicate adapter instances (tracked via `_run_task`, old task cancelled + awaited before new one starts)
- Gateway process not restarted on plugin reload (now force-stopped during `_restore_platform_adapters`)
- Adapter `run()` loop not restarted after hot-swap (clear `_stopped`/`_reconnect_event`, create new task)
- At mentions in `separate` mode silently accumulated instead of being flushed immediately (`process_message_chain`)
- Stale Gateway process left running after multiple reloads
- DM allowlist rejection for Lid users with persisted disk mappings but not loaded on restart

## [0.2.0] - 2026-05-23

### Changed
- Plugin settings page simplified to only `Gateway HTTP 绑定地址`; all other config delegated to platform adapter configurator

## [0.1.0] - 2026-05-19

### Added
- Initial WhatsApp Web Gateway adapter with local Node.js Gateway (Baileys)
- QR code login page
- SSE event stream for real-time message delivery
- DM/group access control (allowlist-based)
- Pre-ack emoji reactions
- Media support (image/video/document/audio)
- Album debounce merging
- WhatsApp interactive components (buttons, lists, polls)
- Command prefix and slash command registration
- Typing indicator and read receipts
- Inbound WhatsApp formatting → Markdown parsing
- Chinese/English/Traditional Chinese i18n
