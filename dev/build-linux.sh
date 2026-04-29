#!/bin/bash
[[ -e .venv/bin/activate ]] || {
	echo 'Creating virtual environment...'
	python3 -m venv .venv --prompt "gedcom-dna-finder" || {
		echo 'Failed to create virtual environment.'
		exit 1
	}
}
source .venv/bin/activate || {
	echo 'Failed to activate virtual environment.'
	exit 1
}
pip install -r ./dev/requirements.txt || {
	echo 'Failed to install dependencies.'
	exit 1
}
python3 ./dev/generate_icon.py ./icons/family_tree.png || {
	echo 'Failed to generate ICO file.'
	exit 1
}
pyinstaller --noconfirm ./dev/gedcom-dna-finder-cli.spec || {
	echo 'pyinstaller failed to build CLI.'
	exit 1
}
pyinstaller --noconfirm ./dev/gedcom-dna-finder-gui.spec || {
	echo 'pyinstaller failed to build GUI.'
	exit 1
}
[ -d "dist" ] || {
	echo 'Cannot find dist build folder.'
	exit 1
}
pushd dist
zip -r "../${out}" .
mv "../${out}" .
popd
