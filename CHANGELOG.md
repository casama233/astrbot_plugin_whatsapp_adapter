# Changelog

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
