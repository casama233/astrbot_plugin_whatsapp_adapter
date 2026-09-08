# AstrBot WhatsApp Adapter

通过本地 Baileys Gateway 接入 WhatsApp Web，支持私聊、群聊、媒体、流式回复和多个账号。

简体中文 · [繁體中文](README.zh-TW.md) · [English](README.en.md) · [最新 Release](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)

## 环境

AstrBot **4.24.2+**；推荐 **Node.js 22/24 LTS** 与 npm，最低 **20.9.0**。Node 20 已停止维护，仅保留兼容回归。CI 覆盖 Ubuntu / Windows、Python 3.11/3.12、Node 20/22/24。

## 快速开始

1. 从 AstrBot 插件市场安装本插件，保持插件连接设置的默认值。
2. 新增 WhatsApp 平台实例：私聊策略选 `allowlist`，在 `allow_from` 填入自己的国际号码，首次验证保持群聊关闭。
3. 打开插件的 **WhatsApp 登录** 管理页，在手机 WhatsApp → **已连接的设备** 扫码，或使用手机号配对码。
4. 显示已连接后启用平台实例，发送一条测试消息。

管理页明确显示操作作用的基准 Gateway / 账号。多账号的实际端口可在「高级连接信息」查看；其他账号的登录方式见 [多实例指南](docs/multi-instance.md)。

插件连接设置与 `default_*` 是共享设置；平台实例保存账号自己的访问策略。旧配置只迁移一次，生效值与来源可在管理页「生效设置与诊断」查看。完整字段集中在 [配置参考](docs/configuration.md)。

## 排障与更新

- 未找到 Node 或依赖待安装：查看管理页 Runtime 状态，按 [故障排查](docs/troubleshooting.md) 处理。
- 登录正常但没有回复：先检查平台实例启用、访问名单和 AstrBot 唤醒条件。
- 提交问题时使用「重新获取并复制诊断」；报告包含实际版本、来源、账号端口及设置来源，并隐藏账号标识和凭证。
- 管理页可检查并安装校验过的 GitHub Release，更新失败会尝试回滚。部署前保留独立备份。

默认 Gateway 为 `127.0.0.1:18789`。请保护 Gateway 端口和 `whatsapp-auth/` 登录凭证。本插件使用非官方 WhatsApp Web 协议，协议变更可能影响连接；详细边界见 [安全与隐私](docs/security.md)。

## 专题文档

| 文档 | 内容 |
| --- | --- |
| [使用指南](docs/zh-CN.md) | 安装与首次接入 |
| [配置参考](docs/configuration.md) | 字段、作用域、迁移与代理 |
| [消息与流式行为](docs/messaging.md) | UMO、唤醒、引用、reaction 与相簿 |
| [多实例](docs/multi-instance.md) | 账号、端口、登录态隔离 |
| [开发指南](docs/development.md) | 实现入口、测试与 i18n |
| [兼容性清单](docs/compatibility-inventory.md) | 外部补丁及移除条件 |
| [发布流程](RELEASING.md) | 候选验证与恢复 |

[贡献指南](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) · [MIT License](LICENSE)
