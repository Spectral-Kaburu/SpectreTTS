"""
SpectreTTS - Selection Grabber
-------------------------------
Grabs the currently highlighted (PRIMARY selection) text.
Works on both X11 and XWayland apps, since GTK/Qt apps still
populate the X11 PRIMARY selection buffer even under Wayland.
"""

import subprocess


def get_selected_text() -> str:
    """
    Returns the current PRIMARY X selection (whatever is highlighted).
    Returns empty string if nothing is selected or xclip fails.
    """
    try:
        result = subprocess.run(
            ["xclip", "-selection", "primary", "-o"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_clipboard_text() -> str:
    """
    Returns the CLIPBOARD selection (last Ctrl+C'd text) as a fallback.
    """
    try:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_text_to_read() -> str:
    """
    Primary entry point: try highlighted selection first.
    Falls back to clipboard only if selection is empty
    (handles the case where focus moved and selection got cleared).
    """
    text = get_selected_text()
    if text:
        return text
    return get_clipboard_text()


if __name__ == "__main__":
    print("Highlight some text anywhere, then press Enter here...")
    input()
    text = get_text_to_read()
    if text:
        print(f"Captured ({len(text)} chars):\n{text}")
    else:
        print("Nothing selected or xclip unavailable.")
        