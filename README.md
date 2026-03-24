# WhatsApp Platform Adapter for AstrBot

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-MPL%202.0-green)](LICENSE)

這是一個為 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 開發的 WhatsApp 平台適配器插件。使用 `whatsapp-bridge` 實現非官方多裝置（Multi-Device）掃碼登入，讓你的機器人能像在 Telegram 或 Discord 上一樣在 WhatsApp 運作。

## 🌟 功能特色

- **文字收發**：支援完整的雙向文字訊息傳輸。
- **多媒體支援**：
  - **接收**：自動下載圖片、音頻、語音訊息、影片與文件，並轉換為 AstrBot 的 MessageChain。
  - **發送**：支援發送本地檔案、URL 或 bytes（圖片、音頻、語音、文件）。
- **預回覆與狀態指示**：
  - **Typing Indicator**：在機器人思考或處理時顯示「正在輸入...」。
  - **預回覆表情**：支援在正式回覆前先送出一個預設表情（如 💭）。
- **串流傳輸 (Streaming)**：支援 LLM 的串流輸出，即時將文字段落發送給使用者。
- **安全與過濾**：
  - **白名單模式**：可限制僅允許特定 JID 或群組觸發機器人。
  - **私聊策略**：可設定允許、拒絕或僅限白名單私聊。
- **已讀回執**：可配置是否在成功處理訊息後回傳已讀。

## 🛠️ 環境準備

由於底層 `whatsapp-bridge` 使用了 Go 語言編寫的橋接服務，請確保環境已安裝：

1. **Go (Golang)**：[下載並安裝 Go](https://go.dev/dl/)，並將 `go` 加入系統 PATH。
2. **Git**：用於自動下載橋接源碼。
3. **FFmpeg (建議)**：若需發送或轉換語音訊息，請確保 `ffmpeg` 已安裝並在 PATH 中。
4. **C 編譯器 (僅 Windows)**：如果是在 Windows 上運行，建議安裝 [MSYS2](https://www.msys2.org/) 並安裝 `mingw-w64-ucrt-x86_64-toolchain`，以支援 CGO。

## 🚀 安裝步驟

1. **安裝插件**：
   在 AstrBot 的插件目錄下克隆此倉庫，或透過 AstrBot WebUI 安裝。
   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter
   ```

2. **安裝 Python 依賴**：
   ```bash
   pip install -r requirements.txt
   ```

3. **啟用平台**：
   - 啟動 AstrBot，進入 **WebUI -> 平台設定**。
   - 點擊 **新增平台**，選擇 `WhatsApp`。
   - 根據需求調整配置（模式、白名單、已讀回執等）。
   - 點擊 **保存並啟用**。

## 📲 首次掃碼登入流程

1. 啟用平台後，觀察 AstrBot 的**控制台日誌**（或平台日誌）。
2. 底層服務會自動下載並編譯橋接器，這可能需要一點時間。
3. 當看到終端輸出 **QR Code** 時，打開手機 WhatsApp。
4. 前往 **設定 > 已連結裝置 > 連結裝置**。
5. 掃描終端上的 QR Code。
6. 成功連線後，憑證會保存在 `data/whatsapp_creds` 中，下次啟動無需重新掃碼。

## ⚙️ 配置項說明

| 配置項 | 說明 | 預設值 |
| :--- | :--- | :--- |
| `mode` | 運作模式（固定為 gateway） | `gateway` |
| `allowlist` | 白名單（填入 chat_jid 或手機號碼） | `[]` |
| `dm_policy` | 私聊策略（allow, deny, allowlist_only） | `allow` |
| `send_read_receipts` | 是否自動發送已讀回執 | `true` |
| `media_max_mb` | 媒體自動下載的大小上限 (MB) | `32` |
| `typing_indicator` | 是否在回覆前顯示「正在輸入...」 | `true` |
| `pre_reply_emoji` | 正式回覆前的預回覆表情（留空則關閉） | `💭` |

## ⚠️ 免責聲明

本插件使用的 `whatsapp-bridge` 基於非官方的 WhatsApp Web API。使用非官方 API 存在被官方封號的風險。請僅用於教育、研究或個人自動化用途，作者不對任何帳號封禁或其他後果負責。

## 🤝 貢獻

歡迎提交 Issue 或 Pull Request 來改進此插件！

## 📄 授權

本項目採用 [Mozilla Public License 2.0](LICENSE) 授權。
