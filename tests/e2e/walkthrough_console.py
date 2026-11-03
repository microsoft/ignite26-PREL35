"""Console presentation for the isolated lab walkthrough; standard library only."""

import os
import shutil
import sys
import textwrap


class Console:
    STYLES = {
        "heading": "1;36", "step": "1;34", "command": "1", "muted": "90",
        "PASS": "1;32", "MANUAL": "1;36", "EXPECTED FAIL": "1;33",
        "OBSERVED FAIL": "1;33", "SKIPPED OPTIONAL": "90", "ERROR": "1;31",
        "PLAN": "36", "NOTE": "33", "CHECKPOINT": "1;35", "WAIT": "36",
        "REVIEW NEEDED": "1;35", "NOT RUN": "90",
    }

    def __init__(self):
        self.windows_ansi = None

    def supports_color(self):
        if (not sys.stdout.isatty() or os.environ.get("TERM") == "dumb"
                or "NO_COLOR" in os.environ or "UV_NO_COLOR" in os.environ):
            return False
        if sys.platform != "win32":
            return True
        if self.windows_ansi is None:
            # Windows PowerShell may need virtual-terminal processing enabled.
            try:
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetStdHandle.argtypes = [wintypes.DWORD]
                kernel.GetStdHandle.restype = wintypes.HANDLE
                kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                handle = kernel.GetStdHandle(-11)
                mode = wintypes.DWORD()
                self.windows_ansi = bool(
                    kernel.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel.SetConsoleMode(handle, mode.value | 0x0004)
                )
            except (AttributeError, OSError):
                self.windows_ansi = False
        return self.windows_ansi

    def styled(self, text, style):
        code = self.STYLES.get(style)
        return f"\033[{code}m{text}\033[0m" if code and self.supports_color() else text

    @property
    def width(self):
        return max(32, min(100, shutil.get_terminal_size((100, 24)).columns))

    def message(self, text, status="", *, style="", indent=2):
        text = f"[{status}] {text}" if status else str(text)
        for line in text.split("\n"):
            wrapped = textwrap.fill(
                line, width=self.width, initial_indent=" " * indent,
                subsequent_indent=" " * (indent + 2), break_long_words=False,
                break_on_hyphens=False,
            )
            print(self.styled(wrapped, style or status), flush=True)

    def heading(self, text, *, character="=", style="heading"):
        print(flush=True)
        self.message(character * self.width, style=style, indent=0)
        self.message(text, style=style, indent=0)
        self.message(character * self.width, style=style, indent=0)

    def block(self, label, text, *, command=False):
        self.message(label + ":", style="muted")
        for line in text.splitlines():
            print(("    > " if command else "    ") + self.styled(line, "command"), flush=True)

    def step(self, index, total, step, heading, preview, *, source):
        self.heading(f"Step {index:02}/{total:02} | {step.title}", character="-", style="step")
        self.message(f"Source: {source}:{step.start}-{step.end}", style="muted")
        self.message("Section: " + heading.replace(r"\(", "(").replace(r"\)", ")"), style="muted")
        if step.kind in ("command", "logged-investigation"):
            self.block("Command", preview, command=True)
        else:
            self.message("Action: " + preview)
        if step.expect in ("pack-fail", "intake-deny", "policy-deny"):
            self.message("An intentional failure must be verified, not just a nonzero exit.", "NOTE")
        if step.optional:
            self.message(f"Optional group: {step.optional} (enable with --optional {step.optional})", "NOTE")
