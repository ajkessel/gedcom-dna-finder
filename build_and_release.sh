#!/bin/bash
# script for building and uploading executables to Github
# intended to run from WSL instance with access to local powershell and remote mac at hostname vmac
# include -c as command line switch to create new release, otherwise latest release will be used
if [ "$1" == "-c" ]
then
   gh release create
fi
current=$(gh release list --json tagName,isLatest --jq '.[] | select(.isLatest) | .tagName')
git pull
source .venv/bin/activate
echo 'Building for Linux platform...'
./build.sh
echo 'Building for Mac platform...'
ssh vmac 'cd gedcom-dna-finder/ ; source .venv/bin/activate ; git pull ; ./build.sh'
echo 'Building for Windows platform...'
pwsh -command 'set-location c:/apps/src/gedcom-dna-finder ; venv ; ./.venv/scripts/activate ; ./build.ps1' 
echo 'Copying built ZIP files locally...'
scp vmac:gedcom-dna-finder/*zip . 
cp /mnt/c/apps/src/gedcom-dna-finder/*zip . 
echo 'Uploading new release to GitHub...'
gh release upload "${current}" *zip --clobber
