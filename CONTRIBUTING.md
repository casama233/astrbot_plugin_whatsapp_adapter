# Contributing

感谢你考虑为 AstrBot WhatsApp Adapter 做贡献。

本项目同时包含 AstrBot Python 平台适配器、Node.js WhatsApp Gateway、Plugin Page、兼容层与发布工具。为了避免修复一个路径却破坏另一个路径，请尽量让提交范围小、行为边界清晰，并为兼容性问题增加回归测试。

## 开始之前

1. 先搜索现有 Issue / PR，确认问题没有正在处理。
2. 对行为修复，优先给出：AstrBot 版本、插件版本、Node.js 版本、运行方式（宿主 / Docker）、是否使用代理、多实例与否。
3. 不要上传 `whatsapp-auth/`、配对码、完整手机号、私聊内容、代理凭证或其他敏感数据。
4. 不要在普通功能 / 修复 PR 中手工修改版本号；版本发布由 [RELEASING.md](RELEASING.md) 的 marker 工作流负责。

## 本地验证

CI 当前执行：

```bash
python scripts/release_contract.py validate-repo
python -m compileall -q .
python -m unittest discover -v tests
npm ci
node --test gateway/*.test.mjs scripts/*.test.mjs
```

提交前建议至少运行与你修改路径直接相关的测试；涉及 Gateway、适配器配置、流式回复、身份、登录生命周期或发布流程时，应运行完整测试集。

## 代码结构

主要入口：

```text
main.py                         插件注册、Page API、更新器与 AI 工具
whatsapp_adapter.py             平台适配器 wrapper / compatibility patch
_whatsapp_adapter_impl.py       平台适配器主体
whatsapp_event.py               消息事件 wrapper
_whatsapp_event_impl.py         普通发送 / 流式发送主体
whatsapp_client.py              Gateway HTTP client / 子进程管理
whatsapp_config_policy.py       配置作用域与迁移
whatsapp_identity.py            PN / LID 身份处理
whatsapp_multi_instance.py      多实例端口、auth 与 owner 隔离
gateway/                        Node.js Gateway 与兼容模块
pages/whatsapp-login/           登录 / 状态 / 更新 Plugin Page
.astrbot-plugin/i18n/           插件和 Page 的多语言资源
```

详细说明见 [开发指南](docs/development.md)。

## i18n 约定

当前正式维护：

- `zh-CN`
- `zh-TW`
- `en-US`

如果新增或修改用户可见配置字段、Plugin Page 文案、状态、确认提示或 Page 事件日志，请在**同一个 PR**中同步三套 `.astrbot-plugin/i18n/*.json`。

Plugin Page 应使用 AstrBot Plugin Page bridge 的 `bridge.t()`、`bridge.getLocale()` 与 `bridge.onContext()`；不要重新在 `index.html` / `app.js` / `sandbox-confirm.js` 中硬编码中文 UI 文案。

`tests/test_plugin_i18n_coverage.py` 会检查：

- `_conf_schema.json` 中可见配置的 description / hint 是否被三套 locale 覆盖；
- options 是否有等长、非空的 labels；
- Login Page 使用的静态 i18n key 是否全部存在；
- Page 源码是否重新出现硬编码 CJK UI 文案；
- Page 是否仍保留运行时 locale 更新钩子。

Python / Node **后端运维日志不做运行时 i18n**。后端日志应保持稳定、可搜索的技术诊断文本；用户可见的 Page 状态和 Page 事件日志才走 locale。

中文主文档为 [README.md](README.md) 与 `docs/`；英文主文档为 [README.en.md](README.en.md) 与 `docs/en/`。修改公共行为时应同步相关英文专题文档。

## PR 建议

一个好的 PR 通常包含：

- 问题或行为差异的简短说明；
- 修改范围与为什么这样实现；
- 风险 / 已知限制；
- 对应测试；
- 如果修改用户可见行为，更新 README / docs / i18n。

不要把无关格式化、重命名和行为变更混在同一个 PR 中。

## WhatsApp / Baileys 注意事项

WhatsApp Web 不是稳定的官方服务端 API。涉及 Baileys 协议行为时：

- 不要仅凭文档或猜测宣称一个能力已经实现；以当前代码、锁定依赖与测试为准。
- 尽量把协议兼容修复限制在小型 compatibility module，并附回归测试。
- 避免在没有必要时改变 `package-lock.json` 或解除关键依赖的精确锁定。
- 不要把 Gateway HTTP 路由当成对第三方长期稳定的公开 API。

## 安全

如果发现凭证泄露、路径穿越、任意目标消息发送、更新器信任边界、跨账号 session 混用等安全问题，请避免在公开 Issue 中贴出可直接利用的敏感材料；先用最少复现信息说明影响范围。

更多安全边界见 [安全与隐私](docs/security.md)。
