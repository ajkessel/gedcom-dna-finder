pyinstaller --onefile --icon=family_tree.ico .\gedcom-dna-finder-cli.py
pyinstaller --onefile --windowed --icon=family_tree.ico .\gedcom-dna-finder-gui.py
compress-archive -path dist\* -destinationpath .\gedcom-dna-finder.zip
