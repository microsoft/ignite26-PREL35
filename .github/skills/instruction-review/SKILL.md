---
name: instruction-review
description: Review and improve instructions/skillable/content.md for technical correctness, clear learner actions, readable results, realistic timing, progress tracking, and verified Skillable Markdown. Use for "review the instructions", "review content.md", "improve the Skillable lab", "check lab wording", or updating attendee guidance after code changes or learner feedback. Preserve lab behavior and authored learning content; do not run live lab actions.
---

# Review the Skillable instructions

Make the guide easy to follow on the lab VM: tell learners what to do, what to look at, and what they learn. Treat the guide as teaching material, not a benchmark report, compliance document, or troubleshooting catalogue.

## Set the scope

- Target `instructions/skillable/content.md`. Honor a requested lab, section, selection, or diff; do not rewrite the entire guide for a local request.
- For a review request, report findings without editing. For an explicit improve, fix, or rewrite request, make surgical edits that preserve the lesson and behavior. Ask before removing a whole authored section, replacing its learning objectives, or changing the exercise.
- Keep code, infrastructure, test automation, and Skillable profile configuration out of scope unless separately requested. Report any necessary changes to them instead of inventing instructions that hide a mismatch.
- Do not run deployments, sign-ins, billable model calls, live lab actions, or project test suites. Do not install tools, choose runtimes, regenerate walkthroughs, publish agents, or update a Skillable profile.
- Announce the lab section being reviewed, not editor or diagnostic operations.

## Read the current evidence

1. Read the current file and its worktree diff. For a full review, read the entire guide in bounded ranges. For a section review, also read its prerequisites and directly dependent steps.
2. Inventory learner actions: commands, portal navigation, file edits, saving changes, prompts, result interpretation, deliberate failures, resets, and optional exercises. Include actions embedded in prose, not only code blocks.
3. Read the actual implementations and configuration needed to verify each changed claim. Check paths, entry points, arguments, defaults, agent names, model deployments, output labels, exit behavior, and artifact locations. Follow referenced PRs when the task supplies them.
4. Check the learner's environment rather than assuming organizer privileges: PowerShell on the Windows VM, repository working directory, predeployed resources, and sign-in established by earlier steps. Flag requirements for role assignment or organizer-only provisioning.
5. Separate evidence sources. Code establishes behavior; deployment files establish configured defaults; measured output establishes that run's results; official documentation establishes syntax and service features. Do not present a default as a verified live setting.
6. Keep unresolved facts in the author-facing review. Do not manufacture model prices, time measurements, quota guarantees, resource names, output, or Skillable tokens.

Use read-only inspection to verify commands where possible. Do not execute a learner command just because it appears harmless: imports, authentication, and `--help` can still have side effects.

Distinguish model calls from agent publication. Decision-1-only runners call an existing model deployment; running them does not create agents. A publishing command can still create a GPT prompt agent used by the explanation-writing path. Verify `scripts/create_agents.py`, the prompt files it discovers, and the selected runner before changing the guide's agent count or prerequisites.

## Apply the writing standard

### Make each action concrete

- Start with the location when needed, then an imperative action: "In PowerShell, run..." or "In Application Insights, select **Logs**."
- Keep each checkbox short and focused on one useful milestone. Combine closely related small actions when they complete the same task.
- Use the repository's Skillable syntax `- []` for main actions, result-review milestones, and restoring settings. Do not put a checkbox on every sentence, optional extra, explanation, or warning. Checkbox progress is not proof that an action succeeded.
- Put the command immediately after the action. Put its explanation and expected result after the command.
- Name the exact output label, file, field, or case to inspect. Replace abstract advice with a check a learner can actually make.
- Keep optional work clearly separate from the required path. Preserve genuine human decision and approval checkpoints.

### Teach without distracting

