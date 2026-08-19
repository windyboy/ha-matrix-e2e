# 设备验证

> 适用对象：使用 Element 验证 Home Assistant 机器人设备的管理员。
>
> 语言：[English](DEVICE_VERIFICATION.md) | [中文](DEVICE_VERIFICATION.zh.md)

设备验证用于确认 Element 中显示的 `Home Assistant matrix_e2ee` 确实是你的 Home Assistant 机器人设备。验证成功后，Element 与机器人会互相信任对方的设备。

## 准备工作

- 安装 Matrix 客户端 [Element](https://element.io/)（手机或桌面版均可）。
- 准备机器人 Matrix 账号及密码，例如 `@ha-bot:example.org`。
- 确认 `matrix_e2ee` 已通过 Home Assistant 界面完成配置并正常连接。

首次在 Element 中登录机器人账号时，请按提示设置安全密钥或恢复方式，并妥善保存恢复密钥。此操作用于建立账号的交叉签名（cross-signing）身份；重置安全存储后可能需要重新设置。

## 推荐方式：使用 Home Assistant 向导

在推荐流程中，验证由 Element 发起。Home Assistant 向导本身不会创建验证事务，只负责等待请求、显示 emoji 并提交确认。机器人主动发起的方式仍可通过高级服务接口使用。

1. 在 Home Assistant 中打开：**设置 → 设备与服务 → Matrix E2EE → 配置 → 验证设备**。
2. 保持等待页面打开。
3. 在 Element 中登录机器人账号，然后打开：**设置 → 会话**。
4. 找到显示名为 `Home Assistant matrix_e2ee` 的设备并选择**验证**。设备 ID 由 Matrix 服务器生成，不是固定值。
5. Element 和 Home Assistant 都会显示一组 emoji。逐项确认两边完全一致。
6. 一致时，在 Home Assistant 中选择**它们匹配**；不一致时选择**它们不匹配**并取消本次验证。
7. 等待 Home Assistant 显示“设备验证完成”。Element 中该设备应同时显示为“已验证”。

一次成功的 SAS 验证会建立双向信任：Element 信任机器人设备，机器人也信任当前 Element 设备。即使两个设备属于同一个 Matrix 账号，也不会自动互相信任。

## 高级方式：通过开发者工具确认

如果无法使用验证向导，可以通过 Home Assistant 开发者工具完成同一流程：

1. 在**开发者工具 → 事件**中监听 `matrix_e2ee_verification`。
2. 从 Element 发起验证。
3. 收到 `stage: sas` 后，将事件中的 `emojis` 与 Element 显示的 emoji 逐项比较。
4. 一致时调用 `matrix_e2ee.confirm_verification`，并填写事件中的 `transaction_id`。
5. 不一致时调用 `matrix_e2ee.cancel_verification`，并填写同一事件中的 `transaction_id`。
6. 收到 `stage: done` 表示验证完成。

如果已经收到 `stage: canceled` 或 `stage: timeout`，该 `transaction_id` 已失效。请从 Element 重新发起，不要继续确认旧事务。

## 备用方式：按指纹建立本地信任

只有无法使用 SAS emoji 验证时才建议使用指纹。它建立的是单向本地信任，不等同于完整的 SAS 验证。

1. 在**开发者工具 → 事件**中监听 `matrix_e2ee_fingerprint`，然后调用 `matrix_e2ee.get_fingerprint`，从事件中读取机器人的 `ed25519` 指纹。
2. 在 Element 的机器人会话中选择手动验证，并通过可信渠道逐字核对指纹。
3. 如需让机器人信任另一台设备，调用 `matrix_e2ee.verify_device_by_fingerprint`，填写 `user_id`、`device_id` 和该设备的 `ed25519` 指纹。

指纹区分大小写，只有完全一致时才会建立信任；不一致时，`matrix_e2ee_error` 会触发 `fingerprint_mismatch`。

## 常见问题

| 现象 | 处理 |
|---|---|
| 找不到机器人设备 | 确认集成已连接；在 Element 会话列表中按显示名 `Home Assistant matrix_e2ee` 查找 |
| `verification_peer_denied` | 确认由机器人自己的账号或 `verification_peer_users` 中允许的用户发起 |
| `verification_timeout` | 验证已超过 4 分钟；从 Element 重新发起 |
| `invalid_transaction` | 使用最新验证事件中的 `transaction_id` |
| emoji 不一致 | 立即取消，核对账号和目标设备后重试；持续异常时再调查账号或通信链路风险 |
| 重启后设备 ID 改变 | 加密存储可能已丢失；这是一个新设备，需要重新验证 |

会话和加密存储必须与 Home Assistant 配置目录一起持久化。恢复同一份存储时，重启不会丢失设备 ID 和验证状态。

## 相关文档

- [SAS 架构说明](SAS_ARCHITECTURE.zh.md)：面向维护者的协议流程和职责边界。
- [matrix-nio 兼容性说明](NIO_COMPAT.zh.md)：SAS 兼容性修正与升级检查。
- [安全模型](../SECURITY.md)：信任边界、存储保护与密钥泄露处置。
