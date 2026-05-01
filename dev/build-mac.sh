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
export PYENV_ROOT="$HOME/.pyenv"
[[ -e "${PYENV_ROOT}/shims/python3.14" ]] || {
	echo 'Installing pyenv for python 3.14.4'
	mkdir -p "${PYENV_ROOT}"
	eval "$(pyenv init -)"
	pyenv install 3.14.4
	pyenv global 3.14.4
}
eval "$(pyenv init -)"
./dev/generate_icns.sh ./icons/family_tree.png || {
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
rm dist/gedcom-dna-finder dist/gedcom-dna-finder-cli
ditto -c -k --sequesterRsrc "dist/" "${out}"
xcrun notarytool submit "${out}" --keychain-profile "notarytool-profile" --wait
xcrun stapler staple ./dist/gedcom-dna-finder.app
rm "${out}"
ditto -c -k --sequesterRsrc "dist/" "${out}"
mv "${out}" dist/

# ── App Store package (.pkg) ────────────────────────────────────────────────
# Requires the "Apple Distribution" certificate in the keychain (used for
# both .app signing and .pkg signing in modern Xcode / Apple toolchains).
# Download it from the Apple Developer portal.
AS_CERT=$(security find-identity -v -p codesigning 2>/dev/null \
	| grep "Apple Distribution" \
	| grep -Eo '[0-9A-Z]{40}' | head -1)

if [[ -n "${AS_CERT}" ]]; then
	echo "Building App Store package..."
	APP_SRC="dist/gedcom-dna-finder.app"
	APP_AS="dist/gedcom-dna-finder-appstore.app"
	PKG="dist/gedcom-dna-finder.pkg"

	# Work from a clean copy so the notarised Developer-ID build is untouched
	rm -rf "${APP_AS}"
	cp -R "${APP_SRC}" "${APP_AS}"

	# Sign from the inside out: .so/.dylib first, then frameworks, then the
	# bundle. --deep is deprecated and fails on PyInstaller bundles with many
	# nested extension modules (errSecInternalComponent).
	find "${APP_AS}/Contents" \( -name "*.so" -o -name "*.dylib" \) | while read -r f; do
		codesign --force --verify --sign "${AS_CERT}" \
			--entitlements "./dev/entitlements-appstore.plist" "${f}" || {
			echo "App Store code-signing failed on: ${f}"
			exit 1
		}
	done
	find "${APP_AS}/Contents/Frameworks" -maxdepth 1 -name "*.framework" | while read -r f; do
		codesign --force --verify --sign "${AS_CERT}" \
			--entitlements "./dev/entitlements-appstore.plist" "${f}" || {
			echo "App Store code-signing failed on: ${f}"
			exit 1
		}
	done
	codesign --force --verify --verbose \
		--sign "${AS_CERT}" \
		--entitlements "./dev/entitlements-appstore.plist" \
		"${APP_AS}" || {
		echo "App Store code-signing failed."
		exit 1
	}

	productbuild \
		--component "${APP_AS}" /Applications \
		--sign "${AS_CERT}" \
		"${PKG}" || {
		echo "productbuild failed."
		exit 1
	}

	rm -rf "${APP_AS}"
	echo "App Store package created: ${PKG}"
else
	echo "No App Store signing certificate found; skipping pkg creation."
	echo "  Missing: Apple Distribution"
fi
