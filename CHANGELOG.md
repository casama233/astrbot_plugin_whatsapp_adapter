# Changelog

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
