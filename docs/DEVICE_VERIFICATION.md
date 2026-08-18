# 设备安全验证：手把手操作指南

一句话：验证就是确认「Home Assistant 里的那个机器人设备，确实是你自己的设备」，防止有人拿到机器人账号密码后，用别的设备冒充它。

## 你需要准备什么

- 一个 Matrix 客户端 **Element**（手机或电脑都行）
- 机器人账号（例如 `@ha-bot:example.org`）的密码

## 整体思路

机器人账号有两把「钥匙」：

- **cross-signing 身份**：只放在 Element 里，代表「这个账号归谁管」。
- **设备密钥**：Home Assistant 里的机器人设备 `BOT_SERVER_01` 自己的一把钥匙。

> 说明：`BOT_SERVER_01` 只是本文的示例标识。实际部署里，机器人设备的**显示名**是 `Home Assistant matrix_e2ee`（`const.py` 的 `DEVICE_NAME`），设备 ID 由服务器随机生成，请在 Element 会话列表里按这个显示名找。

验证 = 让 Element 确认 `BOT_SERVER_01` 这把钥匙可信。确认方式是一串 emoji（像配对蓝牙设备那样）。

## 方式一：HA 界面向导（v0.3 起，推荐）

从 Home Assistant 界面直接发起并确认设备验证，无需在开发者工具里手动调服务：

1. 设置 → 设备与服务 → Matrix E2EE → 配置（Options）。
2. 选择「验证设备」（Verify device）。
3. 从下拉列表选择要验证的设备（列出的是机器人已认识的设备，不含机器人自身）。
4. HA 发起 SAS 验证并显示一串 emoji。
5. 在 Element（或对方设备）上接受验证请求并比对 emoji。
   - 两端完全一致 → 选择「它们匹配」（They match）。
   - 不一致 → 选择「它们不匹配」（They do not match），并检查账号是否被盗。
6. 等待双方 MAC 完成，界面提示「设备验证完成」，Element 里该设备变为绿色「已验证」。

> 说明：下拉列表为空（`no_devices`）表示机器人尚不认识任何其他设备；先让 Element 对机器人发起一次验证（见下方「方式二」），或等待同步后再试。

## 方式二：Element 发起验证

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
| `verification_timeout` | 4 分钟没确认 | 重新发起一次验证 |
| `fingerprint_mismatch` | 指纹对不上 | 停止，重新核对指纹，怀疑中间人 |
| `invalid_transaction` | `transaction_id` 不对 | 从最新 `matrix_e2ee_verification` 事件里复制 |

## 一句话总结

每个设备——**包括机器人自己账号的另一台设备**——都必须「看 emoji → 你确认」才能被信任。没有自动信任。

---

## 附录：SAS 验证流程（源码级参考）

给维护者/审查者的技术参考。事件顺序以 Matrix 规范 `m.key.verification.*`（to-device）为准。

### 关键源码定位

**matrix-nio 0.26.0**（`matrix-nio` 包）：

| 文件 | 符号 | 职责 |
|---|---|---|
| `nio/crypto/olm_machine.py` | `Olm.handle_key_verification()` | SAS 状态机驱动：消费 `KeyVerificationStart/Accept/Key/Mac/Cancel`，负责建立 `Sas`、自动 share key、校验 MAC、`verify_device()` |
| `nio/crypto/sas.py` | `Sas`（`SasState`） | 状态机 `created → started → accepted → key_received → mac_received → canceled`；`share_key()`/`get_mac()`/`accept_verification()`/`receive_key_event()`/`receive_mac_event()`/`accept_sas()`；`verified == (state == mac_received and sas_accepted)` |
| `nio/client/async_client.py` | `start_key_verification()` / `accept_key_verification()` / `confirm_short_auth_string()` / `cancel_key_verification()` | 面向应用的 API，内部 `to_device()` 即时发送 |
| `nio/client/async_client.py` | `sync_forever()` → `send_to_device_messages()` | 定时排空 `outgoing_to_device_messages` 队列 |

**本集成**（`custom_components/matrix_e2ee/client.py`）：

| 符号 | 职责 |
|---|---|
| `_query_device_keys()` | 把指定账号的设备 key 预取进 `device_store`（`_query_own_device_keys()` 是其特例，登录/恢复后查机器人自己账号） |
| `_repair_dropped_start()` | 收到 `start` 但 nio 因设备未知已丢弃时：查 sender 的 key 后把同一个 `start` 重喂给 `nio.olm.handle_key_verification()` |
| `_patch_nio_sas_timeout()` | monkeypatch `Sas.timed_out`，忽略 nio 失效的 60s 事件超时（`_last_event_time` 从不刷新），只保留 5min 总超时 |
| `enable_verification_callbacks()` | 注册 `handle_to_device_event` 到 `add_to_device_callback` |
| `handle_to_device_event()` | 集成回调，处理 `start` / `key` / `mac` / `cancel`（**不处理 `accept`**） |
| `async_start_verification()` / `async_confirm_verification()` / `async_cancel_verification()` | 面向 HA 服务的三个入口 |

### 方向 A：Element 发起（peer-initiated，bot 是接受方，`we_started_it=False`）

