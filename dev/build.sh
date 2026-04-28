#!/bin/bash
command -v brew && export PATH="$(brew --prefix python)/libexec/bin:$PATH"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
cd "${SCRIPT_DIR}/.."
[[ -e ./dist/ ]] && rm -r ./dist/
[[ -e ./src/gedcom-dna-finder-gui.py ]] || {
	echo 'Build files not found.'
	exit 1
}
if [[ $(uname) == "Linux" ]]; then
	echo 'Building for Linux...'
	out="gedcom-dna-finder-linux.zip"
	python3 ./dev/generate_icon.py ./icons/family_tree.png || {
		echo 'Failed to generate ICO file.'
		exit 1
	}
else
	echo 'Building for macOS...'
  export PATH="/usr/local/bin:$PATH"
  command -v brew && export PATH="$(brew --prefix python)/libexec/bin:$PATH"
  export PYENV_ROOT="$HOME/.pyenv"
  [[ -e "${PYENV_ROOT}/shims/python3.14" ]] || {
    echo 'Installing pyenv for python 3.14.4'
    mkdir -p "${PYENV_ROOT}"
    eval "$(pyenv init -)"
    pyenv install 3.14.4
    pyenv global 3.14.4
  }
  eval "$(pyenv init -)"
	out="gedcom-dna-finder-mac.zip"
	./dev/generate_icns.sh ./icons/family_tree.png || {
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
pip install -r ./dev/requirements.txt || {
	echo 'Failed to install dependencies.'
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
