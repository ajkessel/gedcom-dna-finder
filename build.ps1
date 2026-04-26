pyinstaller --noconfirm .\gedcom-dna-finder-gui.spec
pyinstaller --noconfirm .\gedcom-dna-finder-cli.spec
compress-archive -path dist\* -destinationpath .\gedcom-dna-finder-windows.zip -force
