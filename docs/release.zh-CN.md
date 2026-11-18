# PyPI 发布清单

本仓库只做可随时发布的本地构建与校验准备，**绝不自动发布或上传**。最终上传由维护者手动执行。

## 1. 更新版本号

确保以下两处版本号完全一致：

- `pyproject.toml`（`[project].version`）
- `src/agent_taskboard/__init__.py`（`__version__`）

本地核对：

```bash
PYTHONPATH=src python3 -m agent_taskboard --version
```

## 2. 更新 `CHANGELOG.md`

将 `## [Unreleased]` 下的条目整理到新的带日期版本小节 `## [X.Y.Z] - YYYY-MM-DD`（保持中英双语格式）。

## 3. 运行全量测试与演示脚本

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
bash examples/walkthrough.sh
```

## 4. 构建 `sdist` 和 `wheel`

构建到临时目录（不要把 `dist/` 或 `build/` 提交进仓库）：

```bash
rm -rf /tmp/atb-dist
python3 -m build --outdir /tmp/atb-dist
```

如完全离线且当前环境已有 `setuptools >= 70.1`，可仅构建 wheel：

```bash
pip wheel . --no-deps --no-build-isolation -w /tmp/atb-dist
```

检查压缩包文件清单，确认只包含源码、`LICENSE` 和 `README`，不含 `.taskboard/`、`tests/` 或缓存垃圾：

```bash
tar -tzf /tmp/atb-dist/*.tar.gz
python3 -m zipfile -l /tmp/atb-dist/*.whl
```

## 5. 检查分发元数据（`twine check`）

```bash
twine check /tmp/atb-dist/*
```

`.whl` 和 `.tar.gz` 都必须输出 `PASSED`。

## 6. 在临时虚拟环境中冒烟安装验证

```bash
VENV_DIR=$(mktemp -d)
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --no-deps /tmp/atb-dist/*.whl
"$VENV_DIR/bin/atb" --version
"$VENV_DIR/bin/atb" --help
rm -rf "$VENV_DIR"
```

## 7. 手动上传（仅由维护者执行）

**以下最后一步必须由维护者手动执行，Agent 绝不执行上传：**

```bash
# 可选：先上传到 TestPyPI 验证
twine upload --repository testpypi /tmp/atb-dist/*

# 正式上传到 PyPI
twine upload /tmp/atb-dist/*
```

发布完成后清理 `/tmp/atb-dist`，并按需为发布提交打 Git tag。
