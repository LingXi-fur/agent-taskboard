#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ATB_SRC="${ATB_SRC:-$REPO_ROOT/src}"

TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

DEMO_REPO="$TMP_ROOT/demo-repo"
mkdir -p "$DEMO_REPO"
cd "$DEMO_REPO"

git init -b main -q
git config user.name "Demo User"
git config user.email "demo@example.com"

mkdir -p src tests
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
EOF
cat > src/app.py <<'EOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"
EOF

cat > src/core.py <<'EOF'
VERSION = "0.1.0"
EOF

cat > tests/test_app.py <<'EOF'
import unittest
from src.app import greet

class AppTests(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("atb"), "Hello, atb!")

if __name__ == "__main__":
    unittest.main()
EOF

git add .
git commit -m "chore: initial demo repo" -q

# Pick an interpreter that exists: python3 on POSIX, python on some Windows
# Git Bash setups that only expose ``python``.
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

atb() {
  PYTHONPATH="$ATB_SRC" "$PYTHON" -m agent_taskboard "$@"
}

echo "=== 1. atb init ==="
atb init

echo
echo "=== 2. atb new (T001 & T002) ==="
atb new "Update greeting message" --agent claude --allow "src/app.py" --allow "tests/**" --deny "src/core.py"
atb new "Localize greeting" --agent opencode --allow "src/app.py" --allow "tests/**" --deny "src/core.py"
git add .taskboard
git commit -m "chore: add T001 and T002 task sheets" -q

echo
echo "=== 3. atb start (T001 & T002) ==="
atb start T001
atb start T002
git add .taskboard/tasks
git commit -m "chore: mark T001 and T002 doing" -q

WT1="$TMP_ROOT/demo-repo-tasks/T001"
WT2="$TMP_ROOT/demo-repo-tasks/T002"

# T001: modifies allowed src/app.py line 2 AND out-of-scope denied src/core.py
cat > "$WT1/src/app.py" <<'EOF'
def greet(name: str) -> str:
    return f"Hi, {name}!"
EOF
cat > "$WT1/src/core.py" <<'EOF'
VERSION = "0.2.0"
EOF
git -C "$WT1" add src/app.py src/core.py
git -C "$WT1" commit -m "feat: update greeting and bump core" -q

# T002: modifies the exact same line in src/app.py (real merge conflict with T001) and updates test
cat > "$WT2/src/app.py" <<'EOF'
def greet(name: str) -> str:
    return f"你好, {name}!"
EOF
cat > "$WT2/tests/test_app.py" <<'EOF'
import unittest
from src.app import greet

class AppTests(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("atb"), "你好, atb!")

if __name__ == "__main__":
    unittest.main()
EOF
git -C "$WT2" add src/app.py tests/test_app.py
git -C "$WT2" commit -m "feat: localize greeting and update test" -q

echo
echo "=== 4. atb check T001 (scope violation) ==="
atb check T001 || true

echo
echo "=== 5. atb check T002 --test ==="
atb check T002 --test

echo
echo "=== 6. atb status ==="
atb status

echo
echo "=== 7. atb conflicts ==="
atb conflicts || true

echo
echo "=== 8. atb review T001 ==="
atb review T001

echo
echo "=== 9. atb rework T001 ==="
atb rework T001 --note "Revert changes to src/core.py and resolve conflict in src/app.py."

echo
echo "=== 10. atb prompt T001 (rework first) ==="
atb prompt T001 --lang en

echo
echo "=== 11. atb dispatch (forwarding checklist) ==="
atb dispatch

echo
echo "=== 12. atb review T002; done --merge (overlap with T001 is a notice) ==="
atb review T002 >/dev/null
git add .taskboard/tasks/T002.md
git commit -m "chore: mark T002 review" -q
atb done T002 --merge
git add .taskboard/tasks/T002.md
git commit -m "chore: mark T002 done" -q

echo
echo "=== 13. T001 applies the rework note ==="
cat > "$WT1/src/app.py" <<'EOF'
def greet(name: str) -> str:
    return f"你好, {name}!"
EOF
cat > "$WT1/src/core.py" <<'EOF'
VERSION = "0.1.0"
EOF
git -C "$WT1" add src/app.py src/core.py
git -C "$WT1" commit -m "fix: apply the rework note (match localized greeting)" -q

echo
echo "=== 14. atb gc (dry-run preview) ==="
atb gc

echo
echo "=== 15. atb doctor (health check) ==="
atb doctor

echo
echo "=== 16. atb gc --apply & atb log T002 ==="
atb gc --apply
atb log T002
