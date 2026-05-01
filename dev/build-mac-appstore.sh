#!/bin/bash
AS_APP_CERT=$(security find-identity -v -p codesigning 2>/dev/null |
	grep "3rd Party Mac Developer Application" |
	grep -Eo '[0-9A-Z]{40}' | head -1)
AS_INST_CERT=$(security find-identity -v 2>/dev/null |
	grep "3rd Party Mac Developer Installer" |
	grep -Eo '[0-9A-Z]{40}' | head -1)

if [[ -n "${AS_APP_CERT}" && -n "${AS_INST_CERT}" ]]; then
	echo "Building App Store package..."
	APP_SRC="dist/gedcom-dna-finder.app"
	APP_AS="dist/gedcom-dna-finder-appstore.app"
	PKG="dist/gedcom-dna-finder.pkg"

	# Work from a clean copy so the notarised Developer-ID build is untouched
	rm -rf "${APP_AS}"
	cp -R "${APP_SRC}" "${APP_AS}"

	# Ensure all files are readable by non-root users (App Store error 90255).
	chmod -R a+rX "${APP_AS}"

	# Re-sign bottom-up with the App Store identity.
	# --deep triggers errSecInternalComponent on Python .so extension modules,
	# so sign nested components individually first, then the executable, then
	# the bundle. The sandbox entitlement only needs to be on the main executable.
	while IFS= read -r -d '' f; do
		codesign --force --sign "${AS_APP_CERT}" "$f" || {
			echo "App Store code-signing failed on: $f"
			exit 1
		}
	done < <(find "${APP_AS}" -type f \( -name "*.so" -o -name "*.dylib" \) -print0)

	codesign --force --verbose \
		--sign "${AS_APP_CERT}" \
		--entitlements "./dev/entitlements-appstore.plist" \
		"${APP_AS}/Contents/MacOS/gedcom-dna-finder" || {
		echo "App Store code-signing of main executable failed."
		exit 1
	}

	codesign --force --verbose \
		--sign "${AS_APP_CERT}" \
		--entitlements "./dev/entitlements-appstore.plist" \
		"${APP_AS}" || {
		echo "App Store code-signing of bundle failed."
		exit 1
	}

	productbuild \
		--component "${APP_AS}" /Applications \
		--sign "${AS_INST_CERT}" \
		"${PKG}" || {
		echo "productbuild failed."
		exit 1
	}

	rm -rf "${APP_AS}"
	echo "App Store package created: ${PKG}"
else
	echo "No App Store signing certificates found; skipping pkg creation."
	[[ -z "${AS_APP_CERT}" ]] && echo "  Missing: 3rd Party Mac Developer Application"
	[[ -z "${AS_INST_CERT}" ]] && echo "  Missing: 3rd Party Mac Developer Installer"
  exit 1
fi
echo "Submitting App Store package to app store..."
apiKey=$(cat "${HOME}/.appstoreconnect/apikey.txt")
apiIssuer=$(cat "${HOME}/.appstoreconnect/apiissuer.txt")
[ -z "${apiKey}" ] || [ -z "${apiIssuer}" ] && {
  echo "Need apikey and apiissuer to subimt to app store."
  exit 1
}
xcrun altool --validate-app -f dist/gedcom-dna-finder.pkg -t macos --apiKey "${apiKey}" --apiIssuer "${apiIssuer}"
xcrun altool --upload-app -f dist/gedcom-dna-finder.pkg -t macos --apiKey "${apiKey}" --apiIssuer "${apiIssuer}"
