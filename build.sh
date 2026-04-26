#!/bin/bash
rm -r dist/
pyinstaller --noconfirm ./gedcom-dna-finder-cli.spec
pyinstaller --noconfirm ./gedcom-dna-finder-gui.spec
if [[ $(uname) == "Linux" ]]
then 
zip -r gedcom-dna-finder-linux.zip dist/*
else
zip -r gedcom-dna-finder-mac.zip dist/*
fi
