# 安全与隐私

本文件描述实际信任边界，不是 Meta / WhatsApp 的安全保证，也不改变 AstrBot 与模型提供商的数据政策。

## 1. Gateway 只应部署在可信网络

默认监听 `127.0.0.1:18789`。内置 Gateway 使用随机的进程级 Bearer token；所有 HTTP / SSE 请求都需要认证。token 只在运行时传递，不写入插件配置。

不要直接暴露到公网。跨容器 / 主机时使用私有网络与防火墙，并在 external Gateway 和 AstrBot 两侧配置相同、非空的 `WA_GATEWAY_TOKEN`。Bearer 认证不能替代网络隔离，也不是面向公网的完整身份系统。SSE 订阅数量受限。

## 2. 登录目录、二维码与配对码都是凭证

默认登录目录是 `data/plugin_data/astrbot_plugin_whatsapp_adapter/whatsapp-auth/`，secondary 账号使用带实例后缀的独立目录。不要提交、公开上传、复制到其他账号或放在宽松权限的共享目录中。备份应按密钥处理；怀疑泄露时，在手机 WhatsApp 的“已连接的设备”中移除设备后重新登录。

二维码和手机号配对码属于短期凭证，不要截图公开或纳入日志采集。Page / Gateway 避免主动记录手机号与配对码，但外部代理、浏览器扩展和日志采集器仍可能另行记录。

## 3. 本地媒体：保留 AstrBot 插件相容性，明确保护登录目录

**内置 Gateway 的默认允许范围不是仅 temp，而是 AstrBot 的 `data/` 加上 Gateway 临时目录。**这是为了保留 `data/temp/`、`data/temp_images/`、各插件 `plugin_data/` 中的图片、音频与普通文件输出；不会按 `.json` 等副档名统一禁发。

参考上游现有实现（2026-09-08 核对）：[Telegram](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/telegram/tg_event.py) 使用消息元件的 `convert_to_file_path()` / `get_file()`；[Discord](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/discord/discord_platform_event.py) 使用 `MediaResolver` / `get_file()`。不能要求所有正常插件只把媒体输出到一个 temp 目录。但本插件另有 HTTP Gateway 边界，仍需保留来源检查与认证。

`WA_MEDIA_ALLOWED_ROOTS` 的生效规则：

| 设置 | 内置 Gateway | 独立 external Gateway |
| --- | --- | --- |
| 未设置 | 自动加入 AstrBot `data/` | 不自动加入 AstrBot `data/` |
| 设置非空值 | 保留部署者指定的根目录列表，不再覆盖 | 使用该列表 |
| 显式设置为空字符串 | 只允许 Gateway 临时目录 | 只允许 Gateway 临时目录 |

列表使用操作系统路径分隔符：Linux/macOS 为 `:`，Windows 为 `;`。临时目录始终属于允许范围；显式非空列表替换内置的自动 `data/` 范围，不是再把 `data/` 隐式加回。

本地来源必须是普通文件，并以 `realpath` 检查真实目标，防止路径前缀或符号链接绕过。下列范围优先拒绝，即使也在允许根目录中：

- 当前 Gateway 实际的 `WA_AUTH_DIR`，未设置时使用默认登录目录；
- 本插件 `WA_DATA_DIR` 下的 `whatsapp-auth` 和 `whatsapp-auth-*` 默认多账号登录目录；
- 以上目录内的活动会话、历史 session 与密钥文件。

`file://` URL 仍不被 Gateway 原始 `/send/media` 接口接受。普通本地文件发送完不会被删除；Gateway 自己下载的出站临时文件才在发送结束后清理。

**这不是 AstrBot 插件的文件系统沙箱，也不保证识别所有敏感文件。**其他插件配置、数据库、复制到别处的凭证及其他自定义账号目录仍需部署者隔离。高安全环境应显式指定窄范围的媒体输出根目录，不要将秘密与可发送文件混放。能执行本地 Python 的插件本来就具有宿主进程权限，不能靠这个 HTTP 检查隔离恶意插件。

## 4. 远端媒体与解密后的文件

HTTP / HTTPS 媒体先安全下载为临时文件，再交给 Baileys。保留以下限制：拒绝非公网 IP、固定连接到已验证的 DNS 地址、每次重定向重新验证、限制重定向次数、大小和下载时间（包括持续滴流时的绝对截止时间）。默认远端出站上限为 32 MiB；`WA_OUTBOUND_MEDIA_MAX_MB` 可调整，但不能超过实现硬上限。

不要改用 localhost / 私网 URL 绕过文件根目录配置。跨容器的本机路径须在 Gateway 所在容器可见。

入站媒体已在 Linked Device 解密，可能保存在 AstrBot 临时或插件数据目录。它们不再仅受 WhatsApp 传输加密保护；请限制文件系统访问，设置清理与备份保留策略，不要把整个 `plugin_data` 同步到公共存储。

## 5. 访问控制、LLM 与工具

首次使用建议 `dm_policy=allowlist`、`group_policy=disabled`，只放行测试号码。`allow_from`、`groups`、`group_allow_from` 决定哪些消息交给 AstrBot，不能阻止 WhatsApp 账号本身接收消息，也不替代群权限或账号安全。`["*"]` 会明显扩大范围。

Gateway 在第一份有效配置到达前拒绝入站消息。被拒绝的 SSE 事件只保留最少的原因、message ID 与时间戳，不广播正文、手机号或发送者 JID。

解密后的内容是否交给 LLM、embedding / RAG、外部工具或其他插件，取决于 AstrBot 配置。WhatsApp 端到端加密不会阻止自己的 Linked Device 将内容交给这些服务；部署前应确认其数据政策。

原生投票、联系人与活动 AI 工具只允许作用于当前 WhatsApp 会话，不接受任意 target JID，并由 Python 与 Gateway 分层检查。

## 6. 多实例与代理

两个 runtime 不应静默共用同一个 external Gateway endpoint，否则也会共用 WhatsApp session。不要移除 owner 冲突保护；不同账号使用独立端口与登录目录。

代理支持 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`。代理日志尽量只记录脱敏元数据，不记录用户名、密码、路径和 query；仍需保护环境变量与容器配置。

## 7. 更新器信任边界

内置更新器信任本仓库稳定 GitHub Release，验证候选身份、正式 artifact digest、HTTPS 来源、ZIP 路径穿越、重复路径、符号链接 / 特殊文件、大小、插件名称 / 版本与 AstrBot 相容范围，并进行暂存依赖安装、语法检查、目录切换与 reload 后健康检查。

更新不应替换 `plugin_data` 的登录态。Python requirements 改变时，内置更新器拒绝修改 AstrBot 共用环境，须使用 AstrBot 插件管理器更新并重启。远端更新仍意味着信任仓库与供应链；不能消除所有断电窗口。保留插件目录与 `plugin_data` 的独立备份，详见 [发布流程](../RELEASING.md)。

## 8. 日志脱敏与非官方协议

公开日志前删除手机号、个人 JID、QR、配对码、auth 内容、cookies / tokens、代理密码、LLM API key，以及未获授权的正文与媒体 URL。不要上传完整 `whatsapp-auth/` 来复现问题。

本项目是非官方 Baileys / WhatsApp Web 协议，不是 Meta Business Cloud API。协议、Linked Device、编辑 / 媒体能力和账号风控都可能改变；关键业务或高价值账号部署前应自行评估风险。

相关文档：[配置参考](configuration.md) · [多实例](multi-instance.md) · [故障排查](troubleshooting.md)
