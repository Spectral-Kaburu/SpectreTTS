"""
SpectreTTS - Selection Grabber
-------------------------------
Grabs the currently highlighted (PRIMARY selection) text.
Works on both X11 and XWayland apps, since GTK/Qt apps still
populate the X11 PRIMARY selection buffer even under Wayland.
"""

import subprocess
import logging

logger = logging.getLogger(__name__)

def get_selected_text() -> str:
    """
    Returns the current PRIMARY X selection (whatever is highlighted).
    Returns empty string if nothing is selected or xclip fails.
    """
    logger.debug("Attempting to get PRIMARY selection via xclip...")
    try:
        result = subprocess.run(
            ["xclip", "-selection", "primary", "-o"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            logger.debug(f"PRIMARY selection retrieved successfully (length: {len(text)}).")
            return text
        else:
            logger.warning(f"xclip (primary) returned non-zero code {result.returncode}: {result.stderr.strip()}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("xclip (primary) timed out after 2 seconds.")
        return ""
    except FileNotFoundError:
        logger.error("xclip command not found. Please ensure xclip is installed.")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error in get_selected_text: {e}")
        return ""

def get_clipboard_text() -> str:
    """
    Returns the CLIPBOARD selection (last Ctrl+C'd text) as a fallback.
    """
    logger.debug("Attempting to get CLIPBOARD selection via xclip...")
    try:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            logger.debug(f"CLIPBOARD selection retrieved successfully (length: {len(text)}).")
            return text
        else:
            logger.warning(f"xclip (clipboard) returned non-zero code {result.returncode}: {result.stderr.strip()}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("xclip (clipboard) timed out after 2 seconds.")
        return ""
    except FileNotFoundError:
        logger.error("xclip command not found. Please ensure xclip is installed.")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error in get_clipboard_text: {e}")
        return ""

def get_text_to_read() -> str:
    """
    Primary entry point: try highlighted selection first.
    Falls back to clipboard only if selection is empty
    (handles the case where focus moved and selection got cleared).
    """
    logger.info("Starting text capture process...")
    text = get_selected_text()
    if text:
        logger.info("Successfully captured text from PRIMARY selection.")
        return text
        
    logger.info("PRIMARY selection empty or failed. Falling back to CLIPBOARD...")
    text = get_clipboard_text()
    if text:
        logger.info("Successfully captured text from CLIPBOARD.")
        return text
        
    logger.warning("Both PRIMARY and CLIPBOARD selections yielded no text.")
    return ""

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print("Highlight some text anywhere, then press Enter here...")
    input()
    text = get_text_to_read()
    if text:
        print(f"Captured ({len(text)} chars):\n{text}")
    else:
        print("Nothing selected or xclip unavailable.")