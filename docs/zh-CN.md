# AstrBot WhatsApp Adapter 中文文档

## 一句话说明

WhatsApp Web 平台适配器插件。通过本地 Node.js Gateway 接入 WhatsApp（Baileys），支持扫码登录、私聊/群聊、媒体收发、交互组件（按钮/列表/投票）、流式输出、斜线指令和访问控制。

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

重启 AstrBot 或重载插件。

## 登录

1. WebUI 启用插件
2. 添加 `whatsapp` 平台适配器
3. 在插件配置页填写 `allow_from`、`dm_policy` 等（插件配置页优先级最高）
4. 打开插件详情页 → `WhatsApp 登录` Page
5. 手机 WhatsApp → 已连接的设备 → 扫描二维码

## 配置说明

配置合并顺序：**内置默认值 < 平台实例配置 < 插件配置页**。

### 连接

| 键 | 默认值 | 说明 |
|---|---|---|
| `gateway_host` | `127.0.0.1` | Gateway 绑定地址 |
| `gateway_port` | `18789` | Gateway 端口 |
| `auto_start_gateway` | `true` | 自动启动内置 Gateway |
| `node_executable` | `node` | Node.js 路径（需 20+） |
| `auth_dir` | `""` | 认证目录，留空自动 `plugin_data/.../whatsapp-auth` |
| `log_level` | `info` | 日志级别：silent/fatal/error/warn/info/debug/trace |

### 权限

| 键 | 默认值 | 说明 |
|---|---|---|
| `dm_policy` | `allowlist` | 私聊策略：allowlist / open / disabled |
| `allow_from` | `[]` | 私聊允许名单，E.164 格式 `+15551234567`；`["*"]` 开放所有 |
| `group_policy` | `disabled` | 群聊策略：allowlist / open / disabled |
| `groups` | `[]` | 允许的群 JID；`["*"]` 允许所有群 |
| `group_allow_from` | `[]` | 群内允许的发送者，留空回退 `allow_from` |

### 消息

| 键 | 默认值 | 说明 |
|---|---|---|
| `media_caption_mode` | `separate` | separate=文字媒体分开发；caption=文字作为媒体描述 |
| `text_chunk_limit` | `4000` | 文字切片长度 |
| `link_preview_single_url` | `true` | 仅单 URL 启用链接预览 |
| `parse_inbound_formatting` | `true` | 入站 WhatsApp 格式 → Markdown |
| `media_album_debounce_seconds` | `2.5` | 相册去抖秒数，0=关闭 |

### 在线状态 & 预回应

| 键 | 默认值 | 说明 |
|---|---|---|
| `typing_indicator` | `true` | 回复前显示 composing |
| `send_read_receipts` | `true` | 发送已读蓝勾 |
| `mark_online` | `false` | 标记 available 在线（关闭可降低被检测为异常客户端的风险） |
| `reaction_level` | `"ack"` | 反应级别，`off` 禁用/`ack` 启用 |
| `ack_reaction_emoji` | `"👀"` | 预回应表情 |
| `ack_reaction_direct` | `true` | 私聊触发预回应 |
| `ack_reaction_group` | `"mentions"` | 群组模式：`always`/`mentions`/`never` |
| `remove_ack_after_reply` | `false` | 回复后自动清除预回应 |
| `ignore_self_messages` | `false` | 忽略自身号码的消息 |

### 指令 & 高级

| 键 | 默认值 | 说明 |
|---|---|---|
| `command_prefix` | `/` | 斜线指令前缀 |
| `register_commands` | `true` | 启用斜线指令识别 |
| `gateway_health_check_interval` | `60` | 健康检查间隔秒数，0=关闭 |

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

## 流式输出

适配器声明 `support_streaming_message=True`，支持 `send_streaming`。工作原理：

1. 首段文本通过 `/send/text` 发送为普通消息
2. 后续增量通过 `/edit/text` 编辑同一消息逐步追加（0.8s 节流）
3. 遇到媒体组件先 flush 文字，再单独发送媒体
4. 编辑失败时自动降级为新消息接续
5. 回复完成后自动清除预回应表情（`remove_ack_after_reply`）

## 斜线指令

启用 `register_commands=true` 后，自动扫描 AstrBot 已注册的指令。入站消息以 `command_prefix` 开头且匹配指令名时，标记为唤醒消息并进入指令处理流程。

## 文件结构

```
astrbot_plugin_whatsapp_adapter/
├── main.py                     # 插件入口
├── whatsapp_adapter.py         # 平台适配器核心
├── whatsapp_client.py          # Gateway HTTP 客户端
├── whatsapp_event.py           # 消息事件（含流式输出）
├── whatsapp_commands.py        # 指令收集与匹配
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
| POST | `/restart` | 重启 socket |
| POST | `/logout` | 登出 |
| POST | `/presence` | 设置在线状态 |
| POST | `/send/text` | 发送文字 |
| POST | `/edit/text` | 编辑文字 |
| POST | `/send/media` | 发送媒体 |
| POST | `/send/reaction` | 发送 emoji |
| POST | `/send/buttons` | 发送按钮 |
| POST | `/send/list` | 发送列表 |
| POST | `/send/poll` | 发送投票 |
| POST | `/mentions/resolve` | 解析 @提及 |

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

等待 5-10 秒后刷新。如果仍然没有，点「重启连接」。确认已执行 `npm install --omit=dev`。

### 扫码后没收到消息

1. 检查平台实例是否已启用
2. 检查 `allow_from` 是否包含你的号码（E.164 格式 `+国家码号码`）
3. 注：插件配置页会覆盖平台实例配置

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
