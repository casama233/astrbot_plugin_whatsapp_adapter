<div align="center">

<img src="./logo.png" alt="AstrBot WhatsApp Adapter" width="168" />

# AstrBot WhatsApp Adapter

**让 AstrBot 通过本地 Gateway 接入 WhatsApp Web / Baileys**<br>
扫码登录 · 私聊/群聊 · 富媒体 · 流式回复 · 多账号 · 管理页面

**简体中文** · [繁體中文](README.zh-TW.md) · [English](README.en.md)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/casama233/astrbot_plugin_whatsapp_adapter?label=version&color=ff69b4)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.24.2%2B-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-brightgreen.svg)](https://nodejs.org/)
[![Python CI](https://img.shields.io/badge/Python_CI-3.11-blue.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![Tests](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml/badge.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![GitHub Stars](https://img.shields.io/github/stars/casama233/astrbot_plugin_whatsapp_adapter?style=flat&logo=github)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/stargazers)
![动态访问量](https://count.kjchmc.cn/get/@astrbot_plugin_whatsapp_adapter?theme=gelbooru)

</div>

> [!CAUTION]
> **本插件代码由 AI 生成，并经过人工审阅。** 即使经过审阅，仍可能存在未发现的缺陷、安全风险或兼容性问题。请谨慎使用；在重要账号、生产环境或敏感场景部署前，建议先自行审查代码并充分测试。

> [!IMPORTANT]
> 本项目基于 **WhatsApp Web 非官方协议栈 / Baileys**，不是 Meta 官方 WhatsApp Business Cloud API。WhatsApp Web 协议变化可能造成临时兼容问题；生产使用前请自行评估账号、稳定性与业务风险。

## ✨ 为什么用这个适配器？

| 能力 | 说明 |
| --- | --- |
| 🔐 **扫码 / 手机号配对** | 在 AstrBot Plugin Page 中完成默认账号登录，也支持手机号配对码 |
| 💬 **私聊与群聊** | 独立 allowlist / open / disabled 策略，支持成员级访问控制 |
| 🖼️ **富媒体消息** | 图片、音频、视频、文档、贴纸、位置、联系人、按钮、列表、投票等 |
| ⚡ **流式回复** | 首条消息发送后持续编辑；WhatsApp 不允许继续编辑时自动安全降级 |
| 🧩 **引用 / @ / 身份兼容** | 处理 Reply、Mention、PN / LID 与群成员身份映射 |
| 🆔 **稳定公开 UMO** | PN 使用数字 ID，未解析 LID 使用 `lid-N`；运输层 JID 不写入公开 session ID |
| 🧠 **唤醒语义对齐 AstrBot** | 群聊仅引用机器人消息不会触发；真实 @、@全体、命令等才构成唤醒 |
| 🖼️ **连续图片合并** | 私聊短时间连续图片可合并为一次 AstrBot 事件，并尽量保留 caption、提及与顺序 |
| 👥 **多实例 / 多账号** | 内置 Gateway 自动隔离端口和认证目录，避免账号 session 串用 |
| 🌐 **代理支持** | 支持 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` |
| 🔄 **Updater v2** | 锁定 Release candidate、校验 artifact digest、持久事务、健康检查与失败回滚 |
| 🌍 **三语言 UI** | `zh-CN` / `zh-TW` / `en-US`，登录管理页支持运行时切换语言 |

> [!NOTE]
> 当前 WhatsApp Login Page 主要管理**默认 / 基准 Gateway 实例**。secondary 内置实例的二维码目前主要通过 AstrBot / Gateway 日志获取。

## 🚀 快速开始

### 1. 安装插件

优先使用 AstrBot 插件市场 / Cloud 安装。宿主环境需要提供 **Node.js 20+** 与 npm。

<details>
<summary><strong>手工安装</strong></summary>

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
pip install -r requirements.txt
npm install --omit=dev
```

完成后重启 AstrBot 或在 WebUI 重载插件。

</details>

### 2. 新增 WhatsApp 平台实例

首次测试建议：

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

> [!TIP]
> 第一次上线先只放行自己的测试号码。确认登录、回复、媒体和流式输出正常后，再逐步开放群聊或更多号码。

### 3. 登录 WhatsApp

1. 保持插件级 `auto_start_gateway=true`。
2. 打开插件详情中的 **WhatsApp Login / WhatsApp 登录** Page。
3. 使用手机 WhatsApp → **已连接的设备 / Linked devices** 扫描二维码；也可以申请手机号配对码。
4. 页面显示 Connected / 已连接后，启用平台实例并发送测试消息。

## 🧱 架构

```text
AstrBot
  │ Python Platform Adapter / HTTP + SSE
  ▼
Local WhatsApp Gateway (Node.js)
  │ Baileys / WhatsApp Web
  ▼
WhatsApp
```

默认 Gateway 监听 `127.0.0.1:18789`。

> [!WARNING]
> 不要把 Gateway HTTP/SSE 端口直接暴露到公网。`whatsapp-auth/` 包含 WhatsApp Web 登录凭证，也不要提交到 Git、上传到 Issue 或分享给他人。

## ⚙️ 配置模型

| 范围 | 典型字段 | 作用 |
| --- | --- | --- |
| 插件级 Gateway | `gateway_host`、`gateway_port`、`auto_start_gateway`、`auth_dir` | Gateway 连接、进程与基础认证目录 |
| 插件级消息默认值 | `default_typing_indicator`、`default_streaming_edit_throttle` 等 | 所有 WhatsApp 实例共享的默认行为 |
| 平台实例 | `dm_policy`、`groups`、`pre_ack_*`、`apply_ephemeral` | 单账号访问控制与消息行为 |

> [!IMPORTANT]
> `default_streaming_edit_throttle` 当前默认值是 **1.0 秒**。标准 WebUI 只有一组插件级 external Gateway endpoint；多个 external Gateway 账号建议拆分 AstrBot 进程 / 容器。

完整字段见 [配置参考](docs/configuration.md)。

## 🆔 UMO 与群聊唤醒

WhatsApp 的 PN、LID、Hosted、设备 JID 与 `@g.us` 属于运输层身份，仍保留在 `raw_message` / `target_jid`。公开 ID 使用稳定投影：

| 场景 | `session_id` |
| --- | --- |
| 私聊 | 已确认 PN 为数字 ID；未解析 LID 为 `lid-N` |
| 群聊（会话隔离关闭） | 群 JID local part（数字或旧式 `数字-数字`） |
| 群聊（会话隔离开启） | `用户ID_群ID` |

公开投影以首次曝光为准持久化，后续补齐 PN/LID 映射不会让 UMO 漂移；若同一联系人曾分裂为两个投影，确认映射后只按最早投影合并一次。旧 PN / LID / 群 JID session 仍可用于主动发送兼容。

> [!NOTE]
> Reply 引用内容、昵称、message ID 会完整保留给其它插件读取，但**引用机器人消息本身不再被当作 @机器人**。群聊只有真实 @机器人、@全体、命令或 AstrBot 其它正常唤醒条件才会触发回复；pre-ack reaction 也不会反向改变唤醒状态。

## 🌍 多语言与文档

| 语言 | README | 使用指南 |
| --- | --- | --- |
| 简体中文 | **当前文档** | [docs/zh-CN.md](docs/zh-CN.md) |
| 繁體中文 | [README.zh-TW.md](README.zh-TW.md) | [docs/zh-TW.md](docs/zh-TW.md) |
| English | [README.en.md](README.en.md) | [docs/en/index.md](docs/en/index.md) |

### 专题文档

| 文档 | 内容 |
| --- | --- |
| [配置参考](docs/configuration.md) | 配置作用域、默认值、代理变量与迁移 |
| [消息与流式行为](docs/messaging.md) | 入站/出站、UMO、唤醒、相簿、Reply、reaction、streaming |
| [多实例 / 多账号](docs/multi-instance.md) | 端口、认证目录、实例隔离、external Gateway 限制 |
| [故障排查](docs/troubleshooting.md) | 登录、群聊、代理、媒体、更新问题 |
| [安全与隐私](docs/security.md) | Gateway 暴露、凭证、媒体、LLM 与 updater 信任边界 |
| [开发指南](docs/development.md) | 代码结构、测试、Plugin Page i18n 与兼容层 |
| [贡献指南](CONTRIBUTING.md) | Issue / PR 与 i18n 维护约定 |
| [发布流程](RELEASING.md) | 版本 marker、CI、Release artifact 与恢复流程 |
| [变更记录](CHANGELOG.md) | 历史版本功能与修复 |

## ⚡ 消息行为重点

- **Streaming**：默认每 `1.0s` 最多编辑一次；编辑失败时不会盲目重复整段回复。
- **Reaction**：机器人可发送 pre-ack / done reaction；纯 reaction 入站消息当前会被忽略。
- **Reply**：引用元数据会保留，但 Reply 本身不等于群聊唤醒。
- **连续图片**：私聊短时间 burst 可合并；文字、回复、非图片消息会切断合并窗口以保持顺序。
- **并发回复**：每个事件维护独立流状态，typing presence 会协调，避免互相提前停止。

更多细节见 [消息与流式行为](docs/messaging.md)。

## 🔄 Updater v2

管理页可以直接检查并安装本仓库稳定 GitHub Release。当前流程会锁定精确 Release candidate / asset identity、校验正式 artifact digest 与压缩包安全、quiesce 当前 runtime、持久化 transaction 状态，并在 reload 后执行 health gate；失败时保留 rollback 路径。

> [!CAUTION]
> 自更新无法消除所有平台级断电窗口；生产环境请保留插件目录和 `plugin_data` 的独立备份，并阅读 [安全与隐私](docs/security.md) 与 [发布流程](RELEASING.md)。

## 🛠️ 开发与验证

CI 覆盖 Ubuntu 与 Windows；Python 测试环境为 3.11，Node.js 为 20。

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

Plugin Page i18n 由 `tests/test_plugin_i18n_coverage.py` 防回归，新增用户可见配置 / Page 文案时应同步 `zh-CN`、`zh-TW`、`en-US`。

## 📄 License

本项目使用 [MIT License](LICENSE)。
