"""Build the Skillable instructions file from the lab READMEs.

Skillable reads one Markdown file per lab profile. This joins the labs in order, one Skillable page each,
turns repository links into plain text (learners have the repository on the VM, not on GitHub) and swaps the
placeholders for Skillable replacement tokens. Run it after changing any lab README, and commit the result.

Usage: python scripts/build_skillable.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABS = sorted((ROOT / "instructions").glob("lab-*/README.md"))
OUT = ROOT / "instructions" / "skillable" / "content.md"

TOKENS = {
    "<your-resource-group>": "@lab.CloudResourceGroup(1).Name",
    "`<lab-username>`": "++@lab.CloudPortalCredential(User1).Username++",
    "`<lab-password>`": "++@lab.CloudPortalCredential(User1).Password++",
}
RELATIVE_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://)[^)]*\)")


def convert(markdown: str) -> str:
    markdown = RELATIVE_LINK.sub(r"\1", markdown)
    for placeholder, token in TOKENS.items():
        markdown = markdown.replace(placeholder, token)
    return markdown.strip()


assert convert("See [the file](../../a.md) and [docs](https://x.y).") == "See the file and [docs](https://x.y)."
assert convert("Use `<lab-password>`") == "Use ++@lab.CloudPortalCredential(User1).Password++"

if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    pages = [convert(lab.read_text(encoding="utf-8")) for lab in LABS]
    OUT.write_text("\n\n===\n\n".join(pages) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {OUT.relative_to(ROOT)} from {len(pages)} labs")
