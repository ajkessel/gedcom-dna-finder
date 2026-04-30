#!/usr/bin/env bash
#
# dev/build_pypi.sh
#
# Builds a wheel + sdist and uploads them to PyPI via Twine.
# Run from any directory; the script repositions to the repo root.
#
# Prerequisites:
#   - Python 3.8+ in PATH (or activate the project venv first)
#   - PyPI credentials in ~/.pypirc, or set TWINE_USERNAME / TWINE_PASSWORD,
#     or be prepared to enter them interactively.
#
# Usage:
#   ./dev/build_pypi.sh             # build + upload to PyPI
#   ./dev/build_pypi.sh --test-pypi # build + upload to test.pypi.org
#

set -euo pipefail

TEST_PYPI=0
for arg in "$@"; do
    case "$arg" in
        --test-pypi) TEST_PYPI=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

cd "$(dirname "$0")/.."

echo "==> Installing / upgrading build tools..."
python3 -m pip install --upgrade build hatchling twine

echo "==> Cleaning previous dist/ output..."
rm -rf dist/

echo "==> Building sdist..."
python3 -m build -s dev/ --outdir dist/

echo "==> Building wheel..."
python3 -m build -w dev/ --outdir dist/

echo "==> Built artifacts:"
ls -lh dist/

if [ "$TEST_PYPI" -eq 1 ]; then
    echo "==> Uploading to TestPyPI (https://test.pypi.org)..."
    python3 -m twine upload --repository testpypi dist/*
else
    echo "==> Uploading to PyPI..."
    python3 -m twine upload dist/*
fi

echo "==> Done."
