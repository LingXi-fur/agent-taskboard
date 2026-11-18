# PyPI Release Checklist

This repository prepares PyPI-ready artifacts (`sdist` and `wheel`) without auto-publishing. All uploads are run manually by a maintainer.

## 1. Bump version

Keep the version identical in both places:

- `pyproject.toml` (`[project].version`)
- `src/agent_taskboard/__init__.py` (`__version__`)

Verify locally:

```bash
PYTHONPATH=src python3 -m agent_taskboard --version
```

## 2. Update `CHANGELOG.md`

Move items under `## [Unreleased]` into a dated `## [X.Y.Z] - YYYY-MM-DD` section (bilingual EN/ZH entries matching the existing format).

## 3. Run the full test suite

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
bash examples/walkthrough.sh
```

## 4. Build `sdist` and `wheel` cleanly

Build into a clean output directory (never commit `dist/` or `build/`):

```bash
rm -rf /tmp/atb-dist
python3 -m build --outdir /tmp/atb-dist
```

If offline without build-env downloads (requires `setuptools >= 70.1` in the active environment):

```bash
pip wheel . --no-deps --no-build-isolation -w /tmp/atb-dist
```

Inspect archive contents to confirm only source files, `LICENSE`, and `README`s are shipped (no `.taskboard/`, `tests/`, or temporary files):

```bash
tar -tzf /tmp/atb-dist/*.tar.gz
python3 -m zipfile -l /tmp/atb-dist/*.whl
```

## 5. Validate package metadata (`twine check`)

```bash
twine check /tmp/atb-dist/*
```

Both the `.whl` and `.tar.gz` must report `PASSED`.

## 6. Smoke-test in a fresh temporary venv

```bash
VENV_DIR=$(mktemp -d)
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --no-deps /tmp/atb-dist/*.whl
"$VENV_DIR/bin/atb" --version
"$VENV_DIR/bin/atb" --help
rm -rf "$VENV_DIR"
```

## 7. Manual upload (maintainer only)

**Executed manually by a maintainer — never automated by agents:**

```bash
# Optional: verify on TestPyPI first
twine upload --repository testpypi /tmp/atb-dist/*

# Publish to PyPI
twine upload /tmp/atb-dist/*
```

After publishing, clean `/tmp/atb-dist` and tag the release commit if desired.