- Give a short introduction when a concept is new. Explain why it matters to this exercise, not everything known about it.
- Retain useful lab context and established safety boundaries. Remove repeated reminders that a lab is not production, vague references to "approved agents", and governance language that does not help complete the step.
- Do not add hypothetical failure paragraphs to normal instructions. Retain existing deliberate-failure exercises and concrete, documented recovery steps needed for the lab. Recommend a compact expandable hint only when it solves a known learner problem.
- Leave benchmark setup such as connection warm-up out of the normal walkthrough unless the learner must act on it or the lesson specifically examines measurement. Do not imply it trains the model.
- Keep ordinary paragraphs and checkbox items on one source line. Let the editor wrap visually. Preserve structural line breaks in tables, code, lists, and Skillable block syntax; do not reflow the entire file for a local edit.
- Use sentence case for Markdown headings, short paragraphs, and selective bold for UI or output labels. Avoid raw JSON examples when a small readable result teaches the same thing.

Apply these concrete replacements rather than rewriting unnecessary disclaimers:

| Avoid | Prefer |
| --- | --- |
| "Seven known alerts and one run are an experiment, not a release gate or a production accuracy claim." | Delete the obvious lab disclaimer. |
| "Keep the approved agents unchanged." | Delete it; name an agent only when the learner must use or change it. |
| "Savings do not excuse a missed sanctions escalation." | "For ALT-1005, the sanctions-hit case, the expected recommendation is `escalate`. Check whether both models got it right." |
| Normal-path paragraphs about hypothetical missing verdicts and retry delays | Focus on the exercise; keep recovery for known problems separate. |
| Several sentences about speculative warm-up and quota waits | "Allow **about 1-1.5 minutes** for this comparison. Read the explanation below while it runs." |
| "Ignores its output token" or "output tokens are not priced" | "Output tokens are free." when established by the pricing evidence. |

### Make results understandable

- Explain golden-answer correctness separately from reviewer agreement. A reviewer can agree with an incorrect recommendation.
- Use an existing checkmark/cross legend or a small labeled example when available. Never claim the CLI shows a marker that its implementation does not print.
- Distinguish the workflows being compared. A label-only Decision-1 path is not an identical-output replacement for GPT explanations and reviewer issues.
- Explain speed as the reported batch-time ratio and cost as average cost per alert when those are the metrics the script computes. Do not imply that every alert was individually that much faster.
- Check the cost formula against current evidence. Say "output tokens are free" when that is established; do not describe free tokens as ignored, missing, or unpriced. Keep changing prices in the guide or verified configuration, not frozen in this skill.
- Show only the expected result needed to interpret the exercise. Do not invent exact wording, accuracy, confidence, or performance for a future model run.

Replace compact results such as `classic request_info / agree, Decision-1 close_false_positive / agree, expected close_false_positive` with separately labeled results when the CLI supports them:

```text
ALT-1006 | Golden: close_false_positive
  Classic (GPT) request_info [does not match golden] | Reviewer: agree
  Decision-1    close_false_positive [matches golden] | Reviewer: agree
```

Use the CLI's existing colored checkmarks and crosses in place of the bracketed labels when available. The correctness marker belongs to the recommendation, not to the reviewer. In this example, both reviewers agree, but only Decision-1's recommendation matches the golden answer.

### Respect the learner's time

- Use a small, representative sample that preserves the lesson; include the required dispositions and important cases. Do not change the sample or intended coverage without permission.
- Put a useful approximate runtime next to a long-running command. Derive it from supplied measurements or verified behavior, and keep the evidence and calculation in the author-facing review rather than the learner paragraph.
- For comparable work, a simple sample-size estimate is sufficient. For example, half of a roughly 165-second run suggests about 1-1.5 minutes, not a newly invented 2-3-minute warning. Reuse an explicit session-owner estimate unless newer measurements contradict it.
- If there is no evidence for timing, report that to the author rather than inventing a wait.
- Put "While it runs" reading after the command, followed by "When it finishes" and a result checkpoint. Keep reading on the same Skillable page as the running action.
- Verify request counts, concurrency, and configured RPM/TPM limits internally. Do not promise that fewer workers prevent throttling, or invent sleeps and retry explanations. Include quota details only when they change an action the learner needs to take.
- Add up the step timings and compare them with the advertised lab duration. Include explicit waits, reading, portal work, and restoring settings; do not silently squeeze those into an impossible schedule.

