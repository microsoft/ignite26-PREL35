---
name: lab-walkthrough
description: Create or refresh an isolated Python walkthrough runner for the entire Ignite PREL35 lab, using instructions/skillable/content.md as the source of truth. Use when asked to generate the lab runner, automate the lab instructions, create an end-to-end lab walkthrough, or refresh/run the walkthrough after instructions change. Cover Labs 0-4 and the temporary hosted evidence-agent exercise, with readable terminal output and real human checkpoints.
---

# Create the lab walkthrough

Generate or refresh the source-mapped flow in `tests/e2e/run_lab.py` on invocation.
Reuse `tests/e2e/walkthrough_console.py` for presentation. Do not start a live lab run merely
because the user requests generation. Generate and validate the runner first;
execute Azure commands only when the user explicitly requests a live run.

## Keep the runner isolated

- Put all new runner code and runner-specific checks under `tests/e2e/`.
  Name the entry point `run_lab.py`, not `test_*.py`, so pytest does not collect it.
- Use only the standard library and local helpers under `tests/e2e/`.
  Do not import `scripts`, `src`, pytest, conftest, or skill-bundled helpers.
  Call existing lab commands as subprocesses instead of reimplementing agents,
  evaluation, intake, Web IQ setup, or deployment.
- Keep formatting in `walkthrough_console.py`, independent of the guide's text,
  line numbers, command inventory, hashes, targets, and lab execution. Pass current
  step metadata into the helper. Preserve it when refreshing the flow from
  `content.md`; change presentation code only when explicitly requested or needed
  for compatibility. Create the helper only if it is missing, not on every refresh.
- Do not change `pyproject.toml`, `uv.lock`, pytest configuration, application code,
  infrastructure, or the instructions to accommodate the runner.
- Keep any additional runner-specific files in the same directory. Make removing
  `tests/e2e/` sufficient to remove the harness, without unregistering entry points.
  Preserve unrelated files already there.
- Do not install dependencies, scaffold another project, or choose another Python
  runtime. Use the existing repository environment and tools.

## Read the source before generating

1. Read the entire current `instructions/skillable/content.md`, including the
   temporary hosted-agent exercise and optional exercises. Do not generate from
   this skill's coverage table alone.
2. Read `pyproject.toml` and the command implementations needed to understand
   arguments, interactions, exit codes, and artifacts. In particular inspect
   `scripts/set_gateway.py`, `scripts/configure_evidence_agent.py`,
   `src/evaluate.py`, `src/redteam.py`, `src/intake.py`, and `src/decide.py`.
3. Inventory every PowerShell and Kusto code block, inline command, repetition
   requested in prose, manual file edit, portal check, and intentional failure.
   Record the source heading and line range for each action.
4. Embed that inventory in the generated runner. Account for each source action
   as an executable command, equivalent Python operation, human checkpoint, or
   explicitly optional action. Never silently omit a command.
5. Embed the instructions' SHA-256. Refuse live execution if the guide has changed
   since generation, explaining that the runner must be refreshed.
6. If the guide and implementation disagree, report the discrepancy. Do not
   silently rewrite the guide, invent flags, weaken gates, or claim full coverage.
   Leave unresolved executable actions blocked rather than treating them as done.

## Runner interface and output

Support these invocations from the repository root:

```text
uv run --no-sync tests/e2e/run_lab.py --plan
uv run --no-sync tests/e2e/run_lab.py --execute --resource-group <lab-resource-group>
uv run --no-sync tests/e2e/run_lab.py --execute --resource-group <lab-resource-group> --optional tuning
```

Default to plan mode when no execution flag is supplied. Make `--plan` and
`--execute` mutually exclusive. Plan mode must not execute subprocesses, sign in,
write files, pause for input, open applications, sleep, or contact Azure.

Use the runner's file location to derive the repository root, not the current
working directory or the VM-specific `cd` path in the guide. Resolve Skillable
tokens from explicit arguments and the lab's existing `.env` after setup. Never
pass unresolved `@lab...` tokens to a command, guess resource names, or print the
contents of `.env`. Allow a plan to show unresolved values; require them before
executing the affected action.

Minimize prompts: retain one startup authorization, required sign-ins/secrets and
real analyst inputs, completion of an explicitly selected prompt edit, and the
specific confirmation before the real model-create policy test. Do not pause
just to acknowledge explanatory text, read a report, inspect a portal, or run a
Kusto query. Display those actions and mark them `REVIEW NEEDED`; never claim
they were human-verified. Default optional exercises to skipped; use repeatable
`--optional` choices (`editor`, `colors`, `tuning`) to enable
them without extra yes/no prompts.

