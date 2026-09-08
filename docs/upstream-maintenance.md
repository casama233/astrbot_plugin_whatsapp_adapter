# 上游更新與安全檢查

## 哪些更新會提出 PR

Dependabot 設定每週一 02:00 UTC 檢查 npm 與 GitHub Actions。一般 npm minor/patch 合併成小組；Baileys、sharp 和 major 更新維持獨立 PR。Actions 的 minor/patch 可分組，major 分開。版本 PR 上限分別為 4 與 2，避免維護清單被大量小更新塞滿。

這只是提出候選，不是自動合併、發布或修改運行中的帳號。不要為了讓 CI 通過而放寬 Baileys 補丁匹配，亦不要使用 `npm audit fix --force` 一次升級所有依賴。Baileys 不設全版本 ignore，以免安全更新候選也被隱藏。每個候選的實際支援程度與 registry 發布狀態仍需核對。

## lockfile 安全檢查

`Dependency audit` 在套件 manifest / lockfile / 該 workflow 的 PR 變更時，以及每週一 02:23 UTC，執行：

```bash
npm audit --omit=dev --package-lock-only --audit-level=high --json
```

它查詢已提交的生產依賴圖（包含 registry 能識別的間接依賴），不執行安裝腳本，也不自動修復。high/critical 發現或 registry 連線錯誤會讓 job 失敗，不能當成「沒有漏洞」。JSON 報告保留 14 天，GitHub Actions 頁面可查看。

通過只代表該次 registry 查詢沒有達到失敗門檻，不代表沒有低/中等嚴重性通報、沒有未知漏洞或已做安全認證。報告不是 WhatsApp 真機互通測試，亦不包含 AstrBot 共用 Python 環境的安全掃描。

## 合併前確認

套件版本、lockfile、Node 支援要求與 Baileys 補丁必須一致。先通過完整 Python/Node 測試與實際 managed 安裝驗證，再檢查登入、斷線恢復、一般/流式文字及媒體是否有上游破壞性變更；沒有真機驗證時應明確註明。

`aiohttp` 與 AstrBot 共用 Python 環境，不能僅因上游出現新版本就由此插件強制重裝。Python requirements 變動仍走 AstrBot 插件管理器與重啟路徑。

參考：[Dependabot 選項](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)、[npm audit](https://docs.npmjs.com/cli/commands/npm-audit/)。
