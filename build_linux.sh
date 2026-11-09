#!/usr/bin/env bash
# Builds dist/linux/ForeverAI/ForeverAI (Linux).
# Requirements: Python 3.12+ with Tk (tkinter) and the packages from companion/requirements.txt and PyInstaller
#   python3 -m venv companion/.venv
#   companion/.venv/bin/python -m pip install -r companion/requirements.txt pyinstaller -c companion/constraints.txt
# The binary runs on distributions with the same or a newer glibc than the build machine.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$root/companion/.venv/bin/python"
[[ -x "$py" ]] || { echo "Missing companion/.venv - see the build section in README.md" >&2; exit 1; }
"$py" -c "import tkinter" 2>/dev/null || { echo "This Python has no tkinter - install Tk (e.g. python3-tk)" >&2; exit 1; }

data=(--add-data "$root/addon/ForeverAI:addon/ForeverAI" --add-data "$root/assets:assets")
# optional starter knowledge base (public wiki / Blizzard data): your own, else the one in the Windows release
for kb in "$root/companion/data/kb.sqlite" "$root/release/ForeverAI/_internal/seed/kb.sqlite"; do
  if [[ -f "$kb" ]]; then data+=(--add-data "$kb:seed"); break; fi
done
# a standalone Python (uv, python-build-standalone) finds its Tcl/Tk through the interpreter's RPATH, which PyInstaller
# does not follow: bundle the libraries _tkinter cannot resolve on its own
tkso="$("$py" -c 'import _tkinter; print(_tkinter.__file__)')"
libdir="$("$py" -c 'import sys; print(sys.base_prefix)')/lib"
for name in $(ldd "$tkso" | awk '/not found/ {print $1}'); do
  if [[ -f "$libdir/$name" ]]; then data+=(--add-binary "$libdir/$name:."); fi
done

cd "$root/companion"
"$py" -m PyInstaller --noconfirm --clean --windowed --name ForeverAI \
  --distpath "$root/dist/linux" --workpath "$root/build/linux" --specpath "$root/build/linux" \
  --collect-all curl_cffi \
  "${data[@]}" foreverai_app.py
echo "Built: $root/dist/linux/ForeverAI/ForeverAI"
