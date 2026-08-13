# 配置参考

本文以当前代码中的 `_conf_schema.json`、`whatsapp_config_policy.py` 和 WhatsApp 平台模板为准，描述现行配置作用域。历史版本中曾经存在但已经迁移 / deprecated 的字段，不应继续作为新部署方案使用。

## 配置作用域

当前推荐模型不是“所有字段都可以逐实例覆盖”，而是按职责拆分：

1. **插件级 Gateway 配置**：所有实例共享的基准连接 / 进程配置。
2. **插件级消息默认值**：所有实例共享的一般消息行为。
3. **平台实例配置**：真正需要按 WhatsApp 账号变化的访问控制和账号级行为。
4. **内部固定值**：协议 / 兼容安全边界，不对 WebUI 暴露。

旧平台实例如果仍带有历史 Gateway / 行为字段，兼容层可能在迁移期读取它们；不要依赖这些隐藏兼容字段建设新配置。

## 插件级 Gateway 配置

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `gateway_host` | string | `127.0.0.1` | 内置 Gateway 基准监听地址，也是默认实例客户端访问地址 |
| `gateway_port` | int | `18789` | 基准端口；内置多实例的 secondary runtime 会自动向上分配可用端口 |
| `auto_start_gateway` | bool | `true` | 是否由插件 / 平台运行时自动启动内置 Node.js Gateway |
| `node_executable` | string | `node` | Node.js 可执行文件或绝对路径 |
| `auth_dir` | string | 空 | 基准认证目录；留空使用插件数据目录下 `whatsapp-auth` |
| `log_level` | enum | `info` | `silent` / `fatal` / `error` / `warn` / `info` / `debug` / `trace` |

### `gateway_host`

同一容器 / 主机运行 AstrBot 与 Gateway 时，推荐保持 `127.0.0.1`。

如果改成 `0.0.0.0` 或可路由地址，必须自行增加网络层访问控制。Gateway HTTP 本身是插件内部接口，不应当作公网服务暴露。

### `gateway_port`

`18789` 是默认实例的基准端口。启用内置多实例时：

- `id=whatsapp` 优先保留基准端口。
- 其他实例从下一端口开始选择可用端口。
- 实际分配结果会记录到日志。

### `auto_start_gateway`

- `true`：推荐模式。插件负责进程、依赖检查、重连和多实例隔离。
- `false`：连接外部 Gateway。当前标准 WebUI 只有一组插件级 external endpoint，因此同一 AstrBot 进程通常只适合一个 external WhatsApp runtime；多个账号建议拆分进程 / 容器。

### `auth_dir`

留空时默认：

```text
data/plugin_data/astrbot_plugin_whatsapp_adapter/whatsapp-auth
```

多实例下 secondary 实例会使用安全后缀目录，例如：

```text
whatsapp-auth-whatsapp2
```

如果基准 `auth_dir` 是自定义目录，secondary 会在同级创建带实例后缀的独立目录。

## 插件级消息默认值

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `default_link_preview_single_url` | bool | `true` | 纯文本只有一个 URL 时允许生成链接预览 |
| `default_typing_indicator` | bool | `true` | 回复期间发送 composing / paused |
| `default_send_read_receipts` | bool | `true` | 对已接受的消息发送已读回执 |
| `default_mark_online` | bool | `false` | 是否长期保持 available；false 时回复期间仍可短暂在线 |
| `default_parse_inbound_formatting` | bool | `true` | WhatsApp 粗体 / 斜体 / 删除线 / 代码转 AstrBot Markdown |
| `default_media_album_debounce_seconds` | float | `2.5` | 连续图片相簿去抖窗口；`0` 关闭 |
| `default_streaming_edit_throttle` | float | `1.0` | 流式消息编辑的最小间隔；运行时最低保护值为 0.1 秒 |

这些字段会转换为运行时的 `link_preview_single_url`、`typing_indicator`、`send_read_receipts`、`mark_online`、`parse_inbound_formatting`、`media_album_debounce_seconds` 和 `streaming_edit_throttle`。

## 平台实例配置

新平台实例真正持久化的 WhatsApp 专用行为字段如下。

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `dm_policy` | enum | `allowlist` | 私聊：`allowlist` / `open` / `disabled` |
| `allow_from` | list | `[]` | 私聊发送者 allowlist；`["*"]` 表示全部 |
| `group_policy` | enum | `disabled` | 群聊：`allowlist` / `open` / `disabled` |
| `groups` | list | `[]` | 允许接入的群 JID；`["*"]` 表示全部 |
| `group_allow_from` | list | `[]` | 群内发送者 allowlist；留空回退 `allow_from` |
| `media_caption_mode` | enum | `separate` | `separate` / `caption` |
| `ignore_self_messages` | bool | `false` | 是否忽略机器人账号自身发出的入站消息 |
| `pre_ack_emoji` | bool | `true` | 是否启用入站预回应 reaction |
| `pre_ack_emojis` | string | `👀` | 预回应 emoji |
| `pre_ack_private` | bool | `true` | 私聊是否触发 pre-ack |
| `pre_ack_public` | enum | `mentions` | 群聊：`always` / `mentions` / `never` |
| `pre_ack_done_emoji` | string | `✅` | 回复成功后的完成 reaction |
| `apply_ephemeral` | bool | `false` | 出站消息是否套用当前聊天室真实消失消息设置 |

