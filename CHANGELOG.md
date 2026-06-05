# Changelog

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