Format both plan and live output with a visible hierarchy, not just colored
outcome prefixes. Use ASCII separators and whitespace around the run banner,
each lab heading, each numbered step, and the final summary. Show the execution
mode prominently. Give each step labeled source/section metadata and a separate,
indented command block or action description. Keep commands and Kusto queries
copyable and complete; wrap explanatory prose to the terminal width, without
truncating content. Keep ordinary child output between the step preview and result.

Style headings, commands, checkpoint prompts, warnings, propagation waits, and
outcomes so the current action remains obvious during a long run. Show elapsed
time with each live result. Distinguish:
`PASS`, `EXPECTED FAIL`, `OBSERVED FAIL`, `REVIEW NEEDED`, `SKIPPED OPTIONAL`,
`NOT RUN`, and `ERROR`.
Use automatic ANSI colors on supported interactive terminals, including Windows
PowerShell (enable virtual-terminal processing through the standard library when
needed). Fall back to equally structured plain text for redirected output,
`TERM=dumb`, `NO_COLOR`, `UV_NO_COLOR`, or unsupported consoles. Keep child-only
color demonstrations scoped to children; they must not disable runner formatting.
Do not add a terminal UI dependency or import the application's console helper.

Use this shape as a presentation guide, keeping actual source titles and values:

```text
========================================================================
Lab 1 - AgentOps
========================================================================

------------------------------------------------------------------------
Step 15/93 | Run the release gate
------------------------------------------------------------------------
  Source: instructions/skillable/content.md:306-308
  Section: Baseline release gate
  Command:
    > uv run src/evaluate.py --foundry
  Command output:
  ...live child output...

  [PASS] Run the release gate (42.1s)
```

Stream ordinary child-process output as it runs; do not make a long evaluation or
deployment appear frozen. Leave stdin/stdout/stderr attached for interactive
commands, including Azure login, analyst decisions, and hidden Web IQ key input.
End with a per-lab report: outcome counts, elapsed time, every action's title and
status, failure reasons, source-linked pending reviews, and actions not run after
a stop. Print this report on success, error, and cancellation. An automated run
may finish with pending reviews, but must explicitly state they remain unverified.
Do not report a completed lab after a cancelled checkpoint or unexpected error.

## Execute commands safely

- Use argument lists with `subprocess`, `shell=False`, and the repository root as
  `cwd`. Resolve installed executables portably, including Windows CLI launchers.
  Do not concatenate PowerShell into shell strings or execute Markdown wholesale.
- Translate `Copy-Item` with `shutil.copyfile`, environment-variable changes with
  scoped child environments, and JSON edits with the standard library. Explain
  these equivalents in the plan so their source commands remain accounted for.
- Use `git --no-pager diff --no-index` for prompt comparisons. An exit code of 1
  means differences were found, not a command failure.
- Before applying prompt variants, check for existing edits and preserve the
  approved starting bytes. Refuse to overwrite unrelated user work. Represent
  the guide's prompt rollbacks with a safe restoration of the verified starting
  prompt; never use a broad checkout, reset, clean, or recursive deletion.
- Treat the guide's `cd` as the runner's working-directory setup. Enable optional
  `code .` and color-setting demonstrations only through `--optional`; do not open editors
  or alter terminal preferences silently.
- Replace `curl.exe ... -o NUL` probes with equivalent standard-library HTTP
  requests if needed for portability. Assert the observed HTTP statuses, including
  401 without a token and 404 for an unpublished agent. Keep bearer tokens only
  in memory; validate the lab gateway HTTPS target before attaching credentials.
- Capture token acquisition output privately. Never echo tokens, keys,
  authorization headers, hidden input, or credential-bearing command arguments.
  Do not persist subprocess transcripts by default.
- Honor the guide's gateway propagation waits (about a minute), including after
  routing and token-limit changes and restoration. Show progress during waits.
  Do not hide propagation failures behind unbounded retries.
- Stop on unexpected failures, missing tools, wrong targets, or cancellation.
  Preserve the child's useful non-secret diagnostics and exit nonzero.
- Keep the environment disposable but do not assume authority to create it.
  Do not provision resources, assign roles, delete resources, reset the VM, or run
  the repository's deployment/setup scripts beyond the learner commands in the
  guide. Use the documented `azd deploy` for the hosted exercise.

## Cover the entire story

Treat this as a coverage checklist, not a frozen command manifest:

