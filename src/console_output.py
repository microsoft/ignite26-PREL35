"""Terminal colors shared by the command-line scripts."""
import os
import sys


def color(text: str, name: str, *, stream=None) -> str:
    stream = sys.stdout if stream is None else stream
    if not stream.isatty() or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return text
    codes = {"red": 31, "green": 32, "yellow": 33, "cyan": 36}
    return f"\033[{codes[name]}m{text}\033[0m"
