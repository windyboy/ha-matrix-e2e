# Matrix 设备认证（SAS）— 知识沉淀

> 本文档是 `ha-matrix-e2ee`（`custom_components/matrix_e2ee`，基于 matrix-nio 0.26.0 + vodozemac）在
> Matrix 设备验证方面**全部已知知识的汇总**，范围：官方规范、官方文档/实现偏差、认证流程、代码定位、
> 注意事项。目的：避免重复排查、重复实现、重复踩坑。
>
> 配套文档：[`docs/DEVICE_VERIFICATION.md`](DEVICE_VERIFICATION.md)（人话操作指南 + 源码级流程）、
> [`SECURITY.md`](../SECURITY.md)（信任模型与威胁边界）、[`docs/DEVELOPMENT.md`](DEVELOPMENT.md)（测试纪律）。
>
> 维护约定：完成/发现新的验证相关 issue 后，把结论同步到本文档对应章节和「Issue 索引表」，不要只留在 Linear。

---

## 1. 官方规范（Matrix Spec）

规范来源：matrix-spec `content/client-server-api/modules/end_to_end_encryption.md`（注意：**不是**
`sas_verification.md`，后者 404）。涉及两个机制：**验证框架**（`m.key.verification.request/ready/start/.../done/cancel`）
与 **SAS 方法**（`m.sas.v1`）。

### 1.1 验证框架

- **消息通道**：同一账号内验证用 **to-device** 消息；不同用户之间建议用 **in-room** 消息。本项目（bot 验证自己的
  另一台设备）走 to-device。
- **Session ID**：to-device 用 `transaction_id`；in-room 用 event ID。
- **流程**：`request`（发起方声明支持的 methods）→ 对方提示接受 → `ready`（接受方回 methods 交集）→ 用户选方法 →
  `start` → 方法内交换 → `done`。**任意时点**任一方都可发 `cancel`（带 code）。
- **多设备广播**：to-device 的 `request` 广播到对方所有设备（同一 txn）。其中一台接受后，对其他设备发 `cancel`
  （code `m.accepted`）；用户在另一台拒绝则 code `m.user`。in-room 只发一次 request，无 cancel-to-others。
- **提示自动消失**：to-device 按 `timestamp`（in-room 按 `origin_server_ts`）起 **10 分钟**，或收到后 **2 分钟**，
  先到为准。拒绝请求**必须**发 `cancel`，code=`m.user`。

### 1.2 SAS 方法 `m.sas.v1` — 安全原理

灵感来自 **ZRTP hash commitment**：responder 在 `accept` 里先发**自己公钥的 hash（commitment）**，initiator 收到
commitment 后才发自己的公钥。攻击者只有一次猜的机会：验证 n bit 则成功率 `1/2^n`（SAS 全 40+ bit → ~1/10^12）。
分两个阶段：

1. **密钥协商**：双方各生成临时 Curve25519 密钥对，交换公钥（带 commitment 保护），ECDH 得共享秘密。
2. **密钥验证**：用共享秘密派生 HMAC，互相认证各自的设备 ed25519 key。

### 1.3 SAS 全流程（18 步，initiator = Alice / responder = Bob）

| # | 动作 | 消息 | 内容要点 |
|---|---|---|---|
| 1 | 线下安全会面 | — | 双方对照设备显示内容 |
| 2 | 开始验证 | — | 任一方发起 |
| 3 | Alice 发 start | `m.key.verification.start` | **必须先拿到 Bob 设备 key**；含 key_agreement/hash/mac_method/short_authentication_string |
| 4 | Bob 选算法 | — | 从 Alice 支持列表里挑 key agreement/hash/MAC/SAS 方法 |
| 5 | Bob 需已持 Alice 设备 key | — | 否则先 key query |
| 6 | Bob 生成临时 Curve25519 对 | — | 对公钥做 SHA-256 |
| 7 | Bob 回 accept | `m.key.verification.accept` | 含 `commitment`（自己公钥的 hash） |
| 8 | Alice 存 commitment | — | 后续校验 |
| 9 | Alice 生成临时对、发 key | `m.key.verification.key` | **只发公钥** |
| 10 | Bob 发自己 key | `m.key.verification.key` | 已无 commitment 保护风险 |
| 11 | Alice 校验 commitment | — | `commitment == hash(Bob 公钥 + Alice 的 start content)` |
| 12 | 双方 ECDH | — | 临时密钥 → 共享秘密 |
| 13 | 双方显示 SAS | — | emoji/decimal（多方法时用户选） |
| 14 | 用户比对 | — | 两边一致才继续 |
| 15 | 算 MAC | — | 对每个要验证的 key + key ID 列表 |
| 16 | 双方发 mac | `m.key.verification.mac` | |
| 17 | 校验对方 MAC | — | 每个 key 的 MAC + key 列表 MAC 全对 → 设备已验证 |
| 18 | 双方发 done | `m.key.verification.done` | |

