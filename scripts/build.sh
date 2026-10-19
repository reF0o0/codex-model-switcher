#!/bin/zsh
set -eu
task_root="${0:A:h:h}"
task_version="$(< "$task_root/VERSION")"
task_mode="${1:-release}"
task_dev=0
case "$task_mode" in
  release) ;;
  dev|--dev) task_dev=1 ;;
  *) printf '%s\n' '用法：zsh scripts/build.sh [release|dev]' >&2; exit 2 ;;
esac
if (( task_dev )); then
  task_app="$task_root/dist/Codex 模型切换器-dev.app"
  task_zip="$task_root/dist/codex-model-switcher-dev.zip"
else
  task_app="$task_root/dist/Codex 模型切换器.app"
  task_zip="$task_root/dist/codex-model-switcher-macos-arm64.zip"
  # Release builds require the same certificate identity across versions. Never fall back to ad-hoc.
  task_signing_status="$(python3 "$task_root/scripts/signing.py" status)"
  if ! python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1])["ready"] else 1)' "$task_signing_status"; then
    printf '%s\n' '固定签名尚未就绪，构建停止；原应用和安装包保留。请先完成 docs/signing.md 的一次性设置。' >&2
    exit 1
  fi
fi
task_staging="$(mktemp -d /private/tmp/codex-provider-switcher.XXXXXX)"
task_build_app="$task_staging/Codex 模型切换器.app"
mkdir -p "$task_build_app/Contents/MacOS" "$task_build_app/Contents/Resources/backend" "$task_root/dist" "$task_root/build"
/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macosx14.0 \
  "$task_root/app/App.swift" -o "$task_build_app/Contents/MacOS/ProviderSwitcher"
/usr/bin/rsync -a --exclude __pycache__ --exclude '*.pyc' "$task_root/src/" "$task_build_app/Contents/Resources/backend/"
/bin/cp "$task_root/assets/ProviderGPT.png" "$task_root/assets/ProviderDeepSeek.png" "$task_build_app/Contents/Resources/"
task_iconset="$task_staging/AppIcon.iconset"
mkdir -p "$task_iconset"
for task_size in 16 32 128 256 512; do
  /usr/bin/sips -z "$task_size" "$task_size" "$task_root/assets/AppIcon.png" \
    --out "$task_iconset/icon_${task_size}x${task_size}.png" >/dev/null
  task_retina=$((task_size * 2))
  /usr/bin/sips -z "$task_retina" "$task_retina" "$task_root/assets/AppIcon.png" \
    --out "$task_iconset/icon_${task_size}x${task_size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$task_iconset" -o "$task_build_app/Contents/Resources/AppIcon.icns"
/usr/bin/plutil -create xml1 "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIdentifier -string io.github.ref0o0.codex-model-switcher "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleExecutable -string ProviderSwitcher "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleName -string 'Codex 模型切换器' "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIconFile -string AppIcon "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundlePackageType -string APPL "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleShortVersionString -string "$task_version" "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleVersion -string "$task_version" "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert LSMinimumSystemVersion -string 14.0 "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert NSHighResolutionCapable -bool YES "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert NSDocumentsFolderUsageDescription -string '需要访问文稿中的 Codex 工作目录，以检查文件访问和创建兼容接续。' "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert NSDesktopFolderUsageDescription -string '需要访问桌面中的 Codex 工作目录，以创建你选择的兼容接续。' "$task_build_app/Contents/Info.plist"
/usr/bin/plutil -insert NSDownloadsFolderUsageDescription -string '需要访问下载中的 Codex 工作目录，以创建你选择的兼容接续。' "$task_build_app/Contents/Info.plist"
/usr/bin/xattr -cr "$task_build_app"
if (( task_dev )); then
  /usr/bin/codesign --force --sign - "$task_build_app"
else
  python3 "$task_root/scripts/signing.py" sign "$task_build_app"
fi
/usr/bin/codesign --verify --deep --strict "$task_build_app"
if [[ -d "$task_app" ]]; then
  task_previous="$(mktemp -d "$task_root/build/previous-build.XXXXXX")"
  /bin/mv "$task_app" "$task_previous/"
fi
/usr/bin/ditto --norsrc --noextattr "$task_build_app" "$task_app"
/usr/bin/ditto -c -k --norsrc --noextattr --keepParent "$task_build_app" "$task_zip"
/usr/bin/xattr -cr "$task_app"
/usr/bin/codesign --verify --deep "$task_app"
# The recursive cleanup target is only the validated directory from mktemp above.
if [[ "$task_staging" == /private/tmp/codex-provider-switcher.* ]]; then
  /bin/rm -r -- "$task_staging"
fi
printf '%s\n' "$task_app"
