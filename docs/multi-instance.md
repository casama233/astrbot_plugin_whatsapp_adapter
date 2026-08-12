# WhatsApp 多实例 / 多账号

从 v0.2.34 起，本插件支持在同一个 AstrBot 进程中运行多个 WhatsApp 平台实例。每个实例必须使用唯一 `id`，并拥有自己的 WhatsApp 登录 session。

## 设计目标

多实例隔离要同时保证：

- Gateway HTTP endpoint 不串号。
- WhatsApp auth 目录不共用。
- reconnect / reload 不把实例连到其它账号的 socket。
- 端口发生 bind race 时可以安全重新分配。
- terminate / reload 后及时释放端口租约。
- external Gateway 不允许被两个 runtime 静默共用。

## 内置 Gateway 模式

保持插件级：

```json
{
  "auto_start_gateway": true,
  "gateway_host": "127.0.0.1",
  "gateway_port": 18789
}
```

假设基准端口是 `18789`：

- `id=whatsapp` 保留 `18789`，确保默认登录 Page 和旧部署兼容。
- secondary 实例从 `18790` 开始寻找可用端口。
- 非默认实例即使更早启动，也不会抢占基准端口。
- 重连期间尽量维持同一个运行时端口。
- 只有确认发生绑定竞争时才重新选择端口。
- 实际端口会写入日志。

示意：

```text
whatsapp       -> 127.0.0.1:18789
whatsapp2      -> 127.0.0.1:18790
whatsapp-work  -> 127.0.0.1:18791
```

端口只是示意；如果某端口已被其它程序占用，运行时会继续寻找可用端口。

## 认证目录隔离

默认实例：

```text
whatsapp-auth/
```

secondary：

```text
whatsapp-auth-whatsapp2/
whatsapp-auth-whatsapp-work/
```

如果插件级配置了自定义 `auth_dir`：

```text
/custom/wa-auth
```

则默认实例继续使用该路径，secondary 会使用同级带实例后缀的目录。

### 实例 ID 安全规则

实例 `id` 会参与目录名生成。运行时会规范化不适合文件名的字符，并在需要时加入稳定哈希后缀，避免：

- 路径穿越
- 两个不同 ID 清理后发生同名碰撞

仍建议使用简单 ID：

```text
whatsapp
whatsapp2
whatsapp-work
whatsapp-hk
```

## 如何登录第二个账号

当前 **WhatsApp 登录插件 Page 主要连接默认实例的基准 Gateway**。因此：

- 默认实例：直接使用插件 Page 的二维码 / 手机号配对码。
- secondary 内置实例：启动平台后，从 AstrBot / Gateway 日志读取该实例打印的二维码并扫码。

Gateway 在未登录时会把二维码输出到终端日志，同时生成供 HTTP `/qr` 使用的 QR 数据。

不要复制默认实例的认证目录到 secondary。复制凭证会破坏账号隔离，并可能让两个 runtime 争用同一 Linked Device session。

> 当前管理 Page 尚不是一个“多实例账号切换器”。这是 UI 层的已知限制，不影响运行时多实例隔离本身。

## reload / reconnect 生命周期

每个实例都有独立的 endpoint 生命周期锁。

reload 时会按顺序：

1. 阻止健康检查 / run loop 在中间状态抢先重连。
2. 停止旧 Gateway transport。
3. 释放旧 runtime owner。
4. 释放 endpoint 租约。
5. 替换新配置。
6. 重新计算实例 endpoint 和认证目录。
7. 重新加载身份 mapping。
8. 恢复 runtime owner。

这样可以避免“旧进程还没完全退出，另一个账号已经接管端口”的窗口。

## bind race

即使两个实例并发启动，端口检查和真正 Node listen 之间仍可能被外部程序抢占。

如果确认发生 bind conflict：

- 当前实例会释放旧租约。
- 重新寻找 endpoint。
- 更新客户端 base URL。
- 重新启动 Gateway。

其它类型的启动错误不会被误判成“随便换端口再试”。

## external Gateway 模式

```json
{
  "auto_start_gateway": false
}
```

此时插件不会启动本地 Node 进程，也不会为 external Gateway 自动改端口。

同一 AstrBot 进程内，如果两个 WhatsApp adapter runtime 最终解析到完全相同的 external `host:port`，第二个实例会直接报 endpoint owner 冲突，不会静默共享 session。

### 当前 WebUI 的限制

现行配置模型只提供一组**插件级** `gateway_host` / `gateway_port`。标准平台实例配置不会分别持久化独立 external endpoint。

因此在普通 WebUI 部署下：

- 一个 AstrBot 进程 + 一组插件配置，通常只适合一个 external Gateway WhatsApp 账号。
- 如果必须用多个 external Gateway，推荐拆成多个 AstrBot 进程 / 容器 / 独立配置作用域，每个连接自己的 endpoint。
- 不要通过关闭 owner 冲突保护让两个账号共用一个 external Gateway。

## 数据目录示例

```text
data/plugin_data/astrbot_plugin_whatsapp_adapter/
├── config.json
├── whatsapp-auth/
├── whatsapp-auth-whatsapp2/
├── whatsapp-auth-whatsapp-work/
└── media/
```

身份 mapping 文件存放在各自 auth session 中，不会作为跨账号共享缓存使用。

## 故障排查

### secondary 没用到预期端口

检查：

1. 目标端口是否被其它程序占用。
2. AstrBot 日志中的 `WhatsApp multi-instance: allocated gateway port ...`。
3. 是否发生过 bind race 后重新分配。

只要实例连接到自己的认证目录和独立端口，端口不是连续数字本身并不是故障。

### external endpoint already owned

这是保护性错误。

不要关闭保护；应让两个账号使用不同 AstrBot 运行环境 / external endpoint。

### 第二个账号没有二维码

当前插件 Page 不会自动切换到 secondary runtime。查看该平台实例启动后的 Gateway 日志二维码。

### 两个账号出现消息串号

这不应当是正常现象。立即检查：

- 两个平台 `id` 是否唯一。
- 是否人工复制过 auth 目录。
- 是否使用了同一个 external Gateway。
- 日志里每个实例实际 endpoint 和 auth 目录是否不同。

如果确认是插件在独立 endpoint / auth 下仍发生串号，请保留脱敏日志并提交 Issue。

## 相关文档

- [配置参考](configuration.md)
- [故障排查](troubleshooting.md)
- [安全与隐私](security.md)
