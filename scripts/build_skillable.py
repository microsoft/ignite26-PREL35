"""Build the Skillable instructions file from the lab READMEs.

Skillable reads one Markdown file per lab profile. This joins the introduction and the labs in order, one Skillable page each,
adds the Skillable welcome header, turns repository links into plain text (learners have the repository on the VM, not on
GitHub), turns screenshots into Skillable images and swaps the placeholders for Skillable replacement tokens. Run it after
changing any lab README, and commit the result. Upload any new screenshot in instructions/images to the lab profile.

Usage: python scripts/build_skillable.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The introduction and agenda first, then the labs in order.
LABS = [ROOT / "instructions" / "introduction.md", *sorted((ROOT / "instructions").glob("lab-*/README.md"))]
OUT = ROOT / "instructions" / "skillable" / "content.md"

HEADER = """@lab.Title

## Welcome to your lab environment

Sign in to the virtual machine with the password +++@lab.VirtualMachine(Win11-Pro-Base).Password+++

**Note:** Text formatted as +++example+++ is type text. Click it to type it into the virtual machine, so there are no typing errors.

---"""
TOKENS = {
    "<your-resource-group>": "@lab.CloudResourceGroup(PREL35).Name",
    "<lab-username>": "@lab.CloudPortalCredential(User1).Username",
    "<lab-access-token>": "@lab.CloudPortalCredential(User1).AccessToken",
    "<azure-portal-url>": "@lab.CloudPortal.Url",
    "<foundry-name>": "@lab.CloudResourceTemplate(PREL35Ignite).Outputs[FOUNDRY_NAME]",
    "<apim-name>": "@lab.CloudResourceTemplate(PREL35Ignite).Outputs[APIM_NAME]",
    "<apim-gateway-url>": "@lab.CloudResourceTemplate(PREL35Ignite).Outputs[APIM_GATEWAY_URL]",
    "<appinsights-name>": "@lab.CloudResourceTemplate(PREL35Ignite).Outputs[APPLICATIONINSIGHTS_NAME]",
}
IMAGE = re.compile(r"!\[[^\]]*\]\(\.\./images/([^)]+)\)")
RELATIVE_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\((?!https?://)[^)]*\)")


def convert(markdown: str) -> str:
    markdown = RELATIVE_LINK.sub(r"\1", markdown)
    markdown = IMAGE.sub(r"!IMAGE[\1](instructions361796/\1)", markdown)
    for placeholder, token in TOKENS.items():
        markdown = markdown.replace(placeholder, token)
    return markdown.strip()


def build() -> str:
    pages = [convert(lab.read_text(encoding="utf-8")) for lab in LABS]
    return HEADER + "\n\n" + "\n\n===\n\n".join(pages) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT.relative_to(ROOT)} from {len(LABS)} pages")
