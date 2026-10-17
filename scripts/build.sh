#!/bin/zsh
set -eu
task_root="${0:A:h:h}"
task_app="$task_root/dist/Codex 模型切换器.app"
task_version="$(< "$task_root/VERSION")"
task_staging="$(mktemp -d /private/tmp/codex-provider-switcher.XXXXXX)"
task_build_app="$task_staging/Codex 模型切换器.app"
mkdir -p "$task_build_app/Contents/MacOS" "$task_build_app/Contents/Resources/backend" "$task_root/dist" "$task_root/build"
/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macosx14.0 \
  "$task_root/app/App.swift" -o "$task_build_app/Contents/MacOS/ProviderSwitcher"
/usr/bin/rsync -a --exclude __pycache__ --exclude '*.pyc' "$task_root/src/" "$task_build_app/Contents/Resources/backend/"
/usr/bin/plutil -create xml1 "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIdentifier -string io.github.ref0o0.codex-model-switcher "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleExecutable -string ProviderSwitcher "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleName -string 'Codex 模型切换器' "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundlePackageType -string APPL "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleShortVersionString -string "$task_version" "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleVersion -string "$task_version" "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert LSMinimumSystemVersion -string 14.0 "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert NSHighResolutionCapable -bool YES "$task_build_app/Contents/Info.plist"
/usr/bin/xattr -cr "$task_build_app"
/usr/bin/codesign --force --deep --sign - "$task_build_app"
/usr/bin/codesign --verify --deep --strict "$task_build_app"
if [[ -d "$task_app" ]]; then
  task_previous="$(mktemp -d "$task_root/build/previous-build.XXXXXX")"
  /bin/mv "$task_app" "$task_previous/"
fi
/usr/bin/ditto --norsrc --noextattr "$task_build_app" "$task_app"
/usr/bin/ditto -c -k --norsrc --noextattr --keepParent "$task_build_app" "$task_root/dist/codex-model-switcher-macos-arm64.zip"
/usr/bin/codesign --verify --deep "$task_app"
# The recursive cleanup target is only the validated directory from mktemp above.
if [[ "$task_staging" == /private/tmp/codex-provider-switcher.* ]]; then
  /bin/rm -r -- "$task_staging"
fi
printf '%s\n' "$task_app"