## Check Skillable behavior

- Preserve `===` page breaks, `@lab...` replacement tokens, resource aliases, `+++type text+++`, and interactive prompts. Validate their syntax separately from ordinary Markdown.
- Verify referenced files and local links, balanced fences and delimiters, heading hierarchy, checkbox placement, and any actual trailing-space hard breaks.
- Do not convert `- []` to GitHub task syntax merely because a generic Markdown tool prefers it.
- Recommend only documented features with a specific benefit for this guide. Prefer wrapped code blocks, meaningful progress checkpoints, and a small knowledge or hint block over adding more UI.
- Do not invent configured Activity tokens, dropdown variables, or profile features. Mark recommendations requiring Studio setup as author actions, not completed work.
- Check the rendered instructions in Skillable Studio or the lab client if access is available. Inspect navigation, tokens, code copying, line wrapping, indentation, and checkbox behavior. A GitHub or VS Code preview cannot establish Skillable rendering.
- If no Skillable preview is available, say rendering remains unverified in the author-facing result. Do not add that caveat to the attendee guide.

### Use platform features selectively

The syntax below was checked against official Skillable documentation on 2026-11-06. Recheck it when using a new feature or when documentation and Studio rendering disagree. Do not assume that a documented feature is configured in this profile.

| Feature | Syntax or setup | Use in this guide |
| --- | --- | --- |
| Progress checkpoints | `- [] Run the command.`; Skillable also documents `1. []` | Main actions and result checks. Checked tasks contribute to reported progress; avoid unnecessary checkboxes on optional work. |
| Page navigation | `===` on its own line | Preserve lab boundaries and keep a running command with its reading. |
| Wrapped command blocks | A fenced block tagged `powershell-wrap` | Long commands in the narrow instruction pane, without inserting newlines into the command itself. |
| Noncopyable output | A fenced block tagged `text-nocopy` | Sample output rather than executable commands; confirm rendering in Studio. |
| Copy to clipboard | `++text++` | Short values learners need to paste. |
| Type into the VM | `+++text+++` | Input at the active VM cursor; explain the destination when unclear. |
| Additional concept reading | `> [!Knowledge] Text.` | Useful optional background; a "more" link appears after four lines. Never hide required steps. |
| Expandable hint | `> [+Hint] Hint title`, followed by quoted content | Help for a known confusing task, without making everyone read it. |
| Visible note | `> [!Note] Text.` | Environment details learners need now, not obvious lab disclaimers. |
| Important action warning | `> [!Alert] Text.` | Concrete consequential actions, not invented failure scenarios. |
| Screenshots | `![Descriptive alt text](verified-path-or-url)` | The relevant UI region when prose is insufficient; retain accessible text instructions. |

Preserve structural lines for fences, table rows, and block markers. One source line per paragraph does not mean collapsing Markdown structure. Keep special blocks sparse instead of decorating every action.

For a long command, recommend this pattern and verify that copying preserves the original command:

````markdown
- [] Start the comparison on seven alerts:

```powershell-wrap
uv run python src/decisions/compare_investigations.py ALT-1001 ALT-1002 ALT-1005 ALT-1006 ALT-1008 ALT-1010 ALT-1013 --workers 2
```

**While it runs**

Read the explanation of the two investigation paths.
````

Use an expandable hint only when the ordinary result explanation is insufficient:

```markdown
> [+Hint] Read the result markers
>
> A checkmark means the recommendation matches the golden answer. The reviewer verdict is shown separately.
```

### Separate author configuration from learner instructions