**要验证哪些 key**：本设备的 ed25519 key + master signing key（跨用户时**应**含 MSK；「验证他人单设备」的做法已废弃）。

### 1.4 错误处理与 cancel code

- 随时可 cancel；**10 分钟超时**（txn 闲置 10min 也过期）。
- 同一设备多次发起 → recipient 全部 cancel。
- **未知 txn → cancel**（入站 `start`/`cancel` 除外）。
- 无共同方法 → cancel；SAS 不符 → cancel；乱序 → cancel。
- SAS 专属 cancel code：`m.unknown_method`、`m.mismatched_commitment`、`m.mismatched_sas`。
- 框架层 code：`m.user`（用户拒绝）、`m.accepted`、`m.timeout`、`m.unexpected_message`、`m.key_mismatch`。

### 1.5 MAC 计算（wire 格式，容易错）

- **HKDF 参数**：HKDF-SHA256，IKM = 共享秘密，**无 salt**。
- **MAC info 串**（逐字节拼接，无分隔符）：
  `MATRIX_KEY_VERIFICATION_MAC` + 发 MAC 方 `user_id` + `device_id` + 对方 `user_id` + `device_id` + `transaction_id`
  + `key_id`（单个 key）；对 key 列表用字符串 `KEY_IDS`。
- **HMAC 对象**：
  - 单个 key → **unpadded base64** 编码的公钥；
  - key 列表 → 字典序排序、**逗号连接、无空格**的 `alg:id` 列表，例如
    `ed25519:Cross+Signing+Key,ed25519:DEVICEID`。
- MAC 值 base64 编码后放进 `mac` 消息的 `mac` 与 `keys` 字段。
- **版本（关键）**：规范要求「所有当前实现都应用 `hkdf-hmac-sha256.v2`」。legacy `hkdf-hmac-sha256`（v1）因 libolm
  原始实现 bug 使用了**错误 base64 编码**，v2 修正；**v1 已废弃，双方都支持 v2 时 MUST NOT 用 v1**。

### 1.6 SAS 派生

- **HKDF info（`curve25519-hkdf-sha256`）**：
  `MATRIX_KEY_VERIFICATION_SAS|` + start 发起方 `user_id|` + `device_id|` + start 方公钥（unpadded base64）`|`
  + accept 方 `user_id|` + `device_id|` + accept 方公钥（unpadded base64）`|` + `transaction_id`。
  废弃的 `curve25519` 方法：无 `|` 分隔、不含公钥。
- **decimal**：取 5 字节 → 3 个 13-bit 数（0–8191）各 +1000 → 三个 1000–9191 的数。
  位运算：`(B0<<5|B1>>3)+1000`；`((B1&0x7)<<10|B2<<2|B3>>6)+1000`；`((B3&0x3F)<<7|B4>>1)+1000`。
- **emoji**：取 6 字节 → 前 42 bit → 7 组 6-bit → 7 个 0–63 索引 → 查 64 格 emoji 表
  （JSON 在 `matrix-org/matrix-spec` 仓库 `data-definitions/sas-emoji.json`）。

### 1.7 Cross-signing（本项目的「不做」依据）

三把 ed25519 对：**MSK**（master signing key，签 USK/SSK，代表用户身份）、**USK**（user-signing key，只自己可见，
签他人 MSK）、**SSK**（self-signing key，签自己的设备 key）。作用：只需验证一次 MSK 即可信任该用户所有设备。
本集成因 **nio 0.26 不支持自签 cross-signing key**，且自签会把 cross-signing 权威放到 HA 主机上（违背信任边界），
故**不做** SSSS / Key Backup 导入（W1N-147 结论）。可对他人设备验证其 MSK，但 bot 自身无法 bootstrap 新设备。

