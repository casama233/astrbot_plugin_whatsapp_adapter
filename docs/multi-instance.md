# WhatsApp 多实例 / 多账号

本插件支持在同一个 AstrBot 进程中运行多个 WhatsApp 平台实例。每个实例必须使用不同的 `id`，并分别完成 WhatsApp 登录。

## 内置 Gateway 模式

保持插件配置中的 `auto_start_gateway=true` 即可。

假设插件全局 `gateway_port=18789`：

- `id=whatsapp` 保留基准端口 `18789`，与 WhatsApp 登录管理页保持兼容。
- 其他实例从 `18790` 开始自动寻找可用端口，例如 `whatsapp2 -> 18790`、`whatsapp3 -> 18791`。
- 非默认实例即使比 `whatsapp` 更早启动，也不会占用基准端口。
- 重连期间实例会保持同一个运行时端口；只有确认发生端口绑定竞争时才会重新选择端口。
- reload / terminate 会先停止旧 Gateway，再释放端口占用，避免其他账号误连到仍在运行的旧 session。

认证目录同样按实例隔离：

- 默认实例继续使用 `whatsapp-auth`，保持向后兼容。
- 其他实例使用带安全实例标识的独立目录，例如 `whatsapp-auth-whatsapp2`。
- 如果全局配置了自定义 `auth_dir`，默认实例继续使用原目录，其他实例自动使用带实例后缀的 sibling 目录。

新增第二个账号后，请为该实例重新完成二维码或手机号配对登录；不要复制另一个账号的认证目录。

## 外部 Gateway 模式

`auto_start_gateway=false` 时插件不会自动修改外部 Gateway 的端口，也不会在本机占用端口租约。

为了避免两个 AstrBot 平台实例静默连接到同一个 WhatsApp session，同一进程内一个外部 `host:port` 只能由一个 WhatsApp adapter runtime 使用。如果第二个实例解析到完全相同的 external endpoint，插件会直接报配置冲突，而不是共用该 Gateway。

因此，多账号使用外部 Gateway 时必须为每个账号准备独立的 Gateway endpoint，例如：

- 账号 A：`127.0.0.1:19001`
- 账号 B：`127.0.0.1:19002`

当前 Gateway 连接参数属于插件级配置；如果你的 AstrBot 配置方式无法为不同平台实例提供不同的 external endpoint，请使用内置 Gateway 多实例模式，或分别部署外部 Gateway / AstrBot 实例。不要让两个账号共用一个外部 Gateway。

## 实例 ID 安全规则

平台 `id` 会参与默认认证目录名称。插件会将不适合文件名的字符正规化，并在发生正规化时加入稳定哈希后缀，以防路径穿越或两个不同 ID 被清理成同一个目录名。

建议仍使用简单、唯一的 ID，例如：

- `whatsapp`
- `whatsapp2`
- `whatsapp-work`
- `whatsapp-hk`

## 故障排查

如果 secondary instance 没有使用预期端口，请先检查该端口是否已经被其他程序占用。内置 Gateway 会自动继续向上寻找可用端口，并在日志中记录实际分配结果。

如果看到 external Gateway endpoint 已被另一个 adapter 占用的错误，请为两个账号配置不同的外部 Gateway，而不是关闭该保护。该限制用于防止消息和登录 session 串号。
