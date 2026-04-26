#!/bin/bash
rm -r dist/
pyinstaller --noconfirm ./gedcom-dna-finder-cli.py
pyinstaller --noconfirm ./gedcom-dna-finder-gui.py
if [[ $(uname) == "Linux" ]]
then 
zip -r gedcom-dna-finder-linux.zip dist/*
else
zip -r gedcom-dna-finder-mac.zip dist/*
fi
