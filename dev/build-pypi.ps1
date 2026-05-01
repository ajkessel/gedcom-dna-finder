# dev/build-pypi.ps1
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
#   .\dev\build-pypi.ps1             # build + upload to PyPI
#   .\dev\build-pypi.ps1 -TestPyPI  # build + upload to test.pypi.org
#
param(
    [switch]$TestPyPI
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -Path "$PSScriptRoot\.."

if ( Test-Path .\Venv\Scripts\Activate.ps1 ) {
  & ".\venv\Scripts\Activate.ps1"
}

if ( -not ( test-path .\dist\pypi ) ) {
  mkdir .\dist\pypi
}

Write-Host "==> Installing / upgrading build tools..."
python -m pip install --upgrade build hatchling twine

Write-Host "==> Cleaning previous dist/ output..."
Remove-Item -Recurse -Force dist/pypi -ErrorAction SilentlyContinue

Write-Host "==> Building sdist..."
python -m build -s dev/ --outdir dist/pypi

Write-Host "==> Building wheel..."
python -m build -w dev/ --outdir dist/pypi

Write-Host "==> Built artifacts:"
Get-ChildItem dist/pypi | Select-Object Name, Length | Format-Table -AutoSize

if ($TestPyPI) {
    Write-Host "==> Uploading to TestPyPI (https://test.pypi.org)..."
    python -m twine upload --repository testpypi dist/pypi/*
} else {
    Write-Host "==> Uploading to PyPI..."
    python -m twine upload dist/pypi/*
}

Write-Host "==> Done."
