# matrix-nio 0.26.0 兼容补丁

> 语言：[English](NIO_COMPAT.md) | [中文](NIO_COMPAT.zh.md)

本集成钉死 `matrix-nio[e2e]==0.26.0`（见 `manifest.json`），并在每次创建 client 之前
对 `nio.crypto.sas.Sas` 打四个运行时补丁。补丁在
`custom_components/matrix_e2ee/nio_compat.py`，由 `apply_nio_compat_patches()` 应用，
`client.py` 从 `_make_nio()` 调用。每个补丁修的都是 nio 0.26.0 里会破坏与
Element / matrix-rust-sdk SAS 互操作的 bug。

已安装的 `matrix-nio` 版本偏离 `0.26.0` 钉时，`apply_nio_compat_patches()` 还会打警告，
因为这些补丁只确认对该版本正确。

每个补丁都是：

- **幂等** — 用类上的 `_matrix_e2ee_*_patched` 标志守卫。
- **没有 nio 时 no-op** — 每个 `_patch_nio_sas_*()` 包装器把
  `from nio … import …` 包在 `try/except` 里，这样使用 `FakeNio` 的单元测试
  在没装 nio 时也能跑。

## 补丁矩阵

| 补丁 | nio 0.26.0 bug | 去掉后的症状 | 出处 |
|---|---|---|---|
| SAS timeout | `Sas._last_event_time` 只在 `__init__` 赋一次、从不刷新，所以创建 60 秒后无论有没有活动 `timed_out` 都是 `True` | SAS 在 emoji 比对中途死掉 | W1N-172, PR #23 |
| SAS commitment | 用 `hashlib.sha256(...).hexdigest()` 而不是规范要求的 unpadded base64 | Element 以 `m.key_mismatch` 拒绝 `accept` | PR #28, v0.2.8 |
| SAS emoji | 用旧 libolm bit-slicing 再切一遍 vodozemac 已经算好的最终索引 | emoji 对不上 → `m.mismatched_sas` | W1N-175, PR #29 |
| Legacy MAC | 协商 `hkdf-hmac-sha256`（v1），却用 `.v2` 的合法 base64 计算/校验 | Element 以 `m.key_mismatch` 拒绝 `mac` | W1N-177, PR #30 |

## 补丁细节

### 1. SAS timeout — `_apply_sas_timeout_patch`（`nio_compat.py`）

nio 0.26.0 只在 `__init__` 给 `Sas._last_event_time` 赋一次，之后从不更新，
所以无论最近有没有活动，`timed_out` 都会在创建后刚好 60 秒变成 `True`。
人比对 emoji 通常需要超过 60 秒。

补丁把 `timed_out` 属性换成按 `creation_time + _max_age`（5 分钟）计算。
集成自己的 240 秒超时（`VERIFICATION_TIMEOUT_SECONDS`）仍然先触发。

**如果去掉或升级 nio**：SAS 会在比对中途超时。升级时确认 nio 现在会不会刷新
`_last_event_time`；会的话就可以丢掉这个补丁。

### 2. SAS commitment — `_apply_sas_commitment_patch`（`nio_compat.py`）

Matrix 规范要求 `m.key.verification.accept` 里的 SAS commitment 是
`SHA-256(pubkey || canonical_json)` 的 unpadded base64。nio 0.26.0 在
`from_key_verification_start`（接受方）和 `_check_commitment`（发起方）两边
都从 `olm.sha256`（unpadded base64）改成了 `hashlib.sha256(...).hexdigest()`。
两边一起改，所以 nio↔nio 仍然一致，但 Element（base64）会以 `m.key_mismatch` 取消。

补丁在两个方向都恢复 unpadded base64（`_sas_commitment`）。

**如果去掉或升级 nio**：Element 会拒绝 SAS `accept`。升级时检查 nio 是否又改回
发 base64 commitment。

### 3. SAS emoji — `_apply_sas_emoji_patch`（`nio_compat.py`）

vodozemac 的 `EstablishedSas.bytes(info).emoji_indices` 已经返回 7 个最终 emoji
索引（`[u8; 7]`，取值 0–63）。nio 0.26.0 仍用旧 libolm bit-slicing 再切一遍，
把这些索引当原始字节处理，所以渲染出的 emoji 和 Element 对不上（`m.mismatched_sas`）。

补丁直接返回这些索引。

**如果去掉或升级 nio**：emoji 对不上。升级时检查 nio 是否直接消费 `emoji_indices`。

### 4. Legacy MAC — `_apply_sas_mac_patch`（`nio_compat.py`）

nio 0.26.0 只协商 `hkdf-hmac-sha256`（v1，没有 `.v2`），却用标准 `calculate_mac`
（`.v2` 的 wire 格式）计算和校验 MAC。v1 的 wire 格式是 libolm 的 invalid-base64
输出，所以 Element 会以 `m.key_mismatch` 取消。

补丁在 `get_mac` 和 `receive_mac_event` 两边都把 legacy 方法转到
`calculate_mac_invalid_base64`，让生成和校验一致。

这个补丁还修了 nio 的一个潜伏 bug：`receive_mac_event` 的「无已验证设备」分支
把 `state = canceled` 之后无条件覆盖成 `mac_received`（缺 `return`）。打过补丁的
`receive_mac_event` 在该分支后返回（W1N-179, PR #31）。上游 nio 0.26.0 仍有这个 bug。

**如果去掉或升级 nio**：legacy-MAC 对不上，缺 `return` 的 bug 也会回来。升级时检查
nio 是否提供 `.v2` MAC，以及 `receive_mac_event` 缺 `return` 是否已在上游修好。

## 升级清单

把 `matrix-nio` 从 `0.26.0` 往上调时，去掉任何补丁之前先确认：

1. `Sas._last_event_time` 会刷新（或超时模型已经改过）。
2. commitment 是 unpadded base64（不是 hexdigest）。
3. `_generate_emoji` 直接消费 vodozemac 的 `emoji_indices`。
4. legacy `hkdf-hmac-sha256` 使用 `calculate_mac_invalid_base64`，并且
   `receive_mac_event` 不再缺 `return`。

哪一条还坏着，就保留对应补丁，并更新本文件里「已知可工作版本」的说明。
