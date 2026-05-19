# AstrBot WhatsApp Adapter

基于 WhatsApp Web/Baileys 的 AstrBot 消息平台适配器。插件采用本地 Gateway 架构：AstrBot Python 插件负责平台适配、事件转换和 WebUI 管理页，Node.js Gateway 负责 WhatsApp Web 连接、二维码登录、重连、媒体下载和消息投递。

## 功能特性

- 支持在 AstrBot 插件管理页内扫码登录 WhatsApp Web。
- 支持私聊消息接入 AstrBot。
- 支持群聊消息接入 AstrBot。
- 支持私聊访问控制：`allowlist`、`open`、`disabled`。
- 支持群聊访问控制：群 JID allowlist、群成员 sender allowlist、`open`、`disabled`。
- 支持入站文本、图片、音频、视频、文档、贴纸占位。
- 支持出站文本、图片、音频语音、视频、文档。
- 支持入站媒体保存和大小限制。
- 支持 accepted inbound message read receipt。
- 支持长文本分片发送。
- Gateway 默认绑定 `127.0.0.1`，适合本地优先部署。

## 官方插件结构

本仓库按 AstrBot 插件规范组织：

- `metadata.yaml`：插件元数据。
- `main.py`：插件入口，注册 Web API 与平台适配器。
- `_conf_schema.json`：WebUI 可视化配置 Schema。
- `requirements.txt`：Python 依赖。
- `pages/whatsapp-login/index.html`：AstrBot 插件 Page，用于扫码登录和管理 Gateway。
- `.astrbot-plugin/i18n/*.json`：WebUI 元数据、配置项和 Page 国际化文案。
- `gateway/whatsapp-gateway.mjs`：本地 WhatsApp Web Gateway。
- `package.json`：Gateway Node.js 依赖声明。

## 环境要求

- AstrBot `>=4.13.0`。
- Python 依赖：`aiohttp`。
- Node.js 20+，推荐 Node.js 22 LTS 或更新版本。
- 可以访问 WhatsApp Web 的网络环境。

## 安装

