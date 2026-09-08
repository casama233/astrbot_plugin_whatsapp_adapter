# 架構維護邊界

Gateway 直接匯入 `gateway/whatsapp-gateway-impl.mjs`，不在啟動時改寫或產生 JavaScript。群組名稱、成員標籤、私人相簿、穩定性、正常關閉及安全控制已整合到正式源碼。整合前曾逐 byte 核對完整六層補丁產物；只移除了不再使用的補丁標記。

Python 的正式類別位於 `whatsapp_adapter.py`、`whatsapp_event.py`、`whatsapp_client.py`。帳號端口租約、訊息轉換、reaction、presence、認證及關閉都是明確方法或函式呼叫。兩個歷史 `_impl` 入口只保留普通匯入相容性。`GatewayProcess.start` 是唯一啟動入口，`prepare_node_dependencies` 是啟動與更新共用的依賴安裝入口；認證模組只管理憑證，不再複製子進程啟動程式。

## 不可增長預算

`tests/test_architecture_shrink_budget.py` 由 `unittest` 收集，按 LF 正規化的 UTF-8 bytes 計算，並驗證超出 1 byte 必須失敗、CRLF 等價、檔案遺失及 UTF-8 計數。

| 檔案 | 上限 |
| --- | ---: |
| `_whatsapp_adapter_impl.py` | 126 |
| `whatsapp_adapter.py` | 131,156 |
| `_whatsapp_event_impl.py` | 118 |
| `whatsapp_event.py` | 32,004 |
| `_whatsapp_helpers_impl.py` | 60,189 |

歷史 adapter 上限由 125,349 下修為 126；同時限制真正執行的公開模組，避免把搬檔當作減債。公開 adapter 已整合原本 wrapper 的行為、刪除重複方法，總量低於原 impl 與 wrapper 合計。未來抽離後繼續下調上限，不能提高上限容納新邏輯。

`tests/test_canonical_architecture.py` 鎖住明確類別方法、單一啟動及無 `exec`／源碼改寫。`gateway/canonical-runtime.test.mjs` 在隔離子進程啟動正式 Gateway，替換遠端 socket，使用真正 Baileys 套件與 auth serializer，驗證 HTTP 認證、媒體保護、群組查詢、發送邊界和最終憑證保存。

新邏輯依 [開發指南](development.md) 的身份、設定、多帳號及 Gateway 模組邊界放置。外部相容性例外集中記錄於 [相容性清單](compatibility-inventory.md)。
