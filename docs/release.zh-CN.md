# PyPI 发布清单

本仓库只做可随时发布的本地构建与校验准备，**绝不自动打 tag、推送或上传**。打 tag 与最终上传均由维护者手动执行。

## 发布前检查清单

在打 tag 或上传前，逐项确认以下全部通过：

- [ ] `pyproject.toml`（`[project].version`）与 `src/agent_taskboard/__init__.py`（`__version__`）版本号完全一致（`atb --version` 输出一致）。
- [ ] 打包元数据符合 PEP 639 规范（`license = "MIT"`、`license-files = ["LICENSE"]`、无 `License ::` 分类、`build-system.requires = ["setuptools>=77.0"]`、`README.md` 内全部为绝对链接）。
- [ ] `CHANGELOG.md` 已将版本号与实际发布日期填入 `## [X.Y.Z] - YYYY-MM-DD`，顶部保留空的 `## [Unreleased]`，条目保持中英双语且无重复，底部比较链接已更新。
- [ ] `TZ=UTC` 与 `TZ=Asia/Shanghai` 下全量测试均通过，`bash examples/walkthrough.sh` 退出码为 `0`，CI 在 Ubuntu、macOS、Windows 上全部变绿。
- [ ] 干净构建成功、压缩包清单无多余文件、`python3 -m twine check --strict` 对 `.tar.gz` 和 `.whl` 均通过，且临时虚拟环境冒烟安装正常。
- [ ] 仓库中没有未提交或被跟踪的构建产物（`dist/`、`build/`、`*.egg-info`）。

## 1. 更新版本号

确保以下两处版本号完全一致：

- `pyproject.toml`（`[project].version`）
- `src/agent_taskboard/__init__.py`（`__version__`）

本地核对（含 `tests/test_release.py` 与 `tests/test_packaging.py`）：

```bash
PYTHONPATH=src python3 -m agent_taskboard --version
PYTHONPATH=src python3 -m unittest tests/test_release.py tests/test_packaging.py
```

## 2. 更新 `CHANGELOG.md`

将 `## [Unreleased]` 下的条目整理到新的带日期版本小节 `## [X.Y.Z] - YYYY-MM-DD`（保持中英双语格式），顶部保留空的 `## [Unreleased]`，并同步更新底部的版本比较链接。

## 3. 运行全量测试与演示脚本

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
bash examples/walkthrough.sh
```

## 4. 构建 `sdist` 和 `wheel`

构建到临时目录（不要把 `dist/` 或 `build/` 提交进仓库）。构建后端需要 `setuptools>=77.0`（支持 PEP 639 许可证元数据并内置 `bdist_wheel`）：

```bash
rm -rf /tmp/atb-dist
python3 -m build --outdir /tmp/atb-dist
```

若本地 pip 镜像在隔离构建时无法拉取 `setuptools>=77.0`，可指定官方源：

```bash
PIP_INDEX_URL=https://pypi.org/simple python3 -m build --outdir /tmp/atb-dist
```

如完全离线且当前环境已有 `setuptools>=77.0`，可仅构建 wheel：

```bash
pip wheel . --no-deps --no-build-isolation -w /tmp/atb-dist
```

检查压缩包文件清单，确认只包含源码、`LICENSE` 和 `README.md`，不含 `.taskboard/`、`tests/`、`examples/` 或缓存垃圾：

```bash
tar -tzf /tmp/atb-dist/*.tar.gz
python3 -m zipfile -l /tmp/atb-dist/*.whl
```

## 5. 严格校验分发元数据（`twine check --strict`）

```bash
python3 -m twine check --strict /tmp/atb-dist/*
```

`.whl` 和 `.tar.gz` 都必须输出 `PASSED` 且无任何警告（严格校验 PEP 639 `License-Expression` / `License-File` 元数据与 README 渲染）。

## 6. 在临时虚拟环境中冒烟安装验证

```bash
VENV_DIR=$(mktemp -d)
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --no-deps /tmp/atb-dist/*.whl
"$VENV_DIR/bin/atb" --version
"$VENV_DIR/bin/atb" --help
rm -rf "$VENV_DIR"
```

## 7. 先打 tag，再上传（仅由维护者执行）

**务必先在 GitHub 上为已验证的发布提交打 tag 并推送，然后再上传到 PyPI**，以确保 PyPI 产物与不可变的 Git tag 一一对应，且 CHANGELOG 底部的比较链接立即可用。**以下最后一步必须由维护者手动执行，Agent 绝不执行：**

```bash
# 1. 在 main 的发布提交上打带附注 tag 并推送
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main --tags

# 2. 可选：先上传到 TestPyPI 验证
python3 -m twine upload --repository testpypi /tmp/atb-dist/*

# 3. 正式上传到 PyPI
python3 -m twine upload /tmp/atb-dist/*
```

上传完成后，使用发布说明为 `vX.Y.Z` 创建 GitHub Release，并清理 `/tmp/atb-dist`。
