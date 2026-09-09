#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "AppImage builds must be run on Linux." >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64 | amd64)
        APPIMAGE_ARCH="x86_64"
        ;;
    aarch64 | arm64)
        APPIMAGE_ARCH="aarch64"
        ;;
    *)
        echo "Unsupported AppImage architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to build the AppImage." >&2
    exit 1
fi

mapfile -t APP_INFO < <(
    python3 - <<'PY'
import ast
from pathlib import Path

values = {}
tree = ast.parse(Path("aps_midi_prep_tool_app/app_info.py").read_text())
for node in tree.body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        continue
    target = node.targets[0]
    if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
        values[target.id] = node.value.value

print(values["APP_NAME"])
print(values["APP_VERSION"])
PY
)

DISPLAY_NAME="${APP_INFO[0]}"
APP_VERSION="${APP_INFO[1]}"
APP_ID="com.alexpianoservice.APSMidiPrepTool"
APP_BIN="APSMidiPrepTool"
APP_ICON_PNG="$ROOT_DIR/aps_midi_prep_tool_app/aps.png"
APP_ICON_ICO="$ROOT_DIR/aps_midi_prep_tool_app/aps.ico"
APP_METAINFO="$ROOT_DIR/packaging/com.alexpianoservice.APSMidiPrepTool.metainfo.xml"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-appimage}"
BUILD_DIR="$ROOT_DIR/build"
APPIMAGE_BUILD_DIR="$BUILD_DIR/appimage"
PYINSTALLER_BUILD_DIR="$BUILD_DIR/pyinstaller"
APPDIR="$APPIMAGE_BUILD_DIR/$APP_BIN.AppDir"
OUT_DIR="$ROOT_DIR/release"
APPIMAGE_PATH="$OUT_DIR/$APP_BIN-$APP_VERSION-$APPIMAGE_ARCH.AppImage"
CHECKSUM_PATH="$APPIMAGE_PATH.sha256"
APPIMAGETOOL="${APPIMAGETOOL:-$APPIMAGE_BUILD_DIR/appimagetool-$APPIMAGE_ARCH.AppImage}"
APPIMAGETOOL_URL="${APPIMAGETOOL_URL:-https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-$APPIMAGE_ARCH.AppImage}"
BUNDLE_MTOOLS="${BUNDLE_MTOOLS:-1}"
BUNDLE_7ZIP="${BUNDLE_7ZIP:-1}"
BUNDLE_GREASEWEAZLE="${BUNDLE_GREASEWEAZLE:-1}"
BUNDLE_LAME="${BUNDLE_LAME:-1}"
BUNDLE_FLUIDSYNTH="${BUNDLE_FLUIDSYNTH:-0}"
GREASEWEAZLE_REQUIREMENT="${GREASEWEAZLE_REQUIREMENT:-git+https://github.com/keirf/greaseweazle.git@v1.23}"
MTOOLS_COMMANDS=(mformat mcopy mdel mren mdir)
SOUNDFONT_PATH="${SOUNDFONT_PATH:-}"
SOUNDFONT_CANDIDATES=(
    "$ROOT_DIR/aps_midi_prep_tool_app/soundfonts/default.sf2"
    "$ROOT_DIR/aps_midi_prep_tool_app/soundfonts/default.sf3"
    "/usr/share/sounds/sf2/FluidR3_GM.sf2"
    "/usr/share/sounds/sf2/default-GM.sf2"
    "/usr/share/sounds/sf2/TimGM6mb.sf2"
    "/usr/share/sounds/sf3/default-GM.sf3"
    "/usr/share/soundfonts/FluidR3_GM.sf2"
    "/usr/share/soundfonts/default.sf2"
)

for icon_path in "$APP_ICON_PNG" "$APP_ICON_ICO"; do
    if [[ ! -f "$icon_path" ]]; then
        echo "Application icon not found: $icon_path" >&2
        exit 1
    fi
done

if [[ ! -f "$APP_METAINFO" ]]; then
    echo "AppStream metadata not found: $APP_METAINFO" >&2
    exit 1
