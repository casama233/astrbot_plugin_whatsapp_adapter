# 安全与隐私

本文件描述部署和使用本插件时需要明确的信任边界。它不是 Meta / WhatsApp 的安全保证，也不改变 AstrBot 自身的隐私与模型提供商配置。

## 1. Gateway HTTP 只应本地使用

Node.js Gateway 默认绑定：

```text
127.0.0.1:18789
```

请尽量保持 loopback。

Gateway HTTP 是插件内部协议，底层接口本身不应被理解成一套面向公网、带独立用户认证的 Web 服务。插件管理 Page 调用的是 AstrBot Dashboard 鉴权保护下的插件 API，但这不等于你可以把 Node Gateway 端口直接暴露出去。

如果必须跨容器 / 主机访问：

- 使用私有网络。
- 配置防火墙 / security group。
- 不要直接映射到公网 `0.0.0.0:18789`。
- 只允许 AstrBot 所在网络访问。

## 2. WhatsApp 认证目录就是账号凭证

默认：

```text
data/plugin_data/astrbot_plugin_whatsapp_adapter/whatsapp-auth/
```

其中包含 Linked Device / Baileys 登录状态。拿到可用认证目录的人可能能够以已链接设备身份访问账号消息。

因此：

- 不要提交到 Git。
- 不要上传到公开 Issue。
- 不要在多人共享目录中使用宽松权限。
- 备份时按密钥 / token 处理。
- 多实例不要复制其它账号的 auth 目录。

如果怀疑凭证泄露，应在手机 WhatsApp 的“已连接的设备”中移除对应设备，并重新登录。

## 3. 二维码与 pairing code

二维码和手机号 pairing code 都是短期登录凭证。

- 不要截图发到公开聊天。
- 不要写入 Issue。
- 不要加入自动化日志采集。
- Page / Gateway 代码会避免主动记录手机号和 pairing code，但外部反向代理 / 浏览器插件仍可能产生自己的访问日志。

## 4. 入站媒体是解密后的本地文件

WhatsApp Web 在 Linked Device 上完成消息解密后，Gateway 可能把媒体保存到：

```text
data/plugin_data/astrbot_plugin_whatsapp_adapter/media/
```

这些文件已经不再受到“仅在 WhatsApp 传输层中”的端到端加密保护。

请根据自己的隐私要求：

- 限制文件系统访问。
- 定期清理不再需要的媒体。
- 不要把整个 plugin_data 当作普通日志目录同步到公共存储。

## 5. AstrBot 与 LLM 提供商

消息进入 AstrBot 后，是否会被发送到：

- 第三方 LLM API
- embedding / RAG 服务
- 外部工具
- 其它 AstrBot 插件

取决于你的 AstrBot 配置。

WhatsApp 端到端加密保护的是 WhatsApp 设备之间的传输，不会阻止你自己的 Linked Device / AstrBot 在解密后把内容交给其它服务。

部署前应确认所用 LLM 提供商的数据政策与业务合规要求。

## 6. 访问控制不是 WhatsApp 权限系统的替代品

本插件提供：

- `dm_policy`
- `allow_from`
- `group_policy`
- `groups`
- `group_allow_from`

这些是 AstrBot 接入层控制，用来决定哪些消息继续处理。

它们不能阻止 WhatsApp 账号本身收到消息，也不能替代 WhatsApp 群权限、手机端账号安全或网络隔离。

建议默认：

```text
dm_policy=allowlist
group_policy=disabled
```

## 7. AI 原生工具的收件人边界

`whatsapp_create_poll`、`whatsapp_share_contact`、`whatsapp_create_event` 只允许作用于当前 WhatsApp 会话。

实现上：

- 工具没有 target JID 参数。
- Python 层核对当前 event target。
- Gateway 层再次校验输入。

这是为了降低模型通过工具把内容发到其它会话的风险。

## 8. external Gateway 与多实例

同一 AstrBot 进程中两个 runtime 不允许静默共用相同 external `host:port`。

原因是共用一个 Gateway 就等于共用一个 WhatsApp session，可能造成：

- 消息串号
- 身份缓存污染
- 引用错会话
- 认证目录混用

不要关闭这个 owner 冲突保护。

## 9. HTTP / HTTPS 代理凭证

支持 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`。

Gateway 日志会尽量只输出脱敏的代理元数据，不记录：

- 用户名
- 密码
- URL path
- query

仍应避免把带明文密码的环境变量输出到容器诊断页面或进程列表。

## 10. 内置更新器信任边界

插件 Page 的独立更新器只接受本仓库稳定 GitHub Release，并执行多层校验：

- HTTPS / 可信仓库来源
- ZIP 路径穿越
- 重复路径
- 符号链接 / 特殊文件
- 插件名称
- 版本格式
- AstrBot 兼容范围
- 临时目录依赖安装
- Python 语法检查
- 原子目录切换
- reload 失败恢复

更新过程不应该替换 `plugin_data` 的 WhatsApp auth。

即便如此，任何“远程更新代码”的机制都意味着信任 GitHub 仓库和 Release 供应链。高安全环境可以禁用主动更新操作，改用自己的镜像 / 审核流程部署。

## 11. 日志与 Issue 脱敏

公开日志前删除：

- 手机号码
- 可识别个人的 JID
- QR 内容
- pairing code
- auth 文件内容
- cookies / tokens
- proxy 密码
- LLM API key
- 私聊 / 群聊正文和媒体 URL（除非已获授权）

不要上传完整 `whatsapp-auth/` 来“方便复现”。

## 12. 非官方协议风险

本项目基于 Baileys / WhatsApp Web，而不是官方 Business Cloud API。

可能存在：

- Web 协议变更造成暂时失效
- Linked Device 行为变化
- 编辑 / 媒体 / 互动消息能力随 WhatsApp 改动
- 账号风控策略变化

在关键业务、合规业务或高价值账号上使用前，应自行评估是否应该改用官方 API。

## 相关文档

- [配置参考](configuration.md)
- [多实例 / 多账号](multi-instance.md)
- [故障排查](troubleshooting.md)
