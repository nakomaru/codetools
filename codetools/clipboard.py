import hashlib

import pyperclip


def read() -> str | None:
    """Clipboard text, or None when the clipboard is unavailable (another app may hold it open)."""
    try:
        return pyperclip.paste() or ""
    except Exception:
        return None


def write(text: str) -> bool:
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def fingerprint(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8", errors="replace")).hexdigest()
