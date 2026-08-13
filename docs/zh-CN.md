# AstrBot WhatsApp Adapter 中文使用指南

**简体中文** · [繁體中文](zh-TW.md) · [English](en/index.md)

WhatsApp Web 平台适配器插件。通过本地 Node.js Gateway（Baileys）接入 AstrBot，支持扫码 / 手机号配对、私聊与群聊、富媒体、Reply / Mention、流式回复、多账号、代理、管理 Page 与安全更新。

> [!IMPORTANT]
> 本项目使用 WhatsApp Web 非官方协议栈，不是 Meta 官方 WhatsApp Business Cloud API。协议变化可能造成临时兼容问题。

## 架构

```text
AstrBot Python Adapter ← HTTP/SSE → Local Node.js Gateway ← WhatsApp Web → WhatsApp
```

- Python：平台适配、事件转换、访问控制、流式发送、Plugin Page API。
- Node.js：WhatsApp Web 会话、扫码 / 配对、媒体、重连、消息投递。

## 环境要求

- AstrBot `>=4.24.2,<5`
- Node.js `>=20`
- npm
- Python 依赖 `aiohttp>=3.9.0`
- 可访问 WhatsApp Web 的网络环境

## 安装与登录

优先使用 AstrBot 插件市场 / Cloud 安装。手工安装：

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
pip install -r requirements.txt
npm install --omit=dev
```

推荐首次平台配置：

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

然后保持插件级 `auto_start_gateway=true`，打开 **WhatsApp Login** Page，扫码或申请手机号配对码。

> [!TIP]
> 首次部署不要直接使用 `allow_from=["*"]`。先只开放自己的测试号码，确认登录、媒体和流式输出正常后再扩大范围。

## 配置模型

配置分三层：

1. **插件级 Gateway**：`gateway_host`、`gateway_port`、`auto_start_gateway`、`node_executable`、`auth_dir`、`log_level`。
2. **插件级消息默认值**：`default_*`，例如输入状态、已读、在线、格式转换、相簿去抖、streaming edit throttle。
3. **平台实例**：账号级 `dm_policy`、`groups`、`pre_ack_*`、`apply_ephemeral` 等。

`default_streaming_edit_throttle` 默认 **1.0 秒**，运行时最低保护值 `0.1s`。

完整字段见 [配置参考](configuration.md)。

## UMO 与公开 ID

从 v0.2.37 起，新 WhatsApp 事件使用稳定公开 UMO，而不是把运输层 JID 暴露到新会话。

| 场景 | `session_id` | 示例 UMO |
| --- | --- | --- |
| 私聊 | 已确认 PN 为数字 ID；未解析 LID 为 `lid-N` | `实例ID:FriendMessage:用户ID` |
| 群聊，会话隔离关闭 | group JID local part（数字或旧式 `数字-数字`） | `实例ID:GroupMessage:群ID` |
| 群聊，会话隔离开启 | `用户ID_群ID` | `实例ID:GroupMessage:用户ID_群ID` |

PN、LID、Hosted、设备 JID 与 `@g.us` 仍保留在 `raw_message` / `target_jid` 作为运输信息。公开投影以首次曝光为准持久化；映射补齐后 UMO 不漂移，曾分裂的投影只按最早公开 ID 合并一次。旧 PN / LID / 群 JID session 仍可用于主动发送兼容。

## 群聊唤醒与 Reply

从 v0.2.39 起，Reply 引用与唤醒信号严格分离：

- 引用内容、昵称、message ID 与 QQ 兼容字段会完整保留，方便其它插件读取。
- **仅引用机器人消息不会被当成 @机器人。**
- 群聊只有真实 @机器人、@全体、命令或 AstrBot Core 的其它正常唤醒条件才会触发回复。
- pre-ack reaction 与唤醒状态独立；发出 reaction 不会反向把普通群消息标记为已唤醒。

## 消息与媒体

支持常见文本、图片、音频、视频、文档、贴纸、位置、联系人、按钮/列表回应、投票和原生活动。

纯 reaction 入站消息当前会被忽略，不作为普通 AstrBot 消息事件上报；机器人仍可发送 pre-ack / done reaction。

### 连续图片

默认 `default_media_album_debounce_seconds=2.5`。私聊短时间连续图片可合并为一个 AstrBot 事件；每张图的 caption、mention 与顺序会尽量保留。后续文字、Reply、非图片媒体或明显时间边界会先 flush pending 图片。

### 流式输出

1. 第一段可见文字先发送。
2. 后续内容通过 WhatsApp edit 增量更新。
3. 默认编辑最小间隔为 `1.0s`。
4. 媒体会先 flush 文字后单独发送。
5. 编辑不可用时停止不安全编辑，只补发必要的剩余/最终内容。
6. 并发回复使用独立 streaming state，并协调 typing presence。

详见 [消息与流式行为](messaging.md)。

## 多实例 / 多账号

内置 Gateway 模式：

- 默认 `id=whatsapp` 保留基准端口（默认 `18789`）。
- secondary 实例从后续可用端口自动分配。
- 每个账号使用独立 auth 目录。
- reload / reconnect / bind race 有 runtime owner 防护。

> [!NOTE]
> 当前 Login Page 主要面向默认基准 Gateway；secondary 内置实例二维码主要从 AstrBot / Gateway 日志获取。

external Gateway 模式下，同一 `host:port` 不允许被同一 AstrBot 进程内多个 WhatsApp runtime 静默共用。详见 [多实例](multi-instance.md)。

## 代理

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1
```

