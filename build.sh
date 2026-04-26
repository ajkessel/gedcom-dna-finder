#!/bin/sh
pyinstaller --onefile --icon=family_tree.ico ./gedcom-dna-finder-cli.py
pyinstaller --onefile --windowed --icon=family_tree.ico ./gedcom-dna-finder-gui.py
if [[ $(uname) == "Linux" ]]
then 
zip -r gedcom-dna-finder-linux.zip dist/*
else
zip -r gedcom-dna-finder-mac.zip dist/*
fi
