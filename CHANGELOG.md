# Changelog

## [0.2.0] - 2026-05-23

### Changed
- 精简插件配置页（`_conf_schema.json`），只保留 6 个 Gateway 连接项
- 移除冗余的预回应配置项：`pre_ack_private`、`pre_ack_public`、`pre_ack_emojis`、`pre_ack_emoji`
- 权限、消息、指令、在线状态等配置项迁移至平台适配器配置面板设置
- 同步更新 en-US、zh-CN、zh-TW 三语 i18n

## [0.1.0] - 2026-05-23

### Added
- WhatsApp Web Gateway 平台适配器
- 插件管理页扫码登录
- 私聊 & 群聊消息接入
- 访问控制：allowlist / open / disabled
- 媒体收发：图片、音频、视频、文档、贴纸
- 交互组件：WhatsAppButtons、WhatsAppList、WhatsAppPoll、WhatsAppEdit
- 流式输出（send_streaming）
- 斜线指令识别
- 预回应表情 + 打字指示
- Markdown 格式互转
- 相册去抖
- 健康检查 & 自动重连
- 热重载
- i18n：en-US、zh-CN、zh-TW
