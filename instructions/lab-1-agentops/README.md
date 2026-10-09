# Lab 1 - AgentOps

**Objective:** Operate agents and models through production-grade, governed processes.

## Topics

### Covered in this lab

- **Evaluation framework and datasets:** build an evaluation dataset and run evaluators against the agent.
- **Release gates:** run the evaluation-and-gate script that a CI/CD pipeline runs on every change, and see a release pass or get blocked on its score.
- **Evidence Pack:** the artefact the release gate produces for approvers and auditors.
- **Observability:** traces and quality metrics for the agent.

### Consider when moving to production

- **Maturity model and lifecycle:** where your organisation sits today and what the next stage requires.
- **CI/CD for prompts and agents:** wire the release gate you ran into a pipeline such as GitHub Actions, so it runs on every push. The pipeline needs its own Azure identity (a service principal), which is outside a lab's permissions, so this lab walks through it rather than doing it hands-on.
- **Model retirement:** plan for model versions being retired; re-run the same evaluation against the replacement before switching.

## Prerequisites

- _TBD_

## Steps

1. _TBD_

## Next

Continue to [Lab 2 - Security and trust](../lab-2-security-and-trust/README.md).
