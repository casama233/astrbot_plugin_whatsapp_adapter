# AstrBot WhatsApp Adapter 中文文档

## 一句话说明

WhatsApp Web 平台适配器插件。通过本地 Node.js Gateway 接入 WhatsApp（Baileys），支持扫码登录、私聊/群聊、媒体收发、交互组件（按钮/列表/投票/活动）、流式输出、访问控制，以及只绑定当前会话的投票／联系人／活动 AI 原生工具；指令唤醒沿用 AstrBot Core。

## 架构

```
Python 插件 (AstrBot) ←→ HTTP/SSE ←→ Node.js Gateway (Baileys) ←→ WhatsApp Web
```

- **Python 端**：平台适配器、事件转换、消息收发、流式输出、WebUI 管理页
- **Node.js 端**：`@whiskeysockets/baileys` 连接 WhatsApp Web、二维码登录、媒体下载、SSE 事件推送

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
npm install --omit=dev
pip install -r requirements.txt
```

AstrBot Cloud 会安装 Python 依赖；Node.js 20+ 与 npm 仍需由宿主环境提供。Gateway 在首次启动时会自动执行 `npm install --omit=dev`，登录管理页也会先显示 Node、npm 与 Baileys 依赖的预检结果。

重启 AstrBot 或重载插件。

## 登录

1. WebUI 启用插件
2. 添加 `whatsapp` 平台适配器
3. 在 WhatsApp 平台实例中填写 `allow_from`、`dm_policy` 等账号访问控制
4. 打开插件详情页 → `WhatsApp 登录` Page
5. 手机 WhatsApp → 已连接的设备 → 扫描二维码

## 配置说明

配置分为固定行为、插件全局默认和平台实例配置：

- **固定行为**：大小限制由 WhatsApp/Gateway 与内部常量决定；命令匹配与唤醒前缀由 AstrBot Core 处理。
- **插件全局默认**：Gateway 默认连接，以及链接预览、输入/已读/在线状态、入站格式、相簿去抖和流式节流。
- **平台实例配置**：账号访问控制、caption、忽略自身消息、reaction 和消失消息。

优先级：**内置默认 < 插件全局默认 < 平台实例显式配置**。

### 插件全局默认

| 键 | 默认值 | 说明 |
|---|---|---|
| `default_link_preview_single_url` | `true` | 单 URL 链接预览 |
| `default_typing_indicator` | `true` | 回复时显示 composing |
| `default_send_read_receipts` | `true` | 发送已读回执 |
| `default_mark_online` | `false` | 开启时维持在线；关闭时仅在回复期间短暂在线并刷新最后在线时间 |
| `default_parse_inbound_formatting` | `true` | 入站 WhatsApp 格式转 Markdown |
| `default_media_album_debounce_seconds` | `2.5` | 相簿去抖秒数；`0` 关闭 |
| `default_streaming_edit_throttle` | `1.0` | 流式编辑最小间隔 |

Gateway 的 `gateway_host`、`gateway_port`、`auto_start_gateway`、`node_executable`、`auth_dir`、`log_level` 只在插件页设置，避免登录页与平台实例连接到不同 Gateway。

### 平台实例配置

| 键 | 默认值 | 说明 |
|---|---|---|
| `dm_policy` | `allowlist` | 私聊策略 |
| `allow_from` | `[]` | 私聊允许名单 |
| `group_policy` | `disabled` | 群聊策略 |
| `groups` | `[]` | 允许的群 JID |
| `group_allow_from` | `[]` | 群内允许的发送者 |
| `media_caption_mode` | `separate` | `separate` 分开发送；`caption` 作为媒体描述 |
| `ignore_self_messages` | `false` | 忽略账号自身消息 |
| `pre_ack_emoji` | `true` | 启用预回应 reaction |
| `pre_ack_emojis` | `👀` | 预回应表情 |
| `pre_ack_private` | `true` | 私聊预回应 |
| `pre_ack_public` | `mentions` | 群聊：always / mentions / never |
| `pre_ack_done_emoji` | `✅` | 回复完成 reaction |
| `apply_ephemeral` | `false` | 套用聊天室消失消息计时器 |

`caption` 不改变流式媒体顺序；流式文字和媒体仍分开发送。

### 固定行为

- 文字切片及各类媒体大小限制不提供配置入口。
- `/` 等唤醒前缀和所有 CommandFilter 指令由 AstrBot 的全局 `wake_prefix` 与插件系统处理。
- 旧版本留下的固定限制、指令和通用平台字段会被迁移过滤，不再覆盖当前配置层级。

## 交互式组件

在 AstrBot 插件代码中使用 WhatsApp 专用组件：

```python
from astrbot_plugin_whatsapp_adapter.whatsapp_components import (
    WhatsAppButton, WhatsAppButtons,
    WhatsAppListRow, WhatsAppListSection, WhatsAppList,
    WhatsAppPoll, WhatsAppEdit,
)

# 按钮消息（最多 3 个）
WhatsAppButtons(
    body="选择操作：",
    buttons=[WhatsAppButton(text="确认", id="confirm")],
    footer="AstrBot",
)

# 列表消息
WhatsAppList(
    title="菜单", description="请选择",
    button_text="查看",
    sections=[WhatsAppListSection(title="分组", rows=[WhatsAppListRow(title="选项1", id="opt1")])],
)

