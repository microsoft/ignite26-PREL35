# Data

`cases.json` contains 14 fictional alerts, their transactions, documents and screening lists.
`golden.jsonl` contains the expected training outcomes. These are not real customer records.

## A case with useful public research

`ALT-1008` combines incoming payments from several senders, an onward payment to a fictional crypto
exchange and a fictional remote-job message asking the recipient to move money for a commission.
That is realistic enough to research the **pattern**, without searching for the fictional customer.

The `public_research_examples` entry in `cases.json` supplies this fixed query:

```text
site:justice.gov money mule initiative online job offers receive forward money virtual currency
```

The [DOJ Money Mule Initiative](https://www.justice.gov/civil/consumer-protection-branch/money-mule-initiative)
describes those recruitment methods, including virtual currency and people who are deceived into moving money.
Web IQ can supply this public context and further official references. Results can change; verify the returned
links and what they actually say. U.S. guidance provides typology context, not EU legal advice.

Compare the public pattern with the case evidence and legitimate explanations. Do not treat a public article as
proof that the fictional customer committed a crime. Never send customer names, account details, transaction
records or the full case prompt to public web search. The example is not yet wired into the agent workflow.

`investigator/schemes.json` contains eight separate synthetic typology notes for the Search index.
`evidence/scenarios.json` contains PII-free synthetic inputs for the hosted evidence agent.
