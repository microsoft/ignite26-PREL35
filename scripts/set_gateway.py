"""Set a gateway switch (an API Management named value) in your lab."""
import argparse
import re

from scripts.common import ROOT, SetupError, cli, command, message, read_settings

NAMES = ("prompt-shields", "caller-identity", "model-routing", "routing-max-score", "token-limit")


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=NAMES)
    parser.add_argument("value")
    parser.add_argument("--debug", action="store_true", help="Show Azure CLI output for troubleshooting.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"(?:on|off|gateway|obo|[0-9]+)", args.value):
        parser.error("value must be on, off, gateway, obo or a nonnegative integer")
    settings = read_settings(ROOT / ".env")
    for name in ("AZURE_RESOURCE_GROUP", "APIM_NAME"):
        if not settings.get(name):
            raise SetupError(f"Missing {name}. Run uv run setup-lab first.")
    command([
        "az", "apim", "nv", "update", "--resource-group", settings["AZURE_RESOURCE_GROUP"],
        "--service-name", settings["APIM_NAME"], "--named-value-id", args.name, "--value", args.value, "--output", "none",
    ], show=args.debug)
    message(f"{args.name} = {args.value}. The gateway applies it within about a minute.", "green")


if __name__ == "__main__":
    raise SystemExit(main())
