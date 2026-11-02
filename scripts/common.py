"""Shared command execution and local lab settings."""
import functools
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.console_output import color
from scripts.setup_webiq import ENV_ENTRY, SetupError, save_settings

ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger("lab")


def message(text, name="cyan", *, error=False):
    stream = sys.stderr if error else sys.stdout
    print(color(text, name, stream=stream), file=stream)
    LOGGER.info(text)


def command(args, *, show=False, allow_failure=False):
    executable = shutil.which(str(args[0])) or str(args[0])
    try:
        result = subprocess.run(
            [executable, *(str(arg) for arg in args[1:])], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except FileNotFoundError:
        raise SetupError(f"{args[0]} is not installed or is not on PATH.") from None
    if show:
        for text, error in ((result.stdout, False), (result.stderr, True)):
            if text:
                message(text.rstrip(), "yellow" if error else "cyan", error=error)
    if result.returncode and not allow_failure:
        # Never echo command arguments or captured responses: either can contain credentials.
        raise SetupError(f"{' '.join(args[:3])} failed (exit code {result.returncode}).")
    return result


def az_json(*args):
    result = command(["az", *args, "--output", "json"])
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise SetupError("Azure CLI did not return valid JSON. Check your sign-in and resource access.") from None


def read_settings(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SetupError("A regular .env file is required; refusing to overwrite a linked .env file.")
    settings = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if match := ENV_ENTRY.match(line):
                value = match[2].strip()
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        raise SetupError(f"Invalid quoting for .env setting {match[1]}.") from None
                elif len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                settings[match[1]] = value
    return settings


def write_settings(path, updates):
    read_settings(path)
    for key, value in updates.items():
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise SetupError(f"Invalid value for setting {key}.")
    save_settings(path, updates)


def check_target(current, expected, keys, label):
    for key in keys:
        if current.get(key) and str(current[key]).casefold() != str(expected[key]).casefold():
            raise SetupError(f"{label} targets a different lab ({key}). Its settings have not been changed.")


def cli(action):
    @functools.wraps(action)
    def wrapped(argv=None):
        try:
            return action(argv) or 0
        except (KeyboardInterrupt, EOFError):
            message("Setup cancelled. Rerun the command to finish.", "yellow", error=True)
        except SetupError as error:
            message(str(error), "red", error=True)
        return 1
    return wrapped
