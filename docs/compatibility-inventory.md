# 外部相容性清單 / External compatibility inventory

插件自身的 Python class monkeypatch 與六層 Gateway 源碼 patch 已移除。下列例外針對外部依賴，升級時逐項重驗；不能用相容層重新包裹插件自己的正式方法。

| 邊界 / Boundary | 目的、覆蓋與移除條件 / Purpose, coverage and removal |
| --- | --- |
| Baileys `7.0.0-rc14` postinstall `scripts/patch-baileys-ephemeral.mjs` | 保留消失訊息設定 timestamp，避免同秒或歷史 metadata 覆蓋新設定。`scripts/patch-baileys-ephemeral.test.mjs` 對真實鎖定套件及訊息產生器驗證。上游正式提供等價 timestamp 傳遞與過期控制且測試不用 patch 仍通過時移除；不在 Gateway 啟動時執行。 Preserve disappearing-message timestamps; remove when an upstream version passes the same tests without patching. |
| AstrBot `PlatformManager.reload` | 只在外部平台管理器進入原生完整重載前清理舊 WhatsApp 設定；不替換原生終止／啟動流程。當支援的最小 AstrBot 已提供配置正規化 hook 時改用 hook。 Keep native reload and normalize plugin config at the boundary; replace when the minimum supported core exposes a normalization hook. |
| AstrBot streaming after-send hook | `whatsapp_event._needs_streaming_after_hook_compat` 檢查真實控制流程，補上提前 return 漏掉的 after-send hook。`tests/test_whatsapp_markdown.py`覆蓋；當所有支援版本的 core 都完成該 hook 時移除。 Detect actual upstream control flow; remove when all supported cores call the hook for streams. |
| 舊 `_whatsapp_adapter_impl.py`／`_whatsapp_event_impl.py` 匯入路徑 | 僅普通 re-export，無執行期 patch。下一個明示清理內部匯入路徑的版本可以移除；公開 `whatsapp_adapter`／`whatsapp_event` API 保持穩定。 Plain import aliases; removable in an announced internal-path cleanup. |
| `pages/whatsapp-login/sandbox-confirm.js` | 舊快取頁面的無害資產；正式頁面使用 `two-step-action.js`。舊頁面快取淘汰後刪除，不能恢復 iframe modal shim。 Legacy cached asset only; remove after cache retirement. |

一般 reaction 不送交 LLM；短期仲裁觀察由同一 AstrBot 進程中的帳號共享，key 為聊天、訊息、發送者及 emoji，30 秒 TTL、最多 4,096 筆。此設計讓同群機器人協調；不同進程沒有共享記憶體，也沒有永久 reaction 歷史。發送成功後才記錄；紀錄故障不改變已成功發送的結果。

Ordinary reactions never enter LLM delivery. Accounts in one AstrBot process share a bounded arbitration journal keyed by chat, message, sender and emoji (30 seconds, 4,096 entries). Separate processes do not share memory. Only successful sends are recorded; journal failure does not turn successful delivery into a send failure.
