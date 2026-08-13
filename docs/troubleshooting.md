# 故障排查

建议按“运行环境 → Gateway → WhatsApp 登录 → 访问控制 → AstrBot 唤醒 / LLM → 出站发送”的顺序排查，避免一开始就反复删除认证目录。

## 1. 登录 Page 显示运行环境不满足

插件 Page 会在启动 Gateway 前检查：

- Node.js
- npm
- Baileys / Node 生产依赖

先确认：

```bash
node --version
npm --version
```

Node.js 必须 `>=20`。

手工安装仓库时可执行：

```bash
npm install --omit=dev
```

如果是 CI / 开发环境，使用：

```bash
npm ci
```

## 2. Gateway 健康但没有二维码

先区分两种情况：

### 已经登录

已登录 session 不会持续提供新二维码，这是正常的。查看 Page 中的连接状态和当前账号 JID。

### 未登录但没有 QR

1. 等待几秒，让 Baileys 建立 socket。
2. 在 Page 中刷新状态 / QR。
3. 如果状态显示 session invalid / logged out / QR expired，可使用“重建登录 session”流程。
4. 查看 `log_level=debug` 下的 Gateway 日志。
5. 检查网络和代理是否能访问 WhatsApp Web。

不要把“没有二维码”直接等同于“必须删除整个 plugin_data”。

## 3. 扫码后一直正在登录 / 又掉回二维码

常见原因：

- 网络不稳定。
- WhatsApp Web 强制 mandatory restart。
- 认证 session 被明确判定失效。
- 旧凭证目录曾被多进程同时使用。

当前 Gateway 会区分：

- 正常扫码后的 515 restart required
- 暂时网络中断
- QR 过期
- 明确认证失效

不要在扫码刚成功时手动反复重启 / 删除目录；先看日志中的 disconnect kind。

## 4. 手机号配对码失败

### 409

当前 session 已经登录，不需要配对码。

### 429

30 秒冷却或并发请求保护触发。稍后再试。

### 501

当前 Baileys / Gateway 环境无法提供手机号 pairing code。改用二维码。

### 503

Gateway 尚未准备到可请求 pairing code 的登录阶段。等待二维码连接初始化完成后再试。

## 5. 能登录但完全收不到私聊

检查平台实例：

```text
dm_policy
allow_from
```

默认是：

```text
dm_policy=allowlist
allow_from=[]
```

这意味着**默认不会接受任何私聊发送者**，必须填 allowlist 或改成 open。

推荐号码格式：

```text
+85212345678
```

如果日志显示 LID / PN 解析，适配器会尝试映射真实号码；若某联系人无法解析 PN，可在 debug 日志中检查身份候选。

## 6. 群聊不触发

默认：

```text
group_policy=disabled
```

至少检查：

1. `group_policy` 是否启用。
2. `groups` 是否包含当前群 JID。
3. `group_allow_from` 是否允许当前发送者；为空时会回退 `allow_from`。
4. 当前消息是否符合 AstrBot 的唤醒规则。

访问控制通过不等于一定会回复。群聊普通消息可以作为非 wake 事件进入 AstrBot，但最终 LLM / command 是否触发由 AstrBot Core 决定。

## 7. 群名称显示成 JID

当前版本会通过 Baileys GroupMetadata 获取群 subject，并响应群 metadata 更新。

如果仍显示 JID：

1. 确认 Gateway 已连接。
2. 查看 `/group/info` 是否能够取到 subject。
3. 检查是否是旧缓存事件 / 老版本插件。
4. 升级到当前版本后重启 Gateway 再测试。

## 8. 图片分成多条 / 合并结果不符合预期

检查插件级：

```text
default_media_album_debounce_seconds
```

默认 `2.5`。

只有满足候选条件的连续图片才会合并。以下内容会打断 / flush：

- 文本
- reply / quote
- 非图片媒体
- 其它结构化语义消息

群聊带 caption 的图片故意保持保守，不会按私聊 caption burst 方式合并。

若完全不需要合并：

```json
{
  "default_media_album_debounce_seconds": 0
}
```

## 9. 图片合并后 caption 顺序不对

当前版本会在 Gateway 给每个图片媒体项附加自己的 caption / mentions，并在 Python 侧交错构建 `caption -> image`。

如果仍出现错位，请收集：

- 是否私聊 / 群聊
- 图片数量
- 每张 caption 文本（可脱敏）
- 是否带 @提及
- 是否中间夹了 reply / 文本
- `albumCount` 和媒体条目顺序的 debug 日志

## 10. 流式回复只发第一段 / 后续变成新消息