### 私聊策略

`dm_policy=allowlist` 时，只有 `allow_from` 中的身份允许继续处理。

推荐：

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"]
}
```

开放所有私聊：

```json
{
  "dm_policy": "open",
  "allow_from": ["*"]
}
```

`open` 本身已经开放私聊；`["*"]` 主要用于兼容和明确表达。生产部署建议使用具体号码。

### 群聊策略

允许指定群和指定成员：

```json
{
  "group_policy": "allowlist",
  "groups": ["120363000000000000@g.us"],
  "group_allow_from": ["+85212345678"]
}
```

访问控制通过后，是否真正唤醒机器人仍由 AstrBot Core 的 wake / command 规则决定。

### `media_caption_mode`

- `separate`：文本和媒体分开发送。
- `caption`：普通非流式富媒体 MessageChain 中，适合的相邻文本可作为媒体 caption。

流式回复遇到媒体时仍会先 flush 当前文字，再独立发送媒体；不要把 `caption` 理解为“流式媒体也会一直附着在同一条编辑消息里”。

### pre-ack

`pre_ack_public=mentions` 指群聊只有在 @机器人或回复机器人时发送预回应，不代表其它群消息不会进入 AstrBot 的非 wake 事件流程。

### `apply_ephemeral`

只在 Gateway 能取得当前聊天室真实且一致的 ephemeral expiration / setting timestamp 时应用。元数据不完整时宁可发送普通消息，也不会伪造消失消息时间。

## AstrBot Core 负责的行为

以下内容不再由 WhatsApp 插件维护独立配置：

- `wake_prefix`
- CommandFilter 匹配
- 插件 / 指令启用状态
- AstrBot 提供商级 streaming fallback 策略

历史 `command_prefix`、`register_commands` 等字段只作为兼容迁移信息存在，不应继续新增。

## 内部固定限制

运行时包含下列内部安全 / 协议值，当前不作为新 WebUI 配置暴露：

- 普通文本分片基准：`4000`
- 流式单次可编辑分片上限：最多 `3500`
- 通用媒体基准：`50 MiB`
- media-message 内部上限：`100 MiB`
- 文档内部上限：`2048 MiB`
- 音频内部上限：`16 MiB`
- Gateway 健康检查：内部安全默认

实际 WhatsApp / Baileys / 网络端限制可能更低，达到内部上限并不保证服务端一定接受。

## Deprecated / 历史字段

当前代码明确把以下旧字段列入 deprecated / 迁移路径之一：

- `reaction_level`
- `remove_ack_after_reply`
- `inbound_reaction_events`
- `ack_reaction_emoji`
- `ack_reaction_direct`
- `ack_reaction_group`
- 若干早期中文别名字段

特别注意：`inbound_reaction_events` 已不是一个现行开关。纯 reaction 入站消息当前会直接被适配器忽略。

## 代理环境变量

支持：

```text
HTTPS_PROXY
HTTP_PROXY
https_proxy
http_proxy
NO_PROXY
no_proxy
```

优先使用 `HTTPS_PROXY`。

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1,.internal.example.com
```

`NO_PROXY` 支持 `*`、域名、域后缀和可选端口。WebSocket 与媒体请求分别判断是否绕过代理。

目前仅接受 `http://` / `https://` 代理 URL。日志只记录脱敏后的启用状态和主机信息，不应出现代理密码、路径或 query。

## 配置示例

### 专用号码，只允许私聊

```json
{
  "id": "whatsapp",
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

### 指定群聊

```json
{
  "id": "whatsapp",
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "allowlist",
  "groups": ["120363000000000000@g.us"],
  "group_allow_from": ["+85212345678"]
}
```

### 关闭相簿去抖

在插件配置中：

```json
{
  "default_media_album_debounce_seconds": 0
}
```

### 更低频率的流式编辑

```json
{
  "default_streaming_edit_throttle": 1.5
}
```

降低编辑频率通常能减少 WhatsApp 编辑请求数，但实时感会稍弱。

## 相关文档

- [中文使用指南](zh-CN.md)
- [消息与流式行为](messaging.md)
- [多实例 / 多账号](multi-instance.md)
- [故障排查](troubleshooting.md)
- [安全与隐私](security.md)