---

## 2. 官方规范 vs nio 0.26 的偏差（文档/实现漂移 — 最容易重复踩）

| # | 规范/预期 | nio 0.26.0 实际 | 修复 | 出处 |
|---|---|---|---|---|
| 1 | `request → ready` 由框架处理 | **没有实现**；request 被解析为 `UnknownToDeviceEvent` 丢弃 → Element 端显示取消 | 集成自建 `_handle_verification_request` + `_send_verification_ready` | W1N-173, PR #25 |
| 2 | 会话有 10 分钟活动窗口 | `Sas._last_event_time` 只在 `__init__` 赋值、**从不刷新** → `timed_out` 60s 后必真；`clear_verifications()` 每 sync 取消 | monkeypatch `Sas.timed_out`：只留 5min `_max_age`；集成超时 240s 先行触发 | W1N-172, PR #23 |
| 3 | commitment 为 **unpadded base64** | 迁移 vodozemac 后发 **hexdigest**，Element 拒收（`m.key_mismatch`） | `_apply_sas_commitment_patch`：`from_key_verification_start` + `_check_commitment` 都改回 unpadded base64 | PR #28, v0.2.8 |
| 4 | emoji 索引由底层算出 | vodozemac 已返回 7 个最终索引，nio 仍按 libolm 时代 bit-slicing **二次转换** → 两端 emoji 不一致、`m.mismatched_sas` | monkeypatch `_generate_emoji` 直接用 indices | W1N-175, PR #29 |
| 5 | 协商 `hkdf-hmac-sha256`(v1) 时 wire 用 libolm **invalid base64** | nio 用标准 `calculate_mac` → MAC 阶段失败 | monkeypatch `get_mac` + `receive_mac_event`，按 `chosen_mac_method` 选 `calculate_mac_invalid_base64`（libolm-compat feature） | W1N-177, PR #30 |
| 6 | 应优先/只用 `.v2` MAC | nio 0.26 **只协商 v1，不提供 `.v2`** | 保持 v1 + invalid_base64 与 rust-sdk/Element 互操作；**不要**自己加 `.v2`（会扩大协商面但 nio 没实现） | W1N-177 |
| 7 | `keys_query` 应覆盖会话各方 | 只取「共享加密房间用户」→ bot 与自身设备不共享房间 → **永不查询自己** → 入站 SAS 建不起来 | 登录/恢复后把自身 `user_id` 加进 `users_for_key_query` 并预热 | W1N-166, PR #18 |
| 8 | 收到 start 前应有设备 key | 新设备首次 start 命中 device_store KeyError → 丢弃 + 不建 SAS | `_repair_dropped_start`：查 key 后把同一 start 重喂给 `nio.olm.handle_key_verification` | W1N-170, PR #23 |
| 9 | 校验失败应发 `m.key_mismatch` cancel | `receive_mac_event`「无已验证设备」分支置 canceled 后**缺 return**，下一行覆盖为 `mac_received` | 上游 bug，**Backlog**（W1N-179） | W1N-179 |

另：nio 的 `Sas.verified = (state == mac_received) ∧ sas_accepted`，`get_mac()` 在 `sas_accepted=False` 时抛
`LocalProtocolError`——集成代码曾因裸调 get_mac 被 `except Exception` 吞掉而卡死（W1N-142）。

---

## 3. 认证流程（本项目实际路径）

### 3.1 对外接口（已定型）

**服务（admin-only 用 `async_register_admin_service` 强制，W1N-150）**：

| 服务 | 权限 | 说明 |
|---|---|---|
| `start_verification` | **admin** | bot 发起 SAS（入站由 Element 发起时无需调用） |
| `confirm_verification` | **admin** | 人工比对 emoji 后确认；内部 = `accept_sas` + `get_mac`（verified 时再 `verify_device`） |
| `cancel_verification` | **admin** | 取消 |
| `verify_device_by_fingerprint` | **admin** | 单向信任；`user_id` + `device_id` + `ed25519` **精确匹配**，否则 `fingerprint_mismatch` |
| `reauthenticate` | **admin** | Config Flow reauth |
| `get_fingerprint` | 普通注册 | 只读；返回 bot 的 `ed25519`/`curve25519` |
| `send_message` | 普通注册 | 发消息 |