这不一定是 bug。WhatsApp 消息编辑可能不可用，Adapter 会自动降级。

检查：

- Gateway `/edit/text` 是否报错。
- 第一次 `/send/text` 是否返回 message ID。
- 是否超过 WhatsApp 可编辑时间 / 协议限制。
- 是否发生网络错误。
- `default_streaming_edit_throttle` 是否被设得过低。

默认节流是 **1.0 秒**。

编辑失败后，Adapter 会尽量只补发尚未可见的部分，而不是重复整段文本。

## 11. 两个并发回复互相覆盖

当前每个事件有独立 streaming state，不应该让两个回复共用同一 message ID。

如果发现：

- A 回复内容出现在 B 的消息里
- 一个回复结束后另一个突然停止编辑
- typing 状态明显被提前清掉

请升级到包含 v0.2.32 并发流式修复的版本或更高版本，并附带两个事件的 target JID、message ID 和时间顺序日志。

## 12. 机器人长时间显示在线

默认 `default_mark_online=false`。

正常行为仍会在回复期间短暂：

```text
available -> composing -> paused -> unavailable
```

如果回复结束后仍长期在线：

1. 确认 `default_mark_online` 没有开启。
2. 检查是否仍有另一个并发回复。
3. 查看 Gateway presence 日志。

## 13. reaction 没触发 AstrBot 插件

当前纯 reaction 入站事件会被 WhatsApp Adapter 忽略。这是现行行为，不是 `inbound_reaction_events` 配置失效。

pre-ack / done reaction 是**机器人向用户消息发送 reaction**，和“接收入站 reaction”是两个不同能力。

## 14. 媒体发送失败

检查：

- 本地文件路径是否存在。
- 容器内路径映射是否正确。
- URL 是否能从 AstrBot / Gateway 环境访问。
- 文件是否超过 WhatsApp 或内部限制。
- 临时文件是否在发送前被其它程序删除。

内部基准包括：通用媒体 50 MiB、文档 2048 MiB、音频 16 MiB；真实服务端限制可能更低。

入站媒体下载失败时，Gateway 会尝试清理 partial 文件，部分过期媒体可通过 Baileys 重新上传请求恢复。

## 15. 代理设置了但仍连接失败

优先检查：

```text
HTTPS_PROXY
NO_PROXY
```

支持 HTTP / HTTPS proxy URL，不支持 SOCKS URL 直接作为 Gateway proxy 环境变量。

如果你使用 v2rayN / Clash 等本地工具，请确保提供的是可访问的 HTTP 代理端口，并确认容器里的 `127.0.0.1` 是否指向容器自己而不是宿主机。

常见 Docker 示例：

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1
```

Gateway 日志会显示脱敏后的代理启用状态。

## 16. secondary 多实例没有二维码

当前插件 WhatsApp 登录 Page 面向默认基准 Gateway。

secondary 内置实例启动后，请查看 AstrBot / Gateway 日志中的终端二维码。

参见 [多实例 / 多账号](multi-instance.md)。

## 17. external Gateway endpoint 已被占用

如果两个 Adapter runtime 指向同一个 external `host:port`，运行时会阻止第二个实例占用。

这是防止账号 / session 串号的安全保护。

当前标准 WebUI 只有一组插件级 external endpoint。多 external 账号请拆分 AstrBot 进程 / 容器，不要关闭保护。

## 18. 内置更新失败

更新器会分阶段执行：

```text
checking -> downloading -> validating -> installing_dependencies -> installing -> reloading
```

必要时会进入 `rolling_back`。

检查：

- GitHub Release 是否可访问。
- Release ZIP 是否来自本仓库稳定版本。
- ZIP 是否符合插件结构和版本要求。
- Node / Python 依赖是否能安装。
- AstrBot 热重载是否报错。

更新失败不应删除 `plugin_data` 的 WhatsApp 认证目录。

## 19. 日志级别

排查一般问题可临时把：

```json
{
  "log_level": "debug"
}
```

不要长期在生产使用 `trace`，除非确实需要。

提交 Issue 前请脱敏：

- 手机号
- JID 中可识别个人的部分
- auth 目录文件内容
- pairing code
- QR 内容
- proxy 用户名 / 密码
- LLM API key

## 20. 仍无法定位

提交 Issue 时建议包含：

- AstrBot 版本
- 插件版本
- Node.js 版本
- 部署方式（裸机 / Docker）
- 是否使用代理
- 单实例 / 多实例
- 问题发生前后的脱敏日志
- 可重复的最小步骤

不要上传 `whatsapp-auth/`。
