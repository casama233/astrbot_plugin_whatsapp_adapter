# AstrBot WhatsApp Adapter — 繁體中文使用指南

[简体中文](zh-CN.md) · **繁體中文** · [English](en/index.md)

本插件透過本地 Node.js Gateway 將 AstrBot 接入 WhatsApp Web / Baileys，支援 QR Code / 手機號碼配對、私聊與群聊、媒體、Reply / Mention、串流回覆、多帳號與管理頁面。

> [!IMPORTANT]
> 本插件不是 Meta 官方 WhatsApp Business Cloud API，而是使用 WhatsApp Web 非官方協定。正式使用前請自行評估帳號與服務穩定性風險。

## 安裝需求

- AstrBot `>=4.24.2,<5`
- Node.js `>=20`
- npm
- Python 依賴 `aiohttp>=3.9.0`
- 可連線至 WhatsApp Web 的網路環境

## 快速登入

1. 安裝並啟用插件。
2. 建立 `whatsapp` 平台實例。
3. 初次測試建議使用：

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

4. 保持插件級 `auto_start_gateway=true`。
5. 開啟 **WhatsApp Login** Plugin Page。
6. 使用 WhatsApp 手機端 → **已連結的裝置** 掃描 QR Code，或申請手機號碼配對碼。
7. 顯示已連線後啟用平台實例。

> [!TIP]
> `allow_from` 建議使用 E.164 風格國際號碼。`["*"]` 代表全部允許，不建議在第一次部署時直接開放。

## 配置作用域

### 插件級 Gateway

- `gateway_host`
- `gateway_port`
- `auto_start_gateway`
- `node_executable`
- `auth_dir`
- `log_level`

### 插件級訊息預設值

- `default_link_preview_single_url`
- `default_typing_indicator`
- `default_send_read_receipts`
- `default_mark_online`
- `default_parse_inbound_formatting`
- `default_media_album_debounce_seconds`
- `default_streaming_edit_throttle`

### 平台實例

- `dm_policy` / `allow_from`
- `group_policy` / `groups` / `group_allow_from`
- `media_caption_mode`
- `ignore_self_messages`
- `pre_ack_*`
- `apply_ephemeral`

`default_streaming_edit_throttle` 預設為 **1.0 秒**，執行階段最低保護值為 `0.1s`。

## UMO 與公開 ID

WhatsApp 的 PN、LID、Hosted、裝置 JID 與群組 `@g.us` 屬於運輸層身份，仍保留在 `raw_message` 與 `target_jid`；AstrBot 會話使用穩定公開投影：

| 情境 | `session_id` |
| --- | --- |
| 私聊 | 已確認 PN 為數字 ID；未解析 LID 為 `lid-N` |
| 群聊、會話隔離關閉 | group JID local part（數字或舊式 `數字-數字`） |
| 群聊、會話隔離開啟 | `使用者ID_群ID` |

首次公開的投影會持久化，後續補齊 PN/LID 映射不會讓 UMO 漂移；若同一聯絡人曾分裂為兩個投影，則只按最早公開 ID 合併一次。舊版 PN / LID / 群 JID session 仍可用於主動傳送兼容。

## 群聊喚醒與 Reply

> [!NOTE]
> Reply 的引用內容、暱稱、訊息 ID 會保留給下游插件，但只引用機器人訊息**不等同於 @機器人**。群聊只有真實 @機器人、@全體、命令或 AstrBot 其他正常喚醒條件才會觸發回覆。

pre-ack reaction 與喚醒狀態彼此獨立；即使配置允許先傳送 reaction，也不會把普通群訊息反向標記成已喚醒。

## 多實例 / 多帳號

內建 Gateway 模式下：

- 預設 `id=whatsapp` 使用插件級基準連接埠（預設 `18789`）；
- 次要實例會從後續可用連接埠自動分配；
- 每個實例使用獨立 auth 目錄；
- external Gateway endpoint 在同一 AstrBot process 內有 owner 保護，避免不同帳號誤共用同一 WhatsApp session。

> [!NOTE]
> Login Page 目前主要對應基準 Gateway。次要內建實例 QR Code 主要請查看 AstrBot / Gateway 日誌。

## 訊息與串流

- 私聊短時間連續圖片可合併為一個 AstrBot 事件；caption / mention / 順序會盡量保留。
- 純 inbound reaction 目前不會作為一般 AstrBot 訊息事件派發。
- Streaming 會先傳送第一段，再透過 WhatsApp edit 增量更新；不可安全編輯時會降級而不重複整段已傳內容。
- 並行 streaming 事件使用獨立狀態，typing presence 會協調。

## 代理

Gateway 支援 `HTTPS_PROXY`、`HTTP_PROXY` 與 `NO_PROXY`：

```bash
HTTPS_PROXY=http://host.docker.internal:7897
NO_PROXY=localhost,127.0.0.1
```

目前只支援 `http://` / `https://` proxy URL。

## 安全

> [!WARNING]
> 預設 `127.0.0.1` 綁定是安全邊界的一部分。Gateway HTTP/SSE 不是設計給公網直接使用的 API。

`whatsapp-auth/` 內含 linked-device 登入憑證；解密後進入 AstrBot 的訊息是否傳送至第三方 LLM / 工具，取決於你的 AstrBot 配置。

## Updater v2

管理頁自更新會固定 Release candidate / asset identity、驗證正式 artifact digest、持久化 transaction、切換前停止舊 runtime，reload 後執行健康檢查，失敗時保留 rollback 路徑。

## 更多文件

- [主 README（繁體中文）](../README.zh-TW.md)
- [配置參考](configuration.md)
- [訊息與串流](messaging.md)
- [多實例](multi-instance.md)
- [故障排查](troubleshooting.md)
- [安全與隱私](security.md)
- [開發指南](development.md)
- [英文文件](en/index.md)
