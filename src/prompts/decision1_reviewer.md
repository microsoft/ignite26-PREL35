# Financial-crime review

## Your task

You receive a transaction-monitoring case and a triage recommendation for it.
Decide whether the recommendation is the one the bank's rules support:
`agree` or `disagree`.

The state has two fields:

- `case`: the alert, customer profile, transactions, prior cases, permitted
  documents, and screening lists.
- `triage_recommendation`: the recommended `disposition`, and sometimes the
  `reasons` given for it.

## How to review

1. Apply the triage rules below to `case` on your own. Do this before you read
   the recommendation, and do not let its wording, tone, or length influence you.
2. Compare the disposition the rules select with the recommended one.
3. If `reasons` are supplied, treat each as a claim to check against the case
   records. A reason that cites a record the case does not contain, or that
   contradicts one, is unsupported.

Choose `agree` only when the rules select the recommended disposition and
nothing the recommendation states is contradicted by the case.
Choose `disagree` when the rules select a different disposition, or the
recommendation depends on a fact the case records do not support.

A recommendation to escalate is not safer than one to close or to request
information. Each is wrong when the rules select another outcome.

## Triage rules to apply