# 投票
WhatsAppPoll(name="你喜欢的颜色？", options=["红", "蓝", "绿"], selectable_count=1)

# 编辑已发送消息
WhatsAppEdit(message_id="xxx", text="新内容")
```

## AI 原生工具

模型可在当前 WhatsApp 会话调用以下 AstrBot LLM 工具：

- `whatsapp_create_poll`：原生投票（2–12 个不重复选项）
- `whatsapp_share_contact`：原生联系人名片
- `whatsapp_create_event`：含时区、地点与可选结束时间的原生活动

工具不暴露目标 JID 参数，并会核对当前事件的 `target_jid` 与入站 `chatJid`；
模型无法借此指定其他收件人。号码、选项、时间与文字长度会在 Python 和 Gateway
两层校验，只有 Gateway 确认成功后才更新 AstrBot 的发送状态。

## 流式输出

适配器声明 `support_streaming_message=True`，支持 `send_streaming`。工作原理：

1. 首段文本通过 `/send/text` 发送为普通消息
2. 后续增量通过 `/edit/text` 编辑同一消息逐步追加（0.8s 节流）
3. 遇到媒体组件先 flush 文字，再单独发送媒体
4. 编辑失败时自动降级为新消息接续
5. 回复完成后自动用 `pre_ack_done_emoji` 替换预回复表情

## 文件结构

```
astrbot_plugin_whatsapp_adapter/
├── main.py                     # 插件入口
├── whatsapp_adapter.py         # 平台适配器核心
├── whatsapp_client.py          # Gateway HTTP 客户端
├── whatsapp_event.py           # 消息事件（含流式输出）
├── whatsapp_ai_tools.py        # 当前会话原生投票／联系人／活动工具
├── whatsapp_identity.py        # PN／LID／Hosted／多账号身份归一化
├── whatsapp_components.py      # 自定义消息组件
├── whatsapp_helpers.py         # 辅助函数
├── gateway/
│   └── whatsapp-gateway.mjs   # Node.js Gateway（Baileys）
└── pages/whatsapp-login/      # 扫码登录页面
```

## Gateway API

Gateway 默认监听 `127.0.0.1:18789`：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/status` | 完整状态 |
| GET | `/qr` | 二维码 |
| GET | `/events` | SSE 事件流 |
| POST | `/config` | 推送配置 |
| POST | `/group/info` | 获取群名称、群主、管理员与成员资料 |
| POST | `/restart` | 重启 socket |
| POST | `/logout` | 登出 |
| POST | `/session/reset` | 重建登录 session 并生成新二维码 |
| POST | `/presence` | 设置在线状态 |
| POST | `/send/text` | 发送文字 |
| POST | `/edit/text` | 编辑文字 |
| POST | `/send/media` | 发送媒体 |
| POST | `/send/reaction` | 发送 emoji |
| POST | `/send/buttons` | 发送按钮 |
| POST | `/send/list` | 发送列表 |
| POST | `/send/poll` | 发送投票 |
| POST | `/send/contact` | 分享联系人名片 |
| POST | `/send/event` | 建立活动 |
| POST | `/mentions/resolve` | 解析 @提及 |

插件管理页另提供 `/update/status`、`/update/check` 与 `/update/install` 三个受
Dashboard 登录保护的页面 API。它们直接读取本仓库稳定 GitHub Release，不经过
插件市场缓存；安装采用暂存验证、依赖预装、原子切换和重载失败自动回滚。更新
过程不会删除 `plugin_data` 中的认证目录。

## 数据目录

遵循 AstrBot 官方规范，存储于 `data/plugin_data/astrbot_plugin_whatsapp_adapter/`：

```
data/plugin_data/astrbot_plugin_whatsapp_adapter/
├── config.json          # 配置覆盖（手工编辑）
├── whatsapp-auth/       # 登录态（删除需重新扫码）
└── media/               # 入站媒体文件
```

> 从旧版本 (`data/astrbot_plugin_whatsapp_adapter/`) 升级时自动迁移。

## 常见问题

### 页面没有二维码

等待 5-10 秒后刷新。如果登录已失效，点击「刷新二维码」或错误区域的「重试」，页面会隔离旧凭证并建立全新扫码 session。确认已执行 `npm install --omit=dev`。

### 扫码后没收到消息

1. 检查平台实例是否已启用
2. 检查 `allow_from` 是否包含你的号码（E.164 格式 `+国家码号码`）
3. 平台实例显式值会覆盖插件页的全局默认

### 群聊不触发

默认 `group_policy=disabled`。需配置 `group_policy`、`groups`、`group_allow_from`。

### 需要重新登录

在插件 Page 点「登出并重新扫码」，或删除 `whatsapp-auth/` 目录。

### 收到消息但没回复

1. 检查 `dm_policy` / `group_policy` 是否放行
2. 检查 AstrBot 的 LLM 配置和 LLM 是否正常工作
3. 检查 AstrBot 日志（LLM 调用是否有报错）

### 消息发送失败

1. 媒体文件是否超过 Gateway 大小限制（通用 50MB，文档 2048MB）
2. 文件路径是否可访问（容器环境注意路径映射）
3. Gateway 日志中是否有具体错误信息