fi

is_enabled() {
    case "${1,,}" in
        0 | false | no | off)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

download_appimagetool() {
    mkdir -p "$APPIMAGE_BUILD_DIR"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --output "$APPIMAGETOOL" "$APPIMAGETOOL_URL"
    elif command -v wget >/dev/null 2>&1; then
        wget --output-document="$APPIMAGETOOL" "$APPIMAGETOOL_URL"
    else
        echo "curl or wget is required to download appimagetool." >&2
        exit 1
    fi
    chmod +x "$APPIMAGETOOL"
}

copy_required_command() {
    local command_name="$1"
    local source_path
    source_path="$(command -v "$command_name" || true)"
    if [[ -z "$source_path" ]]; then
        echo "$command_name is required to bundle mtools into the AppImage." >&2
        echo "Install mtools on the build machine, or run with BUNDLE_MTOOLS=0." >&2
        exit 1
    fi
    install -Dm755 "$source_path" "$APPDIR/usr/bin/$command_name"
}

copy_shared_libraries() {
    local binary_path="$1"
    mkdir -p "$APPDIR/usr/lib"
    ldd "$binary_path" \
        | awk '
            /=> \// { print $3 }
            /^[[:space:]]*\// { print $1 }
        ' \
        | while IFS= read -r lib_path; do
            [[ -n "$lib_path" && -f "$lib_path" ]] || continue
            case "$lib_path" in
                /lib*/ld-linux* | /usr/lib*/ld-linux*)
                    continue
                    ;;
            esac
            case "$(basename "$lib_path")" in
                libc.so.* | libpthread.so.* | libdl.so.* | libm.so.* | librt.so.* | libresolv.so.*)
                    continue
                    ;;
            esac
            install -Dm755 "$lib_path" "$APPDIR/usr/lib/$(basename "$lib_path")"
        done
}

