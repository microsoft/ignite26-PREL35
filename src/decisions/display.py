"""Terminal output shared by the Decision-1 scripts: headings, colored labels and a timed list of the calls made."""
from scripts.console_output import color

DISPOSITION_COLORS = {"escalate": "red", "close_false_positive": "green", "request_info": "yellow"}


def paint(text, name=None, width=0):
    """Pad first so the columns stay aligned whether or not the terminal shows color."""
    text = f"{text:<{width}}"
    return color(text, name) if name else text


def disposition(label, width=0):
    return paint(label or "-", DISPOSITION_COLORS.get(label), width)


def verdict(label, width=0):
    return paint(label or "-", {"agree": "green", "disagree": "red"}.get(label), width)


def mark(good):
    return color("✓", "green") if good else color("✗", "red")


def usd(value):
    return "-" if value is None else f"${value:.5f}"


def heading(text):
    print("\n" + color(text, "cyan"))


def print_steps(steps, total_label="Total"):
    """What was called, in order, with the time and cost of each call. Each step has what, seconds, cost_usd, model."""
    heading("What happened")
    width = max(len(step["what"]) for step in steps)
    for number, step in enumerate(steps, 1):
        print(f"  {number}. {step['what']:<{width}}  {step['seconds']:>6.1f} s  {usd(step['cost_usd']):>9}  {step['model']}")
    print(f"     {total_label:<{width}}  {sum(s['seconds'] for s in steps):>6.1f} s  "
          f"{usd(sum(s['cost_usd'] for s in steps)):>9}")