> 完整流程以 `request → ready` 开头（由集成 `_handle_verification_request()` 桥接），下表从 SAS 阶段的 `start` 开始。

| # | 事件 | 谁处理 | 动作 |
|---|---|---|
| 1 | `start`（Element → bot） | nio `handle_key_verification` | 查 `device_store`（靠 `_query_device_keys` 预热）；命中则 `Sas.from_key_verification_start` 建 `Sas(we_started_it=False, state=started)` 并注册 `key_verifications[txn]`；**miss 则丢弃该 start 并加 `users_for_key_query`** |
| 2 | — | 集成 `handle_to_device_event("start")` | 若 nio 已丢弃该 start（设备未知），`_repair_dropped_start()` 查 key 后重喂 start 让 nio 建 SAS；随后 `accept_key_verification()` 发 `accept` |
| 3 | `key`（Element → bot） | nio `handle_key_verification` | `receive_key_event()` 建立共享密钥；`not we_started_it` → 自动 `share_key()` 入队 |
| 4 | — | `sync_forever` | `send_to_device_messages()` 发出 bot 的 `key` |
| 5 | — | 集成 `handle_to_device_event("key")` | 发 `stage: sas`（含 emoji） |
| 6 | 用户调 `confirm_verification` | 集成 `async_confirm_verification` | `confirm_short_auth_string()` → `accept_sas()` + `get_mac()`，发 bot 的 `mac` |
| 7 | `mac`（Element → bot） | nio `handle_key_verification` | `receive_mac_event()` 校验，`verified` → `verify_device()` |
| 8 | — | 集成 `handle_to_device_event("mac")` | `verified` 为真 → 发 `stage: done` |

### 方向 B：机器人发起（bot-initiated，`we_started_it=True`）

| # | 事件 | 谁处理 | 动作 |
|---|---|---|---|
| 1 | 用户调 `start_verification` | 集成 `async_start_verification` | `start_key_verification()` 建 `Sas(we_started_it=True, state=created)` 并发 `start` |
| 2 | `accept`（Element → bot） | nio `handle_key_verification` | `receive_accept_event()` → 自动 `share_key()` 入队（initiator 先发 key） |
| 3 | `key`（Element → bot） | nio `handle_key_verification` | `receive_key_event()`；`we_started_it` 为真 → 不再 share |
| 4 | — | 集成 `handle_to_device_event("key")` | 发 `stage: sas` |
| 5 | 用户调 `confirm_verification` | 集成 `async_confirm_verification` | `confirm_short_auth_string()` 发 `mac` |
| 6 | `mac`（Element → bot） | nio `handle_key_verification` | `receive_mac_event()` → `verify_device()` |
| 7 | — | 集成 `handle_to_device_event("mac")` | `verified` → 发 `stage: done` |

### 关键事实

- **request/ready 由集成桥接**：matrix-nio 0.26.0 没有 `m.key.verification.request` / `ready` 处理（request 被解析成 `UnknownToDeviceEvent` 丢弃）。现代 Element 走 `request → ready → start …`，集成 `_handle_verification_request()` 负责校验 request 并回 `ready`，之后才由 nio 的 SAS 状态机接管 `start`。
- **集成不处理 `accept`**：`_verification_kind()` 只映射 `start/key/mac/cancel`，`accept` 由 nio 内部状态机自动处理（方向 B 的 #2）。
- **key 的发送归 nio 管**：无论哪个方向，bot 的 `key` 都由 nio 内部 `share_key()` 入队、`sync_forever` 发出；集成**不应**手动 `share_key`。
- **mac 只由 `confirm_verification` 服务发送一次**：`confirm_short_auth_string()` 内部已 `accept_sas()` + `get_mac()`。
- **已知问题（见对应 issue）**：
  - **W1N-169**：~~当前实现对 key 和 mac 各多发一次（方向 A 中集成手动 `share_key`、`mac` handler 里 `_try_confirm` 重复发 mac）。~~ 已修复：key 由 nio 内部状态机在收到 peer key 时统一发送，mac 仅由 `confirm_verification` 发送。
  - **W1N-170**：~~bot 上线后才新增的设备，第一次 SAS 的 `start` 会被 nio 丢弃（"unknown device"），需重试一次。~~ 已修复：`_repair_dropped_start()` 在收到未知设备的 `start` 时自动查 key 并重喂，无需手动重试。
  - **W1N-172**：~~nio 0.26.0 的 `Sas._last_event_time` 从不刷新，SAS 在创建 60s 后必然超时（即使人还在比对 emoji）。~~ 已修复：`_patch_nio_sas_timeout()` monkeypatch `timed_out` 忽略失效的 60s 事件超时，只保留 5min 总超时；集成自己的超时对齐为 4min。
  - **W1N-173**：~~matrix-nio 0.26.0 缺 `request`/`ready` 握手，Element 的验证请求在第一步就被丢弃，最终只收到 `cancel`。~~ 已修复（P0）：`_handle_verification_request()` 校验 request 并回 `ready`，让 Element 继续发 `start`。P1（`m.key.verification.done`）后续完善。
  - **W1N-171**：SAS 全链路从未在真实 Matrix homeserver 上做过端到端验证（现有测试全用 FakeNio）。暂缓解决。