支持 `HTTPS_PROXY`、`HTTP_PROXY` 及小写变量，`NO_PROXY` / `no_proxy` 会参与 WebSocket 与媒体请求绕过判断。代理 URL 仅支持 `http://` / `https://`。

## 国际化

插件提供 `zh-CN`、`zh-TW`、`en-US` 三套 AstrBot i18n：

- 插件元数据与配置说明随 WebUI locale。
- WhatsApp Login Page 的连接状态、QR / 配对、访问策略、Updater v2、确认提示与事件日志随 locale 动态重渲染。
- Python / Node 后端运维日志保持稳定技术文本，不做 runtime 翻译。

## Updater v2

管理页更新器会固定精确 Release candidate / asset identity，校验正式 artifact digest 与 ZIP 安全，切换前 quiesce active runtime，持久化 transaction，reload 后执行 health gate，并在失败时保留 rollback 路径。

> [!CAUTION]
> 自更新无法消除所有平台级硬断电窗口。生产环境仍应独立备份插件目录与 `plugin_data`。

## 安全建议

> [!WARNING]
> 默认 `127.0.0.1` 绑定是安全边界的一部分。不要直接把 Gateway HTTP/SSE 暴露到公网。

- `whatsapp-auth/` 是敏感登录凭证，不要提交或分享。
- 进入 AstrBot 后的消息是否交给第三方 LLM / 工具取决于你的 AstrBot 配置。
- 多账号不要复用相同 auth 目录或相同平台实例 ID。

更多见 [安全与隐私](security.md)。

## 排障

- 没有 QR：检查 Node.js 20+、npm、Gateway 端口、网络和 Page runtime 状态。
- 扫码后没消息：检查平台实例启用状态和访问控制。
- 群聊不触发：确认 `group_policy` 与 AstrBot wake/command 条件；Reply 本身不再算唤醒。
- 流式变成新消息：WhatsApp edit 受协议 / 时效限制，降级属于保护路径。
- 热重载异常：v0.2.38+ 已补强旧 adapter takeover 与健康检查；仍失败时查看 AstrBot 后端日志。

完整排障见 [故障排查](troubleshooting.md)。

## 开发与贡献

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

`tests/test_plugin_i18n_coverage.py` 会检查三套 locale、配置 schema 与 Login Page i18n 防回归。

更多见 [开发指南](development.md)、[CONTRIBUTING.md](../CONTRIBUTING.md) 与 [RELEASING.md](../RELEASING.md)。
