# PyPI Release Checklist

This repository prepares PyPI-ready artifacts (`sdist` and `wheel`) without auto-publishing. All tagging and uploads are run manually by a maintainer.

## Pre-release Checklist

Before tagging or uploading, confirm every item passes:

- [ ] `pyproject.toml` (`[project].version`) and `src/agent_taskboard/__init__.py` (`__version__`) match the target version (`atb --version` agrees).
- [ ] Packaging metadata matches PEP 639 (`license = "MIT"`, `license-files = ["LICENSE"]`, no `License ::` classifier, `build-system.requires = ["setuptools>=77.0"]`, absolute links in `README.md`).
- [ ] `CHANGELOG.md` has the target version heading with the actual release date (`## [X.Y.Z] - YYYY-MM-DD`), an empty `## [Unreleased]` section above it, bilingual EN/ZH entries, and updated comparison links at the bottom.
- [ ] Full test suite passes under both `TZ=UTC` and `TZ=Asia/Shanghai`, `bash examples/walkthrough.sh` exits `0`, and CI is green on Ubuntu, macOS, and Windows.
- [ ] Clean build succeeds, archive contents are verified, `python3 -m twine check --strict` passes on both `.tar.gz` and `.whl`, and a fresh venv smoke test succeeds.
- [ ] No build artifacts (`dist/`, `build/`, `*.egg-info`) are left tracked or uncommitted in the repository.

## 1. Bump version

Keep the version identical in both places:

- `pyproject.toml` (`[project].version`)
- `src/agent_taskboard/__init__.py` (`__version__`)

Verify locally (including `tests/test_release.py` and `tests/test_packaging.py`):

```bash
PYTHONPATH=src python3 -m agent_taskboard --version
PYTHONPATH=src python3 -m unittest tests/test_release.py tests/test_packaging.py
```

## 2. Update `CHANGELOG.md`

Move items under `## [Unreleased]` into a dated `## [X.Y.Z] - YYYY-MM-DD` section (bilingual EN/ZH entries matching the existing format), leave an empty `## [Unreleased]` section at the top, and update the comparison links at the bottom of the file.

## 3. Run the full test suite

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
bash examples/walkthrough.sh
```

## 4. Build `sdist` and `wheel` cleanly

Build into a clean output directory (never commit `dist/` or `build/`). The build backend requires `setuptools>=77.0` (for PEP 639 license metadata and bundled `bdist_wheel`):

```bash
rm -rf /tmp/atb-dist
python3 -m build --outdir /tmp/atb-dist
```

If a local pip mirror cannot resolve `setuptools>=77.0` during isolated build, set `PIP_INDEX_URL`:

```bash
PIP_INDEX_URL=https://pypi.org/simple python3 -m build --outdir /tmp/atb-dist
```

If offline without build-env downloads (requires `setuptools>=77.0` in the active environment):

```bash
pip wheel . --no-deps --no-build-isolation -w /tmp/atb-dist
```

Inspect archive contents to confirm only source files, `LICENSE`, and `README.md` are shipped (no `.taskboard/`, `tests/`, `examples/`, or temporary files):

```bash
tar -tzf /tmp/atb-dist/*.tar.gz
python3 -m zipfile -l /tmp/atb-dist/*.whl
```

## 5. Validate package metadata (`twine check --strict`)

```bash
python3 -m twine check --strict /tmp/atb-dist/*
```

Both the `.whl` and `.tar.gz` must report `PASSED` with zero warnings (PEP 639 `License-Expression` / `License-File` metadata and README rendering are validated strictly).

## 6. Smoke-test in a fresh temporary venv

```bash
VENV_DIR=$(mktemp -d)
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --no-deps /tmp/atb-dist/*.whl
"$VENV_DIR/bin/atb" --version
"$VENV_DIR/bin/atb" --help
rm -rf "$VENV_DIR"
```

## 7. Tag first, then upload (maintainer only)

**Always tag and push the release commit BEFORE uploading to PyPI**, so the uploaded distributions correspond to an immutable Git tag on GitHub and changelog comparison links resolve immediately. **Never automated by agents — executed manually by a maintainer:**

```bash
# 1. Tag the verified release commit on main and push
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main --tags

# 2. Optional: verify on TestPyPI first
python3 -m twine upload --repository testpypi /tmp/atb-dist/*

# 3. Publish to PyPI
python3 -m twine upload /tmp/atb-dist/*
```

After uploading, create the GitHub Release for `vX.Y.Z` using the release notes, and remove `/tmp/atb-dist`.
