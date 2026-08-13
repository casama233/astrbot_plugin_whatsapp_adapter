# 开发指南

本文面向需要修改适配器、Gateway、Plugin Page、兼容层或发布流程的贡献者。普通使用者优先阅读 [中文使用指南](zh-CN.md) 与 [故障排查](troubleshooting.md)。

## 架构与主要文件

```text
main.py                         插件注册、Page API、Updater v2、AI 工具
whatsapp_adapter.py             Platform wrapper 与运行时 patch
_whatsapp_adapter_impl.py       平台适配器主体
whatsapp_event.py               MessageEvent wrapper
_whatsapp_event_impl.py         普通发送 / 流式发送主体
whatsapp_client.py              Gateway HTTP client 与子进程管理
whatsapp_config_policy.py       配置作用域、默认值与迁移
whatsapp_identity.py            PN / LID 身份归一化
whatsapp_multi_instance.py      多实例端口、auth 与 runtime owner
gateway/                        Node.js Gateway 与兼容模块
pages/whatsapp-login/           登录 / 状态 / Updater v2 Plugin Page
.astrbot-plugin/i18n/           插件 metadata/config/Page 多语言资源
tests/                          Python 回归测试
scripts/*.test.mjs              Node / Page / 脚本回归测试
```

项目将大体量实现与小型兼容层分离；涉及 Baileys 协议差异时，优先增加小型 compatibility module 与对应测试，而不是把临时逻辑散进多个发送路径。

## 配置层级

新增配置前先判断它属于：

- 所有 WhatsApp 实例共用的插件级 Gateway 配置；
- 插件级 `default_*` 行为；
- 单个账号可以不同的平台实例行为。

相关逻辑主要在 `_conf_schema.json`、`whatsapp_config_policy.py` 与 `_whatsapp_adapter_impl.py`。不要重新把已经收回插件层的 Gateway 字段暴露成普通实例字段。

## 多实例

`whatsapp_multi_instance.py` 负责默认实例基准端口、secondary 可用端口、auth 目录隔离、endpoint lease / owner、防 bind race 与 reload/terminate 释放。

修改时重点防止两个账号共享 auth、多个 runtime 静默连接同一 external endpoint、旧 Gateway 尚未停止就释放端口，以及 secondary 抢占默认基准端口。

## 流式回复

核心在 `_whatsapp_event_impl.py`。需要保持：partial stream 已真实投递后立即标记 sent；编辑失败不得重复完整前缀；Message ID 缺失要区分已发送但不可编辑与未发送；并发 event 不共享 streaming state；typing presence 不得被另一个先结束的 stream 提前停止。

## Plugin Page i18n

正式维护：`zh-CN`、`zh-TW`、`en-US`。

Page 使用 AstrBot Plugin Page bridge：

```js
await bridge.ready();
bridge.t("pages.whatsapp-login.some_key", "English fallback");
bridge.getLocale();
bridge.onContext((context) => { /* locale changed */ });
```

静态 markup 使用 `data-i18n`、`data-i18n-title`、`data-i18n-placeholder` 与 `data-i18n-aria-label`；动态连接状态、Updater、配对提示和 Page 事件日志在 `app.js` 中翻译。时间格式交给当前 locale 的 `Intl.DateTimeFormat`，不要固定 `zh-CN`。

`tests/test_plugin_i18n_coverage.py` 会检查 plugin metadata、`_conf_schema.json` 文案、options labels、Page i18n key、硬编码 CJK UI 文案，以及运行时 locale hook。

新增或修改用户可见配置/Page 文案时，三套 locale 必须在同一 PR 同步修改。

### 后端 logger 为什么不做 runtime i18n

Python / Node 后端日志是运维诊断接口，不属于单个浏览器用户的 UI。一个 AstrBot 进程可能同时被不同语言客户端查看，也需要稳定关键字用于 grep、Issue 搜索和错误聚合。因此后端 logger 保持稳定技术文本；Plugin Page 状态、Page event log、确认提示和配置说明才进入 i18n。

未来如果后端错误需要更强本地化，优先返回稳定 error code 供 Page 映射翻译，而不是让 backend 根据浏览器语言改变日志字符串。

## Updater v2 与确认流程

最新 main 的自更新器是 **release-pinned transaction v2**。Login Page 必须保持以下安全契约：

- `update/check` 返回并锁定 Release candidate identity / artifact digest；
- 安装请求提交相同的 `release.candidateToken` 与 `expectedVersion`，不能在第二次点击时偷偷换候选；
- 确认使用 `two-step-action.js` 的 `createTwoStepGate`，不依赖 iframe modal；
- 后端事务状态是持久化的；前端请求在 hot reload 中断后继续轮询 `update/status`；
- 前端 30 分钟轮询上限不能把仍运行的后端事务标记为失败；
- `quiescing`、`health_checking`、`rolling_back` 等阶段必须继续可见；
- 健康检查失败由后端执行回滚。

`sandbox-confirm.js` 现在只是给旧缓存 Page HTML 保留的无害兼容资产，不再承载确认逻辑。不要恢复旧的 `window.confirm` shim。

`scripts/plugin-page-sandbox-confirm.test.mjs` 会锁住无 modal、无动态 HTML sink、`createTwoStepGate` 与 exact candidate token 提交等契约；i18n 改动必须在这些测试之上叠加，而不是替换它们。

## 测试

与 CI 对齐的本地检查：

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

当前 CI 还会在受支持的 Windows 路径验证 Updater v2 相关行为；修改更新器、Page 或路径处理时不要只看单一平台结果。

## Release contract

正常 feature/fix PR 不要手工 bump version。版本发布通过 `.release/X.Y.Z.json` marker 触发；Release workflow 负责同步版本源、更新 CHANGELOG、运行测试、构建/验证 ZIP、生成 SHA-256 并发布。

完整流程见 [RELEASING.md](../RELEASING.md)。

## 文档维护

中文入口为 [README.md](../README.md) 与 `docs/*.md`；英文入口为 [README.en.md](../README.en.md) 与 `docs/en/*.md`。修改公共行为时同步相关中英文专题文档，行为事实以当前代码、配置和测试为准。
