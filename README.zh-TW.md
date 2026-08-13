<div align="center">

<img src="./logo.png" alt="AstrBot WhatsApp Adapter" width="168" />

# AstrBot WhatsApp Adapter

**讓 AstrBot 透過本地 Gateway 接入 WhatsApp Web / Baileys**<br>
掃碼登入 · 私聊/群聊 · 富媒體 · 串流回覆 · 多帳號 · 管理頁面

[简体中文](README.md) · **繁體中文** · [English](README.en.md)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/casama233/astrbot_plugin_whatsapp_adapter?label=version&color=ff69b4)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.24.2%2B-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-brightgreen.svg)](https://nodejs.org/)
[![Python CI](https://img.shields.io/badge/Python_CI-3.11-blue.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![Tests](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml/badge.svg)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/actions/workflows/tests.yml)
[![GitHub Stars](https://img.shields.io/github/stars/casama233/astrbot_plugin_whatsapp_adapter?style=flat&logo=github)](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/stargazers)
![動態訪問量](https://count.kjchmc.cn/get/@astrbot_plugin_whatsapp_adapter?theme=gelbooru)

</div>

> [!IMPORTANT]
> 本專案使用 **WhatsApp Web 非官方協定棧 / Baileys**，不是 Meta 官方 WhatsApp Business Cloud API。協定變更可能造成短期相容問題，正式使用前請自行評估帳號、穩定性與業務風險。

## ✨ 主要能力

| 能力 | 說明 |
| --- | --- |
| 🔐 掃碼 / 手機號碼配對 | 在 AstrBot Plugin Page 登入預設帳號，也支援手機配對碼 |
| 💬 私聊與群聊 | `allowlist` / `open` / `disabled`，並支援群組成員級控制 |
| 🖼️ 富媒體 | 圖片、音訊、影片、文件、貼圖、位置、聯絡人、按鈕、列表、投票等 |
| ⚡ 串流回覆 | 先傳送、再持續編輯；不可編輯時安全降級為後續訊息 |
| 🆔 穩定公開 UMO | PN 使用數字 ID，未解析 LID 使用 `lid-N`；運輸層 JID 不寫入公開 session ID |
| 🧠 AstrBot 喚醒語義 | 只引用機器人訊息不會觸發；真實 @、@全體、命令等才形成喚醒 |
| 👥 多實例 / 多帳號 | 內建 Gateway 自動隔離連接埠與認證目錄 |
| 🔄 Updater v2 | 固定 Release candidate、驗證 artifact、持久 transaction、健康檢查與回復 |
| 🌍 三語 UI | `zh-CN` / `zh-TW` / `en-US`，管理頁可在執行時切換語言 |

> [!NOTE]
> 目前 WhatsApp Login Page 主要管理預設 / 基準 Gateway。次要內建實例的 QR Code 主要透過 AstrBot / Gateway 日誌取得。

## 🚀 快速開始

1. 透過 AstrBot 插件市場 / Cloud 安裝，或手動 clone 本倉庫。
2. 確保宿主提供 **Node.js 20+** 與 npm。
3. 新增 `whatsapp` 平台實例。
4. 初次測試只開放自己的號碼：

```json
{
  "dm_policy": "allowlist",
  "allow_from": ["+85212345678"],
  "group_policy": "disabled"
}
```

5. 保持 `auto_start_gateway=true`。
6. 開啟 **WhatsApp Login** Page，掃描 QR Code 或使用手機號碼配對。
7. 顯示已連線後啟用平台實例並傳送測試訊息。

> [!TIP]
> 第一次上線先不要使用 `allow_from=["*"]`。先完成私聊、媒體與串流回覆驗證，再逐步放寬權限。

## 🧱 架構

```text
AstrBot
  │ Python Platform Adapter / HTTP + SSE
  ▼
Local WhatsApp Gateway (Node.js)
  │ Baileys / WhatsApp Web
  ▼
WhatsApp
```

預設 Gateway 為 `127.0.0.1:18789`。

> [!WARNING]
> 不要把 Gateway HTTP/SSE 直接暴露至公網。`whatsapp-auth/` 是 WhatsApp 登入憑證，請視為敏感資料。

## ⚙️ 配置與多實例

配置分成插件級 Gateway、插件級 `default_*` 訊息預設值，以及平台實例的帳號級存取控制。`default_streaming_edit_throttle` 目前預設為 **1.0 秒**。

多實例模式下，預設帳號保留基準連接埠；次要內建 Gateway 會自動分配後續可用連接埠並使用獨立認證目錄。

## 🆔 UMO 與群聊喚醒

WhatsApp PN、LID、Hosted、裝置 JID 與 `@g.us` 仍保留在 `raw_message` / `target_jid` 作為運輸層資訊。公開投影中，已確認 PN 使用數字 ID，未解析 LID 使用 `lid-N`；群組保留 group JID local part（數字或舊式 `數字-數字`），會話隔離開啟時為 `使用者ID_群ID`。首次公開的投影會持久化，後續補齊 PN/LID 映射不會讓 UMO 漂移。

> [!NOTE]
> Reply 引用內容、暱稱與 message ID 仍會完整保留給其他插件，但**只引用機器人訊息本身不等同 @機器人**。群聊只有真實 @、@全體、命令或 AstrBot 其他正常喚醒條件才會觸發回覆；pre-ack reaction 也不會反向改變喚醒狀態。

## 🌍 語言與文件

| 語言 | README | 使用指南 |
| --- | --- | --- |
| 简体中文 | [README.md](README.md) | [docs/zh-CN.md](docs/zh-CN.md) |
| 繁體中文 | **目前文件** | [docs/zh-TW.md](docs/zh-TW.md) |
| English | [README.en.md](README.en.md) | [docs/en/index.md](docs/en/index.md) |

其他專題：

- [配置參考](docs/configuration.md)
- [訊息與串流行為](docs/messaging.md)
- [多實例 / 多帳號](docs/multi-instance.md)
- [故障排查](docs/troubleshooting.md)
- [安全與隱私](docs/security.md)
- [開發指南](docs/development.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [RELEASING.md](RELEASING.md)
- [CHANGELOG.md](CHANGELOG.md)

## 🔄 Updater v2

管理頁更新器會固定精確 Release candidate，驗證正式 artifact digest，持久化更新 transaction，切換前停止舊 runtime，reload 後進行 health gate，失敗時保留回復路徑。

> [!CAUTION]
> 生產環境仍應對插件目錄與 `plugin_data` 做獨立備份。自更新無法消除所有平台級硬斷電風險。

## 🛠️ 開發與測試

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

CI 覆蓋 Ubuntu / Windows；Plugin Page i18n 由自動測試確保三套 locale 不會漏翻譯。

## 📄 License

本專案採用 [MIT License](LICENSE)。
