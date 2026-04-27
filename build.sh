#!/bin/bash
[[ -e dist/ ]] && rm -r dist/
if [[ $(uname) == "Linux" ]]; then
	echo 'Building for Linux...'
	out="gedcom-dna-finder-linux.zip"
	python ./generate_icon.py family_tree.ico || {
		echo 'Failed to generate ICO file.'
		exit 1
	}
else
	echo 'Building for macOS...'
	out="gedcom-dna-finder-mac.zip"
	./generate_icns.sh -s family_tree.png || {
		echo 'Failed to generate ICNS file.'
		exit 1
	}
fi
[[ -e .venv/bin/activate ]] || {
	echo 'Creating virtual environment...'
	python3 -m venv .venv || {
		echo 'Failed to create virtual environment.'
		exit 1
	}
}
source .venv/bin/activate || {
	echo 'Failed to activate virtual environment.'
	exit 1
}
pip install -r requirements.txt || {
	echo 'Failed to install dependencies.'
	exit 1
}
pyinstaller --noconfirm ./gedcom-dna-finder-cli.spec || {
	echo 'pyinstaller failed to build CLI.'
	exit 1
}
pyinstaller --noconfirm ./gedcom-dna-finder-gui.spec || {
	echo 'pyinstaller failed to build GUI.'
	exit 1
}
[ -d "dist" ] || {
	echo 'Cannot find dist build folder.'
	exit 1
}
pushd dist
zip -r - . >"${out}"
popd