- **Activities and questions:** Create an Activity in the lab profile's **Activities** menu, copy its actual generated token, and insert it into `content.md`. Never invent an Activity token. A useful question could ask why reviewer agreement does not prove golden-answer correctness. Automated checkpoints should inspect deterministic artifacts or configuration, not guarantee probabilistic model answers or write human approvals. Recommend configuration separately; this skill does not create automation. Keep separately requested test-only automation under `tests/e2e/`.
- **Variables:** Skillable documents `@lab.TextBox(name)` and `@lab.Variable(name)`. Reuse existing resource replacement tokens first; do not ask learners to enter values already available on the Resources tab.
- **Conditional content:** For genuinely different optional paths, Skillable documents `@lab.DropDownList(path)[core,extended]` and a section starting with `:::extended(path=extended)`, followed by content and a closing `:::`. Sections are hidden until their variable matches. Do not hide the required path or introduce branching instead of writing clearly.
- **External instructions:** Skillable documents `!instructions[](url)` with a publicly accessible Markdown URL. Do not assume the current profile uses it, publish private content, or change the profile's source during a wording review. If the lab still displays old text, distinguish the saved file, Git revision, and configured instruction source rather than repeatedly overwriting the file.
- **VM command buttons:** Skillable documents ``@[label](`command`){PowerShell visible}`` and related modes. These require Integration Services in the VM image. Do not replace visible learner commands with hidden execution by default: terminal output is part of the lesson. Confirm VM configuration, working directory, visibility, and interaction before recommending buttons.

Prefer each feature's official documentation and actual Studio-generated syntax when examples use inconsistent capitalization or different inclusion mechanisms. Check navigation, progress, replacement values, type-text destinations, copying/wrapping, block expansion, and list indentation in the actual learner preview.

## Edit and verify

1. Before editing, reread the affected passage. Integrate current authored changes; never restore an earlier version of the file wholesale.
2. Make a coherent, minimal patch. Preserve PowerShell command semantics, datasets, case outcomes, human checkpoints, and required restores.
3. Recheck directly dependent prose, numbering, sample size, command counts, agent listings, and timing. Do not update unrelated sections.
4. Review the final diff and reread the saved affected text. If it changed concurrently, reconcile the current file before claiming completion; do not repeatedly overwrite it.
5. Perform a learner read-through using only the guide's preceding context: Can the learner find the location, execute the action, identify the result, explain it, and continue? Fix any passage that needs an extra chat explanation.

## Report to the author

Lead with the useful outcome. For a review, list only actionable findings with `content.md` line ranges, the confusing or incorrect claim, evidence, and a concrete replacement. Separate correctness fixes from optional Skillable improvements. Do not use arbitrary scores or ceremonial readiness labels.

For edits, give a short description of the meaningful changes. State unresolved technical questions or unavailable Skillable rendering plainly. Do not claim a live run, profile update, or rendered preview unless it happened.

## Research sources

Apply Skillable's "where, what, why" and short-action guidance, Microsoft's scannable imperative procedures, and Google's action/command/result ordering within this repository's existing lab structure. Do not mechanically replace its numbering or introduce redundant headings and explanations.

- [Skillable: Instructions writing best practices](https://docs.skillable.com/docs/instructions-writing-best-practices): step structure, copyable input, context, layout, and learner preview.
- [Skillable: Creating instructions with Markdown syntax](https://docs.skillable.com/docs/creating-instructions-with-markdown-syntax): checkboxes, page breaks, code modifiers, type text, blocks, and VM integration.
- [Skillable: Manage instructions outside of Studio](https://docs.skillable.com/docs/manage-instructions-outside-of-studio): external Markdown, rendering differences, and configured Activity tokens.
- [Skillable: Variably display instructions](https://docs.skillable.com/docs/variably-display-instructions): variables, dropdowns, and conditional sections.
- [Microsoft: Writing step-by-step instructions](https://learn.microsoft.com/en-us/style-guide/procedures-instructions/writing-step-by-step-instructions): clear, scannable procedures.
- [Google: Procedures](https://developers.google.com/style/procedures): action/command/result ordering and accessible procedures.
