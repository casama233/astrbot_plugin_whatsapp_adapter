# AstrBot WhatsApp Adapter

透過本地 Baileys Gateway 接入 WhatsApp Web，支援私聊、群聊、媒體、串流回覆及多個帳號。

[简体中文](README.md) · 繁體中文 · [English](README.en.md) · [最新 Release](https://github.com/casama233/astrbot_plugin_whatsapp_adapter/releases)

## 執行環境

AstrBot **4.24.2+**；建議 **Node.js 22/24 LTS** 與 npm，最低 **20.9.0**。Node 20 已停止維護，只保留相容性回歸。CI 涵蓋 Ubuntu / Windows、Python 3.11/3.12、Node 20/22/24。

## 快速開始

1. 從 AstrBot 插件市場安裝，保留插件連線設定的預設值。
2. 新增 WhatsApp 平台實例：私聊策略選 `allowlist`，在 `allow_from` 填入自己的國際電話號碼，首次驗證保持群聊關閉。
3. 開啟插件的 **WhatsApp 登入** 管理頁，在手機 WhatsApp → **已連結的裝置** 掃碼，或使用手機號碼配對碼。
4. 顯示已連線後啟用平台實例，傳送一條測試訊息。

管理頁會顯示操作所作用的基準 Gateway / 帳號。多帳號的實際連接埠可在「進階連線資訊」查看；其他帳號的登入方式見 [多實例指南](docs/multi-instance.md)。

插件連線設定及 `default_*` 是共用設定；平台實例保存帳號自己的存取策略。舊設定只遷移一次，生效值與來源可在管理頁「生效設定與診斷」查看。完整欄位集中在 [設定參考](docs/configuration.md)。

## 排障與更新

- 找不到 Node 或相依套件待安裝：查看管理頁 Runtime 狀態及 [故障排查](docs/troubleshooting.md)。
- 已登入卻沒有回覆：先檢查平台實例是否啟用、允許清單及 AstrBot 喚醒條件。
- 提交問題時使用「重新取得並複製診斷」；報告包含實際版本、來源、帳號連接埠及設定來源，並遮蔽帳號識別碼與憑證。
- 管理頁可以檢查及安裝經過校驗的 GitHub Release，更新失敗會嘗試回滾。部署前保留獨立備份。

預設 Gateway 為 `127.0.0.1:18789`。請保護 Gateway 連接埠及 `whatsapp-auth/` 登入憑證。本插件使用非官方 WhatsApp Web 協議，協議變更可能影響連線；詳細邊界見 [安全與隱私](docs/security.md)。

## 專題文件

| 文件 | 內容 |
| --- | --- |
| [使用指南](docs/zh-TW.md) | 安裝與首次接入 |
| [設定參考](docs/configuration.md) | 欄位、作用域、遷移與代理 |
| [訊息與串流](docs/messaging.md) | UMO、喚醒、引用、reaction 及相簿 |
| [多實例](docs/multi-instance.md) | 帳號、連接埠、登入態隔離 |
| [開發指南](docs/development.md) | 實作入口、測試與 i18n |
| [相容性清單](docs/compatibility-inventory.md) | 外部補丁及移除條件 |
| [發佈流程](RELEASING.md) | 候選驗證與恢復 |

[貢獻指南](CONTRIBUTING.md) · [變更記錄](CHANGELOG.md) · [MIT License](LICENSE)
