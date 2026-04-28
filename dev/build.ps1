Set-Location -Path $PSScriptRoot/..
if ( -not ( Get-Command python -ErrorAction SilentlyContinue ) ) { 
    Write-Output "Python is not installed or not in the PATH. Please install Python and ensure it is in the PATH before running this script." 
    exit 1
}
if ( -not ( Test-Path .\venv\scripts\activate.ps1)) {
    Write-Output "Creating and activating virtual environment, and installing dependencies..."
    python -m venv .\venv --prompt "gedcom-dna-finder" 
    python .\dev\find_ffi_dll.py
    .\venv\Scripts\activate.ps1
    pip install -r .\dev\requirements-windows.txt
}
if ( -not ( Test-Path .\venv\scripts\activate.ps1)) {
    Write-Output "Virtual environment activation script not found. Please ensure the virtual environment is set up correctly." 
    exit 1
}
& ".\venv\Scripts\activate.ps1"
Remove-Item -Recurse -Force -Path dist\
python .\dev\generate_icon.py .\icons\family_tree.png
pyinstaller --noconfirm .\dev\gedcom-dna-finder-gui.spec
pyinstaller --noconfirm .\dev\gedcom-dna-finder-cli.spec
Compress-Archive -Path dist\* -DestinationPath .\dist\gedcom-dna-finder-windows.zip -Force
