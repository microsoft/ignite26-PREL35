# Source

The lab code that learners run. Commands run from the repository root with `uv run`.

| Path | What it does | Lab |
| --- | --- | --- |
| [agents/](agents) | Prompt agent instructions (`triage`, `reviewer`, `triage_systemone`), prompt variants used in Labs 1 and 2, and the hosted evidence agent in [agents/evidence/](agents/evidence) | 0 |
| [investigate.py](investigate.py) | Runs one alert through the triage and reviewer agents, through the API Management gateway | 0, 4 |
| [evaluate.py](evaluate.py) | Release gate: scores the golden alerts and writes an Evidence Pack | 1 |
| [redteam.py](redteam.py) | Runs the attacks in [evals/attacks.json](../evals/attacks.json) against the triage agent | 2 |
| [intake.py](intake.py) | Checks a use case against its evidence and records the approval | 3 |
| [decide.py](decide.py) | Records a named analyst's decision on an alert | 3 |
| [decisions/](decisions) | Decision-1 triage, review, evaluation and comparison with the GPT agents | 4 |

Setup commands (`setup-lab`, `create-agents`, `set-gateway` and others) are in [scripts/](../scripts) and are
listed in [pyproject.toml](../pyproject.toml).