把插件放入 AstrBot 插件目录，例如：

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
```

安装 Gateway Node.js 依赖：

```bash
cd AstrBot/data/plugins/astrbot_plugin_whatsapp_adapter
npm install --omit=dev
```

如 AstrBot 没有自动安装 `requirements.txt`，手动安装 Python 依赖：

```bash
pip install -r requirements.txt
```

重启 AstrBot 或在 WebUI 重载插件。

## 快速开始

1. 在 AstrBot WebUI 中启用本插件。
2. 在平台适配器页面添加 `whatsapp` 平台。
3. 保持 `auto_start_gateway=true`。
4. 打开 AstrBot 插件管理页，进入本插件详情。
5. 在插件配置中填写 `allow_from`、`dm_policy` 等访问控制项。插件配置页是本插件的主要配置来源。
6. 打开 `WhatsApp 登录` / `whatsapp-login` Page。
7. 使用 WhatsApp 手机端进入「已连接的设备」，扫描页面中的二维码。
8. 连接成功后，在 AstrBot 中启用该平台实例。

二维码通过 AstrBot 官方 Plugin Page bridge 获取。浏览器不会直接访问 Gateway；页面调用 AstrBot 插件 API，插件再访问本机 Gateway。

## 推荐配置

专用 WhatsApp 机器人号码，只允许自己的私聊：

```json
{
  "gateway_host": "127.0.0.1",
  "gateway_port": 18789,
  "auto_start_gateway": true,
  "allow_from": ["+15551234567"],
  "dm_policy": "allowlist",
  "group_policy": "disabled"
}
```

允许指定群聊和指定群成员：

```json
{
  "allow_from": ["+15551234567"],
  "dm_policy": "allowlist",
  "group_policy": "allowlist",
  "group_allow_from": ["+15551234567"],
  "groups": ["120363000000000000@g.us"]
}
```

明确开放所有私聊：

```json
{
  "dm_policy": "open",
  "allow_from": ["*"]
}
```

不建议在公网或未受信环境下使用开放策略。

## 配置说明

AstrBot 官方规范中，插件配置和平台实例配置是两套配置。本插件会按以下顺序合并：

- 先读取内置默认值。
- 再读取平台实例配置，满足 AstrBot 平台实例保存/加载流程。
- 最后读取插件配置页配置，并以插件配置页为准。
- 平台实例配置 key 保持英文，WebUI 会通过平台配置元数据显示中文说明。

- `gateway_host`：Gateway HTTP 绑定地址。默认 `127.0.0.1`。
- `gateway_port`：Gateway HTTP/SSE 端口。默认 `18789`。
- `auto_start_gateway`：平台或插件页访问时自动启动内置 Gateway。
- `node_executable`：Node.js 可执行文件路径。默认 `node`。
- `auth_dir`：WhatsApp Web/Baileys 登录态目录。留空时使用 AstrBot 工作目录下的 `data/astrbot_plugin_whatsapp_adapter/whatsapp-auth`。
- `log_level`：Gateway 日志级别，可用 `silent`、`fatal`、`error`、`warn`、`info`、`debug`、`trace`。
- `allow_from`：私聊发送者 allowlist，建议使用 E.164 格式号码，例如 `+15551234567`。
- `dm_policy`：私聊策略，支持 `allowlist`、`open`、`disabled`。
- `group_policy`：群聊 sender 策略，支持 `allowlist`、`open`、`disabled`。
- `group_allow_from`：群聊中允许触发机器人的发送者号码。为空时回退到 `allow_from`。
- `groups`：允许接入的 WhatsApp 群 JID。使用 `*` 表示允许所有群。
- `send_read_receipts`：是否给已接受的入站消息发送 read receipt。
- `mark_online`：连接 WhatsApp Web 后是否主动标记在线/available。关闭后手机端可能只显示最后上线时间。
- `text_chunk_limit`：出站文本分片长度。
- `media_max_mb`：入站和出站媒体大小上限，单位 MB。

## 插件管理页

插件提供 `pages/whatsapp-login/` 管理页，功能包括：

- 查看 Gateway 状态。
- 查看当前 WhatsApp 账号 JID。
- 显示扫码登录二维码。
- 手动刷新状态。
- 重启 WhatsApp Web 连接。
- 登出当前 WhatsApp Web 会话并重新扫码。

如果页面提示暂未收到二维码，通常是 Gateway 正在连接 WhatsApp Web。等待几秒后刷新，或点击「重启连接」。

## Gateway API

Python 插件通过本地 Gateway API 工作：

- `GET /health`
- `GET /status`
- `GET /qr`
- `GET /events`
- `POST /config`
- `POST /restart`
- `POST /logout`
- `POST /send/text`
- `POST /send/media`
- `POST /send/reaction`

这些接口默认只绑定在 `127.0.0.1`。如果容器化部署需要跨容器访问，请只在可信私有网络内暴露端口。

## 数据目录

默认持久化目录：

```text
<AstrBot 工作目录>/data/astrbot_plugin_whatsapp_adapter/
```

其中：

- `whatsapp-auth/` 保存 WhatsApp Web 登录态。
- `media/` 保存入站媒体文件。

删除 `whatsapp-auth/` 会要求重新扫码登录。

## 安全建议

- WhatsApp 消息属于不可信外部输入，首次测试建议保持 `dm_policy=allowlist`、`group_policy=disabled`。
- 不要在公网暴露 Gateway 端口。
- 只有明确知道风险时才使用 `allow_from=["*"]` 或 `groups=["*"]`。
- 群聊建议同时配置 `groups` 和 `group_allow_from`。
- 若使用个人 WhatsApp 号码，注意自发消息、群聊隐私和授权边界。

## 冒烟测试清单

1. 插件能在 AstrBot WebUI 插件页正常加载。
2. 插件详情页能看到 `WhatsApp 登录` Page。
3. 打开 Page 后能看到 Gateway 状态。
4. 首次启动后能显示二维码。
5. 手机 WhatsApp 扫码后状态变为已连接。
6. 配置 `allow_from` 为测试号码。
7. 用测试号码给 WhatsApp 账号发文本，AstrBot 能收到并回复。
8. 测试图片消息接收。
9. 测试 AstrBot 回复文本。
10. 点击 Page 的「登出并重新扫码」，确认可重新生成二维码。

## 发布注意事项

- 不要提交 `node_modules/`、`__pycache__/`、`data/`。
- 插件市场 zip 限制为 16MB，本仓库通过 `.gitignore` 和 `.gitattributes` 排除大文件和生成文件。
- `package-lock.json` 已保留，方便冒烟测试时复现 Node 依赖。

## 许可证

MIT License。详见 `LICENSE`。
