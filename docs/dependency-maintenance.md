# 依賴維護契約

## 安裝、檢查與多帳號

內建 Gateway 使用同一個有時間上限的安裝入口，執行 `npm ci --omit=dev --no-audit --no-fund --ignore-scripts=false`，再確認 Baileys 補丁與核心套件 import 成功。所有步驟成功後才寫入 `node_modules/.astrbot-install.json`。

完成標記包含完整 package.json、package-lock.json、Baileys 補丁腳本的 SHA-256，以及宿主平台與 Node major/ABI/架構。檢查覆蓋所有 lockfile 內的非開發套件（包括巢狀間接依賴），不是只列出幾個重要套件。平台不適用而缺席的 optional 套件可以略過，但已安裝的 optional 套件版本仍必須匹配，sharp 等核心原生功能另外由 import smoke 驗證。此標記不是檔案防竄改／來源認證系統；下載完整性仍由 npm 負責。

舊安裝沒有完成標記時會做一次受控重裝。這可能需要 npm registry 網絡連線；完成後的相同安裝不會每次啟動都重裝。自行執行 npm ci 會移除標記，下次 managed 啟動會重新驗證安裝。現有內建更新器的暫存安裝尚未產生此標記，因此更新後的首次 managed 啟動亦需此驗證；不要把它描述成離線更新。

同一 AstrBot 程序內，各帳號共用插件的 node_modules。開始流程會串行化，並在另一個 managed Gateway 仍存活時拒絕破壞性重裝，回覆明確錯誤；停止所有 WhatsApp Gateway（包括登入管理頁啟動的實例）或重啟 AstrBot 後再試。程序所有權記錄跨插件熱重載保留，第一次升級也會收納仍存活的舊 GatewayProcess。

**不支援兩個獨立 AstrBot 程序或手工啟動的 external Gateway 同時共用一個可變 node_modules 目錄。**它們應使用不同部署目錄；手工重裝前自行停止相關程序。這不是跨程序鎖，也不會擅自停止其他帳號。

取消或逾時會先終止安裝子程序；失敗不會寫入新的成功標記。Windows 使用設定的 Node 執行 npm-cli.js，不依賴 shell 對 npm.cmd 的解析。

## 支援環境

推薦 Node 22 或 24 LTS。Node 20 已 EOL，只保留既有部署回歸測試，不再推薦。程式啟動最低檢查為 20.9.0，與目前 sharp 的最低要求一致。Node 版本、UI 顯示和正式支援聲明的完整同步會另作一批；不能把 Node 24 CI 通過等同於已完成 AstrBot 真機登入驗證。

參考：[Node 生命週期](https://nodejs.org/en/about/previous-releases)、[npm ci](https://docs.npmjs.com/cli/v10/commands/npm-ci/)。

## 驗證

`python -m unittest discover -v tests` 執行完整 Python 回歸；`python scripts/verify-dependencies.py` 在有 npm 網絡與 Node 的環境執行真實安裝／補丁／import，再確認第二次無需重裝。它不連線登入 WhatsApp。CI 對 Ubuntu/Windows 的 Node 20、22、24 矩陣執行此檢查。

## 適配器設計參考

QQ / aiocqhttp 的價值是按消息元件能力轉換：圖片與音訊可以轉 base64，文件獨立處理，普通消息與合併轉發的發送規則不同。參考上游 `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py`（2026-09-08 檢查），不要把其 file URI 或 base64 規則直接當作 WhatsApp Gateway 的協議。WhatsApp 的訊息路由、來源檢查、大小限制與憑證排除仍需自己驗證；本批不更改媒體政策或新開任意 Gateway API。
