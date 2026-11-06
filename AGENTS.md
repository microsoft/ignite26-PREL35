# Agent instructions

## Lab console output

- Every lab command that prints to the terminal uses readable formatting, not raw JSON dumps: short headings,
  aligned columns, and the result a learner needs first. Offer a `--json` flag for the full record.
- Use color through `scripts/console_output.py` (`color(text, name)`). It already turns color off when output is
  piped, when `NO_COLOR` is set, or when the terminal is `dumb`. Do not write ANSI codes directly.
- Show what a learner is paying for: the time and cost of each call, and why a model is called again.
