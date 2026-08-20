# 开发说明

> 适用对象：开发、测试和发布 `matrix_e2ee` 的贡献者。
>
> 语言：[English](DEVELOPMENT.md) | [中文](DEVELOPMENT.zh.md)

本仓库提供 Home Assistant 自定义集成 `custom_components/matrix_e2ee`，不会覆盖 Home Assistant 内置的 `matrix` 域。

## 环境与依赖

| 组件 | 当前约束 |
|---|---|
| Python | 3.14 |
| Home Assistant 测试框架 | `2026.8.2` |
| matrix-nio | `matrix-nio[e2e]==0.26.0` |

运行时依赖以 `custom_components/matrix_e2ee/manifest.json` 为准，开发和测试依赖以 `requirements-dev.txt` 为准。不要在文档中单独维护完整依赖副本。

项目使用 `uv` 和仓库根目录的 `.venv`。所有 Python 命令都通过 `uv run` 执行：

```bash
uv run python --version
uv run python -m pytest
```

第一条命令应显示 Python 3.14.x。升级 matrix-nio 前，必须完成 [NIO_COMPAT.zh.md](NIO_COMPAT.zh.md) 中的检查。

## 测试结构

测试不会连接真实 Matrix homeserver，而是通过模块级 `_NIO_CLIENT_FACTORY` 注入模拟的 `nio.AsyncClient`，并使用临时 Home Assistant 配置和存储目录。

| 类别 | 文件 |
|---|---|
| M1–M4 契约 | `tests/test_m1_contract.py` 至 `tests/test_m4_contract.py` |
| Config、Options、Reauth、Import | `tests/test_config_flow.py`、`tests/test_options_flow.py`、`tests/test_reauth.py`、`tests/test_import.py` |
| Config Entry 生命周期 | `tests/test_entry_lifecycle.py`、`tests/test_manifest.py` |
| 诊断、URL、品牌、连接传感器 | `tests/test_diagnostics.py`、`tests/test_url.py`、`tests/test_brand.py`、`tests/test_binary_sensor.py` |
| matrix-nio 兼容层 | `tests/test_nio_compat.py` |
| HA 事件实体与活动事件 | `tests/test_event.py` |
| 共用测试夹具 / 替身 | `tests/conftest.py`、`tests/fakes.py` |

运行完整测试：

```bash
uv run python -m pytest
```

`FakeNio` 无法发现真实客户端之间的协议编码差异。修改 SAS、承诺值、emoji 或 MAC 时，还需要在真实 homeserver 上使用 Element 手动完成端到端验证；[SAS 架构说明](SAS_ARCHITECTURE.zh.md)记录了这一测试边界。

## 客户端结构

`MatrixE2EEClient` 仍是供 setup、配置流程与服务使用的兼容门面。其生命周期由 `ClientState` 表示；纯粹的允许列表与命令解析规则位于 `helpers.py`；实体更新通过小型监听接口（`add_state_listener` / `add_activity_listener`）连接。这样 Matrix 协议实现不依赖 Home Assistant 实体，同时平台仍可由事件主动更新。

## CI 质量检查

`.github/workflows/tests.yml` 在 push 和 pull request 时运行：

- `lint`：`ruff check` 和 `ruff format --check`。
- `pytest`：运行测试并要求覆盖率不低于 81%。
- `audit`：从 `manifest.json` 提取运行时依赖，再运行 `pip-audit`。

推送前执行：

```bash
uv run python -m ruff check custom_components/ tests/
uv run python -m ruff format --check custom_components/ tests/
uv run python -m pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81
uv run python -c "import json; print('\n'.join(json.load(open('custom_components/matrix_e2ee/manifest.json'))['requirements']))" > runtime-requirements.txt
uv run python -m pip_audit --requirement runtime-requirements.txt
rm runtime-requirements.txt
```

覆盖率门槛为当前项目质量基线。降低门槛必须说明原因，不能只为通过 CI。

## 本地安装

将 `custom_components/matrix_e2ee` 复制到 Home Assistant 配置目录下的 `custom_components/matrix_e2ee`，然后通过 **设置 → 设备与服务 → 添加集成 → Matrix E2EE** 完成配置。

旧版 `matrix_e2ee:` YAML 配置会在启动时导入为 Config Entry。会话和加密存储位于 `<config>/.storage/`，必须与 Home Assistant 配置目录一起持久化。

不要向 Home Assistant OS 的系统 Python 安装开发依赖；本地开发和测试只使用项目虚拟环境。

## 敏感信息

测试、日志和 Git 历史中不得包含：

- Matrix access token 或账号密码
- pickle key
- 消息正文
- 加密存储内容
- 真实 SAS 对话记录或测试凭据

设备公钥可以用于诊断，但仍应避免记录与问题无关的账号和设备信息。

## 相关文档

- [SAS 架构说明](SAS_ARCHITECTURE.zh.md)
- [matrix-nio 兼容性说明](NIO_COMPAT.zh.md)
- [安全模型](../SECURITY.md)