**事件**：
- `matrix_e2ee_verification` — 各 `stage`：`started` / `sas` / `done` / `canceled` / `timeout`。其中只有 `sas` 带 `emojis` + `expires_at`；其余 stage 只带 `transaction_id`/`user_id`/`device_id`（`canceled` 还可能带 `code`/`reason`）
  （W1N-145 `VerificationPrompt`：`transaction_id/user_id/device_id/emojis/expires_at`）。
- `matrix_e2ee_fingerprint` — 启动后 emit bot 指纹。
- `matrix_e2ee_command` / `matrix_e2ee_error` — 命令事件（仅 `verified=True` 且 allowlist 命中才触发）。

**错误码**（`const.py`）：`verification_peer_denied`（发起者非 bot 自身账号或 `allowed_users`）、`verification_timeout`
（集成超时 4min）、`fingerprint_mismatch`、`invalid_transaction`、`device_missing`。

### 3.2 双向流程（wire 时序）

**方向 A：Element 发起（入站）** — 由 `handle_to_device_event` + `_handle_verification_request` 驱动：

```
Element                      HA bot (matrix_e2ee)
   │  m.key.verification.request {txn, methods:[m.sas.v1], timestamp}
   │ ──────────────────────────────────────────────►  _handle_verification_request
   │                                                    校验: sender 允许 / from_device、txn 非空 /
   │                                                    methods 含 m.sas.v1 / timestamp 界内
   │ ◄──────────────────────────────────────────────  m.key.verification.ready {methods:[m.sas.v1]}
   │  m.key.verification.start {txn, ...}
   │ ──────────────────────────────────────────────►  handle_to_device_event (start 分支)
   │                                                    _get_sas==None → _repair_dropped_start（查 key 重喂）
   │                                                    accept_key_verification(txn) → emit started
   │ ◄──────────────────────────────────────────────  m.key.verification.accept（nio 内部,含 commitment）
   │  m.key.verification.key ...                       key 交换 → 双方显示 emoji
   │ ──────────────────────────────────────────────►  (key 分支) emit sas {emojis, expires_at}
   │   HA 侧人工比对 emoji → confirm_verification(txn)
   │                                                    confirm_short_auth_string(txn)
   │                                                    = accept_sas + get_mac + (verified→verify_device)
   │ ◄──────────────────────────────────────────────  m.key.verification.mac（只发一次, W1N-169）
   │  m.key.verification.mac → (mac 分支) sas.verified → emit done
```

**方向 B：bot 发起（出站）** — `start_verification(user_id, device_id)` → `nio.start_key_verification(device)` 发出
start；后续 accept/key/emoji 同方向 A；比对后同样 `confirm_verification` 完成。key 的发送**完全由 nio 内部负责**
（`handle_key_verification` 里 `if not sas.we_started_it: share_key()`），集成不再手动 `share_key()`（W1N-169）。

**状态机要点**：
- 集成在**首次建立验证时**记录 monotonic 时间（`_mark_sas_started` 用 `setdefault`，出站 `async_start_verification`、入站 start、key/mac 分支都调用，但只在首次写入、之后不再刷新）；超时判断 = `sas.timed_out` **或**
  monotonic 差 ≥ 240s（`_sas_is_timed_out`），超时 → `_timeout_verification`：cancel + emit `verification_timeout`。
- `confirm_verification` 是**唯一**完成验证的路径：先查超时，再 `nio.confirm_short_auth_string(txn)`，`sas.verified`
  → emit `done`，否则 emit `sas`（等用户再次 confirm）。
- 入站门控：所有分支（start/key/mac/cancel）统一 `_bootstrap_allowed(sender)`——`sender == session.user_id`
  **或** `sender ∈ allowed_users`，否则 emit `verification_peer_denied`（W1N-143）。

### 3.3 路径二：单向指纹验证（降级/备选）

`get_fingerprint` 取 bot 指纹 → Element「Manually Verify by Text」；信任对端用 `verify_device_by_fingerprint`
（精确匹配后 `nio.olm.verify_device`，**本地单向信任，不是 SAS，不产生 SAS 事件**）。

---

## 4. 代码地图（`custom_components/matrix_e2ee/`）

### client.py（1525 行；无 models.py，模型在此文件）

**nio 补丁区（160–398）**：

