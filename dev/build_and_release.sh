#!/bin/bash
# script for building and uploading executables to Github
# intended to run from WSL instance with access to local powershell and remote mac at hostname vmac
# include -c as command line switch to create new release, otherwise latest release will be used
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
cd "${SCRIPT_DIR}/.."
exec > >(sed 's/\x1b\[[0-9;]*m//g' | tee -a build_and_release.log) 2>&1
printf -- "---------------------------------\ngedcom-dna-finder build log\n$(date)\n---------------------------------\n"
if [ "$1" == "-c" ]; then
	gh release create
fi
current=$(gh release list --json tagName,isLatest --jq '.[] | select(.isLatest) | .tagName')
git pull
source .venv/bin/activate
echo 'Building for Linux platform...'
stdbuf -o0 ./dev/build.sh
echo 'Building for Windows platform...'
stdbuf -o0 pwsh -command 'set-location c:/apps/src/gedcom-dna-finder ; git pull ; venv ; ./dev/build.ps1'
echo 'Building for Mac platform...'
stdbuf -o0 ssh mac 'cd src/gedcom-dna-finder/ ; git pull ; stdbuf -o0./dev/build.sh'
echo 'Copying built ZIP files locally...'
scp mac:src/gedcom-dna-finder/dist/*zip ./dist
cp /mnt/c/apps/src/gedcom-dna-finder/dist/*zip ./dist
echo 'Uploading new release to GitHub...'
gh release upload "${current}" ./dist/*zip --clobber
