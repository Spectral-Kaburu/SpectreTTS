#!/usr/bin/env bash
#
# SpectreTTS - Hotkey Registration
# ----------------------------------
# Registers Ctrl+Alt+R as a GNOME custom keybinding that calls
# hotkey_trigger.py. Works under both X11 and Wayland sessions
# since GNOME's compositor owns the global shortcut, not our script.
#
# Safe to re-run — checks for existing SpectreTTS binding first.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRIGGER_SCRIPT="$SCRIPT_DIR/hotkey_trigger.py"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: Could not find Python at $PYTHON_BIN"
    echo "Please create a virtual environment at .venv first."
    exit 1
fi

COMMAND="$PYTHON_BIN $TRIGGER_SCRIPT"
KEYBINDING="<Ctrl><Alt>r"
NAME="SpectreTTS Read Selection"

BASE_PATH="org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_BASE="$BASE_PATH.custom-keybinding"
FULL_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/customspectretts/"

# Get existing custom keybindings list
EXISTING=$(gsettings get "$BASE_PATH" custom-keybindings)

# Add customspectretts slot if not already in the list
if ! echo "$EXISTING" | grep -q "customspectretts"; then
    echo "Registering SpectreTTS in GNOME keybindings list..."
    if [ "$EXISTING" = "@as []" ] || [ -z "$EXISTING" ]; then
        NEW_LIST="['$FULL_PATH']"
    else
        NEW_LIST=$(echo "$EXISTING" | sed "s|]$|, '$FULL_PATH']|")
    fi
    gsettings set "$BASE_PATH" custom-keybindings "$NEW_LIST"
fi

# Always update the binding details so moving project directories is handled
echo "Updating keybinding settings..."
gsettings set "$CUSTOM_BASE:$FULL_PATH" name "$NAME"
gsettings set "$CUSTOM_BASE:$FULL_PATH" command "$COMMAND"
gsettings set "$CUSTOM_BASE:$FULL_PATH" binding "$KEYBINDING"

echo "Done. Ctrl+Alt+R is now bound to: $COMMAND"
echo ""
echo "Verify in GNOME Settings > Keyboard > View and Customize Shortcuts > Custom Shortcuts"
