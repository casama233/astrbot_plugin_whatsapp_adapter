# AstrBot WhatsApp Adapter

基于 WhatsApp Web/Baileys 的 AstrBot 消息平台适配器。采用本地 Gateway 架构：Python 插件负责平台适配、事件转换和 WebUI 管理页，Node.js Gateway 负责 WhatsApp Web 连接、二维码登录、重连、媒体下载和消息投递。

支持**流式输出**（streaming）、**交互式按钮/列表/投票**、**预回应表情**、**打字指示**、**Markdown 格式互转**和**相册去抖**；指令与唤醒前缀直接沿用 AstrBot Core。

## 功能特性

- 插件管理页内扫码登录 WhatsApp Web
- 私聊 & 群聊消息接入
- 私聊访问控制：`allowlist` / `open` / `disabled`
- 群聊访问控制：群 JID allowlist + 群成员 sender allowlist
- 入站文本、图片、音频、视频、文档、贴纸、位置、联系人、按钮回应、列表回应、投票
- 出站文本（自动 Markdown ➜ WhatsApp 格式转换，长文本分片）、图片、音频、视频、文档、贴纸
- 出站交互组件：`WhatsAppButtons`、`WhatsAppList`、`WhatsAppPoll`、`WhatsAppEdit`（消息编辑）
- **流式输出**（streaming）：首次回复作为新消息发送，后续更新通过编辑同一消息逐步追加（带节流）
- **预回应表情**（pre-ack）：被 @ 或回复时先发一个 emoji 反应，降低 LLM 响应延迟感知
- **打字指示**：发送前显示 "composing"，发送完恢复 "available"
- **媒体说明文字模式**：`separate`（分开发送）或 `caption`（作为媒体描述）
- **相册去抖**：短时间内的连拍多图合并为一条消息
- **Markdown 格式互转**：入站 WhatsApp `*粗体*` `_斜体_` `~删除线~` ```代码``` 自动转 Markdown
- **链接预览控制**：仅单 URL 消息启用预览卡片
- **已读回执 & 在线状态**
- **入站表情回应事件**（可选）：将用户 emoji reaction 转为 AstrBot 事件
- **LID → PN 归一化**：稳定私聊会话 ID
- **健康检查 & 自动重连**
- **热重载**：修改配置无需重启 AstrBot
- Gateway 默认绑定 `127.0.0.1`，适合本地优先部署

## 插件结构

```
astrbot_plugin_whatsapp_adapter/
├── main.py                      # 插件入口（Star 子类），注册 Web API 与公告板适配器
├── whatsapp_adapter.py          # 平台适配器（Platform 子类），核心消息收发逻辑
├── whatsapp_client.py           # Gateway HTTP 客户端 + 子进程管理
├── whatsapp_event.py            # 消息事件（AstrMessageEvent 子类），流式输出
├── whatsapp_components.py       # 自定义 WhatsApp 消息组件
├── whatsapp_helpers.py          # 共享工具函数
├── metadata.yaml                # 插件元数据
├── _conf_schema.json            # WebUI 插件配置 Schema
├── requirements.txt             # Python 依赖
├── package.json                 # Node.js Gateway 依赖
├── .astrbot-plugin/i18n/       # WebUI 国际化
│   ├── en-US.json
│   └── zh-CN.json
├── pages/whatsapp-login/       # 插件管理页（扫码登录 UI）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── gateway/
│   └── whatsapp-gateway.mjs    # Node.js WhatsApp Web Gateway（Baileys）
└── docs/
    └── zh-CN.md                 # 中文文档
```

## 环境要求

