# 架構技術債收斂

本文件記錄不影響玩家／管理員行為的 P2 架構約束。目標不是一次重寫 WhatsApp Adapter，而是在已有 CI、Updater v2、多實例與相容層測試之上，阻止歷史大檔重新膨脹。

## Shrink-only implementation budget

以 2026-08-31 canonical `main` 為基線：

- `_whatsapp_adapter_impl.py`：最多 **125,349 bytes**；
- `_whatsapp_helpers_impl.py`：最多 **60,189 bytes**。

`tests/test_architecture_shrink_budget.py` 在既有 Python 3.11／3.12、Linux／Windows 測試矩陣中執行。超過基線應視為架構回歸，不應透過提高上限解決。

當抽離工作讓某檔案縮小後，應把對應上限同步降低到新的實際大小，使已消化的技術債不再長回去。

## 新邏輯的落點

遵循現有 `docs/development.md` 的邊界：

- Baileys／協議差異：小型 compatibility module + 對應 regression test；
- 身份與 PN/LID：`whatsapp_identity.py`；
- 多實例／port／auth ownership：`whatsapp_multi_instance.py`；
- 配置作用域與 migration：`whatsapp_config_policy.py`；
- MessageEvent 發送／streaming：事件邊界或獨立 helper；
- Gateway 行為：`gateway/` 中可單測的模組；
- Plugin Page／Updater UI：`pages/whatsapp-login/` 與既有 Page test。

如果某項改動確實需要接入歷史 impl，應讓入口保持薄，將可獨立描述、測試、復用的策略抽到專用模組。整個 PR 合併後，受控 impl 的總大小仍不得高於當前 budget。

## 不改變的契約

這個 P2 gate 不改：

- WhatsApp 收發、reaction arbitration、streaming 語義；
- PN／LID 身份規則；
- 多實例端口與 auth 隔離；
- 配置 schema；
- Updater v2 發版與回滾協議；
- 版本號與 Release 流程。

它只建立一條不可逆的維護規則：**historical impl 可以逐步縮小，但不能重新成為新功能垃圾桶。**
