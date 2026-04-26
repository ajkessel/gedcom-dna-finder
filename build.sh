#!/bin/sh
pyinstaller --noconfirm --onefile --icon=family_tree.ico ./gedcom-dna-finder-cli.py
pyinstaller --noconfirm --onefile --windowed --icon=family_tree.ico ./gedcom-dna-finder-gui.py
if [[ $(uname) == "Linux" ]]
then 
zip -r gedcom-dna-finder-linux.zip dist/*
else
zip -r gedcom-dna-finder-mac.zip dist/*
fi
