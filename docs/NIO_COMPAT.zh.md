# matrix-nio 0.26.0 兼容性说明

> 适用对象：维护 SAS 兼容层或准备升级 matrix-nio 的开发者。
>
> 语言：[English](NIO_COMPAT.md) | [中文](NIO_COMPAT.zh.md)

本集成将 `matrix-nio[e2e]` 固定为 `0.26.0`，并在创建客户端前对 `nio.crypto.sas.Sas` 应用四项运行时兼容性修正。这些修正位于 `custom_components/matrix_e2ee/nio_compat.py`，由 `_make_nio()` 调用 `apply_nio_compat_patches()` 统一启用。

这些修正只针对 0.26.0 验证过。如果实际安装版本不同，集成会记录警告。每项修正都可以重复应用；未安装 nio 时会直接跳过，以便使用 `FakeNio` 运行单元测试。

## 修正概览

| 修正 | 0.26.0 的问题 | 用户可见结果 |
|---|---|---|
| SAS 超时 | `_last_event_time` 从不刷新，事务创建 60 秒后必定超时 | emoji 比对中途取消 |
| 承诺值 | 使用十六进制摘要，而规范要求无填充 Base64 | Element 在密钥交换后校验承诺值时以 `m.key_mismatch` 取消事务 |
| emoji 索引 | 对 vodozemac 已生成的索引再次进行旧版位切分 | 两端 emoji 不一致 |
| 旧版 MAC | 协商 v1，却使用 v2 的 Base64 编码格式 | Element 以 `m.key_mismatch` 拒绝 MAC |

## 1. SAS 超时

入口：`_apply_sas_timeout_patch()`。

matrix-nio 0.26.0 只在 `Sas` 初始化时设置 `_last_event_time`，之后不再更新。因此 `timed_out` 会在创建 60 秒后返回 `True`，即使期间仍有消息交换。

修正后的 `timed_out` 只使用 `creation_time + _max_age`（5 分钟）。本集成会在确认和处理入站事件时另行检查更短的 240 秒期限。

升级时应确认新版 nio 会刷新活动时间，或已经采用其他正确的超时模型。

## 2. SAS 承诺值

入口：`_apply_sas_commitment_patch()`。

Matrix 要求 `m.key.verification.accept` 中的承诺值（commitment）使用：

```text
Base64_without_padding(SHA-256(public_key || canonical_start_content))
```

matrix-nio 0.26.0 在生成和校验两端都改用了 `hexdigest()`。nio 与 nio 之间仍能互相验证，但 Element 使用规范规定的 Base64，因此会拒绝该事务。

修正同时替换 `from_key_verification_start()` 和 `_check_commitment()`，恢复无填充 Base64。升级时必须同时检查生成与校验两个方向。

## 3. SAS emoji

入口：`_apply_sas_emoji_patch()`。

`EstablishedSas.bytes(info).emoji_indices` 已经返回 7 个最终索引，范围为 0–63。matrix-nio 0.26.0 仍按旧版 libolm 的逻辑再次进行位切分，导致显示结果与 Element 不同。

修正后的 `_generate_emoji()` 直接将这些索引映射到 emoji 表。升级时应确认新版 nio 直接使用 `emoji_indices`。

## 4. 旧版 MAC

入口：`_apply_sas_mac_patch()`。

matrix-nio 0.26.0 只协商 `hkdf-hmac-sha256`（v1），但默认调用 `calculate_mac()`，产生 v2 使用的标准 Base64。v1 的线上编码格式（wire format）需要兼容 libolm 的非标准 Base64。

修正后的 `get_mac()` 和 `receive_mac_event()` 都根据协商方法选择 `calculate_mac_invalid_base64()`。生成与校验必须保持一致，不能只修改其中一个方向。

该修正还处理 `receive_mac_event()` 的一个状态错误：没有任何设备通过 MAC 校验时，函数在设置 `canceled` 后必须立即返回，否则状态会被覆盖为 `mac_received`。

## 升级检查

升级 `matrix-nio` 前：

1. 在隔离分支中更新 `manifest.json` 和开发依赖。
2. 检查新版 nio 是否正确维护 SAS 活动时间。
3. 确认承诺值使用无填充 Base64，而不是 `hexdigest()`。
4. 确认 `_generate_emoji()` 直接使用 vodozemac 的最终索引。
5. 检查 v1 与 `.v2` MAC 的协商和编码实现，并确认 `receive_mac_event()` 不会覆盖取消状态。
6. 仅删除已由上游正确实现的对应修正，不要一次性移除整个兼容层。
7. 运行 `tests/test_nio_compat.py` 和完整测试套件。
8. 使用真实 Matrix homeserver 与 Element 完成一次双向 SAS 验证。
9. 在**三处同步**更新已确认可用的版本：本文档、`nio_compat.py` 中的 `NIO_COMPAT_VERSION`、`manifest.json` 中的 `matrix-nio[e2e]==…` 固定版本。

## 相关文档

- [SAS 架构说明](SAS_ARCHITECTURE.zh.md)
- [开发说明](DEVELOPMENT.zh.md)
