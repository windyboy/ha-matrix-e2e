# 设备安全验证：手把手操作指南

一句话：验证就是确认「Home Assistant 里的那个机器人设备，确实是你自己的设备」，防止有人拿到机器人账号密码后，用别的设备冒充它。

## 你需要准备什么

- 一个 Matrix 客户端 **Element**（手机或电脑都行）
- 机器人账号（例如 `@ha-bot:example.org`）的密码

## 整体思路

机器人账号有两把「钥匙」：

- **cross-signing 身份**：只放在 Element 里，代表「这个账号归谁管」。
- **设备密钥**：Home Assistant 里的机器人设备 `BOT_SERVER_01` 自己的一把钥匙。

验证 = 让 Element 确认 `BOT_SERVER_01` 这把钥匙可信。确认方式是一串 emoji（像配对蓝牙设备那样）。

## 第 0 步（一辈子只做一次）：建立账号身份

1. 用机器人账号登录 Element。
2. Element 首次登录会提示「设置安全密钥 / 恢复」——按提示完成，这是给账号建立 cross-signing 身份。
3. 记下并保存恢复密钥（重要）。

## 第 1 步：启动 Home Assistant

1. 装好 `matrix_e2ee`，在 UI 里添加（设置 → 设备与服务 → 添加集成 → Matrix E2EE）。
2. 添加成功即可。此时机器人设备 `BOT_SERVER_01` 已上线，并上传了自己的设备密钥。

## 第 2 步：在 Element 发起验证

1. Element → 设置 → 安全与隐私 → 会话 / 设备。
2. 找到 `BOT_SERVER_01`（通常是未验证的灰色盾牌）。
3. 点它 → 验证。

整个流程由 **Element 发起**，HA 这端不需要调用 `matrix_e2ee.start_verification`。

## 第 3 步：比对 emoji

1. Element 显示一串 emoji（比如 🐶 🌙 🔑）。
2. 回到 Home Assistant → 开发者工具 → 事件，监听 `matrix_e2ee_verification`。会依次看到 `stage: started`（验证已开始），然后是 `stage: sas`（进入比对），其中的 `emojis` 就是 HA 这一端显示的 emoji。
3. **逐个比对两端 emoji 是否完全一致。**

## 第 4 步：确认

- 两端完全一致 → 开发者工具 → 服务，调用 `matrix_e2ee.confirm_verification`，填 `transaction_id`（就是 `matrix_e2ee_verification` 事件里的那个）。
- 不一致 → 调用 `matrix_e2ee.cancel_verification`，并检查账号是否被盗。

## 第 5 步：看到 Verified

确认后，Element 里 `BOT_SERVER_01` 变成绿色「已验证」。HA 端也会发一个 `stage: done` 事件。

## 重启后还在吗？

在。重启 HA 会恢复**同一个**设备 `BOT_SERVER_01`，验证状态保留。如果重启后设备 ID 变了，说明 crypto store 丢失，需要按 runbook 重新验证。

## 备选：用指纹手动信任（不推荐，除非没法用 SAS）

这是「单向本地信任」，不是完整 SAS：

1. HA 开发者工具 → 服务 `matrix_e2ee.get_fingerprint`，或读 `matrix_e2ee_fingerprint` 事件，拿到机器人的 `ed25519` 指纹。
2. Element 里对机器人会话用「手动验证」比对指纹。
3. 想从机器人这一端信任某设备：调用 `matrix_e2ee.verify_device_by_fingerprint`，填 `user_id`、`device_id`、该设备的 `ed25519`。**指纹必须完全一致才生效**，不一致会报 `fingerprint_mismatch`。

注意：这条路不经过 emoji 比对，安全依赖你亲自在可信渠道比对指纹。

## 出错怎么办

如果事件出现 `stage: canceled`：这次验证已作废（transaction 失效），**不要**调用 `confirm_verification`，回到第 2 步从 Element 重新发起。

| 现象 | 含义 | 处理 |
|---|---|---|
| `verification_peer_denied` | 发起验证的不是机器人自己或 `allowed_users` | 检查是谁在发起 |
| `verification_timeout` | 10 分钟没确认 | 重新发起一次验证 |
| `fingerprint_mismatch` | 指纹对不上 | 停止，重新核对指纹，怀疑中间人 |
| `invalid_transaction` | `transaction_id` 不对 | 从最新 `matrix_e2ee_verification` 事件里复制 |

## 一句话总结

每个设备——**包括机器人自己账号的另一台设备**——都必须「看 emoji → 你确认」才能被信任。没有自动信任。