| Section | Required behavior |
| --- | --- |
| Lab 0 | Use real sign-in; run setup and agent publication; run `configure-evidence-agent` with real hidden key input if requested, then `azd deploy`; list the portal tour for review; investigate ALT-1001 twice; validate the 401 and 404 gateway probes. |
| Lab 1 | Run the Foundry-backed gate; compare/apply the concise prompt; publish and observe its failed gate; restore/publish/retest; evaluate gpt-5-nano and restore the approved model; inspect Evidence Packs and traces. Offer the optional prompt-tuning exercise and its repeated gate runs, then restore. |
| Lab 2 | Run the Foundry-backed red team and individual attacks; list guardrail inspection for review; compare/apply/publish the helpful prompt; rerun the documented attacks; enable Prompt Shields, wait, rerun red team and gate; restore/publish/retest and list report reviews. |
| Lab 3 | Observe initial intake refusal; set only the data policy's `enforce` field to true; regenerate gate/red-team evidence; invoke the hosted evidence agent with the guide's PII-free scenario; run both real analyst decisions (ALT-1008, ALT-1014) interactively; run intake again; demonstrate Azure Policy denial only after a specific confirmation. |
| Lab 4 | Measure baseline cost; enable routing, wait and reevaluate, then disable routing; lower token limit, wait and investigate with scoped `OPENAI_LOG`; restore the limit and environment; list retry observations, the cost-attribution query, and wrap-up for review. |

Expose optional sections explicitly in CLI help. Record an unselected optional
section as `SKIPPED OPTIONAL`, never as `PASS`. Preserve file/report reviews,
portal steps, Kusto queries, and the final wrap-up as unverified review items.
Display the exact source Kusto query for the user to run in the portal, without
claiming it was executed. Do not pretend to automate or verify a portal check.

## Distinguish intentional failures from broken execution

Do not use a blanket "exit 1 is acceptable" rule.

- For full evaluation/red-team runs, require a newly produced Evidence Pack,
  not an old folder. Read its manifest/report and match the verdict to the process
  result before classifying a negative outcome.
- Expect the concise-prompt release gate to fail. If it unexpectedly passes,
  report that the teaching outcome was not reproduced; do not fabricate failure.
- Treat smaller-model scores and documented intermittent attack successes as
  observations when the guide allows either result. Show the real verdict.
  Do not silently repeat billable runs until a desired verdict appears.
- For individual attacks, inspect the actual attack outcome rather than requiring
  a full-suite Evidence Pack that this command does not produce.
- For initial intake refusal, require completed governance checks and the
  refusal diagnostic, not just exit code 1. Treat later intake failure as blocked
  approval; do not edit approval records or thresholds to make it pass.
- Before the unapproved-model command, warn that it sends a real create request
  which could create a billable deployment if policy is misconfigured. Require a
  dedicated human confirmation even during an otherwise approved live run.
  Classify `EXPECTED FAIL` only when the response confirms a policy denial.
  Authentication, authorization, quota, and network errors do not prove policy.
  If creation succeeds, stop and report it; do not delete resources automatically.

## Preserve real human participation

Pause only for required Azure/browser sign-ins, selected prompt edits, and real
Web IQ key input. Show portal observations and the Web IQ billing explanation
without acknowledgment prompts. Never enter a test key or manufacture
an account, a decision, an analyst, a reason, or an approval.

Run `src/decide.py` with inherited terminal input. Explain the guide's approve
and overrule exercises, but let the signed-in person make both decisions and
provide their own reason. Never pipe canned answers into either command.

Require an interactive terminal for live execution. Let the user cancel a
required checkpoint or interrupt execution; print an incomplete report with
nonzero exit and mark every remaining action `NOT RUN`.

Track runner-owned transient prompt and environment changes. Restore those local
changes on failure or cancellation without erasing preexisting edits. Preserve
Evidence Packs and decisions. On success retain the guide's intended final
state, including enforced data policy and approval. If cloud state remains
partway through an exercise, print the precise recovery commands; do not conceal
it, republish agents automatically, or reset all lab state.

## Validate and hand off

1. Validate syntax and exercise `--help` and `--plan`. Verify that planning makes
   no subprocess calls or local/cloud changes and covers every inventoried action.
2. Check orchestration offline with temporary files and fake commands: command
   ordering, source drift, expected versus genuine failures, existing user edits,
   cancelled checkpoints, deferred manual reviews, optional opt-ins, final reports,
   and local restoration. Keep durable runner checks in
   `tests/e2e/`; do not run project-wide test suites or live lab commands.
   Check terminal and redirected formatting, color opt-outs, readable narrow
   terminals, and separation of child output and results. Preserve private token
   capture and inherited terminal input; never format or echo hidden credentials.
3. Do not claim that offline checks prove Azure behavior. Report any source/code
   discrepancies and remaining manual checks plainly.
4. Report the generated file, plan/live invocations, and the billable/interactive
   nature of live execution. Do not execute it against Azure without a subsequent
   explicit request to do so.
