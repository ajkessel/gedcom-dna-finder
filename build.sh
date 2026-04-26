#!/bin/bash
rm -r dist/
pyinstaller --noconfirm ./gedcom-dna-finder-cli.spec || { echo 'pyinstaller failed to build CLI.'; exit 1; }
pyinstaller --noconfirm ./gedcom-dna-finder-gui.spec || { echo 'pyinstaller failed to build GUI.'; exit 1; }
[ -d "dist" ] || { echo 'Cannot find dist build folder.'; exit 1; }
pushd dist
if [[ $(uname) == "Linux" ]]
then 
zip -r - . > ../gedcom-dna-finder-linux.zip
else
zip -r - . > ../gedcom-dna-finder-mac.zip 
fi
popd
