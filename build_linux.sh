#!/usr/bin/env bash
# Builds the Linux app: release/linux/ForeverAI-Linux/ForeverAI-Linux. With --package it also writes
# release/ForeverAI-<version>-Linux-x86_64.tar.gz, the file published on GitHub Releases (.github/workflows/release.yml).
# Requirements: Python 3.12+ with Tk (tkinter) and the packages from companion/requirements.txt and PyInstaller
#   python3 -m venv companion/.venv
#   companion/.venv/bin/python -m pip install -r companion/requirements.txt pyinstaller -c companion/constraints.txt
# The binary runs on distributions with the same or a newer glibc than the build machine.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${PYTHON:-$root/companion/.venv/bin/python}"
[[ -x "$py" ]] || { echo "Missing companion/.venv - see the build section in README.md" >&2; exit 1; }
"$py" -c "import tkinter" 2>/dev/null || { echo "This Python has no tkinter - install Tk (e.g. python3-tk)" >&2; exit 1; }
name=ForeverAI-Linux
# a running app reads its code from these files: replacing them under it breaks the parts it loads later
exe="$root/release/linux/$name/$name"
for p in /proc/[0-9]*/exe; do
  if [[ "$(readlink "$p" 2>/dev/null || true)" == "$exe"* ]]; then
    pid="${p#/proc/}"
    echo "ForeverAI is running from $exe (pid ${pid%/exe}): close it before building" >&2
    exit 1
  fi
done
version="$("$py" -c 'import re,sys; print(re.search(r"__version__ = \"(.+?)\"", open(sys.argv[1]).read()).group(1))' \
  "$root/companion/foreverai/__init__.py")"

data=(--add-data "$root/addon/ForeverAI:addon/ForeverAI" --add-data "$root/assets:assets")
# starter knowledge base (public wiki / Blizzard data): your own fresher one, else the one in the repository
for kb in "$root/companion/data/kb.sqlite" "$root/seed/kb.sqlite"; do
  if [[ -f "$kb" ]]; then data+=(--add-data "$kb:seed"); break; fi
done
# a standalone Python (uv, python-build-standalone) finds its Tcl/Tk through the interpreter's RPATH, which PyInstaller
# does not follow: bundle the libraries _tkinter cannot resolve on its own
tkso="$("$py" -c 'import _tkinter; print(_tkinter.__file__)')"
libdir="$("$py" -c 'import sys; print(sys.base_prefix)')/lib"
standalone_tk=
for lib in $(ldd "$tkso" | awk '/not found/ {print $1}'); do
  if [[ -f "$libdir/$lib" ]]; then data+=(--add-binary "$libdir/$lib:."); standalone_tk=1; fi
done

cd "$root/companion"
"$py" -m PyInstaller --noconfirm --clean --windowed --name "$name" \
  --distpath "$root/release/linux" --workpath "$root/build/linux" --specpath "$root/build/linux" \
  --collect-all curl_cffi \
  "${data[@]}" foreverai_app.py
echo "Built: $root/release/linux/$name/$name (ForeverAI $version)"
if [[ -n "$standalone_tk" ]]; then
  echo "Warning: $py is a standalone Python; its Tk has no font rendering, so the window shows bitmap fonts." >&2
  echo "  Build with the distribution's Python and Tk (e.g. PYTHON=/path/to/venv-from-/usr/bin/python3)." >&2
fi

if [[ "${1:-}" == "--package" ]]; then
  archive="$root/release/ForeverAI-$version-Linux-x86_64.tar.gz"
  tar -C "$root/release/linux" -czf "$archive" "$name"  # tar keeps the executable bit
  echo "Package: $archive"
fi