- AstrBot `>=4.13.0`
- Python 依赖：`aiohttp>=3.9.0`
- Node.js 20+，推荐 Node.js 22 LTS
- 可以访问 WhatsApp Web 的网络环境

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
npm install --omit=dev
pip install -r requirements.txt
```

重启 AstrBot 或在 WebUI 重载插件。

## 快速开始

1. 在 AstrBot WebUI 启用插件
2. 添加 `whatsapp` 平台适配器
3. 保持 `auto_start_gateway=true`
4. 在 WhatsApp 平台实例中填写 `allow_from`、`dm_policy` 等账号访问控制项
5. 打开 `WhatsApp 登录` / `whatsapp-login` Page
6. 使用 WhatsApp 手机端「已连接的设备」扫描二维码
7. 连接成功后启用平台实例

## 配置说明

配置按职责分为三层：

1. **固定行为**：WhatsApp/Gateway 的文字与媒体大小限制由代码和协议决定；指令唤醒完全使用 AstrBot 的 `wake_prefix` 与 `CommandFilter`，本插件不再重复配置。
2. **插件全局默认**：Gateway 默认连接参数，以及所有 WhatsApp 实例共用的 `default_*` 消息行为。
3. **平台实例配置**：某个 WhatsApp 账号独有的访问控制、媒体 caption、忽略自身消息、reaction 与消失消息行为。

运行时优先级为：**内置默认 < 插件全局默认 < 平台实例显式配置**。

### Gateway 连接

这些字段只在插件配置页设置，登录管理页与所有 WhatsApp 平台实例共用同一组 Gateway 连接：

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `gateway_host` | string | `127.0.0.1` | Gateway HTTP 绑定地址 |
| `gateway_port` | int | `18789` | Gateway HTTP/SSE 端口 |
| `auto_start_gateway` | bool | `true` | 自动启动内置 Node.js Gateway |
| `node_executable` | string | `node` | Node.js 可执行文件路径 |
| `auth_dir` | string | `""` | WhatsApp 登录态目录，留空自动选择插件数据目录 |
| `log_level` | string | `info` | Gateway 日志级别 |

### 插件全局消息默认

| 键 | 默认值 | 说明 |
|---|---|---|
| `default_link_preview_single_url` | `true` | 纯文字仅包含一个 URL 时生成链接预览 |
| `default_typing_indicator` | `true` | 回复期间发送 composing 状态 |
| `default_send_read_receipts` | `true` | 对已接受消息发送已读回执 |
| `default_mark_online` | `false` | 定期发送 available 在线状态 |
| `default_parse_inbound_formatting` | `true` | 将 WhatsApp 原生格式转为 Markdown |
| `default_media_album_debounce_seconds` | `2.5` | 连续图片合并为相簿的等待时间；`0` 关闭 |
| `default_streaming_edit_throttle` | `1.0` | 流式消息编辑的最小间隔（秒） |

### 平台实例配置

| 键 | 默认值 | 说明 |
|---|---|---|
| `dm_policy` | `allowlist` | 私聊策略：`allowlist` / `open` / `disabled` |
| `allow_from` | `[]` | 私聊允许名单；`["*"]` 表示全部允许 |
| `group_policy` | `disabled` | 群聊策略：`allowlist` / `open` / `disabled` |
| `groups` | `[]` | 允许的群 JID；`["*"]` 表示全部允许 |
| `group_allow_from` | `[]` | 群内允许的发送者，留空回退到 `allow_from` |
| `media_caption_mode` | `separate` | `separate` 分开发送；`caption` 将紧邻媒体前的文字作为描述 |
| `ignore_self_messages` | `false` | 忽略机器人账号自身发送的消息 |
| `pre_ack_emoji` | `true` | 启用预回应 reaction |
| `pre_ack_emojis` | `👀` | 预回应表情 |
| `pre_ack_private` | `true` | 私聊触发预回应 |
| `pre_ack_public` | `mentions` | 群聊：`always` / `mentions` / `never` |
| `pre_ack_done_emoji` | `✅` | 回复完成时使用的 reaction |
| `apply_ephemeral` | `false` | 外寄消息是否套用聊天室消失消息计时器 |

`caption` 只影响普通富媒体 MessageChain；流式回复中途出现媒体时仍会先完成文字，再独立发送媒体。

### 固定限制与 AstrBot 通用行为

- 出站文字切片、图片/视频/音频/文档大小限制为内部常量，不接受插件或平台配置覆盖。
- 指令前缀、指令启用状态和命令匹配由 AstrBot Core 的 `wake_prefix`、CommandFilter 和插件启用状态统一处理。旧版非 `/` 的 WhatsApp 专用前缀会兼容一个版本并输出迁移提示。
- Gateway 健康检查间隔保持内部安全默认值。

## 自定义消息组件

插件提供 6 个 WhatsApp 专用组件，可在 `MessageChain` 中使用：

```python
from whatsapp_components import WhatsAppButtons, WhatsAppButton, WhatsAppList, WhatsAppListRow, WhatsAppListSection, WhatsAppPoll, WhatsAppEdit

# 按钮消息（最多 3 个按钮）
WhatsAppButtons(
    body="选择操作：",
    buttons=[WhatsAppButton(text="确认", id="confirm"), WhatsAppButton(text="取消", id="cancel")],
    footer="Powered by AstrBot",
)

# 列表消息
WhatsAppList(
    title="菜单",
    description="请选择一个选项",
    button_text="查看选项",
    sections=[WhatsAppListSection(title="分组A", rows=[WhatsAppListRow(title="选项1", id="opt1")])],
    footer="底部文字",
)

# 投票
WhatsAppPoll(name="你喜欢的颜色？", options=["红", "蓝", "绿"], selectable_count=1)

