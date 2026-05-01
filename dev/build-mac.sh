#!/bin/bash
out="gedcom-dna-finder-mac.zip"
echo 'Building for macOS...'
if [[ "$OSTYPE" != "darwin"* ]]; then
	echo 'This script is intended to be run on macOS.'
	exit 1
fi
if [[ -e ${HOME}/.config/p ]]; then
	echo 'Unlocking keychain...'
	security unlock-keychain -p "$(cat ${HOME}/.config/p)" "${HOME}/Library/Keychains/login.keychain-db"
else
	echo 'Password file not found at ~/.config/p, skipping automatic keychain unlock.'
	security unlock-keychain "${HOME}/Library/Keychains/login.keychain-db"
fi
export PATH="/usr/local/bin:$PATH"
command -v brew && export PATH="$(brew --prefix python)/libexec/bin:$PATH" || {
	echo 'homebrew not found, we will still try to build but this script has not been tested on MacOS without brew.'
}
command -v pyenv || {
	echo 'pyenv missing, attempting to install from homebrew...'
	brew install pyenv
}
# preference is for universal2 python from python.org
# alternatively, set up pyenv environment
[[ -e "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3" ]] && {
  export PATH="/Library/Frameworks/Python.framework/Versions/3.14/bin/:${PATH}"
} || {
  export PYENV_ROOT="$HOME/.pyenv"
  [[ -e "${PYENV_ROOT}/shims/python3.14" ]] || {
    echo 'Installing pyenv for python 3.14.4'
    mkdir -p "${PYENV_ROOT}"
    eval "$(pyenv init -)"
    export PYTHON_CONFIGURE_OPTS="--enable-universal-archs=universal2 --with-universal-archs=universal2"
    pyenv install 3.14.4
    pyenv global 3.14.4
  }
  eval "$(pyenv init -)"
}
./dev/generate-icns.sh ./icons/family_tree.png || {
	echo 'Failed to generate ICNS file.'
	exit 1
}
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
python3 ./dev/generate-icon.py ./icons/family_tree.png || {
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
AS_APP_CERT=$(security find-identity -v -p codesigning 2>/dev/null |
	grep "3rd Party Mac Developer Application" |
	grep -Eo '[0-9A-Z]{40}' | head -1)
codesign -s "${AS_APP_CERT}" -f --timestamp -o runtime -i "com.ajkessel.gedcom-dna-finder" "dist/gedcom-dna-finder.app"
ditto -c -k --sequesterRsrc "dist/gedcom-dna-finder.app" "${out}"
xcrun notarytool submit "${out}" --keychain-profile "notarytool-profile" --wait
xcrun stapler staple ./dist/gedcom-dna-finder.app
rm "${out}"
ditto -c -k  --sequesterRsrc --keepParent "dist/gedcom-dna-finder.app" "${out}"
mv "${out}" dist/

