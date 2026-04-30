# dev/build_pypi.ps1
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
#   .\dev\build_pypi.ps1             # build + upload to PyPI
#   .\dev\build_pypi.ps1 -TestPyPI  # build + upload to test.pypi.org
#
param(
    [switch]$TestPyPI
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -Path "$PSScriptRoot\.."

Write-Host "==> Installing / upgrading build tools..."
python -m pip install --upgrade build hatchling twine

Write-Host "==> Cleaning previous dist/ output..."
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue

Write-Host "==> Building sdist..."
python -m build -s dev/ --outdir dist/

Write-Host "==> Building wheel..."
python -m build -w dev/ --outdir dist/

Write-Host "==> Built artifacts:"
Get-ChildItem dist | Select-Object Name, Length | Format-Table -AutoSize

if ($TestPyPI) {
    Write-Host "==> Uploading to TestPyPI (https://test.pypi.org)..."
    python -m twine upload --repository testpypi dist/*
} else {
    Write-Host "==> Uploading to PyPI..."
    python -m twine upload dist/*
}

Write-Host "==> Done."
