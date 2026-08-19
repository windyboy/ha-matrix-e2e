# 安全

> 语言：[English](SECURITY.md) | [中文](SECURITY.zh.md)

`matrix_e2ee` 以 Home Assistant 自定义集成的形式运行带端到端加密的专用 Matrix 机器人。本文记录信任模型与两种受支持的设备验证路径。

## 信任边界

- Home Assistant 主机是 E2EE 端点。它保存机器人设备的私钥（`pickle_key`、Olm/Megolm 会话）并解密 Matrix 文本。
- **交叉签名权限从不放在本主机上。** 机器人的 `master` / `self-signing` / `user-signing` 密钥保留在受信任的 Element 设备（或离线恢复材料）中。集成只持有机器人自己的设备密钥，不能为新设备签名授信。
- 主机、备份、日志或加密存储被攻破，等同于机器人设备被攻破。请把它们作为同一整体保护。Home Assistant 的私有存储不是磁盘加密；若需抵御离线窃取，请使用主机/卷级加密。

## 单个机器人 / 单个 config entry

`matrix_e2ee` 只支持一个 config entry（`"single_config_entry": true`）。会话文件（`.storage/matrix_e2ee_session.json`）和加密存储（`.storage/matrix_e2ee_store/`）是集成级全局资源，不是按 entry 隔离的：第二个 entry 会把它们重新绑定到另一个账号和设备。因此 Config Flow 对任何第二个 entry（无论用户名是否相同）都会以「Already configured」中止。不要尝试通过本集成运行两个机器人账号。

## 重新配置与服务器 origin 变更

重新配置允许修改 homeserver URL。集成比较的是 **origin**（scheme、host、port），而不是 path 或末尾斜杠：

- **同一 origin**（仅 path 或末尾斜杠变化）：保留会话与加密存储，同一设备及其信任状态继续有效。
- **不同 origin**：集成 **失败关闭** —— 将旧会话文件和加密存储隔离（重命名为带 `.quarantined-*` 后缀，从不直接删除），绝不把旧 access token 发往新 origin，并要求你重新输入密码以作为**新设备**登录。之后必须重新做设备验证；新设备不会继承旧设备的信任。

## 设备验证（两条路径）

使用 **设置 → 设备与服务 → Matrix E2EE → 配置 → 验证设备** 打开实时 SAS emoji 向导。向导等待 Element 发起验证，不会自己创建事务。`start_verification` / `confirm_verification` / `cancel_verification` 服务仍可供高级用途使用，`matrix_e2ee_verification` 会报告每个阶段。

### 1. SAS（双向，需人工确认）

1. 在 Element 中登录机器人账号。该设备成为管理/引导设备。
2. 在 Element 中为机器人账号引导交叉签名身份。
3. 启动集成；它会创建显示名为 `Home Assistant matrix_e2ee`、由服务器生成 device ID 的设备。
4. 在 Home Assistant 中打开向导，再从 Element 的「会话」页验证该设备。
5. 逐项比对 SAS emoji，仅在两端完全一致时在 Home Assistant 中确认。
6. MAC 交换完成后，Element 会将该设备显示为已验证。

没有自动确认。即使是机器人同一账号下的第二台设备，也必须经过同样的人工 emoji 比对和显式 `confirm_verification`。只有机器人自己的账号或 `verification_peer_users` 中的用户可以发起 SAS。未授权的框架请求会被忽略；未授权的 SAS 事件会发出 `verification_peer_denied`。

### 2. 单向指纹（备用）

1. 监听 `matrix_e2ee_fingerprint`，再调用 `matrix_e2ee.get_fingerprint`，从事件中读取机器人的 `ed25519` 设备密钥。
2. 在 Element 中打开机器人用户的会话，使用「通过文本手动验证」。
3. 将会话密钥与指纹比对。这只建立 Element 侧对机器人的信任。

若要在不做 SAS 的情况下让机器人信任某台设备，调用 `matrix_e2ee.verify_device_by_fingerprint` 并提供该设备的 `ed25519` 密钥。仅在指纹完全一致时建立信任；否则 `matrix_e2ee_error` 会发出 `fingerprint_mismatch`。这是本地信任，不是 SAS，也不是交叉签名。

## 为什么不做交叉签名自签？

matrix-nio 0.26 未实现交叉签名密钥引导或自签。自签也会把机器人的交叉签名权限放到 Home Assistant 主机上，这是本集成有意避免的。

操作步骤见 [设备验证](docs/DEVICE_VERIFICATION.zh.md)。实现边界见 [SAS 架构](docs/SAS_ARCHITECTURE.zh.md)。

## 会话与加密存储必须成对

`.storage/matrix_e2ee_session.json`（含 `user_id`、`device_id`、`access_token`、`pickle_key`）与 `.storage/matrix_e2ee_store/`（Olm/Megolm、设备信任、sync token）是一对。备份或迁移时必须一起复制；缺少任一方或配对不一致，都应视为新设备。仅有 session 文件无法恢复 Megolm 历史。

## 合法迁移到新主机

1. 在旧主机上停止 Home Assistant（或禁用本集成），避免同一设备双端在线。
2. 将 session 文件与加密存储**成对**复制到新主机相同路径。
3. 在新主机安装相同版本的 `matrix_e2ee` 并重启。
4. 通过**设置 → 设备与服务**重新添加集成（流程仍会要求输入密码；由于 session 文件存在，客户端会恢复同一 `device_id`，而不是创建新设备）。若文件缺失或不匹配，按新设备处理并重新验证。

## 密钥泄露或主机被盗

1. 在 homeserver 上吊销机器人的 Matrix 设备。
2. 删除 `.storage/matrix_e2ee_session.json` 和 `.storage/matrix_e2ee_store/`。
3. 删除集成后通过 UI 重新添加；首次登录会创建新设备。
4. 通过 SAS（或指纹）重新验证应信任的设备。旧密文不可恢复。
