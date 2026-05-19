# AstrBot WhatsApp Adapter 中文文档

本文档面向安装、配置和冒烟测试。项目 README 已包含完整说明，此处提供更短的操作版。

## 一句话说明

这是一个 AstrBot 消息平台适配器插件，通过本地 Node.js Gateway 接入 WhatsApp Web，并在 AstrBot 插件管理页提供二维码登录页面。

## 安装步骤

```bash
cd AstrBot/data/plugins
git clone https://github.com/casama233/astrbot_plugin_whatsapp_adapter.git
cd astrbot_plugin_whatsapp_adapter
npm install --omit=dev
pip install -r requirements.txt
```

随后重启 AstrBot 或在 WebUI 重载插件。

## 登录步骤

1. AstrBot WebUI 启用插件。
2. 添加 `whatsapp` 平台适配器。
3. 在插件配置中填写访问控制项，例如 `allow_from` 和 `dm_policy`。插件配置页是主要配置来源。
4. 打开插件详情页。
5. 进入 `whatsapp-login` 页面。
6. 使用 WhatsApp 手机端扫描二维码。

配置合并顺序为：内置默认值 < 平台实例配置 < 插件配置页。最终以插件配置页为准。

平台实例配置 key 保持英文，WebUI 会通过平台配置元数据显示中文说明。

## 最小安全配置

```json
{
  "allow_from": ["+15551234567"],
  "dm_policy": "allowlist",
  "group_policy": "disabled"
}
```

## 常见问题

### 页面没有二维码

等待 5 到 10 秒后点击刷新。如果仍无二维码，点击「重启连接」。确认已经执行 `npm install --omit=dev`。

### 扫码后没有收到消息

检查平台实例是否启用，检查 `allow_from` 是否包含发送者号码，号码建议写成 `+国家码号码` 格式。

检查插件配置页中的 `allow_from` 是否包含发送者号码。插件配置页会覆盖平台实例配置。

### 群聊不触发

默认 `group_policy=disabled`。需要配置 `group_policy`、`groups` 和 `group_allow_from`。

### 需要重新登录

在插件 Page 点击「登出并重新扫码」，或删除数据目录中的 `whatsapp-auth/`。