find_soundfont() {
    local candidate
    if [[ -n "$SOUNDFONT_PATH" && -f "$SOUNDFONT_PATH" ]]; then
        printf '%s\n' "$SOUNDFONT_PATH"
        return 0
    fi
    for candidate in "${SOUNDFONT_CANDIDATES[@]}"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

copy_fluidsynth_bundle() {
    local fluidsynth_path soundfont_path soundfont_ext soundfont_target
    fluidsynth_path="$(command -v fluidsynth || true)"
    if [[ -z "$fluidsynth_path" ]]; then
        echo "fluidsynth is required to bundle acoustic piano previews into the AppImage." >&2
        echo "Install fluidsynth on the build machine, or run with BUNDLE_FLUIDSYNTH=0." >&2
        exit 1
    fi
    if ! soundfont_path="$(find_soundfont)"; then
        echo "A GM SoundFont is required to bundle acoustic piano previews into the AppImage." >&2
        echo "Install fluid-soundfont-gm or set SOUNDFONT_PATH=/path/to/piano.sf2, or run with BUNDLE_FLUIDSYNTH=0." >&2
        exit 1
    fi
    install -Dm755 "$fluidsynth_path" "$APPDIR/usr/bin/fluidsynth"
    copy_shared_libraries "$fluidsynth_path"
    soundfont_ext="${soundfont_path##*.}"
    soundfont_target="$APPDIR/usr/share/aps-midi-prep-tool/soundfonts/default.$soundfont_ext"
    install -Dm644 "$soundfont_path" "$soundfont_target"
}

copy_lame_bundle() {
    local lame_path
    lame_path="$(command -v lame || true)"
    if [[ -z "$lame_path" ]]; then
        echo "lame is required to bundle MP3 rendering support into the AppImage." >&2
        echo "Install lame on the build machine, or run with BUNDLE_LAME=0." >&2
        exit 1
    fi
    install -Dm755 "$lame_path" "$APPDIR/usr/bin/lame"
    copy_shared_libraries "$lame_path"
}

copy_7zip_bundle() {
    local sevenzip_path sevenzip_binary sevenzip_dir
    sevenzip_path="$(command -v 7z || true)"
    if [[ -z "$sevenzip_path" ]]; then
        echo "7z is required to bundle 7-Zip image inspection into the AppImage." >&2
        echo "Install 7zip/p7zip on the build machine, or run with BUNDLE_7ZIP=0." >&2
        exit 1
    fi

    if [[ -L "$sevenzip_path" ]]; then
        sevenzip_path="$(readlink -f "$sevenzip_path")"
    fi
    if file "$sevenzip_path" | grep -qi "shell script"; then
        sevenzip_binary="/usr/lib/7zip/7z"
    else
        sevenzip_binary="$sevenzip_path"
    fi
    if [[ ! -x "$sevenzip_binary" ]]; then
        echo "Could not locate the real 7-Zip binary for $sevenzip_path." >&2
        exit 1
    fi

    sevenzip_dir="$(dirname "$sevenzip_binary")"
    install -Dm755 "$sevenzip_binary" "$APPDIR/usr/lib/7zip/7z"
    if [[ -f "$sevenzip_dir/7z.so" ]]; then
        install -Dm755 "$sevenzip_dir/7z.so" "$APPDIR/usr/lib/7zip/7z.so"
        copy_shared_libraries "$sevenzip_dir/7z.so"
    fi
    copy_shared_libraries "$sevenzip_binary"

    cat > "$APPDIR/usr/bin/7z" <<'EOF'
#!/usr/bin/env bash
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
export LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/lib/7zip:${LD_LIBRARY_PATH:-}"
exec "$APPDIR/usr/lib/7zip/7z" "$@"
EOF
    chmod +x "$APPDIR/usr/bin/7z"
}

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install --upgrade -r requirements-build.txt

if is_enabled "$BUNDLE_GREASEWEAZLE"; then
    if ! command -v git >/dev/null 2>&1; then
        echo "git is required to install Greaseweazle for the AppImage bundle." >&2
        echo "Install git on the build machine, or run with BUNDLE_GREASEWEAZLE=0." >&2
        exit 1
    fi
    "$VENV_DIR/bin/python" -m pip install --upgrade "$GREASEWEAZLE_REQUIREMENT"
fi

rm -rf "$PYINSTALLER_BUILD_DIR" "$ROOT_DIR/dist/$APP_BIN" "$APPDIR"
mkdir -p "$PYINSTALLER_BUILD_DIR" "$APPDIR/usr/bin" "$OUT_DIR"

"$VENV_DIR/bin/python" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "$APP_BIN" \
    --icon "$APP_ICON_ICO" \
    --distpath "$ROOT_DIR/dist" \
    --workpath "$PYINSTALLER_BUILD_DIR/work" \
    --specpath "$PYINSTALLER_BUILD_DIR/spec" \
    --add-data "$APP_ICON_PNG:aps_midi_prep_tool_app" \
    --add-data "$APP_ICON_ICO:aps_midi_prep_tool_app" \
    aps_midi_prep_tool.py

cp -a "$ROOT_DIR/dist/$APP_BIN/." "$APPDIR/usr/bin/"

if is_enabled "$BUNDLE_MTOOLS"; then
    for command_name in "${MTOOLS_COMMANDS[@]}"; do
        copy_required_command "$command_name"
    done
fi

if is_enabled "$BUNDLE_7ZIP"; then
    copy_7zip_bundle
fi

if is_enabled "$BUNDLE_LAME"; then
    copy_lame_bundle
fi

if is_enabled "$BUNDLE_FLUIDSYNTH"; then
    copy_fluidsynth_bundle
fi

if is_enabled "$BUNDLE_GREASEWEAZLE"; then
    GREASEWEAZLE_ENTRY="$PYINSTALLER_BUILD_DIR/gw_entry.py"
    cat > "$GREASEWEAZLE_ENTRY" <<'PY'
import sys
from greaseweazle.cli import main

if __name__ == "__main__":
    sys.exit(main())
PY
    "$VENV_DIR/bin/python" -m PyInstaller \
        --noconfirm \
        --clean \
        --console \
        --onefile \
        --name gw \
        --distpath "$PYINSTALLER_BUILD_DIR/gw-dist" \
        --workpath "$PYINSTALLER_BUILD_DIR/gw-work" \
        --specpath "$PYINSTALLER_BUILD_DIR/gw-spec" \
        --collect-all greaseweazle \
        "$GREASEWEAZLE_ENTRY"
    install -Dm755 "$PYINSTALLER_BUILD_DIR/gw-dist/gw" "$APPDIR/usr/bin/gw"
    ln -sf gw "$APPDIR/usr/bin/greaseweazle"
fi

install -Dm644 "$APP_ICON_PNG" "$APPDIR/$APP_ID.png"
install -Dm644 "$APP_ICON_PNG" "$APPDIR/.DirIcon"
install -Dm644 "$APP_ICON_PNG" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
install -Dm644 "$ROOT_DIR/LICENSE" "$APPDIR/usr/share/doc/$APP_ID/LICENSE"
install -Dm644 "$ROOT_DIR/NOTICE" "$APPDIR/usr/share/doc/$APP_ID/NOTICE"
install -Dm644 "$ROOT_DIR/README.md" "$APPDIR/usr/share/doc/$APP_ID/README.md"
install -Dm644 "$ROOT_DIR/CHANGELOG.md" "$APPDIR/usr/share/doc/$APP_ID/CHANGELOG.md"
install -Dm644 "$APP_METAINFO" "$APPDIR/usr/share/metainfo/$APP_ID.appdata.xml"

cat > "$APPDIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$DISPLAY_NAME
Comment=Disklavier preservation and preparation workstation
Exec=$APP_BIN
Icon=$APP_ID
Categories=AudioVideo;Audio;
Terminal=false
EOF
install -Dm644 "$APPDIR/$APP_ID.desktop" "$APPDIR/usr/share/applications/$APP_ID.desktop"

cat > "$APPDIR/AppRun" <<EOF
#!/usr/bin/env bash
set -e
APPDIR="\$(dirname "\$(readlink -f "\$0")")"
export PATH="\$APPDIR/usr/bin:\$PATH"
export LD_LIBRARY_PATH="\$APPDIR/usr/lib:\${LD_LIBRARY_PATH:-}"
if [[ -z "\${APS_MIDI_PREP_SOUNDFONT:-}" ]]; then
    for soundfont in "\$APPDIR/usr/share/aps-midi-prep-tool/soundfonts/default.sf2" "\$APPDIR/usr/share/aps-midi-prep-tool/soundfonts/default.sf3"; do
        if [[ -f "\$soundfont" ]]; then
            export APS_MIDI_PREP_SOUNDFONT="\$soundfont"
            break
        fi
    done
fi
if [[ -z "\${QT_QPA_PLATFORM:-}" ]]; then
    export QT_QPA_PLATFORM=xcb
fi
exec "\$APPDIR/usr/bin/$APP_BIN" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

if [[ ! -x "$APPIMAGETOOL" ]]; then
    download_appimagetool
fi

APPIMAGETOOL_ARGS=()
if command -v appstreamcli >/dev/null 2>&1; then
    # Validate locally: homepage availability must not decide whether a build
    # succeeds. appimagetool otherwise repeats validation with network checks.
    appstreamcli validate --no-net "$APP_METAINFO"
    APPIMAGETOOL_ARGS+=(--no-appstream)
fi

rm -f "$APPIMAGE_PATH" "$CHECKSUM_PATH"
ARCH="$APPIMAGE_ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "${APPIMAGETOOL_ARGS[@]}" "$APPDIR" "$APPIMAGE_PATH"

chmod +x "$APPIMAGE_PATH"
(
    cd "$OUT_DIR"
    sha256sum "$(basename "$APPIMAGE_PATH")" > "$(basename "$CHECKSUM_PATH")"
)
echo "Built $APPIMAGE_PATH"
echo "Wrote $CHECKSUM_PATH"
