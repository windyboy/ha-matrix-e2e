# SAS 设备验证架构

> 适用对象：维护 `matrix_e2ee` 验证流程、信任策略或 matrix-nio 兼容层的开发者。
>
> 语言：[English](SAS_ARCHITECTURE.md) | [中文](SAS_ARCHITECTURE.zh.md)

本文说明本集成如何实现 Matrix 短认证字符串（Short Authentication String，SAS）设备验证。用户操作步骤见[设备验证指南](DEVICE_VERIFICATION.zh.md)，四项 matrix-nio 修正见[兼容性说明](NIO_COMPAT.zh.md)。

## 范围

本集成使用 to-device 消息实现 `m.sas.v1`，支持两种方向：

- Element 发起，Home Assistant 机器人响应。这是 UI 向导采用的推荐流程。
- 机器人通过 `start_verification` 服务向已知设备发起。

两种方向都要求用户人工比对 emoji，并显式确认。协议上的 `accept` 只表示继续协商，不代表信任设备。

本集成不负责：

- 在 Home Assistant 主机上创建或管理交叉签名私钥。
- 导入安全密钥备份或安全秘密存储（SSSS）。
- 自动信任同账号设备。
- 在未验证设备与明文通信之间降级。

协议背景以 Matrix 规范的 [Key verification framework](https://spec.matrix.org/latest/client-server-api/#key-verification-framework) 和 SAS 方法为准；本文只记录项目实际行为。

## 信任模型

Home Assistant 主机是一个独立的 E2EE 端点，保存机器人设备私钥、Olm/Megolm 会话和已验证设备状态。交叉签名的 master、self-signing 和 user-signing 私钥保留在受信任的 Element 设备或离线恢复材料中。

由此得到以下约束：

1. 主机或加密存储泄露等同于机器人设备泄露。
2. 同一 Matrix 账号下的其他设备仍需独立验证。
3. SAS 成功后建立双向信任；指纹验证只建立调用方本地的单向信任。
4. 加密存储丢失后产生的新设备不能继承旧设备的信任。

完整威胁边界和泄露处置见 [SECURITY.md](../SECURITY.md)。

## 组件职责

| 组件 | 职责 |
|---|---|
| Element | 发起推荐验证流程，显示 emoji，维护交叉签名身份 |
| Options Flow | 等待入站 SAS，显示机器人侧 emoji，收集用户的匹配或取消决定 |
| `client.py` | 校验发起者、桥接缺失的框架消息、管理超时、触发 HA 事件和服务 |
| matrix-nio | 维护 `Sas` 状态机，交换临时密钥，生成 emoji，生成并校验 MAC |
| `nio_compat.py` | 修正 matrix-nio 0.26.0 的四项互操作问题 |

`enable_verification_callbacks()` 同时注册通用 to-device 回调与 nio SAS 事件回调。`handle_to_device_event()` 只处理 `start`、`key`、`mac` 和 `cancel`；`accept` 由 matrix-nio 状态机内部处理。

## Element 发起的推荐流程

Options Flow 的 `async_step_verify_device()` 只等待入站请求，不主动创建验证事务。

```text
Element                         Home Assistant / matrix-nio
   |  request  -------------------------------->  校验发起者、设备、方法和时间戳
   |  <--------------------------------  ready
   |  start    -------------------------------->  建立或修复 SAS，发送 accept
   |  <------------------------------->  key 交换
   |                                             触发 stage: sas
   |             用户在两端比对 emoji
   |                                             confirm_short_auth_string()
   |  <------------------------------->  mac 交换
   |                                             验证设备
   |  <--------------------------------  done
   |                                             触发 stage: done
```

matrix-nio 0.26.0 不实现 `request`、`ready` 和 `done` 框架消息。本集成通过 `_handle_verification_request()`、`_send_verification_ready()` 和 `_send_verification_done()` 补齐它们：

- `request` 必须来自机器人自己的账号或 `verification_peer_users` 允许的用户。
- 请求必须包含 `m.sas.v1`、有效设备 ID、事务 ID 和允许范围内的时间戳。
- `ready` 只宣布支持 SAS，不建立信任。
- MAC 校验完成后发送 `done`，使 Element 从等待状态进入完成状态。

如果新设备尚未进入 nio 的 `device_store`，nio 会丢弃第一次 `start`。`_repair_dropped_start()` 查询发送方设备密钥后，将同一个事件重新交给 nio，避免用户手动重试。

## 机器人发起的流程

`async_start_verification(user_id, device_id)` 只能对 `device_store` 中的已知设备发起，并返回事务 ID。后续 `accept`、`key` 和 MAC 处理仍由 nio 状态机完成。

该方向主要供服务调用和自动化使用。它不会绕过人工确认：只有 `async_confirm_verification(transaction_id)` 会接受 SAS 并发送机器人的 MAC。

## 状态、事件与超时

`matrix_e2ee_verification` 事件使用以下阶段：

| 阶段 | 含义 |
|---|---|
| `started` | SAS 事务已建立 |
| `sas` | emoji 已生成，可供用户比较 |
| `done` | MAC 校验完成，设备已验证 |
| `canceled` | 任一方取消事务 |
| `timeout` | 本集成的验证窗口已过期 |

`sas` 阶段额外包含 `emojis` 和 `expires_at`。其他阶段携带事务及对端设备标识；取消事件还可能包含协议错误码与原因。

本集成首次发现事务时记录 240 秒期限，后续事件不会刷新期限。确认操作和入站事件处理会检查该期限，Options Flow 也会在相同时间后停止等待。过期事务会在下一次接受检查的交互中被取消并拒绝其 `transaction_id`；完全空闲的事务可能继续保留到 nio 清理。nio 自身的超时缺陷由兼容层处理。

## 安全不变量

修改验证代码时必须保持：

1. 只有人工确认 emoji 匹配后才能调用 `confirm_short_auth_string()`。
2. 不能根据“同一账号”自动信任设备。
3. 入站验证的所有分支都必须执行 `_bootstrap_allowed()` 检查。
4. 指纹必须按区分大小写的字符串精确匹配，不能使用 `casefold()`。
5. `key` 由 nio 状态机发送；集成不能再次调用 `share_key()`。
6. MAC 只由确认路径发送一次，生成与校验逻辑必须同步修改。
7. 未验证设备不能触发命令事件，也不能回退到明文。
8. 日志和事件不能包含 access token、pickle key、消息正文或加密存储内容。

## 测试边界

现有测试使用 `FakeNio` 和 `FakeSas`，覆盖服务、事件、超时、门控和 Options Flow，但不能发现真实客户端之间的线上编码差异。对 SAS、commitment、emoji 或 MAC 的改动，应额外在真实 Matrix homeserver 与 Element 上执行端到端验证。

相关测试：

- `tests/test_m3_contract.py`：验证服务、事件与信任策略。
- `tests/test_options_flow.py`：UI 向导。
- `tests/test_nio_compat.py`：matrix-nio 兼容层。

## 相关文档

- [设备验证指南](DEVICE_VERIFICATION.zh.md)
- [matrix-nio 兼容性说明](NIO_COMPAT.zh.md)
- [开发说明](DEVELOPMENT.zh.md)
- [安全模型](../SECURITY.md)
