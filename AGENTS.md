# Agent instructions

## Lab console output

- Every lab command that prints to the terminal uses readable formatting, not raw JSON dumps: short headings,
  aligned columns, and the result a learner needs first. Offer a `--json` flag for the full record.
- Use color through `scripts/console_output.py` (`color(text, name)`). It already turns color off when output is
  piped, when `NO_COLOR` is set, or when the terminal is `dumb`. Do not write ANSI codes directly.
- Show what a learner is paying for: the time and cost of each call, and why a model is called again.

## Lab code and test boundaries

- Keep optional AI-generated walkthrough runners, their helpers, and test-only automation under `tests/e2e/`. Lab code must not depend on this harness.
- When using the walkthrough skill, generate or refresh runners only. Reuse stable helpers without modifying them.
- Give helpers standard, pytest-discoverable unit tests, even though the live runners are optional.
- Keep unit tests for `src/` lab code, including `decide.py`, under `tests/`.
- Use `test_*.py` names for unit tests so `uv run pytest` collects both helper and lab-code tests. Do not use those names for live runners or automatically execute live lab actions during unit tests.
- Keep simulated input/identity and generated test artifacts isolated under `tests/e2e/`; never write automated approvals into learner decision or approval records.
