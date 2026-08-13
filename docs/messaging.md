# 消息与流式行为

本文记录 WhatsApp Adapter 的消息语义，重点说明平台差异、UMO、唤醒、Reply、相簿与 streaming 行为。

## 入站消息

支持文本、图片、音频、视频、文档、贴纸、位置、联系人、按钮/列表回应、投票、原生活动、引用消息和 mention 元数据。

纯 emoji reaction 当前会被识别后忽略，不继续作为普通 AstrBot 消息事件派发；旧 `inbound_reaction_events` 已属于 deprecated 兼容字段。

## WhatsApp → AstrBot 格式

启用 `default_parse_inbound_formatting=true` 时，常见 WhatsApp `*粗体*`、`_斜体_`、`~删除线~` 与代码格式会转换为 Markdown。

v0.2.39 还修复了颜文字中孤立反引号误吞后续 Markdown 的情况，避免后续内容被错误包装为代码。

## UMO 与稳定公开 ID

从 v0.2.37 起，PN、LID、Hosted、设备 JID 与群 `@g.us` 作为运输层身份保留在 `raw_message` / `target_jid`，AstrBot 会话使用稳定公开投影：

| 场景 | `session_id` |
| --- | --- |
| 私聊 | 已确认 PN 为数字 ID；未解析 LID 为 `lid-N` |
| 群聊，会话隔离关闭 | group JID local part（数字或旧式 `数字-数字`） |
| 群聊，会话隔离开启 | `用户ID_群ID` |

`sender.user_id`、`self_id`、`group_id` 与常用 OneBot 投影字段使用同一套稳定规则。主动发送仍兼容旧 PN、LID、群 JID 与 `用户ID_群JID` session。

Gateway 会尽量在首条未知 LID 消息进入 AstrBot 管道前解析 PN/LID。首次公开的投影会持久化；后续补齐映射不会移动 UMO，若同一联系人曾分裂为两个投影，则按最早公开 ID 合并一次。

## Reply 与群聊唤醒

Reply 是引用语义，不再等同于唤醒信号。

- 引用内容、昵称、message ID 与 QQ 兼容字段会完整保留。
- 仅引用机器人消息不会被伪装成 @机器人。
- 群聊只有真实 @机器人、@全体、命令或 AstrBot Core 其它正常 wake 条件才会触发回复。
- pre-ack reaction 与 wake state 完全独立；即使发送 reaction，也不会把普通群消息标记为已唤醒。
- 出站只有 MessageChain 中存在 `Reply` 才发送 WhatsApp quoted message；分段发送只引用一次。

## 连续图片与相簿去抖

默认 `default_media_album_debounce_seconds=2.5`。

同一 socket generation、chat JID、sender JID 下的候选图片可进入 debounce buffer。后续文本、Reply、非图片媒体或其它结构化消息会先 flush pending 图片，避免顺序倒置。

私聊 captioned image burst 会尽量保留每张图片自己的 caption、mentions、display names 与 mention-all；群聊 captioned image 保持更保守的合并策略。

设为 `0` 可关闭：

```json
{"default_media_album_debounce_seconds": 0}
```

## 出站 MessageChain

常见标准组件包括 `Plain`、`Reply`、`At` / `AtAll`、`Image`、`Record`、`Video`、`File`、`Location`。

WhatsApp 专用组件包括 `WhatsAppButtons`、`WhatsAppList`、`WhatsAppPoll`、`WhatsAppEdit` 等。

## 流式输出

平台声明 `support_streaming_message=True`，默认 `streaming_edit_throttle=1.0s`。

正常流程：

1. 第一段可见文字通过 `/send/text` 发出。
2. 后续内容在 throttle 到期后通过 `/edit/text` 增量更新。
3. 长文本可拆成多个 chunk；已存在 chunk 编辑，新 chunk 发送。
4. 遇到媒体先 flush 文字，再独立发送媒体。
5. stream 结束时强制 final render / flush。

编辑失败、message ID 不可编辑、chunk 结构回溯或网络错误时，Adapter 会停止不安全编辑，并根据已投递 raw offset 只补发需要的剩余内容，尽量避免整段重复。

并发回复各自维护 streaming state；typing presence 会协调，所以一个回复结束不会提前停止另一个仍在进行的回复。

## pre-ack reaction

pre-ack 是机器人向入站消息主动发送 reaction，不等于接收入站 reaction，也不等于唤醒。

默认：`👀` 作为预回应，成功回复后尝试更新为 `✅`。私聊由 `pre_ack_private` 控制；群聊由 `pre_ack_public=always/mentions/never` 控制。

## 输入状态、在线与已读

- `default_typing_indicator=true`：回复期间发送 composing / paused。
- `default_mark_online=false`：不长期保持 available，但回复期间可短暂上线，结束后恢复 unavailable。
- `default_send_read_receipts=true`：对通过访问控制并被接受处理的消息发送已读回执。

## 群名称与群资料

Gateway 从 Baileys GroupMetadata 获取 subject、owner、admins 与 participants，并填充 AstrBot 标准 group name / group info。群 metadata 会缓存并响应 `groups.update`。

## 消失消息

`apply_ephemeral=true` 时仅使用聊天室真实 ephemeral expiration / setting timestamp；metadata 不完整或冲突时发送普通消息，不伪造设置时间。

## AI 原生工具

当前会话可使用：

- `whatsapp_create_poll`
- `whatsapp_share_contact`
- `whatsapp_create_event`

工具不暴露任意目标 JID，并在 Python / Gateway 两层确认当前会话与参数。

## Gateway 内部消息端点

当前包括 `/send/text`、`/edit/text`、`/send/media`、`/send/location`、`/send/reaction`、`/send/buttons`、`/send/list`、`/send/poll`、`/send/contact`、`/send/event`。

这些属于插件内部协议，不应被第三方视为长期稳定公开 API。

## 相关文档

- [配置参考](configuration.md)
- [多实例 / 多账号](multi-instance.md)
- [故障排查](troubleshooting.md)
- [安全与隐私](security.md)