| 函数 | 行 | 作用 |
|---|---|---|
| `_apply_sas_timeout_patch` | 164 | monkeypatch `Sas.timed_out`：verified/canceled→False；`now-creation ≥ _max_age(5min)`→canceled+`_timeout_error`+True。**忽略 nio 60s 事件超时 bug** |
| `_sas_commitment(pubkey, canonical)` | 197 | SHA-256(pubkey+canonical) 的 unpadded base64 |
| `_apply_sas_commitment_patch` | 208 | patch `from_key_verification_start`（accept commitment）+ `_check_commitment`（initiator 校验）→ unpadded base64 |
| `_apply_sas_emoji_patch` | 256 | `_generate_emoji` 直接用 `established_sas.bytes(info).emoji_indices` 映射，不做 bit-slicing |
| `_apply_sas_mac_patch` | 286 | 见下 |
| `_select_mac_func` | 304 | `chosen_mac_method=="hkdf-hmac-sha256"` → `calculate_mac_invalid_base64`，否则 `calculate_mac` |
| `get_mac`（patch） | 309 | `sas_accepted=False`/canceled 抛 `LocalProtocolError`；按规范拼 info、`ed25519:{own_device}` + `KEY_IDS` |
| `receive_mac_event`（patch） | 339 | verified→return；`state!=key_received`→canceled；KEY_IDS 校验→`_key_mismatch_error`；逐 key device_id 匹配 + MAC 校验 → verified_devices；空→canceled（**缺 return，复刻 W1N-179 bug**） |
| `_patch_nio_sas_timeout/_commitment/_emoji/_mac` | 188/246/277/390 | `try: from nio … except Exception: return`（测试无 nio 时 no-op） |

**验证服务（936–1525）**：

| 函数 | 行 | 作用 |
|---|---|---|
| `enable_verification_callbacks` | 936 | 注册 `handle_to_device_event`（KeyVerificationEvent）+ `_handle_verification_request`（ToDeviceEvent） |
| `_emit_verification(stage, **extra)` | 947 | fire `matrix_e2ee_verification` + warning log |
| `_verification_expires_at` | 953 | now UTC + `_verification_timeout` ISO 串 |
| `_lookup_device` / `_get_sas` | 959 / 972 | device_store 查设备 / `nio.key_verifications.get(txn)` |
| `_sas_party` / `_sas_emojis` | 978 / 990 | 提取对端 (user,device) / `sas.get_emoji()`（异常吞掉→None，`list[list[str]]`） |
| `_mark_sas_started` / `_sas_is_timed_out` | 1006 / 1009 | monotonic 记录 / 超时判断 |
| `async_start_verification` | 1018 | 拒软登出；查设备→`nio.start_key_verification`；emit `started` |
| `async_verify_device_by_fingerprint` | 1060 | `actual.strip()!=ed25519.strip()` 精确等值（W1N-159）→`fingerprint_mismatch`；`nio.olm.verify_device` |
| `async_confirm_verification` | 1090 | 超时检查→`nio.confirm_short_auth_string(txn)`；verified→emit `done`，否则 emit `sas`。**唯一验设备路径** |
| `async_cancel_verification` | 1139 | `nio.cancel_key_verification(txn, reject=False)` → emit `canceled` |
| `_timeout_verification` | 1169 | cancel + emit `verification_timeout` + `timeout` |
| `_bootstrap_allowed(sender)` | 1187 | `sender==session.user_id` 或 `sender in allowed_users`（W1N-143 门控） |
| `_repair_dropped_start` | 1196 | `_query_device_keys(sender)` 后重喂 `nio.olm.handle_key_verification(event)`（W1N-170） |
| `_handle_verification_request` | 1222 | 解析 request；校验 sender/txn/methods/timestamp → `_send_verification_ready`；**不建 SAS 状态**（W1N-173） |
| `_send_verification_ready` | 1283 | 回 `ready` {from_device: own, methods:[m.sas.v1], transaction_id} |
| `handle_to_device_event` | 1314 | 主分发：门控→cancel→timeout→start（emoji 检查/repair/accept/emit）→key（emit sas）→mac（verified→emit done） |
| `_verification_kind` | 1458 | 类名/type 串 → start/key/mac/cancel |
| `_request_timestamp_valid` | 1447 | 未来≤5min 且 age≤10min |
| `_transaction_id_from_verifications` | 1480 | 匹配 other user+device；唯一则兜底 |
| `_verification_error_code` | 1496 | timeout→`verification_timeout`；LocalProtocolError/does not exist→`invalid_transaction`；unverified→`unverified_device`；否则 `send_failed` |

