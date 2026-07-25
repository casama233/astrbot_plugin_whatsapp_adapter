from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

readme_path = ROOT / "README.md"
readme = readme_path.read_text("utf-8")
readme = readme.replace(
    "支持**流式输出**（streaming）、**交互式按钮/列表/投票**、**斜线指令**、**预回应表情**、**打字指示**、**Markdown 格式互转**和**相册去抖**。",
    "支持**流式输出**（streaming）、**交互式按钮/列表/投票**、**预回应表情**、**打字指示**、**Markdown 格式互转**和**相册去抖**；指令与唤醒前缀直接沿用 AstrBot Core。",
)
readme = re.sub(r"(?m)^- \*\*斜线指令系统\*\*：.*\n", "", readme)
readme = re.sub(r"(?m)^├── whatsapp_commands\.py.*\n", "", readme)
readme = readme.replace(
    "4. 在插件配置中填写 `allow_from`、`dm_policy` 等访问控制项（插件配置页优先级最高）",
    "4. 在 WhatsApp 平台实例中填写 `allow_from`、`dm_policy` 等账号访问控制项",
)
readme_config = '''## 配置说明

配置按职责分为三层：

1. **固定行为**：WhatsApp/Gateway 的文字与媒体大小限制由代码和协议决定；指令唤醒完全使用 AstrBot 的 `wake_prefix` 与 `CommandFilter`，本插件不再重复配置。
2. **插件全局默认**：Gateway 默认连接参数，以及所有 WhatsApp 实例共用的 `default_*` 消息行为。
3. **平台实例配置**：某个 WhatsApp 账号独有的连接覆盖、访问控制、媒体 caption、忽略自身消息、reaction 与消失消息行为。

运行时优先级为：**内置默认 < 插件全局默认 < 平台实例显式配置**。

### Gateway 连接

这些字段可在插件页提供全局默认，也可由某个平台实例覆盖：

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
- 指令前缀、指令启用状态和命令匹配由 AstrBot Core 的 `wake_prefix`、CommandFilter 和插件启用状态统一处理。
- Gateway 健康检查间隔保持内部安全默认值。

'''
readme, count = re.subn(
    r"(?ms)^## 配置说明\n.*?(?=^## 自定义消息组件)",
    readme_config,
    readme,
    count=1,
)
if count != 1:
    raise RuntimeError("README config section not found")
readme_path.write_text(readme, "utf-8")

doc_path = ROOT / "docs/zh-CN.md"
doc = doc_path.read_text("utf-8")
doc = doc.replace(
    "WhatsApp Web 平台适配器插件。通过本地 Node.js Gateway 接入 WhatsApp（Baileys），支持扫码登录、私聊/群聊、媒体收发、交互组件（按钮/列表/投票）、流式输出、斜线指令和访问控制。",
    "WhatsApp Web 平台适配器插件。通过本地 Node.js Gateway 接入 WhatsApp（Baileys），支持扫码登录、私聊/群聊、媒体收发、交互组件（按钮/列表/投票）、流式输出和访问控制；指令唤醒沿用 AstrBot Core。",
)
doc = doc.replace(
    "3. 在插件配置页填写 `allow_from`、`dm_policy` 等（插件配置页优先级最高）",
    "3. 在 WhatsApp 平台实例中填写 `allow_from`、`dm_policy` 等账号访问控制",
)
doc_config = '''## 配置说明

配置分为固定行为、插件全局默认和平台实例配置：

- **固定行为**：大小限制由 WhatsApp/Gateway 与内部常量决定；命令匹配与唤醒前缀由 AstrBot Core 处理。
- **插件全局默认**：Gateway 默认连接，以及链接预览、输入/已读/在线状态、入站格式、相簿去抖和流式节流。
- **平台实例配置**：账号连接覆盖、访问控制、caption、忽略自身消息、reaction 和消失消息。

优先级：**内置默认 < 插件全局默认 < 平台实例显式配置**。

### 插件全局默认

| 键 | 默认值 | 说明 |
|---|---|---|
| `default_link_preview_single_url` | `true` | 单 URL 链接预览 |
| `default_typing_indicator` | `true` | 回复时显示 composing |
| `default_send_read_receipts` | `true` | 发送已读回执 |
| `default_mark_online` | `false` | 标记 available 在线 |
| `default_parse_inbound_formatting` | `true` | 入站 WhatsApp 格式转 Markdown |
| `default_media_album_debounce_seconds` | `2.5` | 相簿去抖秒数；`0` 关闭 |
| `default_streaming_edit_throttle` | `1.0` | 流式编辑最小间隔 |

Gateway 的 `gateway_host`、`gateway_port`、`auto_start_gateway`、`node_executable`、`auth_dir`、`log_level` 也可在插件页作为全局默认，并由平台实例覆盖。

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

'''
doc, count = re.subn(
    r"(?ms)^## 配置说明\n.*?(?=^## 交互式组件)",
    doc_config,
    doc,
    count=1,
)
if count != 1:
    raise RuntimeError("Chinese doc config section not found")
doc = re.sub(r"(?ms)^## 斜线指令\n.*?(?=^## 文件结构)", "", doc)
doc = re.sub(r"(?m)^├── whatsapp_commands\.py.*\n", "", doc)
doc = doc.replace("3. 注：插件配置页会覆盖平台实例配置", "3. 平台实例显式值会覆盖插件页的全局默认")
doc_path.write_text(doc, "utf-8")

config_path = ROOT / ".pre-commit-config.yaml"
config = config_path.read_text("utf-8")
hook = '''      - id: update-config-docs
        name: Update config docs
        entry: python .github/scripts/update_config_docs.py
        language: system
        pass_filenames: false
'''
config_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
