# 开发说明

> 语言：[English](DEVELOPMENT.md) | [中文](DEVELOPMENT.zh.md)

本仓库是 Home Assistant 自定义集成（`custom_components/matrix_e2ee`）。它**不会**覆盖内置的 `matrix` 域。

## 支持的版本

| 组件 | 版本 |
| --- | --- |
| Python | 3.14 |
| Home Assistant | 2026.8.x（测试框架 `2026.8.2`） |
| matrix-nio | `0.26.0`（钉死；`matrix-nio[e2e]==0.26.0`） |
| vodozemac | `>=0.9.0.post2` |
| peewee | `~=3.14` |
| cachetools | `>=5.3` |
| atomicwrites | `~=1.4` |

运行时依赖钉在 `custom_components/matrix_e2ee/manifest.json`；开发/测试钉在 `requirements-dev.txt`。CI 使用 Python 3.14（GitHub Actions `ubuntu-latest`）。升级 `matrix-nio` 前必须按 [NIO_COMPAT.zh.md](NIO_COMPAT.zh.md) 的升级清单检查。

## 测试

- 使用 **pytest**，mock `nio.AsyncClient`，并用临时的 Home Assistant 配置/存储目录。
- Config Flow / Options Flow / reauth / entry 生命周期测试走 Home Assistant 测试框架（`pytest-homeassistant-custom-component` + `homeassistant`）。
- **不要**在测试或 git 里使用真实 Matrix homeserver、access token、pickle key、crypto store 或 SAS 对话记录。

在仓库根目录：

```bash
uv run python -m pytest
```

`uv` 管理项目的 `.venv`（依赖在 `requirements-dev.txt`），并自动选用 `.venv` 里的解释器。

这些测试 mock `nio.AsyncClient`；flow/生命周期测试走 Home Assistant 测试框架，通过模块级 `_NIO_CLIENT_FACTORY` 注入 mock 的 nio client。不要自己发明绕过方案，也不要往 Home Assistant OS 里装包。

M1 契约：`tests/test_m1_contract.py`。M2 契约：`tests/test_m2_contract.py`。M3 契约：`tests/test_m3_contract.py`。M4 契约：`tests/test_m4_contract.py`。Config Flow：`tests/test_config_flow.py`、`tests/test_reauth.py`、`tests/test_options_flow.py`、`tests/test_import.py`、`tests/test_entry_lifecycle.py`、`tests/test_manifest.py`。SAS 补丁：`tests/test_nio_compat.py`。共用 fake client：`tests/fakes.py`。

## CI 质量门槛

`.github/workflows/tests.yml` 在每次 push 和 pull request 上跑三个 job：

- **lint** — `ruff check` 和 `ruff format --check`（配置在 `ruff.toml`）。
- **pytest** — `pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81`（覆盖率下限跟着实测基线走）。
- **audit** — 对从 `manifest.json` 抽出的运行时依赖跑 `pip-audit`。

推送前在本地跑同一套检查：

```bash
uv run python -m ruff check custom_components/ tests/
uv run python -m ruff format --check custom_components/ tests/
uv run python -m pytest --cov=custom_components.matrix_e2ee --cov-fail-under=81
uv run python -m pip_audit --requirement <(uv run python -c "import json; print('\n'.join(json.load(open('custom_components/matrix_e2ee/manifest.json'))['requirements']))")
```

## nio 兼容性

集成为了与 Element 的 SAS 互操作，会对 `matrix-nio` 0.26.0 的 `Sas` 打四个运行时补丁。补丁矩阵和升级 `matrix-nio` 前的检查清单见 [NIO_COMPAT.zh.md](NIO_COMPAT.zh.md)。

## 本地布局

把 `custom_components/matrix_e2ee` 复制到 Home Assistant 实例的 `<config>/custom_components/matrix_e2ee`。通过 Config Flow UI 配置（**设置 → 设备与服务 → 添加集成 → Matrix E2EE**）。残留的 `matrix_e2ee:` YAML 块会在启动时导入成 config entry。

session 和 crypto store 运行时写在 `<config>/.storage/` 下。这些路径已 gitignore，必须和 Home Assistant 待在同一块持久卷上。

## 密钥

永远不要记录或提交：

- access token
- pickle key
- 消息正文
- crypto store 内容
- homeserver 密码或测试凭据