### const.py（79 行）

`SERVICE_START/CONFIRM/CANCEL_VERIFICATION`、`SERVICE_VERIFY_DEVICE_BY_FINGERPRINT`；`ATTR_ED25519/TRANSACTION_ID/
USER_ID/DEVICE_ID`；`EVENT_VERIFICATION`/`EVENT_FINGERPRINT`；错误码见 §3.1；`VERIFICATION_TIMEOUT_SECONDS=240`；
`VERIFICATION_REQUEST_MAX_FUTURE_MS`/`MAX_AGE_MS`；`SAS_METHOD_V1="m.sas.v1"`；`VERIFICATION_REQUEST/READY/START/
ACCEPT/KEY/MAC/DONE/CANCEL` 类型串。

### __init__.py

`_register_services`(136) 服务→client 方法映射；`_fire_event`(123)；`_options`(126) 读 `allowed_users/allowed_rooms`；
`async_setup_entry`(268)/`async_unload_entry`(321)。

---

## 5. 注意的问题（Checklist / 教训）

**协议 / wire 层**
1. **MAC 生成与校验必须一起改**（`get_mac` + `receive_mac_event` 同步 monkeypatch），只改一边 = 互不兼容。
2. **`.v2` 不要自己加**：nio 不提供 `_mac_v2`，不要扩大协商面。
3. **accept/key/mac 的发送权要分清**：key 归 nio 内部（`we_started_it` 判断），mac 只由 `confirm_verification` 发；
   集成只做事件映射与人工 confirm 触发（W1N-169 双发教训）。
4. commitment 必须 unpadded base64（不是 hexdigest）；emoji 索引不得二次转换；legacy MAC 用 invalid base64。
5. SAS HKDF info / MAC info 都是**无分隔符逐字节拼接**，字段顺序（发起方在前）与方向不能错。

**nio 状态机坑**
6. `Sas.timed_out` 被 nio 60s bug 污染 → 必须 monkeypatch，用 240s 集成超时先行。
7. `get_mac()` 在 `sas_accepted=False` 抛异常 → 必须走 `confirm_short_auth_string`，别裸调。
8. 入站 start 需设备 key：`_get_sas==None` 时先 `_repair_dropped_start`（查 key 重喂），不要直接放弃。
9. `keys_query` 默认不含 bot 自己 → 登录/恢复后主动预热自身 keys。

**信任 / 安全**
10. **无自动信任**：同账号 ≠ 可信，包括 `@bot` 自身设备（W1N-153）。
11. 指纹比较**必须精确等值**，禁 `casefold()`（unpadded base64 大小写敏感，W1N-159）。
12. 验证/指纹/reauth 服务必须 admin-only（W1N-150）；入站所有分支统一门控 `_bootstrap_allowed`（W1N-143）。
13. request 的 timestamp 校验：未来≤5min、age≤10min（W1N-173）。
14. fail-closed：未验证设备不触发命令事件、不回退明文、`ignore_unverified_devices` 永不开启、日志不落 secret（W1N-136）。

**运维 / 测试**
15. 设备 ID 变了 = store 丢失 → 按 runbook 重新验证，不静默新建设备（W1N-134/138）。
16. 测试只用 FakeNio/FakeSas，**协议级问题单测不可见** → 缺真实 homeserver e2e（W1N-171 Backlog）。
17. 上游 nio `receive_mac_event` 缺 return bug 已复刻到我们的 patch 里（W1N-179 Backlog），改上游时同步改。

---

## 6. Issue 索引表（Linear → 知识）

状态：✅ Done ｜ ⏳ Backlog

