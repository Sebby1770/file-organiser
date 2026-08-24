#!/bin/zsh
# Double-click this file after installing Python 3. Opens the app — no commands to type.
cd "$(dirname "$0")/.."
if command -v python3 >/dev/null 2>&1; then
  exec python3 -m file_organiser
fi
osascript -e 'display alert "File Organiser" message "Python 3 is required. Install it from python.org, then double-click this file again."'
exit 1