# 编辑已发送消息
WhatsAppEdit(message_id="xxx", text="新的内容")
```

## 流式输出

适配器注册时声明了 `support_streaming_message=True`。当 AstrBot 调用 `send_streaming` 时：

1. 首条文本通过 `POST /send/text` 发送为普通消息
2. 后续增量通过 `POST /edit/text` 编辑同一条消息，0.8s 节流
3. 遇到媒体组件（图片/按钮等）时先 flush 文字，再单独发送媒体
4. 如果编辑失败（WhatsApp 限制消息编辑有时效），自动降级为发送新消息
5. 支持 `type="break"` 的 MessageChain 分段

## 推荐配置

专用号码，仅私聊（Gateway 地址和端口在插件配置页设置）：
```json
{
  "allow_from": ["+15551234567"],
  "dm_policy": "allowlist",
  "group_policy": "disabled"
}
```

允许指定群聊：
```json
{
  "allow_from": ["+15551234567"],
  "dm_policy": "allowlist",
  "group_policy": "allowlist",
  "group_allow_from": ["+15551234567"],
  "groups": ["120363000000000000@g.us"]
}
```

开放所有私聊（谨慎）：
```json
{
  "dm_policy": "open",
  "allow_from": ["*"]
}
```

## 插件管理页

`pages/whatsapp-login/` 提供完整的管理界面：
- Gateway 运行状态 & 健康状态
- 当前 WhatsApp 账号 JID
- 二维码展示（扫码登录）
- 手动刷新状态
- 重启 WhatsApp Web 连接
- 登出并重新扫码

## Gateway API

Python 插件通过 HTTP 与 Gateway（`127.0.0.1:18789`）通信：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/status` | 完整状态（含连接状态、账号 JID、配置摘要） |
| GET | `/qr` | 获取二维码 |
| GET | `/events` | SSE 事件流 |
| POST | `/config` | 推送访问控制配置 |
| POST | `/restart` | 重启 Baileys socket |
| POST | `/logout` | 登出并清除认证 |
| POST | `/session/reset` | 隔离失效认证并建立全新扫码 session |
| POST | `/presence` | 设置回复期间的状态（composing / paused）；关闭常驻在线时会在回复结束后自动恢复离线 |
| POST | `/send/text` | 发送文字（含引用回复、提及、链接预览） |
| POST | `/edit/text` | 编辑已发送文字 |
| POST | `/send/media` | 发送媒体（image/video/audio/document/sticker） |
| POST | `/send/reaction` | 发送 emoji 反应 |
| POST | `/send/buttons` | 发送交互按钮 |
| POST | `/send/list` | 发送列表选择 |
| POST | `/send/poll` | 发送投票 |
| POST | `/mentions/resolve` | 解析 @提及为 JID |

## 数据目录

插件数据遵循 AstrBot 官方规范，存储于 `data/plugin_data/astrbot_plugin_whatsapp_adapter/`：

```
<AstrBot Data>/plugin_data/astrbot_plugin_whatsapp_adapter/
├── config.json          # 插件配置覆盖（手工编辑）
├── whatsapp-auth/       # WhatsApp Web 登录态（删除后需重新扫码）
└── media/               # 入站媒体文件
```

> 从旧版本 (`data/astrbot_plugin_whatsapp_adapter/`) 升级时，插件会在首次访问时自动将数据复制到新路径。

## 安全建议

- 首次测试使用 `dm_policy=allowlist` + `group_policy=disabled`
- 不在公网暴露 Gateway 端口（默认绑定 `127.0.0.1`）
- `allow_from=["*"]` 和 `groups=["*"]` 仅在明确知道风险时使用
- 群聊建议同时配置 `groups` 和 `group_allow_from`
- 使用个人号码时注意自发消息和授权边界

## 冒烟测试清单

1. 插件能在 WebUI 正常加载
2. 插件详情页可见 `WhatsApp 登录` Page
3. Page 能显示 Gateway 状态
4. 首次启动显示二维码
5. 扫码后状态变为 connected
6. 配置 `allow_from` 为测试号码
7. 测试号码发送文本，AstrBot 能收到并回复
8. 测试图片消息接收
9. 测试 AstrBot 回复文本
10. 登出后重新生成二维码

## 发布注意事项

- 不提交 `node_modules/`、`__pycache__/`、`data/`
- 插件市场 zip 限制 16MB，通过 `.gitignore` / `.gitattributes` 排除
- 保留 `package-lock.json` 以复现 Node 依赖
- `metadata.yaml` 中 `version` 字段不带 `v` 前缀，使用 semver 格式
- `astrbot_version` 遵循 PEP 440 规范，不加 `v` 前缀

## 致谢

部分设计思路参考了 OpenClaw 项目。

## 许可证

MIT License。详见 `LICENSE`。