| Issue | 主题 | 知识章节 | 状态 |
|---|---|---|---|
| W1N-134 | M2 E2EE 登录/原子 session/恢复同一设备 | §5-15 | ✅ |
| W1N-135 | M2 加密收发 + sync tokens（消费验证状态） | §3 | ✅ |
| W1N-136 | M2 fail-closed、allowlist、不记录 secret | §5-14 | ✅ |
| W1N-137 | M3 SAS 服务/事件 + verified-device 策略 | §3.1 | ✅ |
| W1N-138 | M4 软登出/store 丢失恢复/diagnostics | §5-15 | ✅ |
| W1N-141 | 集成设备安全验证指南（parent of A–F） | — | ✅ |
| W1N-142 | A SAS auto-completion（修 get_mac 卡死）→ 后被 W1N-153 撤销自动分支 | §2 / §5-7 | ✅ |
| W1N-143 | B 入站 SAS 发起者门控 | §3.2 / §5-12 | ✅ |
| W1N-144 | C 单向指纹验证（get_fingerprint / verify_device） | §3.3 | ✅ |
| W1N-145 | D VerificationPrompt 模型 + expires_at | §3.1 | ✅ |
| W1N-147 | F SECURITY.md + SAS/指纹指南（cross-signing 不做 SSSS） | §1.7 | ✅ |
| W1N-149 | 安全加固总纲（P0 自动确认 / P0 admin-only / P1 指纹门控） | §5 | ✅ |
| W1N-150 | B admin-only 强制 | §3.1 / §5-12 | ✅ |
| W1N-151 | C verify_device_by_fingerprint（ed25519 精确门控） | §3.3 | ✅ |
| W1N-153 | A 删除同账号 SAS 自动确认 | §5-10 | ✅ |
| W1N-154 | E 文档改为手动确认 + 新指纹服务名 | — | ✅ |
| W1N-156 | 加固跟进：allowlist 拆分 + P2 质量（**未做**） | §8 | ⏳ |
| W1N-157 | F 人话版验证指南（docs/DEVICE_VERIFICATION.md） | — | ✅ |
| W1N-158 | M5 Config Flow（reauth/设备验证 UI 路径动机） | — | ✅ |
| W1N-159 | casefold 指纹比较 bug → 精确匹配 | §5-11 | ✅ |
| W1N-162 | Config Flow reconfigure + reauth | — | ✅ |
| W1N-165 | 文档更新 + release 0.2.0（SAS 保持 service/event-based） | §8 | ✅ |
| W1N-166 | keys_query 不查同账号 → 预热 own device keys | §2-7 / §5-9 | ✅ |
| W1N-169 | key/mac 双发（协议正确性） | §5-3 | ✅ |
| W1N-170 | 启动后新增设备首次 start 被丢 → 自动查 key 重喂 | §2-8 / §5-8 | ✅ |
| W1N-171 | 缺真实 homeserver 端到端 SAS 测试 | §5-16 | ⏳ |
| W1N-172 | nio 60s 必死超时（_last_event_time 不刷新） | §2-2 / §5-6 | ✅ |
| W1N-173 | request → ready 桥接（nio 缺框架） | §2-1 / §5-13 | ✅ |
| W1N-174/176 | 部署 matrix_e2ee(e) 到 hass.windy.lan | — | ✅ |
| W1N-175 | emoji 双转换（vodozemac indices） | §2-4 | ✅ |
| W1N-177 | legacy MAC invalid-base64 | §2-5/6 / §5-1/2 | ✅ |
| W1N-178 | 部署 v0.2.10（含 legacy MAC 修复） | — | ✅ |
| W1N-179 | receive_mac_event 缺 return 覆盖 canceled（**未做**） | §2-9 / §5-17 | ⏳ |

---

## 7. 当前版本状态与待办

- 已发布至 **v0.2.10**（wire 修复线：commitment v0.2.8 → emoji v0.2.9 → legacy MAC v0.2.10，均已部署
  hass.windy.lan）。
- SAS 保持 **service/event-based**（v0.2.x）；HA 内 live SAS emoji UI 延后到 v0.3（W1N-165 / SECURITY.md）。

**Backlog（避免重复工作，动手前先查这 3 条）**：
1. **W1N-156** — 拆分 `allowed_users` 为「命令权限」与「SAS 验证权限」两组；SAS 不支持算法显式 cancel；
   setup/stop/reauth async lock；事务绑定（存预期 user/device/创建时间）；CI manifest 依赖校验；ruff。
2. **W1N-179** — nio `receive_mac_event` 缺 return（上游 + 我们的 monkeypatch 同步修，加单测）。
3. **W1N-171** — 真实 homeserver 端到端 SAS 测试（docker Synapse 或人工 runbook）。
